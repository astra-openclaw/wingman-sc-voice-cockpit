"""Single listener; NavPoint's route bodies unchanged, protections cover deck and WS."""
from __future__ import annotations
import asyncio
import hashlib
import json
import mimetypes
from pathlib import Path
import re
import secrets
import socket
import threading
import time
import anyio

from fastapi import WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
import uvicorn

from ..navpoint.navpoint_ui.app import NavPointServer, _LOCAL_HOST, _SESSION_COOKIE

STATIC = Path(__file__).resolve().parents[1]/"deck"/"static"
STATIC_NAMES = frozenset({
    "navdesk.js","navdesk.css","app.js","i18n.js","icons.js","canvas.js","awake.js","intro.js","fx.js",
    "deck.css","themes.css","hud.css","fx.css","intro.css","icon.svg",
    "icon-180.png","icon-192.png","icon-512.png","manifest.webmanifest"
})
STATIC_FILES = {name:(STATIC/name,mimetypes.guess_type(name)[0] or "application/octet-stream") for name in STATIC_NAMES}

def pairing_code(directory):
    path=Path(directory)/"deck_pairing_code"
    try:
        code=path.read_text(encoding="utf-8").strip()
        if re.fullmatch("[a-f0-9]{16}",code):return code
    except OSError:pass
    code=secrets.token_hex(8)
    path.write_text(code,encoding="utf-8")
    return code

class CockpitServer(NavPointServer):
    def __init__(self, db, deck, directory, **kwargs):
        self.deck=deck
        self.pairing_code=pairing_code(directory)
        self._attempts={}
        self._attempt_lock=threading.Lock()
        self._socket=None
        self._background=None
        self.heartbeat_timeout=5
        super().__init__(db,**kwargs)
        # Typed pairing also authenticates the plain /nav link, even on loopback.
        self._access_token = self._access_token or self.pairing_code

    @property
    def pairing_url(self):
        """Durable code link; the legacy share-cookie URL remains unchanged."""
        host = self._lan_host or "127.0.0.1"
        return f"http://{host}:{self._port}/?t={self.pairing_code}"

    async def nav_snapshot(self):
        """One-second desk snapshot, including changes made by any DB writer.

        Position updates change update_token too; a content revision avoids an
        unnecessary list request on every OCR sample. Never log the table.
        """
        rows = self._db.get_navpoints()
        target = self._active_target
        if target:
            # Rename/notes may have changed the stored row since target selection.
            # Do not overwrite a concurrently selected target with the old one.
            current = next((row for row in rows if row.id == target.id), None)
            if self._active_target is target:
                self._active_target = current
                if current is None:
                    self._notify_target_change(None)
        endpoint = next(r.endpoint for r in self._app.routes if r.path == "/api/nav/state")
        state = await endpoint()
        data = [self._db.navpoint_to_dict(row) for row in rows]
        state["navpoints_revision"] = hashlib.sha256(
            json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return state

    def _peer_host_ok(self, request):
        host=request.headers.get("host","")
        local_host=bool(_LOCAL_HOST.fullmatch(host))
        shared_host=bool(self._lan_host and re.fullmatch(re.escape(self._lan_host)+r"(?::[0-9]{1,5})?",host))
        peer=request.client.host if request.client else ""
        if peer in {"127.0.0.1","::1"}:return local_host or shared_host
        # No forwarded-header trust. LAN sharing never exposes this to public sources.
        import ipaddress
        try:
            ip=ipaddress.ip_address(peer)
            private=ip.is_private and not (ip.is_loopback or ip.is_unspecified or ip.is_multicast or ip.is_reserved or ip.is_link_local)
        except ValueError:private=False
        return bool(self._lan_host and private and (local_host or shared_host))

    def _session(self, request):
        cookie=request.cookies.get(_SESSION_COOKIE,"")
        if self._access_token and secrets.compare_digest(cookie.encode(),self._access_token.encode()):return 200
        supplied=request.query_params.get("t","").strip().lower()
        address=request.client.host if request.client else ""
        with self._attempt_lock:
            now=time.monotonic()
            times,until=self._attempts.get(address,([],0))
            if until>now:return 429
            times=[t for t in times if now-t<60]
            if supplied and secrets.compare_digest(supplied.encode(),self.pairing_code.encode()):
                self._attempts.pop(address,None);return 200
            if supplied:
                times.append(now)
                until=now+60 if len(times)>=5 else 0
                # Keep bounded under a large number of unauthenticated clients.
                if len(self._attempts)>1024:
                    self._attempts={ip:value for ip,value in self._attempts.items()
                                    if value[1]>now or any(now-t<60 for t in value[0])}
                    if address not in self._attempts and len(self._attempts)>1024:return 429
                self._attempts[address]=(times,until)
                if until:return 429
        return 403

    async def _protect(self, request, call_next):
        if not self._peer_host_ok(request):return JSONResponse({"error":"Invalid source or host"},status_code=400)
        host=request.headers.get("host","")
        if request.method not in {"GET","HEAD"} and (
            request.headers.get("origin","")!=f"{request.url.scheme}://{host}" or
            request.headers.get("sec-fetch-site")=="cross-site"
        ):return JSONResponse({"error":"Open this dashboard directly to make changes."},status_code=403)
        if request.url.path=="/" and request.method=="GET":
            supplied=request.query_params.get("share","")
            if self._access_token and secrets.compare_digest(supplied.encode(),self._access_token.encode()):
                response=RedirectResponse("/",status_code=303,headers={"Referrer-Policy":"no-referrer","Cache-Control":"no-store"})
                response.set_cookie(_SESSION_COOKIE,self._access_token,httponly=True,samesite="strict")
                return response
        path=request.url.path
        public=path=="/" or path[1:] in STATIC_NAMES
        local=request.client and request.client.host in {"127.0.0.1","::1"} and _LOCAL_HOST.fullmatch(host)
        # Loopback NavPoint page keeps its original local-PC authentication behavior.
        required=not public and not (local and (path=="/nav" or path.startswith(("/api/nav","/nav/static/"))))
        status=None
        if required or "t" in request.query_params:
            status=self._session(request)
            if status==429 or required and status!=200:
                return JSONResponse({"error":"Pair this device","status":status},status_code=status)
        response=await call_next(request)
        if status==200 and "t" in request.query_params:
            response.set_cookie(_SESSION_COOKIE,self._access_token,httponly=True,samesite="strict")
        response.headers["Referrer-Policy"]="no-referrer"
        response.headers["Cache-Control"]="no-store"
        return response

    def _create_app(self):
        app=super()._create_app()
        # Change only the navigation HTML's path/static names; every API body remains inherited.
        from starlette.routing import compile_path
        for route in app.routes:
            if route.path=="/":
                route.path="/nav"
                route.path_regex,route.path_format,route.param_convertors=compile_path(route.path)
                original=route.endpoint
                async def nav_index():
                    text=await original()
                    return HTMLResponse(text.replace('/static/','/nav/static/').replace('<nav aria-label="Dashboard sections">', '<nav aria-label="Dashboard sections"><a href="/" id="deck-link"><span class="nav-number">00</span>Tablet Deck</a>'))
                route.endpoint=nav_index
                # FastAPI has already compiled the old endpoint; replace the route explicitly.
                app.routes.remove(route)
                app.add_api_route("/nav",nav_index,methods=["GET"],response_class=HTMLResponse)
                break
        for route in app.routes:
            if route.path=="/static/{filename:path}":
                route.path="/nav/static/{filename:path}"
                route.path_regex,route.path_format,route.param_convertors=compile_path(route.path)

        @app.get("/",response_class=HTMLResponse)
        async def index():
            if not self.deck.skill.deck_setting("deck_enabled",True):return RedirectResponse("/nav")
            return HTMLResponse((STATIC/"index.html").read_text(encoding="utf-8"))
        @app.get("/layout.json")
        async def layout(request:Request):
            layouts=self.deck.manifest.get("layouts",{})
            key="phone" if request.query_params.get("device")=="phone" else "tablet"
            selected=request.query_params.get("layout","")
            files=layouts.get("files",{})
            return files.get(selected) or layouts.get(key) or {"name":"SC Voice-Cockpit","pages":[{"name":"Preparing verified controls","buttons":[]}]}
        @app.get("/layouts.json")
        async def layouts(request:Request):
            phone=request.query_params.get("device")=="phone"
            key="phone" if phone else "tablet"
            layouts=self.deck.manifest.get("layouts",{})
            files=layouts.get("files",{})
            if files:
                choices=[{"file":name,"name":row.get("name",name)} for name,row in files.items()
                         if not row.get("canvas") or (row["canvas"][1]<500)==phone]
                return {"layouts":choices,"pcChoice":"hud-phone.json" if phone else "hud.json"}
            return {"layouts":[{"file":key,"name":layouts.get(key,{}).get("name","SC Voice-Cockpit")}],"pcChoice":key}
        @app.get("/voice.json")
        async def voice():
            commands=[]
            for name,row in self.deck.manifest.get("commands",{}).items():
                obj=self.deck.skill.wingman.commands.get(name)
                phrases=obj.get("instant_activation",[]) if isinstance(obj,dict) else getattr(obj,"instant_activation",[]) or []
                commands.extend([[phrase,name] for phrase in phrases])
            ready=bool(self.deck.skill.deck_setting("deck_tablet_mic",True))
            return {"on":ready,"ready":ready,"state":"ready" if ready else "off","commands":commands}
        @app.get("/links.json")
        async def links():return {"links":[]}
        @app.get("/{filename}")
        async def static(filename:str):
            asset=STATIC_FILES.get(filename)
            if asset is None:return JSONResponse({"error":"Not found"},status_code=404)
            return Response(asset[0].read_bytes(),media_type=asset[1])

        @app.websocket("/ws")
        async def ws(socket:WebSocket):
            if not self._peer_host_ok(socket) or not self.deck.skill.deck_setting("deck_enabled",True):
                await socket.close(code=1008);return
            host=socket.headers.get("host","")
            if socket.headers.get("origin","")!="http://"+host or socket.headers.get("sec-fetch-site")=="cross-site" or self._session(socket)!=200:
                await socket.close(code=1008);return
            await socket.accept()
            key=secrets.token_hex(12);out=asyncio.Queue(maxsize=128)
            self.deck.loop=asyncio.get_running_loop()
            with self.deck.lock:
                self.deck.clients[key]=out
                states=dict(self.deck.states)
            ready=bool(self.deck.skill.deck_setting("deck_tablet_mic",True))
            await socket.send_json({"t":"hello","states":states,"voice":ready,"voiceStatus":{"state":"ready" if ready else "off"}})
            if self.deck.listening:await socket.send_json({"t":"listening","on":True})
            async def sender():
                while True:await socket.send_json(await out.get())
            task=asyncio.create_task(sender())
            heartbeat=time.monotonic()
            try:
                while True:
                    # Only hb extends the lease; arbitrary traffic cannot hold keys forever.
                    remaining=self.heartbeat_timeout-(time.monotonic()-heartbeat)
                    if remaining<=0:break
                    msg=await asyncio.wait_for(socket.receive_json(),timeout=remaining)
                    if not isinstance(msg,dict):continue
                    if msg.get("t")=="hb":heartbeat=time.monotonic()
                    await asyncio.to_thread(self.deck.receive,key,msg)
            except (WebSocketDisconnect,asyncio.TimeoutError,RuntimeError,ValueError,asyncio.CancelledError):
                pass
            finally:
                # ASGI shutdown cancels the receive scope. Always finish input cleanup.
                with anyio.CancelScope(shield=True):
                    await asyncio.to_thread(self.deck.disconnect,key)
                    task.cancel()
                    await asyncio.gather(task,return_exceptions=True)
                    try:await socket.close()
                    except (RuntimeError,WebSocketDisconnect):pass

        async def nav_push():
            while True:
                self.deck.broadcast({"t":"nav","state":await self.nav_snapshot()})
                await asyncio.sleep(1)
        @app.on_event("startup")
        async def startup():
            self.deck.loop=asyncio.get_running_loop()
            self._background=asyncio.create_task(nav_push())
        @app.on_event("shutdown")
        async def shutdown():
            if self._background:
                self._background.cancel()
                await asyncio.gather(self._background,return_exceptions=True)
            for key in list(self.deck.clients):self.deck.disconnect(key)
        return app

    def start(self):
        if self.is_running:return True
        sock=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
        try:
            # Bind before starting the thread: port conflicts are synchronous and nonfatal.
            sock.bind((self.bind_host,self._port));sock.listen(128)
            self._port=sock.getsockname()[1]
        except OSError:
            sock.close();return False
        self._socket=sock
        self._server=uvicorn.Server(uvicorn.Config(self.served_app,host=self.bind_host,port=self._port,proxy_headers=False,log_level="warning",access_log=False))
        self._thread=threading.Thread(target=lambda:self._server.run(sockets=[sock]),name="sc-voice-cockpit-web",daemon=True)
        self._thread.start()
        deadline=time.monotonic()+3
        while self._thread.is_alive() and not self._server.started and time.monotonic()<deadline:
            time.sleep(.005)
        if not self._server.started:
            self.stop();return False
        return True

    def stop(self):
        server,thread=self._server,self._thread
        if server:server.should_exit=True
        if thread and thread is not threading.current_thread():thread.join(timeout=8)
        if thread and thread.is_alive():
            if server:server.force_exit=True
            thread.join(timeout=2)
        if self._socket:self._socket.close()
        self._socket=None
        if not thread or not thread.is_alive():self._server=None;self._thread=None
