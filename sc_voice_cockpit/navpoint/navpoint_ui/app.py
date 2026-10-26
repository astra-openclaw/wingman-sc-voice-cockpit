"""
navpoint_ui/app.py — NavPoint HUD FastAPI server
Author: Mallachi
"""

from __future__ import annotations

import ipaddress
import logging
import re
import secrets
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

from sc_voice_cockpit.navpoint import coordinate_stack
from sc_voice_cockpit.navpoint.navigation import POSITION_FRESHNESS_S, space_guidance, ring_deflection, stack_space_guidance
from sc_voice_cockpit.navpoint.active_frame import same_planet, target_snapshot
import math

if TYPE_CHECKING:
    from sc_voice_cockpit.navpoint.database import NavPoint, NavPointDatabase


logger = logging.getLogger(__name__)
_STATIC_DIR = Path(__file__).parent / "static"
# The only files the page loads. A requested name is looked up here and never
# joined onto a path, so "../", backslashes, drive letters and UNC names cannot
# reach any other file on disk.
_STATIC_FILES = {"app.js": "application/javascript", "style.css": "text/css"}
# Requests addressed to this computer. Wingman's frozen build has no Starlette
# trustedhost module, so _protect() checks the Host header itself, the same way
# Accountant's dashboard (skills/sc_accountant/erp_web.py) does.
_LOCAL_HOST = re.compile(r"(?:127\.0\.0\.1|localhost|\[::1\])(?::[0-9]{1,5})?", re.IGNORECASE)
_SESSION_COOKIE = "sc_navpoint_session"

# POSITION_FRESHNESS_S is re-exported from navigation so the page, the server
# and the ring overlay all enforce one staleness rule; see its definition there.


class NavPointServer:
    """FastAPI server for the NavPoint HUD."""

    def __init__(
        self,
        db: "NavPointDatabase",
        port: int = 7869,
        arrival_alert_m: float = 1000.0,
        arrival_stop_m: float = 250.0,
        on_target_change: "Callable[[NavPoint | None], None] | None" = None,
        share_on_lan: bool = False,
        access_token: str = "",
    ) -> None:
        self._db = db
        self._port = port
        # The API has no other authentication, so the server listens on
        # loopback unless phone sharing is asked for, and a phone then needs
        # the QR code's token (see _protect).
        self._lan_host = ""
        if share_on_lan:
            from .sharing import get_lan_ip
            address = get_lan_ip()
            if address:
                parsed = ipaddress.ip_address(address)
                if parsed.is_private and not (
                    parsed.is_loopback or parsed.is_unspecified or parsed.is_multicast
                ):
                    self._lan_host = address
        self._access_token = (
            (access_token or secrets.token_urlsafe(18)) if self._lan_host else ""
        )
        self._arrival_alert_m = arrival_alert_m
        self._arrival_stop_m = arrival_stop_m
        # The page can change the target too, and until this existed that change
        # reached no further than this object: the skill kept its own
        # `_active_target`, so clearing from the page left the poll loop running
        # and the ring guiding to a target the page had already forgotten.
        self._on_target_change = on_target_change
        self._thread: threading.Thread | None = None
        self._server: uvicorn.Server | None = None
        self._active_target: "NavPoint | None" = None
        self._current_position: dict | None = None
        self._update_token: int = 0
        self._app = self._create_app()

    # ------------------------------------------------------------------ #
    # State updates (called from main skill)
    # ------------------------------------------------------------------ #

    def set_active_target(self, navpoint: "NavPoint | None") -> None:
        self._active_target = navpoint
        self._update_token += 1

    def _notify_target_change(self, navpoint: "NavPoint | None") -> None:
        """Tell the skill the page changed the target.

        Runs on the server's own thread. A failing callback must never turn a
        successful state change into an HTTP error, so it is logged and
        swallowed: the page's view is already correct either way.
        """
        if self._on_target_change is None:
            return
        try:
            self._on_target_change(navpoint)
        except Exception:
            logger.exception("NavPoint HUD target-change callback failed")

    def set_position(self, pos_data: dict) -> None:
        self._current_position = pos_data
        self._update_token += 1

    def notify_update(self) -> None:
        self._update_token += 1

    # ------------------------------------------------------------------ #
    # Access
    # ------------------------------------------------------------------ #

    async def _protect(self, request: Request, call_next):
        """Admit this computer, and a phone holding the QR code's session."""
        host = request.headers.get("host", "")
        local_host = _LOCAL_HOST.fullmatch(host)
        shared_host = self._lan_host and re.fullmatch(
            re.escape(self._lan_host) + r"(?::[0-9]{1,5})?", host
        )
        if not (local_host or shared_host):
            # Another site's page that rebinds its name to this computer.
            return JSONResponse({"error": "Invalid host"}, status_code=400)
        # A spoofed Host header never skips the session check.
        local_client = request.client and request.client.host in {"127.0.0.1", "::1"}
        if self._lan_host and not (local_host and local_client):
            supplied = request.query_params.get("share", "")
            if (
                request.method == "GET"
                and request.url.path == "/"
                and secrets.compare_digest(supplied.encode(), self._access_token.encode())
            ):
                response = RedirectResponse(
                    "/",
                    status_code=303,
                    headers={"Referrer-Policy": "no-referrer", "Cache-Control": "no-store"},
                )
                response.set_cookie(
                    _SESSION_COOKIE, self._access_token, httponly=True, samesite="strict"
                )
                return response
            session = request.cookies.get(_SESSION_COOKIE, "")
            if not secrets.compare_digest(session.encode(), self._access_token.encode()):
                return JSONResponse(
                    {"error": "Scan NavPoint's QR code in Wingman to open this dashboard."},
                    status_code=403,
                )
        if request.method not in {"GET", "HEAD"}:
            # Changes must come from this dashboard's own page.
            expected = f"{request.url.scheme}://{host}"
            if (
                request.headers.get("origin", "") != expected
                or request.headers.get("sec-fetch-site") == "cross-site"
            ):
                return JSONResponse(
                    {"error": "Open this dashboard directly to make changes."},
                    status_code=403,
                )
        return await call_next(request)

    @property
    def served_app(self):
        """The app as the network sees it, with _protect() before every route.

        Route tests drive ``_app`` directly through TestClient; access tests
        drive this.
        """
        return BaseHTTPMiddleware(self._app, dispatch=self._protect)

    # ------------------------------------------------------------------ #
    # App
    # ------------------------------------------------------------------ #

    def _create_app(self) -> FastAPI:
        app = FastAPI(title="SC NavPoint HUD", docs_url=None, redoc_url=None)

        # Static files
        @app.get("/", response_class=HTMLResponse)
        async def index():
            return _STATIC_DIR.joinpath("index.html").read_text(encoding="utf-8")

        @app.get("/static/{filename:path}")
        async def static_file(filename: str):
            media = _STATIC_FILES.get(filename)
            if media is None:
                return JSONResponse({"error": "Not found"}, status_code=404)
            content = _STATIC_DIR.joinpath(filename).read_text(encoding="utf-8")
            return HTMLResponse(content=content, media_type=media,
                                headers={"Cache-Control": "no-cache"})

        # API: Waypoints
        @app.get("/api/navpoints")
        async def get_navpoints(body: str = ""):
            navpoints = self._db.get_navpoints(body=body or None)
            return {
                "navpoints": [self._db.navpoint_to_dict(n) for n in navpoints],
                "bodies": self._db.get_distinct_bodies(),
            }

        @app.delete("/api/navpoints/{navpoint_id}")
        async def delete_navpoint(navpoint_id: int):
            self._db.delete_navpoint(navpoint_id)
            was_target = bool(
                self._active_target and self._active_target.id == navpoint_id
            )
            if was_target:
                self._active_target = None
            self._update_token += 1
            if was_target:
                # Deleting what is being navigated to has to stop the
                # navigation, or the ring keeps guiding to a row that no longer
                # exists.
                self._notify_target_change(None)
            return {"success": True}

        @app.put("/api/navpoints/{navpoint_id}/name")
        async def rename_navpoint(navpoint_id: int, body: dict):
            new_name = body.get("name", "")
            if new_name:
                self._db.rename_navpoint(navpoint_id, new_name)
                self._update_token += 1
            return {"success": bool(new_name)}

        @app.put("/api/navpoints/{navpoint_id}/notes")
        async def update_notes(navpoint_id: int, body: dict):
            notes = body.get("notes", "")
            self._db.update_notes(navpoint_id, notes)
            self._update_token += 1
            return {"success": True}

        # API: Navigation
        @app.get("/api/nav/state")
        async def get_nav_state():
            """Poll endpoint — returns full navigation state and an update token."""
            target = None
            if self._active_target:
                target = self._db.navpoint_to_dict(self._active_target)
            # Age is computed server-side so browser clock skew is irrelevant;
            # null when there is no position or it carries no capture stamp.
            position_age_s = None
            if self._current_position:
                captured_at = self._current_position.get("captured_at")
                if captured_at is not None:
                    position_age_s = time.time() - captured_at
            # Open space has no north, so the browser's flat-frame bearing math
            # cannot describe it. Rather than port the CamDir trigonometry into
            # JavaScript and keep two implementations in step, the server
            # computes system-frame guidance and the HUD just renders it.
            space = None
            pos = self._current_position
            precision = (pos or {}).get("precision") or {}
            precision_matches = (precision.get("mode") != "cruise"
                                 or precision.get("target_id") == (target or {}).get("id"))
            planet = None
            if (pos and pos.get("active_frame") in ("planet", "site") and precision_matches
                    and same_planet(pos, target)):
                distance = math.dist([pos[k] for k in ("x", "y", "z")],
                                     [target[k] for k in ("x", "y", "z")])
                offsets = ring_deflection(pos, target, position_age_s)
                planet = {"distance_km": distance / 1000,
                          "horizontal_offset_deg": offsets[0] if offsets else None,
                          "vertical_angle_deg": offsets[1] if offsets else None}
            if (
                target
                and precision_matches
                and not (pos or {}).get("body")
                and target.get("frame") == "system"
                and pos
                and pos.get("system_frame")
                and all(pos.get(k) is not None for k in ("x", "y", "z"))
            ):
                # A waypoint from another session's frame is not addressable.
                if not (
                    target.get("frame_id")
                    and pos.get("frame_id")
                    and target["frame_id"] != pos["frame_id"]
                ):
                    space = space_guidance(
                        pos.get("camdir"),
                        (pos["x"], pos["y"], pos["z"]),
                        (target["x"], target["y"], target["z"]),
                    )

            # The stack route is decided by the same pure selector the skill
            # uses, on the server, for the same reason the space guidance is:
            # two implementations of a routing rule cannot be kept in step, and
            # this one owns the 3 Mm ceiling. The page renders the verdict.
            stack_route = None
            if target and target.get("frame") == "stack" and pos and not pos.get("body") and precision_matches:
                decision = coordinate_stack.select_route(
                    coordinate_stack.from_json(
                        getattr(self._active_target, "coordinate_stack", None)
                    ),
                    coordinate_stack.from_dict(pos.get("coordinate_stack") or {}),
                )
                stack_route = {
                    "mode": decision.mode,
                    "reason": decision.reason,
                    "distance_km": (
                        None if decision.distance_m is None
                        else decision.distance_m / 1000.0
                    ),
                    "allow_direction": decision.allow_direction,
                }
                stack_target = dict(target, coordinate_stack=self._active_target.coordinate_stack)
                space = stack_space_guidance(pos, stack_target, position_age_s)
                if space is not None:
                    stack_route["distance_km"] = space["distance_km"]
                    stack_route["allow_direction"] = "horizontal_offset_deg" in space

            return {
                "active_target": target,
                "current_position": self._current_position,
                "position_age_s": position_age_s,
                "arrival_alert_m": self._arrival_alert_m,
                "arrival_stop_m": self._arrival_stop_m,
                "space_guidance": space,
                "planet_guidance": planet,
                "stack_route": stack_route,
                "update_token": self._update_token,
            }

        @app.post("/api/nav/target/{navpoint_id}")
        async def set_nav_target(navpoint_id: int):
            """Set active navigation target by ID."""
            navpoint = self._db.find_navpoint_by_id(navpoint_id)
            if not navpoint:
                return JSONResponse({"error": "Not found"}, status_code=404)

            # Only a fresh, stamped position may refuse a target; a stale or
            # unstamped reading behaves like no position (target set, no refusal).
            pos = self._current_position or {}
            captured_at = pos.get("captured_at")
            fresh = (
                captured_at is not None
                and time.time() - captured_at <= POSITION_FRESHNESS_S
            )
            pos_body = pos.get("body", "") if fresh else ""
            if pos_body and not same_planet(self._current_position, target_snapshot(navpoint)):
                # Proven cross-body: refuse without setting the target.
                return JSONResponse(
                    {
                        "error": (
                            f"Waypoint is on {navpoint.body}, you are on "
                            f"{pos_body}; cross-body navigation is not supported, "
                            f"travel to {navpoint.body} first."
                        ),
                        "target_body": navpoint.body,
                        "current_body": pos_body,
                    },
                    status_code=409,
                )

            self._active_target = navpoint
            self._update_token += 1
            self._notify_target_change(navpoint)
            return {"success": True, "target": self._db.navpoint_to_dict(navpoint)}

        @app.delete("/api/nav/target")
        async def clear_nav_target():
            self._active_target = None
            self._update_token += 1
            self._notify_target_change(None)
            return {"success": True}

        return app

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        config = uvicorn.Config(
            self.served_app,
            host=self.bind_host,
            port=self._port,
            # _protect() trusts loopback, so the peer must be the real one.
            proxy_headers=False,
            log_level="warning",
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(
            target=self._server.run,
            name="sc-navpoint-ui",
            daemon=True,
        )
        self._thread.start()
        logger.info("NavPoint HUD started on %s:%d", self.bind_host, self._port)

    def stop(self) -> None:
        if self._server:
            self._server.should_exit = True
            self._server = None
        self._thread = None
        logger.info("NavPoint HUD stopped")

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._port}"

    @property
    def bind_host(self) -> str:
        """All interfaces only while phone sharing is on."""
        return "0.0.0.0" if self._lan_host else "127.0.0.1"

    @property
    def lan_url(self) -> str:
        if not self._lan_host:
            return ""
        return f"http://{self._lan_host}:{self._port}/?share={self._access_token}"

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()
