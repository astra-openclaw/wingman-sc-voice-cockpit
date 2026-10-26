"""
database.py — NavPoint SQLite persistence
Author: Mallachi
"""

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from sc_voice_cockpit.navpoint.navpoint_location_names import display_name


logger = logging.getLogger(__name__)

# Schema version 4 (coordinate stack): `frame` says which coordinate frame a row
# lives in, and `frame_id` identifies the specific frame instance. Bump
# _SCHEMA_VERSION and the _init_db migration together when the shape changes.
#
# frame = 'local'  -> body-fixed, the surface case. `body` is the frame key and
#                     frame_id is empty.
# frame = 'system' -> the star system's frame, for waypoints in open space.
#                     `body` is empty and frame_id holds the SolarSystem zone id.
# frame = 'stack'  -> the coordinate-stack rows, v4 onward. Root is the address,
#                     `coordinate_stack` holds the whole accepted capture, and
#                     NOTHING about the row is keyed on a zone name. Deliberately
#                     a NEW value rather than a reinterpretation of the two
#                     above: an old row and a new row mean different things by
#                     the same `x/y/z`, so overloading either would silently
#                     re-address every waypoint the user already has.
#
# The frames are never comparable: a coordinate means a different place in each,
# so every distance or bearing between rows must check `frame` first.
_SCHEMA_VERSION = 4

_FRAME_LOCAL = "local"
_FRAME_SYSTEM = "system"
_FRAME_STACK = "stack"

# Legacy rows carry 0, meaning "no Root position was ever captured for this
# waypoint". NULL Root columns are therefore MEANINGFUL and are never
# backfilled: synthesising a Root position for a body-fixed local row would be
# inventing a coordinate, which is the one thing this skill exists not to do.
_COORDINATE_SCHEMA_LEGACY = 0
_COORDINATE_SCHEMA_STACK = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS navpoints (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    server_id   TEXT    NOT NULL DEFAULT '',
    x           REAL    NOT NULL DEFAULT 0,
    y           REAL    NOT NULL DEFAULT 0,
    z           REAL    NOT NULL DEFAULT 0,
    body        TEXT    NOT NULL,
    system      TEXT    NOT NULL DEFAULT '',
    zone        TEXT    NOT NULL DEFAULT '',
    heading     REAL    NOT NULL DEFAULT 0,
    timestamp   TEXT    NOT NULL,
    notes       TEXT    NOT NULL DEFAULT '',
    frame       TEXT    NOT NULL DEFAULT 'local',
    frame_id    TEXT    NOT NULL DEFAULT '',
    coordinate_schema INTEGER NOT NULL DEFAULT 0,
    root_x      REAL    NULL,
    root_y      REAL    NULL,
    root_z      REAL    NULL,
    coordinate_stack  TEXT NULL
);
"""

# Every column added since v2, with the clause that adds it. Driving both
# migration paths off ONE list is deliberate: a v2 database upgrading straight
# to v4 must end up identical to a v2 that went through v3, and two hand-written
# ladders drift.
_ADDITIVE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("frame", f"TEXT NOT NULL DEFAULT '{_FRAME_LOCAL}'"),
    ("frame_id", "TEXT NOT NULL DEFAULT ''"),
    ("coordinate_schema", "INTEGER NOT NULL DEFAULT 0"),
    ("root_x", "REAL NULL"),
    ("root_y", "REAL NULL"),
    ("root_z", "REAL NULL"),
    ("coordinate_stack", "TEXT NULL"),
)


@dataclass
class NavPoint:
    id: int
    name: str
    server_id: str
    x: float
    y: float
    z: float
    body: str
    system: str
    zone: str
    heading: float
    timestamp: str
    notes: str
    frame: str = _FRAME_LOCAL
    frame_id: str = ""
    # v4 fields, appended with defaults so every existing constructor call and
    # every existing test keeps working unchanged.
    coordinate_schema: int = _COORDINATE_SCHEMA_LEGACY
    root_x: float | None = None
    root_y: float | None = None
    root_z: float | None = None
    coordinate_stack: str | None = None


class NavPointDatabase:
    """SQLite-backed navpoint store."""

    def __init__(self, db_dir: str) -> None:
        db_path = Path(db_dir) / "navpoints.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._path = str(db_path)
        self._init_db()
        logger.info("NavPoint database at %s", self._path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _add_missing_columns(conn: sqlite3.Connection) -> list[str]:
        """Add any post-v2 column the table does not have yet.

        Purely additive: SQLite's ADD COLUMN rewrites no rows and touches no
        existing value, so every waypoint survives byte for byte and simply
        acquires defaults. Returns the columns it added, for the log.
        """
        existing = {row[1] for row in conn.execute("PRAGMA table_info(navpoints)")}
        added = []
        for name, clause in _ADDITIVE_COLUMNS:
            if name not in existing:
                conn.execute(f"ALTER TABLE navpoints ADD COLUMN {name} {clause}")
                added.append(name)
        return added

    def _init_db(self) -> None:
        """Create or migrate the navpoints table to schema v4.

        A DB below version 2 (a fresh file or the pre-4.7 planet/moon table) has
        its table dropped and rebuilt: pre-4.7 stellar waypoints are stale twice
        over and are deliberately not migrated. Only a non-empty pre-v2 table
        being wiped is worth a warning; a fresh init stays silent.

        v2 and v3 upgrade ADDITIVELY, in one step whichever they start from. The
        user's existing waypoints keep every field they had and gain
        `coordinate_schema = 0` with NULL Root columns, which is the honest
        record that no Root position was ever captured for them. Nothing is
        reinterpreted: a v3 `system` row stays a `system` row and keeps its
        frame-id behaviour.
        """
        with self._connect() as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version >= _SCHEMA_VERSION:
                conn.executescript(_SCHEMA)
                return

            if version in (2, 3):
                added = self._add_missing_columns(conn)
                conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
                logger.info(
                    "NavPoint schema upgraded from v%d to v%d: added %s, "
                    "existing waypoints kept unchanged.",
                    version,
                    _SCHEMA_VERSION,
                    ", ".join(added) or "no columns",
                )
                return

            table_exists = (
                conn.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'table' AND name = 'navpoints'"
                ).fetchone()
                is not None
            )
            stale_rows = 0
            if table_exists:
                stale_rows = conn.execute("SELECT COUNT(*) FROM navpoints").fetchone()[0]

            conn.executescript("DROP TABLE IF EXISTS navpoints;\n" + _SCHEMA)
            conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")

            if stale_rows > 0:
                logger.warning(
                    "NavPoint schema upgraded to v%d: wiped %d pre-v2 waypoint(s). "
                    "Pre-4.7 stellar waypoints are stale and are not migrated.",
                    _SCHEMA_VERSION,
                    stale_rows,
                )

    # ------------------------------------------------------------------ #
    # Write operations
    # ------------------------------------------------------------------ #

    def add_navpoint(
        self,
        name: str,
        server_id: str,
        body: str,
        x: float,
        y: float,
        z: float,
        system: str = "",
        zone: str = "",
        heading: float = 0.0,
        notes: str = "",
        frame: str = _FRAME_LOCAL,
        frame_id: str = "",
        coordinate_schema: int = _COORDINATE_SCHEMA_LEGACY,
        root_x: float | None = None,
        root_y: float | None = None,
        root_z: float | None = None,
        coordinate_stack: str | None = None,
    ) -> NavPoint:
        if frame not in (_FRAME_LOCAL, _FRAME_SYSTEM, _FRAME_STACK):
            raise ValueError(f"unknown frame {frame!r}")
        if frame == _FRAME_LOCAL and (not body or not body.strip()):
            raise ValueError(
                "body is required: a local XYZ is meaningless without the planet "
                "or moon that owns the frame."
            )
        if frame == _FRAME_SYSTEM and (not frame_id or not frame_id.strip()):
            raise ValueError(
                "frame_id is required for a system-frame waypoint: without the "
                "SolarSystem zone id there is no way to tell a later session's "
                "frame from this one."
            )
        if frame == _FRAME_STACK:
            # A stack row's whole point is that Root addresses it. Storing one
            # without a Root position would leave a waypoint that no route can
            # ever be computed for and that nothing downstream could tell from a
            # legacy row.
            if coordinate_schema != _COORDINATE_SCHEMA_STACK:
                raise ValueError(
                    f"a stack waypoint carries coordinate_schema "
                    f"{_COORDINATE_SCHEMA_STACK}, not {coordinate_schema!r}."
                )
            if None in (root_x, root_y, root_z):
                raise ValueError(
                    "root_x, root_y and root_z are required for a stack "
                    "waypoint: Root is its only long-range address."
                )
            if not coordinate_stack or not str(coordinate_stack).strip():
                raise ValueError(
                    "coordinate_stack is required for a stack waypoint: without "
                    "the nested rows there is no local candidate to fall back "
                    "from, only a Root position wearing a stack row's label."
                )
        timestamp = datetime.now().isoformat()
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO navpoints
                   (name, server_id, x, y, z, body, system, zone, heading,
                    timestamp, notes, frame, frame_id, coordinate_schema,
                    root_x, root_y, root_z, coordinate_stack)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (name, server_id, x, y, z, body, system, zone, heading,
                 timestamp, notes, frame, frame_id, coordinate_schema,
                 root_x, root_y, root_z, coordinate_stack),
            )
        return NavPoint(
            id=cur.lastrowid,
            name=name,
            server_id=server_id,
            x=x,
            y=y,
            z=z,
            body=body,
            system=system,
            zone=zone,
            heading=heading,
            frame=frame,
            frame_id=frame_id,
            timestamp=timestamp,
            notes=notes,
            coordinate_schema=coordinate_schema,
            root_x=root_x,
            root_y=root_y,
            root_z=root_z,
            coordinate_stack=coordinate_stack,
        )

    def delete_navpoint(self, navpoint_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM navpoints WHERE id = ?", (navpoint_id,))

    def wipe_on_server_change(self, current_server_id: str) -> int:
        """Wipe all waypoints when a proven server change is detected.

        Fail-safe against OCR garble: both sides of the comparison must be
        non-empty to prove a change. A blank current_server_id (an unreadable
        server line) never wipes. Only a stored row carrying a NON-EMPTY
        server_id that differs from current_server_id proves the session moved
        servers; when that happens ALL rows are deleted, empty-server rows
        included, because a proven server change makes them stale too.

        Returns the number of rows deleted (0 when nothing is wiped).
        """
        if not current_server_id or not current_server_id.strip():
            return 0
        with self._connect() as conn:
            changed = conn.execute(
                "SELECT COUNT(*) FROM navpoints "
                "WHERE server_id != '' AND server_id != ?",
                (current_server_id,),
            ).fetchone()[0]
            if changed == 0:
                return 0
            total = conn.execute("SELECT COUNT(*) FROM navpoints").fetchone()[0]
            conn.execute("DELETE FROM navpoints")
        return total

    def get_stored_server_id(self) -> str:
        """Return the stored session's server_id that wipe keys on.

        wipe_on_server_change wipes when a stored row carries a NON-EMPTY
        server_id differing from the current one; this returns that stored
        server_id (the newest non-empty one) so a caller can confirm a change
        across two captures before triggering the wipe. Empty string when no
        row carries a server_id. In normal use every surviving row shares one
        server_id (a wipe clears the rest before new marks are added), so a
        single value faithfully represents the stored session and cannot
        disagree with wipe_on_server_change's own row selection.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT server_id FROM navpoints "
                "WHERE server_id != '' ORDER BY timestamp DESC LIMIT 1"
            ).fetchone()
        return row["server_id"] if row else ""

    def rename_navpoint(self, navpoint_id: int, new_name: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE navpoints SET name = ? WHERE id = ?",
                (new_name, navpoint_id),
            )

    def update_notes(self, navpoint_id: int, notes: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE navpoints SET notes = ? WHERE id = ?",
                (notes, navpoint_id),
            )

    # ------------------------------------------------------------------ #
    # Read operations
    # ------------------------------------------------------------------ #

    def get_navpoints(self, body: str | None = None) -> list[NavPoint]:
        where = ""
        params: list[str] = []
        if body:
            where = "WHERE LOWER(body) = LOWER(?)"
            params.append(body)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM navpoints {where} ORDER BY timestamp DESC",
                params,
            ).fetchall()
        return [self._row_to_navpoint(r) for r in rows]

    def find_navpoint_by_name(self, name: str) -> NavPoint | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM navpoints WHERE LOWER(name) = LOWER(?)",
                (name,),
            ).fetchone()
        return self._row_to_navpoint(row) if row else None

    def find_navpoint_by_id(self, navpoint_id: int) -> NavPoint | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM navpoints WHERE id = ?",
                (navpoint_id,),
            ).fetchone()
        return self._row_to_navpoint(row) if row else None

    def search_navpoints(self, query: str) -> list[NavPoint]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM navpoints WHERE LOWER(name) LIKE LOWER(?) ORDER BY timestamp DESC",
                (f"%{query}%",),
            ).fetchall()
        return [self._row_to_navpoint(r) for r in rows]

    def count_navpoints(self) -> int:
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM navpoints").fetchone()[0]

    def get_distinct_bodies(self) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT body FROM navpoints WHERE body != '' ORDER BY body"
            ).fetchall()
        return [r["body"] for r in rows]

    # ------------------------------------------------------------------ #
    # Serialisation
    # ------------------------------------------------------------------ #

    @staticmethod
    def _row_to_navpoint(row: sqlite3.Row) -> NavPoint:
        keys = row.keys()
        return NavPoint(
            id=row["id"],
            name=row["name"],
            server_id=row["server_id"],
            x=row["x"],
            y=row["y"],
            z=row["z"],
            body=row["body"],
            system=row["system"],
            zone=row["zone"],
            heading=row["heading"],
            timestamp=row["timestamp"],
            notes=row["notes"],
            frame=row["frame"] if "frame" in keys else _FRAME_LOCAL,
            frame_id=row["frame_id"] if "frame_id" in keys else "",
            coordinate_schema=(
                row["coordinate_schema"] if "coordinate_schema" in keys
                else _COORDINATE_SCHEMA_LEGACY
            ),
            root_x=row["root_x"] if "root_x" in keys else None,
            root_y=row["root_y"] if "root_y" in keys else None,
            root_z=row["root_z"] if "root_z" in keys else None,
            coordinate_stack=(
                row["coordinate_stack"] if "coordinate_stack" in keys else None
            ),
        )

    @staticmethod
    def navpoint_to_dict(np: NavPoint) -> dict:
        return {
            "id": np.id,
            "name": np.name,
            "server_id": np.server_id,
            "x": np.x,
            "y": np.y,
            "z": np.z,
            "body": np.body,
            "display_location": display_name(np.frame_id, np.body),
            "system": np.system,
            "zone": np.zone,
            "heading": np.heading,
            "timestamp": np.timestamp,
            "notes": np.notes,
            # The HUD needs the frame to know whether a body-keyed comparison
            # is even meaningful for this row.
            "frame": np.frame,
            "frame_id": np.frame_id,
            # 0 on every legacy row, 1 on a coordinate-stack row. The HUD reads
            # it to label the row's mode; the stack JSON itself is deliberately
            # NOT sent, because nothing on the page routes and a whole nested
            # stack per waypoint would be paid for on every refresh.
            "coordinate_schema": np.coordinate_schema,
        }
