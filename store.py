#!/usr/bin/env python3
"""Shared persistence layer for the F1 dashboard surfaces.

Two applications read/write this store and they must never disagree about what
is true, so the database (not the UI, not the process) is the single source:

  * server.py    -- live capture.  Writes rows tagged source='live'.
  * archive.py   -- past-race analysis UI.  Read-only except for chat.
  * tools/backfill_openf1.py -- fills historical gaps, tagged source='openf1'.

Provenance matters because the OpenF1 archive is NOT a superset of the live
feed: it can restore laps we never captured, but it has no sector segments and
no full race-control stream.  Every row records where it came from and which
fields are missing, so the archive UI (and the LLM briefing) can show a column
as unknown instead of silently inventing it.

Writers: SQLite WAL allows one writer at a time, so every helper here keeps
transactions short and uses a busy timeout.  A bulk backfill must write in
small batches or it will stall live capture during a race.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

import points

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / "data"
# F1_DASH_DB lets a scratch copy be used (tests, dry experiments) without ever
# touching the real archive. Both writers read this, so they stay in agreement.
DB_PATH = Path(os.environ.get("F1_DASH_DB") or (DATA_DIR / "f1dash.db")).expanduser()

# Writers in different processes must not block each other for long.
BUSY_TIMEOUT_MS = 15000

SOURCE_LIVE = "live"
SOURCE_OPENF1 = "openf1"

SCHEMA = """
CREATE TABLE IF NOT EXISTS races(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  path TEXT UNIQUE NOT NULL,
  meeting TEXT, official TEXT, session TEXT, year INTEGER,
  start_date TEXT, end_date TEXT, total_laps INTEGER,
  first_seen TEXT, last_seen TEXT, final_status TEXT,
  snap TEXT
);
CREATE TABLE IF NOT EXISTS frames(
  race_id INTEGER NOT NULL, lap INTEGER NOT NULL, ts TEXT, snap TEXT NOT NULL,
  PRIMARY KEY(race_id, lap)
);
CREATE TABLE IF NOT EXISTS lap_times(
  race_id INTEGER NOT NULL, num TEXT NOT NULL, lap INTEGER NOT NULL, sec REAL NOT NULL,
  PRIMARY KEY(race_id, num, lap)
);
CREATE TABLE IF NOT EXISTS lap_speeds(
  race_id INTEGER NOT NULL, num TEXT NOT NULL, lap INTEGER NOT NULL,
  i1 INTEGER, i2 INTEGER, st INTEGER,
  source TEXT NOT NULL DEFAULT 'live',
  PRIMARY KEY(race_id, num, lap)
);
CREATE TABLE IF NOT EXISTS chat(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  race_id INTEGER NOT NULL,
  role TEXT NOT NULL, text TEXT NOT NULL, meta TEXT, err INTEGER DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS chat_race ON chat(race_id, id);
CREATE TABLE IF NOT EXISTS prefs(
  key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL
);
-- One row per answered question: what the call actually cost.
--
-- Why a table and not just the meta string in `chat`: the question "what has
-- this race cost me / how big is my context" is an aggregate query, and the chat
-- row's meta is a display string. Keeping numbers here also survives a restart,
-- so the usage strip still shows history after a service restart.
CREATE TABLE IF NOT EXISTS usage(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  race_id INTEGER, ts TEXT NOT NULL,
  level TEXT, prompt_tokens INTEGER, cached_tokens INTEGER,
  completion_tokens INTEGER, reasoning_tokens INTEGER,
  credits_cr REAL, truncated INTEGER DEFAULT 0, context_chars INTEGER
);
-- Sector splits live in their own table on purpose.
--
-- They are ~90% of a frame's bytes, so keeping them inside frames.snap would
-- push the archive from ~19 MB to ~190 MB a season for data that is queried as
-- numbers ("where did he lose that lap"), not as a picture of the whole field.
-- One row per driver per lap is ~120 B, so a season costs a couple of MB.
--
-- seg1..seg3 are F1 mini-sector STATUS CODES, not times: the feed never
-- publishes mini-sector durations, only whether each one was a personal best /
-- session best / no improvement. Stored as CSV of ints so the archive keeps the
-- evidence without us inventing a meaning for each code.
CREATE TABLE IF NOT EXISTS sectors(
  race_id INTEGER NOT NULL, num TEXT NOT NULL, lap INTEGER NOT NULL,
  s1 REAL, s2 REAL, s3 REAL,
  seg1 TEXT, seg2 TEXT, seg3 TEXT,
  source TEXT NOT NULL DEFAULT 'live',
  PRIMARY KEY(race_id, num, lap)
);
-- Championship standings pulled from the public API, cached so the standings
-- page works with no network at all.
--
-- Why cache the normalised rows instead of the raw response: the page must work
-- after an outage of unknown length, and the API's shape may change between
-- seasons. Normalised rows keep the page rendering when the parser is updated
-- for a new shape -- the old snapshot still displays, with its own timestamp.
--
-- fetched_at is the honesty field. There is no "refresh on read" here: a reader
-- with no network must be able to say exactly how old what it is showing is,
-- and invent nothing in between.
CREATE TABLE IF NOT EXISTS standings_snapshots(
  season INTEGER NOT NULL,
  kind TEXT NOT NULL,              -- 'driver' | 'constructor'
  round INTEGER,
  rows TEXT NOT NULL,              -- JSON list, already normalised
  source TEXT, url TEXT,
  fetched_at TEXT NOT NULL,
  PRIMARY KEY(season, kind)
);
-- AI provider profiles: who this installation talks to, and with whose key.
--
-- Stored in the database rather than in code because the live and archive
-- surfaces are separate processes with separate module state, and because a
-- second person using this install must be able to pick their own provider
-- without editing files. `api_key` is a secret: it is written with the DB file
-- at 0600 and is NEVER returned to a browser (ai_config._row_public).
CREATE TABLE IF NOT EXISTS ai_profiles(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  label TEXT NOT NULL,
  base_url TEXT NOT NULL,
  model TEXT NOT NULL,
  api_key TEXT,
  is_default INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
-- Which rounds of a season are SPRINT weekends, curated.
--
-- Why stored instead of derived: the Ergast-compatible calendar we cache carries
-- round/name/date only -- no sprint flag. Without this table the welcome grid
-- cannot know whether a weekend has five sessions or six, and guessing would
-- either hide a real Sprint Qualifying card or invent one. Missing rows are not
-- an error: callers fall back to "what was actually captured" and say so.
CREATE TABLE IF NOT EXISTS sprint_rounds(
  season INTEGER NOT NULL,
  round INTEGER NOT NULL,
  source TEXT,
  updated_at TEXT NOT NULL,
  PRIMARY KEY(season, round)
);
-- Every fetch attempt, successful or not. The page reports the last failure
-- instead of pretending the cached picture is current.
CREATE TABLE IF NOT EXISTS standings_attempts(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL, season INTEGER, kind TEXT,
  ok INTEGER NOT NULL, err TEXT, url TEXT
);
CREATE INDEX IF NOT EXISTS standings_attempts_ts ON standings_attempts(ts);
"""

# Columns added after the first release. ALTER TABLE ADD COLUMN is cheap and
# safe on an existing database, so migrations are just "add if absent".
MIGRATIONS = {
    "races": {
        # F1's own meeting key. server.py reads it from SessionInfo.Meeting.Key,
        # OpenF1 uses the same numbering -- this is how backfill matches a race
        # without guessing from dates or round numbers.
        "meeting_key": "INTEGER",
        "source": "TEXT",
    },
    "frames": {
        # 'live' | 'openf1' -- who produced this row.
        "source": "TEXT",
        # JSON list of field names this row cannot provide (e.g. ["sectors","rc"]).
        "missing": "TEXT",
    },
}

_local = threading.local()


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=BUSY_TIMEOUT_MS / 1000.0)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    return conn


@contextlib.contextmanager
def db():
    """Short transaction with a busy timeout.

    Re-entrant per thread (nested use is a no-op) so helpers can call each other
    without deadlocking on their own lock.
    """
    depth = getattr(_local, "depth", 0)
    if depth:
        yield _local.conn
        return
    conn = _connect()
    _local.conn, _local.depth = conn, 1
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _local.depth = 0
        _local.conn = None
        conn.close()


def _owner_only(path, mode: int) -> None:
    """Best effort chmod. Never raises: the file may live on a filesystem that
    ignores modes, and the archive must keep working either way."""
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def init_db() -> None:
    # 0700 on the directory from the very first run. The default umask leaves it
    # at 0775, which is world-readable until the first API key is saved -- and
    # that is a window in which a fresh install has nothing to hide yet but will.
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _owner_only(DATA_DIR, 0o700)
    conn = _connect()
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.executescript(SCHEMA)
        for table, cols in MIGRATIONS.items():
            have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
            for col, typ in cols.items():
                if col not in have:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
        conn.commit()
    finally:
        conn.close()
    # A key may be written later into this database; start owner-only rather
    # than waiting for ai_config to tighten it after the first save.
    for suffix in ("", "-wal", "-shm"):
        _owner_only(str(DB_PATH) + suffix, 0o600)


# ---------- races ----------
def upsert_race(path: str, *, meeting=None, official=None, session=None, year=None,
                start_date=None, end_date=None, meeting_key=None,
                source: str = SOURCE_LIVE) -> int:
    """Create the race row if missing. `source` records who created it: the live
    writer by default, SOURCE_OPENF1 when a backfill opened the row for a race
    that was never captured live -- the row's provenance must not claim "live"."""
    with db() as c:
        row = c.execute("SELECT id FROM races WHERE path=?", (path,)).fetchone()
        if row:
            rid = int(row["id"])
            if meeting_key is not None:
                c.execute("UPDATE races SET meeting_key=COALESCE(meeting_key,?) WHERE id=?",
                          (meeting_key, rid))
            return rid
        cur = c.execute(
            "INSERT INTO races(path,meeting,official,session,year,start_date,end_date,"
            "total_laps,first_seen,last_seen,meeting_key,source) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (path, meeting, official, session, year, start_date, end_date, None,
             now_iso(), now_iso(), meeting_key, source))
        return int(cur.lastrowid)


def touch_race(rid: int) -> None:
    with db() as c:
        c.execute("UPDATE races SET last_seen=? WHERE id=?", (now_iso(), rid))


def list_races() -> list[dict]:
    with db() as c:
        rows = c.execute(
            "SELECT r.*, "
            "(SELECT MIN(lap) FROM frames f WHERE f.race_id=r.id) AS first_lap, "
            "(SELECT MAX(lap) FROM frames f WHERE f.race_id=r.id) AS last_lap, "
            "(SELECT COUNT(*) FROM frames f WHERE f.race_id=r.id) AS n_frames, "
            "(SELECT COUNT(*) FROM lap_times lt WHERE lt.race_id=r.id) AS n_lap_times, "
            "(SELECT COUNT(*) FROM chat ch WHERE ch.race_id=r.id) AS n_chat, "
            "(SELECT COUNT(*) FROM frames f WHERE f.race_id=r.id AND f.source='openf1') AS n_openf1 "
            "FROM races r ORDER BY COALESCE(NULLIF(r.start_date,''), r.first_seen) DESC, r.id DESC"
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d.pop("snap", None)
        out.append(d)
    return out


def get_race(rid: int) -> dict | None:
    with db() as c:
        row = c.execute("SELECT * FROM races WHERE id=?", (rid,)).fetchone()
    if row is None:
        return None
    d = dict(row)
    d.pop("snap", None)
    return d


def race_laps(rid: int) -> list[dict]:
    """Stored laps with their provenance, ascending."""
    with db() as c:
        rows = c.execute("SELECT lap, source, missing FROM frames WHERE race_id=? ORDER BY lap",
                         (rid,)).fetchall()
    out = []
    for r in rows:
        try:
            missing = json.loads(r["missing"]) if r["missing"] else []
        except Exception:
            missing = []
        out.append({"lap": int(r["lap"]), "source": r["source"] or SOURCE_LIVE, "missing": missing})
    return out


def race_lap_numbers(rid: int) -> list[int]:
    with db() as c:
        return [int(r["lap"]) for r in
                c.execute("SELECT lap FROM frames WHERE race_id=? ORDER BY lap", (rid,))]


# ---------- frames ----------
def stored_snapshot(rid: int, lap: int | None = None) -> dict | None:
    """Nearest stored frame at or before `lap`; earliest frame as a fallback."""
    row = frame_row(rid, lap)
    return row["snap"] if row else None


def frame_row(rid: int, lap: int | None = None) -> dict | None:
    """Nearest stored frame plus its provenance: {'snap', 'lap', 'source', 'missing', 'ts'}."""
    try:
        with db() as c:
            if lap is None:
                fr = c.execute("SELECT lap,ts,snap,source,missing FROM frames WHERE race_id=? "
                               "ORDER BY lap DESC LIMIT 1", (rid,)).fetchone()
            else:
                fr = c.execute("SELECT lap,ts,snap,source,missing FROM frames WHERE race_id=? AND lap<=? "
                               "ORDER BY lap DESC LIMIT 1", (rid, int(lap))).fetchone()
                if fr is None:
                    fr = c.execute("SELECT lap,ts,snap,source,missing FROM frames WHERE race_id=? "
                                   "ORDER BY lap ASC LIMIT 1", (rid,)).fetchone()
    except Exception:
        return None
    if fr is None:
        return None
    try:
        snap = json.loads(fr["snap"])
    except Exception:
        return None
    try:
        missing = json.loads(fr["missing"]) if fr["missing"] else []
    except Exception:
        missing = []
    return {"snap": snap, "lap": int(fr["lap"]), "ts": fr["ts"],
            "source": fr["source"] or SOURCE_LIVE, "missing": missing}


def put_frame(rid: int, lap: int, snap: dict, *, source: str = SOURCE_LIVE,
              ts: str | None = None, missing: list[str] | None = None,
              overwrite: bool = True) -> None:
    """Write one lap frame.

    overwrite=False is the backfill mode: it must never replace a frame the live
    capture produced, because live data is strictly richer.
    """
    payload = json.dumps(snap, default=str)
    miss = json.dumps(sorted(set(missing or [])))
    verb = ("INSERT INTO frames(race_id,lap,ts,snap,source,missing) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(race_id,lap) DO UPDATE SET snap=excluded.snap, ts=excluded.ts, "
            "source=excluded.source, missing=excluded.missing")
    if not overwrite:
        verb = "INSERT OR IGNORE INTO frames(race_id,lap,ts,snap,source,missing) VALUES(?,?,?,?,?,?)"
    with db() as c:
        c.execute(verb, (rid, int(lap), ts or now_iso(), payload, source, miss))


# ---------- lap times ----------
def lap_times_for(rid: int, upto_lap: int | None = None) -> dict[str, list[float]]:
    """num -> ascending seconds, optionally truncated at `upto_lap`."""
    if upto_lap is None:
        sql, args = "SELECT num,lap,sec FROM lap_times WHERE race_id=? ORDER BY num,lap", (rid,)
    else:
        sql = ("SELECT num,lap,sec FROM lap_times WHERE race_id=? AND lap<=? ORDER BY num,lap")
        args = (rid, int(upto_lap))
    try:
        with db() as c:
            rows = c.execute(sql, args).fetchall()
    except Exception:
        return {}
    per: dict[str, list[float]] = {}
    for r in rows:
        per.setdefault(str(r["num"]), []).append(float(r["sec"]))
    return per


def all_lap_times(rid: int) -> dict[str, dict[int, float]]:
    """num -> {lap: seconds}; used for charts and for rebuilding live state."""
    try:
        with db() as c:
            rows = c.execute("SELECT num,lap,sec FROM lap_times WHERE race_id=?", (rid,)).fetchall()
    except Exception:
        return {}
    out: dict[str, dict[int, float]] = {}
    for r in rows:
        # A row with no duration is not a lap: keeping it would blow up the
        # float() below -- and every reader of this map -- on a single bad row.
        if r["sec"] is None or r["lap"] is None:
            continue
        out.setdefault(str(r["num"]), {})[int(r["lap"])] = float(r["sec"])
    return out


def put_lap_times(rid: int, items, *, batch: int = 500) -> int:
    """Bulk insert (num, lap, sec) in short batches; existing rows win.

    Returns the number of rows actually inserted.
    """
    inserted = 0
    buf: list[tuple] = []
    with db() as c:
        for num, lap, sec in items:
            if sec is None or sec <= 0:
                continue
            buf.append((rid, str(num), int(lap), float(sec)))
            if len(buf) >= batch:
                inserted += _flush_lap_times(c, buf)
                buf = []
        if buf:
            inserted += _flush_lap_times(c, buf)
    return inserted


def _flush_lap_times(c: sqlite3.Connection, buf: list[tuple]) -> int:
    before = c.total_changes
    c.executemany("INSERT OR IGNORE INTO lap_times(race_id,num,lap,sec) VALUES(?,?,?,?)", buf)
    return c.total_changes - before


# ---------- sectors ----------
def _segs(v) -> str | None:
    """Mini-sector status codes -> CSV. Accepts a list of ints or of {Status:..}."""
    if not v:
        return None
    out = []
    for s in v:
        if isinstance(s, dict):
            s = s.get("Status")
        out.append("" if s is None else str(int(s)))
    return ",".join(out)


def _sec(v) -> float | None:
    """Sector value -> seconds. Live sends '27.316', OpenF1 sends 27.316."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def put_sectors(rid: int, items, *, source: str = SOURCE_LIVE, batch: int = 500) -> int:
    """Write sector splits. items = (num, lap, s1, s2, s3, seg1, seg2, seg3).

    Fields merge instead of overwrite: the timing feed publishes a sector as the
    lap progresses, so a later write may carry more information but must never
    null out a value we already have. A row captured live keeps source='live'
    even if a backfill writes the same lap again -- provenance describes where
    the data came from first, and live is the richer source.
    """
    written = 0
    buf: list[tuple] = []
    sql = """
    INSERT INTO sectors(race_id,num,lap,s1,s2,s3,seg1,seg2,seg3,source)
    VALUES(?,?,?,?,?,?,?,?,?,?)
    ON CONFLICT(race_id,num,lap) DO UPDATE SET
      s1=COALESCE(excluded.s1,s1), s2=COALESCE(excluded.s2,s2), s3=COALESCE(excluded.s3,s3),
      seg1=COALESCE(excluded.seg1,seg1), seg2=COALESCE(excluded.seg2,seg2),
      seg3=COALESCE(excluded.seg3,seg3),
      source=CASE WHEN sectors.source=? THEN sectors.source ELSE excluded.source END
    """
    with db() as c:
        for it in items:
            num, lap = it[0], it[1]
            if lap is None:
                continue
            s1, s2, s3 = (_sec(it[2]) if len(it) > 2 else None,
                          _sec(it[3]) if len(it) > 3 else None,
                          _sec(it[4]) if len(it) > 4 else None)
            segs = [_segs(it[i]) if len(it) > i else None for i in (5, 6, 7)]
            if s1 is None and s2 is None and s3 is None and not any(segs):
                continue
            buf.append((rid, str(num), int(lap), s1, s2, s3, segs[0], segs[1], segs[2],
                        source, SOURCE_LIVE))
            if len(buf) >= batch:
                c.executemany(sql, buf)
                written += len(buf)
                buf = []
        if buf:
            c.executemany(sql, buf)
            written += len(buf)
    return written


def sectors_for(rid: int, lap: int | None = None) -> dict[str, dict]:
    """num -> {s1,s2,s3,segs,source} for one lap (or the newest lap that has any)."""
    with db() as c:
        if lap is None:
            row = c.execute("SELECT MAX(lap) FROM sectors WHERE race_id=?", (rid,)).fetchone()
            if not row or row[0] is None:
                return {}, None
            lap = int(row[0])
        rows = c.execute(
            "SELECT num,s1,s2,s3,seg1,seg2,seg3,source FROM sectors "
            "WHERE race_id=? AND lap=?", (rid, int(lap))).fetchall()
    out = {}
    for r in rows:
        out[r["num"]] = {"s1": r["s1"], "s2": r["s2"], "s3": r["s3"],
                         "segs": [r["seg1"], r["seg2"], r["seg3"]],
                         "source": r["source"]}
    return out, lap


def all_sector_rows(rid: int) -> dict[str, dict[int, dict]]:
    """num -> lap -> {s1,s2,s3,segs,source} for every stored sector of the race.

    This is the ONE query definition behind all_sectors and sector_bests: both
    used to carry their own SELECT, and the moment the table gained the
    mini-sector codes only one of them would have learned about them. `segs` is
    the three CSV strings of STATUS codes (see the `sectors` DDL) -- never
    durations, because no source publishes those.
    """
    out: dict[str, dict[int, dict]] = {}
    with db() as c:
        for r in c.execute("SELECT num,lap,s1,s2,s3,seg1,seg2,seg3,source FROM sectors "
                           "WHERE race_id=? ORDER BY lap", (rid,)):
            out.setdefault(r["num"], {})[int(r["lap"])] = {
                "s1": r["s1"], "s2": r["s2"], "s3": r["s3"],
                "segs": [r["seg1"], r["seg2"], r["seg3"]],
                "source": r["source"]}
    return out


def all_sectors(rid: int) -> dict[str, dict[int, list]]:
    """num -> lap -> [s1,s2,s3]. Missing sectors stay None inside the triple."""
    return {num: {lap: [cell["s1"], cell["s2"], cell["s3"]] for lap, cell in per.items()}
            for num, per in all_sector_rows(rid).items()}


def sector_bests(rid: int) -> dict:
    """Best time per sector per driver, plus the field best for each sector.

    This is what makes "where did he lose the lap" answerable: a driver's own
    best sectors summed give an ideal lap, and the difference to the actual lap
    is time left on the table.
    """
    per: dict[str, list[float | None]] = {}
    field: list[float | None] = [None, None, None]
    for num, laps in all_sectors(rid).items():
        best = [None, None, None]
        for triple in laps.values():
            for i in (0, 1, 2):
                v = triple[i]
                if v is None:
                    continue
                if best[i] is None or v < best[i]:
                    best[i] = v
                if field[i] is None or v < field[i]:
                    field[i] = v
        per[num] = best
    return {"per_driver": per, "field": field}


# ---------- preferences ----------
# The user's output preferences (time format, answer shape, language) are facts
# about the person, not about a race, so they live in their own table instead of
# inside a race snapshot. They are stored server-side on purpose: the browser's
# localStorage dies with the browser, and both surfaces (live + archive) must
# answer in the same shape.
def put_speeds(rid: int, items, *, source: str = SOURCE_LIVE, batch: int = 500) -> int:
    """Bulk insert (num, lap, i1, i2, st). COALESCE merge, like put_sectors.

    A lap can arrive with only some of the three speeds (OpenF1 omits a speed
    when the car did not cross the loop cleanly), so a later partial write must
    not wipe a value an earlier write already had.
    """
    if not items:
        return 0
    buf = [(rid, str(n), int(l), i1, i2, st, source) for n, l, i1, i2, st in items]
    with db() as c:
        c.executemany(
            """INSERT INTO lap_speeds(race_id,num,lap,i1,i2,st,source)
               VALUES(?,?,?,?,?,?,?)
               ON CONFLICT(race_id,num,lap) DO UPDATE SET
                 i1=COALESCE(excluded.i1, i1),
                 i2=COALESCE(excluded.i2, i2),
                 st=COALESCE(excluded.st, st)""", buf)
    return len(buf)


def speeds_for(rid: int, lap: int | None = None) -> dict[str, dict]:
    """num -> {i1,i2,st} for one lap (or the newest lap that has any)."""
    q = "SELECT num,i1,i2,st FROM lap_speeds WHERE race_id=?"
    args: list = [rid]
    if lap is None:
        q += " ORDER BY lap DESC"
    else:
        q += " AND lap=?"
        args.append(lap)
    out: dict[str, dict] = {}
    try:
        with db() as c:
            for r in c.execute(q, args):
                num = str(r["num"])
                if num in out:
                    continue
                out[num] = {"i1": r["i1"], "i2": r["i2"], "st": r["st"]}
    except Exception:
        return {}
    return out


def speeds_count(rid: int) -> int:
    try:
        with db() as c:
            return c.execute("SELECT COUNT(*) FROM lap_speeds WHERE race_id=?",
                             (rid,)).fetchone()[0]
    except Exception:
        return 0


def prefs_all() -> dict[str, str]:
    try:
        with db() as c:
            return {str(r["key"]): str(r["value"])
                    for r in c.execute("SELECT key,value FROM prefs")}
    except Exception:
        return {}


def pref_set(key: str, value: str) -> None:
    with db() as c:
        c.execute("INSERT INTO prefs(key,value,updated_at) VALUES(?,?,?) "
                  "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
                  "updated_at=excluded.updated_at", (str(key), str(value), now_iso()))


def pref_delete(key: str) -> None:
    with db() as c:
        c.execute("DELETE FROM prefs WHERE key=?", (str(key),))


def prefs_clear() -> None:
    with db() as c:
        c.execute("DELETE FROM prefs")


# ---------- usage / cost ----------
def usage_add(rid, meta: dict) -> None:
    """Record what one answer cost. Missing numbers stay NULL, never 0."""
    with db() as c:
        c.execute("INSERT INTO usage(race_id,ts,level,prompt_tokens,cached_tokens,"
                  "completion_tokens,reasoning_tokens,credits_cr,truncated,context_chars) "
                  "VALUES(?,?,?,?,?,?,?,?,?,?)",
                  (rid, now_iso(), meta.get("level"), meta.get("prompt_tokens"),
                   meta.get("cached_tokens"), meta.get("completion_tokens"),
                   meta.get("reasoning_tokens"), meta.get("credits_cr"),
                   1 if meta.get("truncated") else 0, meta.get("context_chars")))


def usage_summary(limit: int = 20) -> dict:
    """Aggregate over the last `limit` answers: enough to answer "ne kadar harcadı".

    Totals are per-call sums; `avg_prompt` is the average context size, which is
    the number that actually matters for context-window planning. Credits are the
    provider's own balance from the most recent answer, not a computed spend.
    """
    with db() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT * FROM usage ORDER BY id DESC LIMIT ?", (limit,))]
        total = c.execute("SELECT COUNT(*) FROM usage").fetchone()[0]
    if not rows:
        return {"calls": 0, "total_calls": int(total)}
    def _sum(k):
        return sum(r[k] or 0 for r in rows)
    prompts = [r["prompt_tokens"] for r in rows if r["prompt_tokens"]]
    cached = [r["cached_tokens"] for r in rows if r["cached_tokens"] is not None]
    return {
        "calls": len(rows),
        "total_calls": int(total),
        "prompt_tokens": _sum("prompt_tokens"),
        "cached_tokens": _sum("cached_tokens"),
        "completion_tokens": _sum("completion_tokens"),
        "reasoning_tokens": _sum("reasoning_tokens"),
        "truncated": _sum("truncated"),
        "avg_prompt": round(sum(prompts) / len(prompts)) if prompts else None,
        "cache_pct": round(100 * _sum("cached_tokens") / _sum("prompt_tokens")) if _sum("prompt_tokens") else None,
        "credits_cr": next((r["credits_cr"] for r in rows if r["credits_cr"] is not None), None),
        "last": rows[0],
    }


# ---------- chat ----------
def chat_add(rid: int, role: str, text: str, meta: str = "", err: bool = False) -> None:
    with db() as c:
        c.execute("INSERT INTO chat(race_id,role,text,meta,err,created_at) VALUES(?,?,?,?,?,?)",
                  (rid, role, text, meta or "", 1 if err else 0, now_iso()))


def chat_history(rid: int, limit: int = 8) -> list[dict]:
    with db() as c:
        rows = c.execute("SELECT role,text FROM chat WHERE race_id=? AND err=0 "
                         "ORDER BY id DESC LIMIT ?", (rid, limit)).fetchall()
    return [{"role": r["role"], "content": r["text"]} for r in reversed(rows)]


def chat_all(rid: int) -> list[dict]:
    with db() as c:
        return [dict(r) for r in c.execute(
            "SELECT id,role,text,meta,err,created_at FROM chat WHERE race_id=? ORDER BY id", (rid,))]


def chat_count(rid: int) -> int:
    with db() as c:
        return int(c.execute("SELECT COUNT(*) FROM chat WHERE race_id=?", (rid,)).fetchone()[0])


def chat_clear(rid: int) -> None:
    with db() as c:
        c.execute("DELETE FROM chat WHERE race_id=?", (rid,))


def update_race_state(rid: int, *, total_laps=None, final_status=None, snap_json=None) -> None:
    """Cheap per-tick bookkeeping for the live writer."""
    with db() as c:
        c.execute("UPDATE races SET last_seen=?, total_laps=COALESCE(?,total_laps), "
                  "final_status=COALESCE(?,final_status), snap=? WHERE id=?",
                  (now_iso(), total_laps, final_status,
                   snap_json if snap_json is not None else None, rid))


def rc_messages(rid: int) -> list[dict]:
    """Every race-control message stored for a race, oldest first, deduplicated.

    Why this is needed at all: `RaceControlMessages` is a rolling stream held in
    the writer's memory, so a service restart mid-race wipes the history. A
    5-second penalty given on lap 12 then vanishes from the live table on lap 46
    even though it is still in force -- measured exactly that way, after a restart
    left 6 messages where the race had produced dozens.

    The stored frames already carry the messages (compact() keeps `rc`), so the
    penalty state can be rebuilt from them. Deduplicated on (lap, message)
    because consecutive frames repeat the same rolling window.
    """
    with db() as c:
        rows = c.execute("SELECT snap FROM frames WHERE race_id=? ORDER BY lap", (rid,)).fetchall()
    seen = set()
    out = []
    for r in rows:
        try:
            snap = json.loads(r["snap"])
        except Exception:
            continue
        for m in snap.get("rc") or []:
            if not isinstance(m, dict):
                continue
            key = (m.get("lap"), m.get("msg"))
            if key in seen:
                continue
            seen.add(key)
            out.append(m)
    out.sort(key=lambda m: (m.get("lap") or 0))
    return out


def all_row_grids(rid: int) -> dict[str, int]:
    """num -> grid slot, harvested from any frame of this race that has one.

    The grid is fixed for a whole race, so the live capture's value is valid for
    every lap including ones a backfill reconstructed.
    """
    out: dict[str, int] = {}
    with db() as c:
        rows = c.execute("SELECT snap FROM frames WHERE race_id=?", (rid,)).fetchall()
    for r in rows:
        try:
            snap = json.loads(r["snap"])
        except Exception:
            continue
        for row in (snap.get("rows") or []):
            num, g = str(row.get("num")), row.get("grid")
            if g is not None and num not in out:
                try:
                    out[num] = int(g)
                except (TypeError, ValueError):
                    pass
    return out


def race_driver_meta(rid: int) -> dict[str, dict]:
    """num -> {code, team, grid, stints} from the newest frame that lists the driver.

    Why newest-first instead of "the final frame": a driver who retired (or lost a
    wheel on the last lap) is absent from the last frame's rows, yet his laps are
    in lap_times. Walking frames backwards keeps him identifiable, so the briefing
    can name him instead of hiding a chunk of the race.
    """
    try:
        with db() as c:
            rows = c.execute("SELECT snap FROM frames WHERE race_id=? ORDER BY lap DESC",
                             (rid,)).fetchall()
    except Exception:
        return {}
    out: dict[str, dict] = {}
    for r in rows:
        try:
            snap = json.loads(r["snap"])
        except Exception:
            continue
        for row in (snap.get("rows") or []):
            num = str(row.get("num"))
            if not num or num in out:
                continue
            out[num] = {"code": row.get("code") or num,
                        "team": row.get("team") or "",
                        "grid": row.get("grid"),
                        "stints": row.get("stints") or []}
    return out


# ---------- championship standings cache ----------
# Written by two processes: the standings surface on a refresh, and the live
# server when it has nothing to watch (so the cache is warm before a race whose
# network will die mid-session). Short transactions, same as every other writer.
STANDING_KIND_DRIVER = "driver"
STANDING_KIND_CONSTRUCTOR = "constructor"
# The season calendar, cached in the same table. Not a standings table, but it
# answers the question a live projection cannot skip: "is the cached table
# already counting the race being shown right now?" Without the schedule, a
# projection can only guess, and a guess here double-counts a race.
STANDING_KIND_SCHEDULE = "schedule"
STANDING_KINDS = (STANDING_KIND_DRIVER, STANDING_KIND_CONSTRUCTOR)
_STANDING_KINDS_ALL = STANDING_KINDS + (STANDING_KIND_SCHEDULE,)


def put_standings(season: int, kind: str, rows: list[dict], *, round_no=None,
                  source: str = "jolpica", url: str = "") -> None:
    """Store one normalised standings snapshot, replacing the previous one.

    Replacing rather than appending: the standings page shows the newest picture
    it has, and the age of that picture is `fetched_at`. An append-only history
    would be a season-results archive, which is a different (and much bigger)
    promise than this cache makes.
    """
    if kind not in _STANDING_KINDS_ALL:
        raise ValueError(f"bilinmeyen sıralama türü: {kind!r}")
    payload = json.dumps(rows, ensure_ascii=False, default=str)
    with db() as c:
        c.execute(
            "INSERT INTO standings_snapshots(season,kind,round,rows,source,url,fetched_at) "
            "VALUES(?,?,?,?,?,?,?) ON CONFLICT(season,kind) DO UPDATE SET "
            "round=excluded.round, rows=excluded.rows, source=excluded.source, "
            "url=excluded.url, fetched_at=excluded.fetched_at",
            (int(season), kind, round_no, payload, source, url, now_iso()))


def get_standings(season: int, kind: str) -> dict | None:
    """One cached snapshot with its provenance, or None when never fetched."""
    try:
        with db() as c:
            row = c.execute(
                "SELECT season,kind,round,rows,source,url,fetched_at "
                "FROM standings_snapshots WHERE season=? AND kind=?",
                (int(season), kind)).fetchone()
    except Exception:
        return None
    if row is None:
        return None
    try:
        rows = json.loads(row["rows"])
    except Exception:
        return None
    return {"season": int(row["season"]), "kind": row["kind"], "round": row["round"],
            "rows": rows, "source": row["source"], "url": row["url"],
            "fetched_at": row["fetched_at"]}


def standings_seasons() -> list[int]:
    """Seasons we hold any cached snapshot for, newest first."""
    try:
        with db() as c:
            return [int(r[0]) for r in c.execute(
                "SELECT DISTINCT season FROM standings_snapshots ORDER BY season DESC")]
    except Exception:
        return []


def log_standings_attempt(season: int | None, kind: str | None, ok: bool,
                          err: str = "", url: str = "") -> None:
    """Record a fetch attempt so a failed refresh is visible, not silent."""
    with db() as c:
        c.execute("INSERT INTO standings_attempts(ts,season,kind,ok,err,url) VALUES(?,?,?,?,?,?)",
                  (now_iso(), season, kind, 1 if ok else 0, (err or "")[:300], url or ""))
        # Trim: this is a status log, not an audit trail worth unbounded growth.
        c.execute("DELETE FROM standings_attempts WHERE id <= "
                  "(SELECT MAX(id) FROM standings_attempts) - 200")


def last_standings_attempt(season: int | None = None, kind: str | None = None) -> dict | None:
    """Newest attempt, optionally narrowed to one season and/or one kind.

    Both filters matter: a refresh logs a driver row and a constructor row in
    turn, so "the newest attempt" for the driver table is not the newest attempt
    overall -- reading it without the kind reports the wrong table's error.
    """
    where, args = [], []
    if season is not None:
        where.append("season=?")
        args.append(int(season))
    if kind is not None:
        where.append("kind=?")
        args.append(kind)
    sql = "SELECT * FROM standings_attempts"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT 1"
    try:
        with db() as c:
            row = c.execute(sql, tuple(args)).fetchone()
    except Exception:
        return None
    if row is None:
        return None
    d = dict(row)
    d["ok"] = bool(d.get("ok"))
    return d


# ---------- weekend ledger ----------
# A race weekend is not one row: it is five or six rows in `races`, one per
# session, each with its own frames, laps and chat transcript. The welcome grid
# and the weekend briefing both need "the sessions of THIS weekend, in order,
# with what state each one is in" -- and both must compute it the same way, or
# the card grid and the answer will disagree about the same weekend.
#
# Two hard constraints came out of the real archive (Mexico City 2026, ids
# 19/20/22 + a Race row and no Qualifying row at all):
#
#   1. Gaps are real. A weekend can be missing Qualifying entirely, so the
#      caller gets the sessions that exist and a list of the expected ones that
#      do not. Nothing is invented to fill the gap.
#   2. A future `start_date` does NOT mean "not started": that Race row had
#      three frames while its start_date was still a day away. State therefore
#      comes from capture evidence (frames, last_seen, final_status), never from
#      comparing dates against the clock.

#: Canonical session order for a weekend, and the two weekend shapes.
#: A sprint weekend drops FP2/FP3 for Sprint Qualifying + Sprint.
KIND_ORDER = ("FP1", "FP2", "FP3", "SQ", "SP", "Q", "RACE")
NORMAL_KINDS = ("FP1", "FP2", "FP3", "Q", "RACE")
SPRINT_KINDS = ("FP1", "SQ", "SP", "Q", "RACE")

#: kind -> short label for a card. The briefing uses points.session_type()'s
#: longer labels; a card has ~120 px, so it gets these.
KIND_LABELS = {
    "FP1": "Antrenman 1",
    "FP2": "Antrenman 2",
    "FP3": "Antrenman 3",
    "SQ": "Sprint sıralaması",
    "SP": "Sprint",
    "Q": "Sıralama",
    "RACE": "Yarış",
}

_KIND_BY_POINTS = {
    points.KIND_QUALIFYING: "Q",
    points.KIND_SPRINT_QUALIFYING: "SQ",
    points.KIND_SPRINT: "SP",
    points.KIND_RACE: "RACE",
}


def session_kind(session_name) -> str | None:
    """'Practice 2' -> 'FP2', 'Sprint Qualifying' -> 'SQ', ... None if unknown.

    The classification itself is NOT re-implemented here: it comes from
    points.session_type(), which is the one place that knows a sprint race is not
    a sprint qualifying session. This function only adds what that module does
    not carry -- which practice number it is, and the short card code.
    """
    name = (session_name or "").strip()
    if not name:
        return None
    kind = points.session_type(name)["kind"]
    if kind == points.KIND_PRACTICE:
        m = re.search(r"\b(?:practice|p|antrenman)\s*([123])\b", name, re.I)
        if not m:
            # "Practice" with no number: the feed has always sent one, but a
            # missing number must not silently become FP1 and shadow a real FP1.
            return "FP?"
        return "FP" + m.group(1)
    return _KIND_BY_POINTS.get(kind)


#: Short label for a CARD only. The long KIND_LABELS wording ("Antrenman 1") is
#: right inside a briefing sentence and too long on a 168 px card: measured on a
#: real phone, "ANTRENMAN 1" wrapped onto two lines on every practice card and the
#: badge then sat beside a broken label. Cards use these; the briefing keeps the
#: long form. Kept next to KIND_LABELS so the two never drift apart.
KIND_SHORT = {
    "FP1": "Ant. 1",
    "FP2": "Ant. 2",
    "FP3": "Ant. 3",
    "SQ": "Sprint s.",
    "SP": "Sprint",
    "Q": "Sıralama",
    "RACE": "Yarış",
}


def kind_label(kind: str | None) -> str:
    if not kind:
        return "Bilinmeyen seans"
    return KIND_LABELS.get(kind, kind)


def card_label(kind: str | None) -> str:
    """Short label for a session card; falls back to the long form."""
    if not kind:
        return "Bilinmeyen"
    return KIND_SHORT.get(kind, kind_label(kind))


def weekend_key(race: dict) -> str:
    """Stable identity for the weekend a race row belongs to.

    `meeting_key` (F1's own numbering) is authoritative when present. It is NULL
    on rows older than the migration and on rows a backfill created without it,
    and the fallback must not be a guess: season + meeting name identifies a
    weekend uniquely within a season, and it never merges two rounds.
    """
    mk = race.get("meeting_key")
    if mk not in (None, ""):
        return f"mk:{int(mk)}"
    season = race.get("year") or ""
    meeting = (race.get("meeting") or "").strip().lower()
    if meeting:
        return f"n:{season}:{meeting}"
    # Last resort: this row alone. Better a weekend of one session than a
    # weekend that silently absorbed a different meeting.
    return f"id:{race.get('id')}"


def weekend_siblings(race: dict, *, rows: list[dict] | None = None) -> list[dict]:
    """Every race row of the same weekend as `race`, in session order.

    Reads through list_races() rather than issuing new SQL: that call already
    carries the counts a card needs (n_frames, n_lap_times, n_chat), and one
    query for ~20 rows at once is cheaper than four correlated subqueries per
    session. It also guarantees the grid and the archive menu describe the same
    rows.
    """
    if not race:
        return []
    key = weekend_key(race)
    if rows is None:
        try:
            rows = list_races()
        except Exception:
            return [race]
    same = [r for r in rows if weekend_key(r) == key]
    if not same:
        same = [race]

    def sort_key(r):
        k = session_kind(r.get("session"))
        order = KIND_ORDER.index(k) if k in KIND_ORDER else len(KIND_ORDER)
        # Within one kind (duplicate captures of the same session) the earlier
        # start wins, then the lower id, so the order is at least deterministic.
        return (order, str(r.get("start_date") or ""), int(r.get("id") or 0))

    same.sort(key=sort_key)
    return same


def weekend_of(rid: int, *, rows: list[dict] | None = None) -> dict | None:
    """The weekend a race id belongs to: its key, its label and its sessions.

    `rows` lets a caller that already holds `list_races()` pass it in. Without that
    the grouping re-runs `list_races()` once PER RACE, which is invisible at 23
    rows and cost 2.3 s of wall clock at 95 (measured on the real archive after
    the 68-session backfill) -- and the welcome page calls this for every race on
    every load.
    """
    race = get_race(rid)
    if not race:
        return None
    sessions = weekend_siblings(race, rows=rows)
    first = sessions[0] if sessions else race
    return {
        "key": weekend_key(race),
        "meeting": race.get("meeting") or "",
        "official": race.get("official") or "",
        "year": race.get("year"),
        "meeting_key": race.get("meeting_key"),
        # The weekend's own date: the earliest stored session date. Used to
        # match the cached calendar; never to decide whether a session ran.
        "start_date": min((s.get("start_date") or "" for s in sessions
                           if s.get("start_date")), default=first.get("start_date") or ""),
        "sessions": sessions,
    }


def sprint_rounds(season: int) -> set[int]:
    """Curated sprint rounds for a season. Empty set when unknown -- and empty
    must be read as "unknown", not as "no sprint weekends this year"."""
    try:
        with db() as c:
            return {int(r[0]) for r in c.execute(
                "SELECT round FROM sprint_rounds WHERE season=?", (int(season),))}
    except Exception:
        return set()


def put_sprint_round(season: int, round_no: int, *, source: str = "manual") -> None:
    with db() as c:
        c.execute("INSERT INTO sprint_rounds(season,round,source,updated_at) VALUES(?,?,?,?) "
                  "ON CONFLICT(season,round) DO UPDATE SET source=excluded.source, "
                  "updated_at=excluded.updated_at",
                  (int(season), int(round_no), source, now_iso()))



def delete_sprint_round(season: int, round_no: int) -> None:
    with db() as c:
        c.execute("DELETE FROM sprint_rounds WHERE season=? AND round=?", (int(season), int(round_no)))
