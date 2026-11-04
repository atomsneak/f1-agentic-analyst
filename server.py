#!/usr/bin/env python3
"""F1 live dashboard: official timing feed -> HTML + SSE + AI chat + persistent race archive.

Everything that matters survives a restart / a cleared browser:
  * data/f1dash.db  -> races, lap-by-lap frames, per-lap times and the chat transcript
  * the transcript is stored per race, so each past race keeps its own chat history
"""
import asyncio, base64, contextlib, json, os, re, signal, threading, time, zlib, urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import statistics as stats

import websockets
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

import store
import analysis
import routing
import penalties
import points
import ai_config
import chat_request
import pwa
import standings
from analysis import (ARCHIVE_ROW_FIELDS, compact, context_text, _meta_text,
                      llm_chat, archived_snapshot)

RS = "\x1e"
# Data source: Formula 1's public live-timing WebSocket. This is NOT an official
# public API and not licensed for redistribution or commercial use -- it is the
# feed the official site itself consumes. Running this locally for your own
# viewing is one thing; re-publishing the data is governed by Formula 1's terms
# and Formula One Licensing B.V.'s trademark rights. See README, "Lisans,
# kaynaklar ve yasal notlar".
FEED_NEGOTIATE = "https://livetiming.formula1.com/signalrcore/negotiate?negotiateVersion=1"
TOPICS = ["SessionInfo", "SessionStatus", "TimingData", "TimingAppData", "DriverList",
          "LapCount", "TrackStatus", "RaceControlMessages", "SessionData", "WeatherData",
          "TimingStats", "Position.z", "TyreStintSeries", "TopThree",
          # ExtrapolatedClock carries {Remaining:"0:42:31"} -- the session countdown.
          # It was never subscribed, which is why "how long is left" had no answer at
          # all: the number is only published on this topic.
          "ExtrapolatedClock"]

HERE = Path(__file__).resolve().parent
STATE: dict[str, Any] = {}
# Both caches below are SESSION-scoped, and that scoping is the whole point.
#
# They used to be keyed by driver number alone. A single module-level dict then
# survived the session change, so Practice 1 inherited every lap of the previous
# race: measured on 2026-10-30, the P1 race row held 1258 lap rows of which 1163
# were byte-identical to the United States GP race, and only 95 were real P1 laps
# (laps 4-33). The briefing showed a complete 56-lap, three-stint grand prix
# inside a practice session -- and the model was right to call it a race, because
# that is literally what it was reading. Scoping by session path makes that
# impossible: a new session starts with an empty bucket and can only be filled
# with its own data.
LAP_HISTORY: dict[str, dict[str, dict[int, float]]] = {}   # session -> num -> lap -> sec
# session -> num -> {stint_index(str, 1-based): {"compound":..., "laps":..., "new":...}}
STINTS: dict[str, dict[str, dict[str, dict]]] = {}
VALID_COMPOUNDS = ("SOFT", "MEDIUM", "HARD", "INTERMEDIATE", "WET")
FEED_OK = False
FEED_TS = None
FEED_ERR = None

CURRENT_RACE_ID: int | None = None
ARCHIVE_SECS = 20.0          # refresh the stored frame of the current lap this often


# ---------- persistence ----------
# The DB layer lives in store.py: archive.py and tools/backfill_openf1.py share it
# so all three surfaces read the same truth. This module is the live writer only.
DATA_DIR, DB_PATH = store.DATA_DIR, store.DB_PATH
db = store.db
_now_iso = store.now_iso
_init_db = store.init_db


# ---------- feed ----------
# Honest identity. This used to send "BestHTTP" -- the User-Agent of Formula 1's
# own mobile client -- which is an impersonation of software we are not, and the
# weakest line in the whole project if anyone ever asks what this thing is. The
# feed is fetched as what it is: a personal dashboard. If a deployment needs a
# different string (some networks filter unknown agents) set F1_FEED_UA rather
# than editing the source.
FEED_UA = (os.environ.get("F1_FEED_UA") or "f1-agentic-analyst/1.0 (personal dashboard)").strip()


def _negotiate():
    req = urllib.request.Request(FEED_NEGOTIATE, method="POST",
                                 headers={"User-Agent": FEED_UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8-sig"))


def _inflate(payload: str):
    raw = base64.b64decode(payload)
    for wbits in (zlib.MAX_WBITS, -zlib.MAX_WBITS):
        for skip in (0, 4, 2):
            try:
                return json.loads(zlib.decompress(raw[skip:], wbits).decode("utf-8-sig"))
            except Exception:
                continue
    return None


_REPLACE_LISTS = ("Sectors",)


def _merge(dst, src, _key=None):
    if isinstance(src, dict) and isinstance(dst, dict):
        for k, v in src.items():
            if v is None:
                dst.pop(k, None)
            elif isinstance(v, dict) and isinstance(dst.get(k), dict):
                _merge(dst[k], v, k)
            else:
                dst[k] = v
        return dst
    if isinstance(src, list) and isinstance(dst, list):
        if src and isinstance(src[0], dict) and "Utc" in src[0]:
            dst.extend(src)
            del dst[:-400]
            return dst
        # Sectors (and anything else declared per-lap above) describes the LAST
        # completed lap, not an append-only stream. Extending them kept every
        # previous lap's splits in front of the current one, so secs[:3] read the
        # OLDEST lap of the session -- one of the causes of the sector numbers
        # disagreeing with the lap time by tens of seconds.
        if _key in _REPLACE_LISTS:
            return src
        dst.extend(src)
        return dst
    return src


def _sector_list(raw) -> list:
    """Sectors as a 3-slot ordered list, whatever shape the feed used.

    The snapshot sends a LIST; patches send an index-keyed DICT. Passing the dict
    straight through is what raised `KeyError: slice(None, 3, None)` on every
    tick -- printed as "sektor eklenemedi" and "archive: frame write failed", so
    no sector reached the briefing at all in that session.
    """
    if isinstance(raw, dict):
        try:
            keys = sorted(raw, key=lambda k: int(k))
        except (TypeError, ValueError):
            keys = sorted(raw)
        return [raw[k] for k in keys]
    if isinstance(raw, list):
        return list(raw)
    return []


def _seg_statuses(raw) -> list | None:
    """Mini-sector status codes as a plain list, whatever shape the feed used.

    Segments is a list of `{Status: 2048}` in a full snapshot, but an index-keyed
    DICT in patches -- and iterating a dict yields its KEYS, so
    `for g in Segments: g.get("Status")` raised `'str' object has no attribute
    'get'`. That exception escaped from inside the archive tick's single try
    block, so the same tick also lost its lap times, sector row, frame and race
    state: measured 754 logged failures between Oct 31 01:00 and 21:00, and no
    new frame stored for the whole stretch. Cheap fixes here; the tick stays
    all-or-nothing on purpose (a partial tick is worse than a skipped one).
    """
    items = _sector_list(raw)
    if not items:
        return None
    out = []
    for g in items:
        if isinstance(g, dict):
            out.append(g.get("Status"))
        elif isinstance(g, (int, float, str)):
            # A bare code is already the status; a string is a nested CSV of them.
            out.append(g)
        else:
            out.append(None)
    return out or None


def _sess_key() -> str:
    """Identity of the session the live feed is currently publishing.

    SessionInfo.Path is the same stable key the archive uses (races.path), so the
    in-memory caches and the stored rows agree on what "this session" means. The
    empty string is a real, temporary state: the very first frames of a
    connection may arrive before SessionInfo, and those get migrated as soon as
    the identity is known (see _absorb_result).
    """
    si = STATE.get("SessionInfo") or {}
    path = si.get("Path")
    if not path and si.get("Key") is not None:
        path = f"session-{si['Key']}"
    return str(path or "")


def _migrate_session_key() -> None:
    """Move anything captured before SessionInfo arrived into the real bucket."""
    key = _sess_key()
    if not key:
        return
    pre = LAP_HISTORY.pop("", None)
    if pre:
        cur = LAP_HISTORY.setdefault(key, {})
        for num, hist in pre.items():
            cur.setdefault(num, {}).update(hist)
    pre = STINTS.pop("", None)
    if pre:
        cur = STINTS.setdefault(key, {})
        for num, book in pre.items():
            cur.setdefault(num, {}).update(book)


def _track_stints(data):
    """TimingAppData arrives as Stints LIST in the snapshot but as an index-keyed DICT in
    patches; merging the two shapes destroys the compounds. Normalise into STINTS."""
    lines = (data or {}).get("Lines") or {}
    if not isinstance(lines, dict):
        return
    per_session = STINTS.setdefault(_sess_key(), {})
    for num, line in lines.items():
        if not isinstance(line, dict) or "Stints" not in line:
            continue
        raw = line["Stints"]
        if isinstance(raw, list):
            items = {str(i + 1): v for i, v in enumerate(raw) if isinstance(v, dict)}
        elif isinstance(raw, dict):
            items = {str(k): v for k, v in raw.items() if isinstance(v, dict)}
        else:
            continue
        book = per_session.setdefault(num, {})
        for idx, entry in items.items():
            cur = book.setdefault(idx, {})
            comp = (entry.get("Compound") or "").upper()
            if comp in VALID_COMPOUNDS:
                cur["compound"] = comp
            if entry.get("TotalLaps") is not None:
                cur["laps"] = entry["TotalLaps"]
            if entry.get("New") is not None:
                cur["new"] = str(entry.get("New")) == "true"
            if entry.get("StartLaps") is not None:
                cur["age_base"] = entry["StartLaps"]


def _absorb_result(res: dict):
    # SessionInfo first: everything else is filed under the session it belongs to,
    # and the first frames of a connection can carry laps plus the identity in one
    # payload. Reading the identity last would file those laps under "".
    keys = sorted(res, key=lambda k: (k != "SessionInfo", k != "SessionStatus"))
    for k in keys:
        v = res[k]
        if k == "TimingAppData":
            _track_stints(v)
            STATE[k] = v
            continue
        if k.endswith(".z") and isinstance(v, str):
            d = _inflate(v)
            if d is not None:
                STATE[k] = d
        else:
            STATE[k] = v
    _migrate_session_key()


def _absorb_patch(item):
    if not isinstance(item, list) or len(item) < 2:
        return
    topic, data = item[0], item[1]
    if not isinstance(topic, str):
        return
    if topic.endswith(".z") and isinstance(data, str):
        data = _inflate(data)
        if data is None:
            return
    if topic == "TimingAppData":
        _track_stints(data)
    if topic in STATE:
        STATE[topic] = _merge(STATE[topic], data)
    else:
        STATE[topic] = data
    if topic == "SessionInfo":
        # A patch can introduce a brand-new session mid-connection (F1 reuses one
        # socket across the weekend). Anything captured before the identity landed
        # belongs to it, and every cached lap/stint from the previous session must
        # stay where it is -- in its own bucket, never in this one.
        _migrate_session_key()


async def feed_loop():
    global FEED_OK, FEED_TS, FEED_ERR
    backoff = 2
    while True:
        try:
            neg = await asyncio.to_thread(_negotiate)
            url = f"wss://livetiming.formula1.com/signalrcore?connectionToken={neg['connectionToken']}"
            async with websockets.connect(url, additional_headers={"User-Agent": FEED_UA},
                                          max_size=None, open_timeout=20) as ws:
                await ws.send(json.dumps({"protocol": "json", "version": 1}) + RS)
                await ws.send(json.dumps({"type": 1, "target": "Subscribe",
                                          "arguments": [TOPICS], "invocationId": "0"}) + RS)
                FEED_OK, FEED_ERR, backoff = True, None, 2
                async for msg in ws:
                    if isinstance(msg, bytes):
                        msg = msg.decode("utf-8", "replace")
                    for frame in msg.split(RS):
                        if not frame.strip():
                            continue
                        try:
                            rec = json.loads(frame)
                        except Exception:
                            continue
                        if not isinstance(rec, dict):
                            continue
                        if rec.get("type") == 3 and isinstance(rec.get("result"), dict):
                            _absorb_result(rec["result"])
                        elif rec.get("type") == 1 and rec.get("target") == "feed":
                            args = rec.get("arguments") or []
                            _absorb_patch(args)
                            if len(args) > 2:
                                FEED_TS = args[2]
                    _record_laps()
                    _archive_tick()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            FEED_OK = False
            FEED_ERR = f"{type(e).__name__}: {e}"[:200]
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)


def _to_sec(v):
    if not v or not isinstance(v, str):
        return None
    try:
        p = v.split(":")
        return float(p[0]) * 60 + float(p[1]) if len(p) == 2 else float(v)
    except Exception:
        return None


def _record_laps():
    td = STATE.get("TimingData", {})
    hist_all = LAP_HISTORY.setdefault(_sess_key(), {})
    for num, d in (td.get("Lines") or {}).items():
        lt = (d.get("LastLapTime") or {}).get("Value")
        ln = d.get("NumberOfLaps")
        s = _to_sec(lt)
        if s and ln:
            hist_all.setdefault(num, {})[int(ln)] = s


# ---------- race identity + archive ----------
_race_cache: dict[str, int] = {}
_arch_state = {"lap": None, "t": 0.0}
_seen_state = {"t": 0.0}
_lapnums: dict[int, set] = {}     # race_id -> {(num, lap)} already written to lap_times


def _session_meta():
    si = STATE.get("SessionInfo") or {}
    meeting = si.get("Meeting") or {}
    path = si.get("Path")
    if not path and si.get("Key") is not None:
        path = f"session-{si['Key']}"
    if not path:
        return None
    return {
        "path": str(path),
        "meeting": meeting.get("Name") or "",
        "official": meeting.get("OfficialName") or "",
        "session": si.get("Name") or si.get("Type") or "",
        "year": meeting.get("Year") or (int(str(si.get("StartDate") or "0")[:4]) if str(si.get("StartDate") or "")[:4].isdigit() else None),
        "start_date": si.get("StartDate") or "",
        "end_date": si.get("EndDate") or "",
        # F1's own meeting key -- store it so OpenF1 backfill can match this race
        # by key instead of guessing from dates or round numbers.
        "meeting_key": meeting.get("Key"),
    }


def _preload_lap_times(rid: int):
    """Seed this session's cache from its own stored laps.

    Only rows of `rid` are read, and they are written into the bucket of the
    session that is live right now. The previous version read the right rows but
    wrote them into a driver-keyed global, so a new session inherited the old
    session's laps and could not tell the difference afterwards.
    """
    per = store.all_lap_times(rid)
    stored = _lapnums.setdefault(rid, set())
    hist_all = LAP_HISTORY.setdefault(_sess_key(), {})
    for num, hist in per.items():
        for lap, sec in hist.items():
            hist_all.setdefault(num, {}).setdefault(int(lap), float(sec))
            stored.add((num, int(lap)))


def ensure_race():
    """Stable per-session identity: SessionInfo.Path (e.g. 2026/2026-10-25_..._Race/)."""
    global CURRENT_RACE_ID
    meta = _session_meta()
    if meta is None:
        return CURRENT_RACE_ID
    path = meta["path"]
    rid = _race_cache.get(path)
    if rid is not None:
        CURRENT_RACE_ID = rid
        if time.time() - _seen_state["t"] > 15:
            _seen_state["t"] = time.time()
            try:
                store.touch_race(rid)
            except Exception:
                pass
        return rid
    try:
        rid = store.upsert_race(path, meeting=meta["meeting"], official=meta["official"],
                                session=meta["session"], year=meta["year"],
                                start_date=meta["start_date"], end_date=meta["end_date"],
                                meeting_key=meta["meeting_key"])
    except Exception as e:
        print("archive: race upsert failed:", e)
        return CURRENT_RACE_ID
    _race_cache[path] = rid
    CURRENT_RACE_ID = rid
    _preload_lap_times(rid)
    return rid




def _cheap_lap():
    """Current lap without building the whole snapshot (called on every feed message)."""
    lc = STATE.get("LapCount") or {}
    lap = lc.get("CurrentLap")
    if lap is None:
        lap = (STATE.get("TimingData") or {}).get("LapCount") or {}
        lap = lap.get("CurrentLap") if isinstance(lap, dict) else None
    try:
        return int(lap or 0)
    except (TypeError, ValueError):
        return 0


def _sector_rows():
    """Sector splits from the live feed, one tuple per driver, LAP-VERIFIED.

    The old version of this function trusted `NumberOfLaps` to identify the lap
    the sectors belong to. Measured against OpenF1 on the Mexico City qualifying
    session, that was wrong by ONE for essentially every lap (275 of 286 matched
    at offset -1, 11 at -2): the sectors of lap N were stored against lap N+1.

    The damage was not cosmetic. Sector times are what IDEAL and LOSS are built
    from, so every loss number in a practice/qualifying session was computed
    against the wrong lap -- which is exactly what the assistant reported as
    "sector data is internally inconsistent". Races were unaffected because a
    race's lap counter and the feed's NumberOfLaps agree.

    The fix does not guess a constant offset: it uses the lap time itself as the
    check. `TimingData.Lines[n].LastLapTime` is the duration of the lap those
    sectors belong to, so whichever lap number makes `s1+s2+s3 == LastLapTime`
    is the right one. When no candidate matches (a sector was stopped, or the
    lap is partial) nothing is stored rather than something wrong.
    """
    lines = (STATE.get("TimingData") or {}).get("Lines") or {}
    out = []
    for num, d in lines.items():
        secs = _sector_list(d.get("Sectors"))
        if not secs:
            continue
        vals, segs = [], []
        for s in secs[:3]:
            if not isinstance(s, dict) or s.get("Stopped"):
                vals.append(None)
                segs.append(None)
                continue
            # The RAW feed value is kept in the tuple: store.put_sectors converts
            # it, and a second conversion here would change the shape the rest of
            # the pipeline (and its tests) sees.
            vals.append(s.get("Value"))
            segs.append(_seg_statuses(s.get("Segments")))
        while len(vals) < 3:
            vals.append(None)
            segs.append(None)
        if all(v is None for v in vals) and not any(segs):
            continue

        lap = _sector_lap(num, d, vals)
        if lap is None:
            # Unverifiable: the sector sum does not match this driver's known
            # lap times. Storing it would corrupt IDEAL/LOSS for that lap.
            continue
        out.append((num, lap, vals[0], vals[1], vals[2], segs[0], segs[1], segs[2]))
    return out


#: How far the feed's own counter may sit from the truth. Measured worst case was
#: 2 laps (a driver who pitted twice under one counter tick), so the window is
#: small and closed: an unexplained jump is a bug, not something to absorb.
_SECTOR_LAP_WINDOW = (-2, -1, 0, 1, 2)
#: Sector times arrive to three decimals; the lap time to three as well. Two
#: independent strings of the same measurement agree far tighter than this.
_SECTOR_SUM_TOL = 0.05


def _sector_lap(num, line: dict, vals: list) -> int | None:
    """Which lap these sectors belong to, proved by the sector sum.

    The selector refuses to trust the feed's lap counter alone, and the reason is
    concrete: `NumberOfLaps` is published at the moment a sector completes, while
    `LastLapTime` updates when the lap is closed. The two therefore carry
    DIFFERENT lap numbers for the same lap, and the sector row was filed under the
    counter's number -- one lap ahead of the lap time it belonged to. Verified on
    Mexico City qualifying: the sectors of lap N sat on lap N+1 for 275 of 286
    laps, which is why their sum disagreed with the stored lap time 86% of the
    time and why IDEAL/LOSS were computed against the wrong lap.

    An earlier version of this function tried "if the sector sum equals
    LastLapTime, keep the counter". That is exactly the assumption that produced
    the bug: the pair IS internally consistent, they just disagree with the lap
    records we already hold. So the evidence used here is our own stored lap
    times, and the counter only breaks ties.
    """
    nums = [store._sec(v) for v in vals]
    if not all(v is not None for v in nums):
        # Cannot verify. Harmless: put_sectors merges with COALESCE, so a partial
        # row can add a value but never overwrite a verified one.
        hint = line.get("NumberOfLaps")
        try:
            return int(hint) if hint else None
        except (TypeError, ValueError):
            return None

    total = round(sum(nums), 3)
    hist = (LAP_HISTORY.get(_sess_key()) or {}).get(str(num)) or {}
    if not hist:
        # Nothing recorded for this driver yet, so nothing to verify against.
        # This is the first moments of a session (and the state the shape tests
        # drive): use the counter rather than dropping the row, and let
        # put_sectors' COALESCE merge protect it if a verified row exists later.
        hint = line.get("NumberOfLaps")
        try:
            return int(hint) if hint else None
        except (TypeError, ValueError):
            return None
    matches = [lap for lap, sec in hist.items()
               if sec is not None and abs(sec - total) <= _SECTOR_SUM_TOL]
    if not matches:
        # Sum complete but no stored lap time agrees. Refuse: a mislabelled
        # sector silently corrupts IDEAL, LOSS and every answer built on them.
        return None
    try:
        hint = int(line.get("NumberOfLaps") or 0)
    except (TypeError, ValueError):
        hint = None
    if hint is not None and hint in matches:
        return hint
    if len(matches) == 1:
        return matches[0]
    # Two laps with an identical duration (a repeated time) and the counter is
    # not one of them: prefer the closest lap to the counter, which is the one
    # the driver just finished.
    if hint is not None:
        return min(matches, key=lambda lap: abs(lap - hint))
    return None


def _archive_tick():
    """Called from the feed loop: keeps the DB in sync with the live session."""
    rid = ensure_race()
    if rid is None:
        return
    lap = _cheap_lap()
    now = time.time()
    if lap == _arch_state["lap"] and now - _arch_state["t"] < ARCHIVE_SECS:
        return
    # Only now pay for the full snapshot: ~0.4 ms, and only once per lap / ARCHIVE_SECS.
    s = snapshot()
    # Key the frame by something that actually advances. In a race that is the
    # feed's lap counter; in practice/qualifying the counter is a clock tick and
    # would collapse the whole session onto one row (measured: P1 held 1 frame
    # against 95 timed laps). The field's completed-lap count advances in both.
    frame_lap = lap
    if not (s.get("session_type") or {}).get("has_race_distance"):
        frame_lap = int(s.get("field_laps") or 0) or int(lap or 0)
    _arch_state["lap"], _arch_state["t"] = lap, now
    stored = _lapnums.setdefault(rid, set())
    fresh = []
    # Only THIS session's laps. Reading every bucket would re-import the previous
    # session's lap times the moment a new session starts -- which is exactly how
    # the United States GP ended up inside the Mexico practice session.
    for num, hist in (LAP_HISTORY.get(_sess_key()) or {}).items():
        for ln, sec in hist.items():
            key = (num, int(ln))
            if key not in stored:
                fresh.append((num, int(ln), sec))
                stored.add(key)
    try:
        if fresh:
            store.put_lap_times(rid, fresh)
        secs = _sector_rows()
        if secs:
            store.put_sectors(rid, secs, source=store.SOURCE_LIVE)
        # Live capture always wins: source='live' and no missing fields.
        store.put_frame(rid, frame_lap, compact(s), source=store.SOURCE_LIVE,
                        ts=s.get("updated") or _now_iso(), missing=[])
        store.update_race_state(rid, total_laps=s.get("total"), final_status=s.get("status"),
                                snap_json=json.dumps(compact(s), default=str))
    except Exception as e:
        print("archive: frame write failed:", e)


# ---------- snapshot ----------
def _pit_count(stints) -> int | None:
    """Pit stops for a driver, from the stint list alone.

    `None` when we hold no stint data at all: showing 0 would claim a driver has
    not stopped when we simply do not know yet.
    """
    if not stints:
        return None
    return max(len(stints) - 1, 0)


def _stints(num):
    book = (STINTS.get(_sess_key()) or {}).get(num) or {}
    out = []
    for idx in sorted(book, key=lambda x: int(x) if str(x).isdigit() else 0):
        e = book[idx]
        if not e:
            continue
        out.append({"compound": e.get("compound") or "UNKNOWN",
                    "laps": e.get("laps"),
                    "age_base": e.get("age_base") or 0,
                    "new": bool(e.get("new"))})
    return out


def _grid(num):
    line = ((STATE.get("TimingAppData") or {}).get("Lines") or {}).get(num) or {}
    g = line.get("GridPos")
    try:
        return int(g) if g not in (None, "", "0") else None
    except (TypeError, ValueError):
        return None


def _countdown() -> str | None:
    """Session clock, from the only topic that publishes it.

    SessionStatus carries Status but NOT a remaining-time field; the countdown
    lives in ExtrapolatedClock.Remaining ("0:42:31"). Reading the wrong topic is
    why "seans bitimine ne kadar var" had no answer at all, in any session.
    """
    ec = STATE.get("ExtrapolatedClock") or {}
    if not isinstance(ec, dict):
        return None
    rem = ec.get("Remaining")
    if not rem or not isinstance(rem, str):
        return None
    return rem.strip() or None


def _session_lap(session_is_distance: bool) -> int | None:
    """The lap number this frame should be keyed by.

    A race has a real lap counter (LapCount.CurrentLap). Practice and qualifying
    have no race distance: that counter ticks with the session clock and stops
    moving, so keying archive frames by it wrote ONE frame per practice session
    and kept overwriting it -- P1 2026-10-30 held a single frame (lap 56) while
    95 real laps were timed.

    For those sessions the number of laps the field has actually completed is a
    real, advancing quantity, so the frame is keyed by the most laps any driver
    has done. It is not a race lap, and the caller labels it as such.
    """
    if session_is_distance:
        return None        # caller keeps the feed's own lap counter
    best = 0
    for d in ((STATE.get("TimingData") or {}).get("Lines") or {}).values():
        try:
            best = max(best, int(d.get("NumberOfLaps") or 0))
        except (TypeError, ValueError):
            continue
    return best


def snapshot():
    td = STATE.get("TimingData") or {}
    drv = STATE.get("DriverList") or {}
    lines = td.get("Lines") or {}
    lc = STATE.get("LapCount") or {}
    lap = lc.get("CurrentLap") or (td.get("LapCount") or {}).get("CurrentLap")
    total = lc.get("TotalLaps")
    ss = STATE.get("SessionStatus") or {}
    si = STATE.get("SessionInfo") or {}
    weather = STATE.get("WeatherData") or {}
    # What kind of session this is, decided once and carried in the snapshot so
    # the briefing, the UI and the archive all describe the same thing.
    stype = points.session_type(si.get("Name"), si.get("Type"))
    fields_lap = _session_lap(stype["has_race_distance"])
    rows = []
    hist_all = LAP_HISTORY.get(_sess_key()) or {}
    for num, d in lines.items():
        info = drv.get(num) or {}
        hist = hist_all.get(num) or {}
        last8 = [v for k, v in sorted(hist.items())[-8:]]
        med = round(stats.median(last8), 3) if len(last8) >= 2 else None
        trend = round(last8[-1] - last8[0], 3) if len(last8) >= 2 else None
        st = _stints(num)
        cur = st[-1] if st else None
        rows.append({
            "pos": int(d.get("Position") or 99), "num": num,
            "code": info.get("Tla") or num,
            "grid": _grid(num),
            "name": info.get("FullName") or "",
            "team": info.get("TeamName") or "",
            "gap": d.get("GapToLeader") or "",
            "int": (d.get("IntervalToPositionAhead") or {}).get("Value") or "",
            "laps": d.get("NumberOfLaps"),
            "last": (d.get("LastLapTime") or {}).get("Value") or "",
            "best": (d.get("BestLapTime") or {}).get("Value") or "",
            # PIT: derived from the stints, not from NumberOfPitStops.
            #
            # The feed publishes NumberOfPitStops, and it is wrong at the start of
            # a race: measured on Mexico City (frame per lap stored), every driver
            # read pit=5 or pit=6 for laps 1-27 and then dropped to pit=1 at lap
            # 28 -- while their stint list showed one stop the whole time. A
            # number that says "five stops" during lap 3 is not a small error: the
            # whole pit-strategy column, the "who still has to stop" chips and the
            # assistant's undercut reasoning all sit on top of it.
            #
            # Stints - 1 is the count we can DEFEND: a stint boundary is created by
            # a tyre change, which is a pit stop. It cannot be inflated by a feed
            # field that has not settled yet.
            "pit": _pit_count(st),
            "inpit": bool(d.get("InPit")),
            "retired": bool(d.get("Retired")),
            "sectors": d.get("Sectors"),
            "tyre": (cur or {}).get("compound"),
            "tyre_age": (cur or {}).get("laps"),
            "stints": st,
            "med8": med, "trend": trend,
            "color": info.get("TeamColour") or "",
        })
    rows.sort(key=lambda r: r["pos"])
    rc = (STATE.get("RaceControlMessages") or {}).get("Messages") or {}
    rc_items = list(rc.values()) if isinstance(rc, dict) else list(rc)
    ts = STATE.get("TrackStatus") or {}
    snap = {
        "ok": FEED_OK, "err": FEED_ERR, "updated": FEED_TS, "server_time": time.strftime("%H:%M:%S"),
        "race_id": CURRENT_RACE_ID, "archived": False,
        "meeting": (si.get("Meeting") or {}).get("Name"),
        "official": (si.get("Meeting") or {}).get("OfficialName"),
        "session": si.get("Name"),
        "session_type": stype,
        "status": (ss.get("Status") if isinstance(ss, dict) else ss) or "Unknown",
        "countdown": _countdown(),
        "lap": lap, "total": total,
        # For a practice/qualifying session `lap` is a clock tick, not race
        # distance. `field_laps` is the number of laps the field has actually
        # completed, which is the number a question about "kaç tur atıldı" wants.
        "field_laps": fields_lap,
        "track": ts.get("Message") or ts.get("Status"),
        "air": weather.get("AirTemp"), "track_temp": weather.get("TrackTemp"),
        "humidity": weather.get("Humidity"), "rain": weather.get("Rainfall"),
        "wind": weather.get("WindSpeed"),
        "rows": rows,
        # The whole race-control stream, not the last 25: a penalty given on lap 5
        # is still in force on lap 50, so trimming the list here would make it
        # disappear from the table halfway through the race. 400 lines is the cap
        # the merge already enforces.
        "rc": [{"utc": m.get("Utc"), "lap": m.get("Lap"), "cat": m.get("Category"),
                "msg": m.get("Message")} for m in rc_items[-400:]],
    }
    # Penalties, parsed once here so the row badge, the archive and the briefing
    # all read the same verdict (see penalties.py for why this is not a regex in
    # the page).
    try:
        # The live buffer is authoritative, but a restart empties it and a penalty
        # stays in force for the rest of the race. Merge the stored messages in so
        # a mid-race restart cannot erase a sanction from the table.
        msgs = list(snap["rc"])
        try:
            stored = analysis.stored_rc(CURRENT_RACE_ID)
            if stored and len(stored) > len(msgs):
                have = {(m.get("lap"), m.get("msg")) for m in msgs}
                msgs = [m for m in stored if (m.get("lap"), m.get("msg")) not in have] + msgs
                snap["rc_recovered"] = len(msgs) - len(snap["rc"])
        except Exception as e:
            print("rc geri yüklenemedi:", e)
        snap["penalties"] = penalties.parse(msgs)
        for row in rows:
            pc = snap["penalties"]["by_car"].get(str(row.get("num")))
            row["penalty"] = penalties.label(pc) if pc else ""
            row["penalty_secs"] = (pc or {}).get("total_secs") or 0
            row["penalty_served"] = bool((pc or {}).get("served"))
    except Exception as e:
        snap["penalties"] = {"by_car": {}, "pending": 0, "unparsed": [],
                             "error": f"{type(e).__name__}: {e}"[:120]}
        for row in rows:
            row["penalty"] = ""
    # Sector splits plus IDEAL/LOSS -- the same numbers the archive reports, so a
    # live question about "which sector did he lose" is answerable mid-race.
    # Cheap in steady state: the best-sector scan behind it is memoised for a few
    # seconds, and this whole path runs once a second for the SSE stream.
    try:
        analysis.attach_live_sectors(CURRENT_RACE_ID, snap)
    except Exception as e:
        print("sektor eklenemedi:", e)
    # Provisional championship table. Built from the cached base plus the order
    # we are already holding, so a position change shows up on the next frame
    # with no extra fetch and no extra cost worth measuring.
    try:
        snap["standings"] = provisional_view(snap)
    except Exception as e:
        snap["standings"] = {"provisional": None, "reason": f"hesaplanamadı: {e}"[:200]}
    return snap




# ---------- deterministic routing (live surface) ----------
def _route_extra(snap: dict, rid: int | None, question: str) -> None:
    """Attach the rows a question asks for, before the model is called.

    A live agent sees one snapshot. "What happened on lap 20" or "what happened
    in the last race" therefore has no answer inside that snapshot, and a model
    left to its own devices either refuses or invents. Both intents are cheap to
    detect and cheap to serve from the same database the archive uses, so the
    lookup happens here and the model only ever reads stored numbers.

    No match means no guess: when a question points at a past race but names
    none of the stored ones, the archive's own table is attached so the model
    can ask which race instead of picking one.
    """
    if not question:
        return
    cur_lap = snap.get("lap")
    blocks: list[str] = []
    lap = routing.lap_ref(question)
    if (lap and routing.wants_past_lap(question) and rid
            and isinstance(cur_lap, (int, float)) and lap < int(cur_lap)):
        blk = routing.past_lap_block(rid, int(lap))
        if blk:
            blocks.append(blk)
            snap["past_of_current_race"] = True
    elif routing.wants_other_race(question):
        other = routing.find_race(question, rid)
        blk = routing.other_race_block(other) if other else None
        blocks.append(blk or routing.race_menu())
    if blocks:
        snap["extra_blocks"] = blocks


# ---------- championship standings (for the provisional panel) ----------
# The base table is the season's official points, cached in SQLite by standings.py.
# It is refreshed in the background, never on the request path: snapshot() runs
# once a second for every open tab and must not wait on a 15 s HTTP timeout.
STANDINGS_REFRESH_SECS = 600.0
# SSE heartbeat period. 20 s is far below the shortest proxy idle cut-off we
# might sit behind (~100 s) and well above one snapshot tick, so it costs one
# comment line per silent minute and nothing at all while laps are running.
SSE_PING_SECS = 20.0
_standings_state: dict[str, Any] = {"at": 0.0, "season": None, "base": None,
                                    "cons": None, "schedule": None, "err": None}


def _race_round(meta: dict) -> int | None:
    """Which round the live race is, from the cached calendar (None if unknown)."""
    season = meta.get("year")
    if not season:
        return None
    sched = _standings_state.get("schedule")
    if not sched:
        return None
    return standings.round_for(meta.get("start_date") or "", meta.get("meeting") or "", sched)


def _standings_refresh_blocking(season: int | None) -> dict:
    """Fetch what the provisional panel needs. Runs in a worker thread."""
    if not season:
        return {"ok": False, "error": "sezon bilinmiyor"}
    report = standings.refresh(season)
    schedule = standings.ensure_schedule(season)
    view = standings.get_standings_view(season, store.STANDING_KIND_DRIVER)
    cons = standings.get_standings_view(season, store.STANDING_KIND_CONSTRUCTOR)
    return {"ok": report.get("ok"), "report": report,
            "season": season, "schedule": schedule,
            "base": view, "cons": cons}


async def standings_loop():
    """Keep the standings cache warm while the feed is up.

    Runs regardless of whether a race is on: a race whose network dies
    mid-session is exactly when a warm cache is worth having, and two HTTP calls
    per ten minutes is nothing.
    """
    while True:
        try:
            meta = _session_meta() or {}
            season = meta.get("year") or standings.season_now()
            res = await asyncio.to_thread(_standings_refresh_blocking, season)
            _standings_state.update({
                "at": time.time(), "season": season,
                "base": res.get("base"), "cons": res.get("cons"),
                "schedule": res.get("schedule") or [],
                "err": None if res.get("ok") else _standings_err(res),
            })
        except asyncio.CancelledError:
            raise
        except Exception as e:               # a broken cache must not kill the feed
            _standings_state["err"] = f"{type(e).__name__}: {e}"[:200]
            print("standings: yenileme hatası:", e)
        await asyncio.sleep(STANDINGS_REFRESH_SECS)


def _standings_err(res: dict) -> str:
    rep = res.get("report") or {}
    errs = [f"{k}: {v.get('error')}" for k, v in (rep.get("results") or {}).items()
            if not v.get("ok")]
    return ("; ".join(errs) or rep.get("error") or "bilinmeyen hata")[:200]


def _session_finished(snap: dict) -> bool:
    """True once the session cannot move any more.

    Used only to word the refusal correctly: after the flag, "puanlar iki kez
    sayılırdı" is technically true but reads like a bug report. What the user
    actually needs to know is "the race is over, the official table is here".
    """
    s = (snap.get("status") or "").strip().lower()
    return any(k in s for k in ("end", "finish", "final", "abort"))


def provisional_view(snap: dict) -> dict:
    """The championship table if this race ended in its current order.

    Returns {"provisional": {...} | None, "reason": str|None, "base": {...}}.
    A projection that cannot be trusted is refused outright (see
    standings.base_rows_for_projection) -- a double-counted race is worse than a
    panel that says "taban yok".
    """
    meta = _session_meta() or {}
    season = meta.get("year") or (_standings_state.get("season"))
    if not season:
        return {"provisional": None, "reason": "sezon bilinmiyor", "base": None}
    if not points.pays_points(snap.get("session")):
        return {"provisional": None, "reason": "bu seansta puan verilmiyor",
                "base": None, "season": season}

    base_view = _standings_state.get("base")
    if not isinstance(base_view, dict) or not base_view.get("has_data"):
        return {"provisional": None, "season": season,
                "reason": "puan tabanı önbellekte yok", "base": base_view}

    rr = _race_round(meta)
    base = standings.base_rows_for_projection(
        season, race_round=rr, race_date=meta.get("start_date"),
        meeting=meta.get("meeting"))
    if base.get("unsafe"):
        reason = base.get("reason")
        if _session_finished(snap):
            # The cause goes in `detail`, not into `reason`: the page already
            # says "seans bitti", so repeating it here reads like a stutter.
            return {"provisional": None, "season": season, "finished": True,
                    "reason": "resmî sıralama artık kesinleşti; güncel tablo "
                              "Puan Durumu sayfasında",
                    "detail": reason, "base": base}
    cons = (_standings_state.get("cons") or {}).get("rows") or []
    proj = points.provisional(
        snap.get("rows") or [], base.get("rows") or [], cons,
        kind=points.session_kind(snap.get("session")),
        lap=snap.get("lap"), total_laps=snap.get("total"))
    proj["base_fetched_at"] = base.get("fetched_at")
    proj["base_round"] = base.get("round")
    proj["race_round"] = rr
    proj["check"] = base.get("check")
    return {"provisional": proj, "season": season, "reason": None, "base": base,
            "error": _standings_state.get("err")}


# ---------- chat persistence ----------
def _rid_for(race) -> int | None:
    if race in (None, "", "live"):
        return CURRENT_RACE_ID or ensure_race()
    try:
        return int(race)
    except (TypeError, ValueError):
        return None


def _chat_add(rid: int, role: str, text: str, meta: str = "", err: bool = False):
    store.chat_add(rid, role, text, meta, err)


def _chat_history(rid: int, limit: int = 8):
    return store.chat_history(rid, limit)



# ---------- app ----------
# Set when the process is asked to stop. An SSE stream keeps its connection open for
# as long as a browser tab is watching, and uvicorn's graceful shutdown waits for
# those connections BEFORE it runs lifespan shutdown -- so a flag set from lifespan
# is always too late and systemd ends up SIGKILLing us (observed in the journal).
# It is therefore set from the signal handler itself, chained in front of uvicorn's.
STOP = asyncio.Event()


def _hook_stop_on_signal() -> None:
    """Make SIGTERM/SIGINT close SSE streams immediately, then let uvicorn continue."""
    if threading.current_thread() is not threading.main_thread():
        return      # signals only reach the main thread; nothing to chain otherwise
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        prev = signal.getsignal(sig)

        def handler(s, frame, _prev=prev):
            # Signal handlers run in the main thread, but call_soon_threadsafe also
            # works there and never touches loop internals from a signal context.
            loop.call_soon_threadsafe(STOP.set)
            if callable(_prev):
                _prev(s, frame)

        try:
            signal.signal(sig, handler)
        except (ValueError, OSError) as e:
            print("signal hook kurulamadı:", e)


@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI):
    _init_db()
    ensure_race()
    _hook_stop_on_signal()
    task = asyncio.create_task(feed_loop())
    stask = asyncio.create_task(standings_loop())
    try:
        yield
    finally:
        STOP.set()
        task.cancel()
        stask.cancel()
        for t in (task, stask):
            with contextlib.suppress(BaseException):
                await t


app = FastAPI(lifespan=lifespan)
# Shared shell assets (theme.css, shell.js). Mounted, not read per request, so
# the browser can cache them; the files are the single copy all three surfaces
# use, so there is nothing to keep in sync by hand.
app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")
# PWA: manifest + service worker at the surface root. Shared so the three
# manifests cannot drift apart (see pwa.py for why start_url is relative).
pwa.install(app, "live")
# AI provider settings: both chat surfaces must be able to read AND write the
# same profile rows, because they are separate processes over one database.
ai_config.install(app)


@app.get("/", response_class=HTMLResponse)
async def index():
    return (HERE / "index.html").read_text(encoding="utf-8")


@app.get("/api/state")
async def api_state():
    return JSONResponse(snapshot())


@app.get("/api/stream")
async def api_stream(request: Request):
    async def gen():
        last = None
        # Heartbeat. Between laps nothing changes, so this stream can be silent
        # for a minute or more -- and any proxy in front of us has an idle
        # timeout that silently drops a quiet connection; the shortest common
        # one sits around 100 s. A comment line (`:`) restarts that timer
        # without triggering EventSource's message handler, so the browser
        # stays on the same connection and sees no fake event.
        last_write = time.monotonic()
        # No request.is_disconnected() here on purpose: it awaits a silent client
        # forever, so the loop can never notice STOP and SIGTERM hangs until systemd
        # SIGKILLs us. A dead client surfaces as a failed send, which Starlette turns
        # into generator close; shutdown surfaces through STOP or a cancellation.
        try:
            while not STOP.is_set():
                s = snapshot()
                payload = json.dumps(s, default=str)
                if payload != last:
                    last = payload
                    last_write = time.monotonic()
                    yield f"data: {payload}\n\n"
                elif time.monotonic() - last_write >= SSE_PING_SECS:
                    last_write = time.monotonic()
                    yield ": ping\n\n"
                await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            # uvicorn cancels live streams once its graceful-shutdown window ends;
            # returning quietly keeps the journal free of fake error tracebacks
            # (EventSource reconnects on its own).
            return
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ----- race archive -----
@app.get("/api/scope")
async def api_scope(race: str = "live"):
    """Which sessions of this chat's weekend can be added to the context.

    The chip row is built from this, so the page cannot offer a session that has
    no record -- clicking one and getting an unchanged answer is the failure this
    endpoint exists to make impossible. `owner` is the chat's own session: it is
    always in scope and cannot be removed, because a chat belongs to a session.
    """
    rid = _rid_for(race)
    return JSONResponse(_scope_payload(rid))


def _scope_payload(rid):
    import weekend as _weekend
    if not rid:
        return {"owner": None, "sessions": []}
    try:
        ov = _weekend.overview(int(rid))
    except Exception as e:
        return {"owner": None, "sessions": [], "error": f"{type(e).__name__}: {e}"[:200]}
    if not ov:
        return {"owner": None, "sessions": []}
    owner = store.session_kind((store.get_race(int(rid)) or {}).get("session"))
    return {
        "owner": owner,
        "meeting": ov.get("meeting"),
        "cards": len(ov.get("cards") or []),
        "sessions": [{"kind": c["kind"], "label": c["label"],
                      "has_data": bool(c.get("laps")), "state": c["state"],
                      "rid": c.get("rid")} for c in ov.get("cards") or []],
    }


@app.get("/api/races")
async def api_races():
    try:
        races = store.list_races()
    except Exception as e:
        return JSONResponse({"races": [], "current": CURRENT_RACE_ID, "error": str(e)[:200]})
    for r in races:
        r["live"] = (r["id"] == CURRENT_RACE_ID)
    return JSONResponse({"races": races, "current": CURRENT_RACE_ID})


@app.get("/api/races/{rid}")
async def api_race(rid: int):
    try:
        race = store.get_race(rid)
        if race is None:
            return JSONResponse({"error": "yarış bulunamadı"}, status_code=404)
        laps = store.race_laps(rid)
    except Exception as e:
        return JSONResponse({"error": str(e)[:200]}, status_code=500)
    snap = archived_snapshot(rid)
    return JSONResponse({"race": race, "laps": [x["lap"] for x in laps],
                         "frame_sources": laps, "snapshot": snap})


@app.get("/api/races/{rid}/snapshot")
async def api_race_snapshot(rid: int, lap: int | None = None):
    snap = archived_snapshot(rid, lap)
    if snap is None:
        return JSONResponse({"error": "bu yarış için kayıt yok"}, status_code=404)
    return JSONResponse(snap)


# ----- chat (server-side, per race) -----
@app.get("/api/prefs")
async def api_prefs():
    """Learned, server-side output preferences (shared by both surfaces)."""
    p = analysis.prefs()
    return JSONResponse({"prefs": p, "summary": analysis.prefs_summary(p)})


@app.post("/api/prefs")
async def api_prefs_set(request: Request):
    """Set one preference, or wipe them all. Learning also happens in /api/chat."""
    body = await request.json()
    if body.get("reset"):
        store.prefs_clear()
        return JSONResponse({"ok": True, "prefs": analysis.prefs()})
    key, value = str(body.get("key") or ""), str(body.get("value") or "")
    if key not in analysis.PREF_DEFAULTS:
        return JSONResponse({"ok": False, "reason": "bilinmeyen tercih: " + key}, status_code=400)
    if not value:
        store.pref_delete(key)
    elif (key, value) not in analysis.PREF_LABELS:
        return JSONResponse({"ok": False, "reason": "geçersiz değer"}, status_code=400)
    else:
        store.pref_set(key, value)
    p = analysis.prefs()
    return JSONResponse({"ok": True, "prefs": p, "summary": analysis.prefs_summary(p)})


@app.get("/api/usage")
async def api_usage():
    """What the assistant has cost: context size, cache share, truncations, credits.

    Numbers come from the provider's own `usage` block (plus its credit balance);
    nothing is estimated. Stored in the DB, so the archive tab shows the same
    numbers even though it is a different process.
    """
    return JSONResponse(store.usage_summary())


@app.get("/api/chat")
async def api_chat_list(race: str = "live"):
    rid = _rid_for(race)
    msgs = []
    if rid:
        try:
            msgs = store.chat_all(rid)
        except Exception:
            msgs = []
    return JSONResponse({"race_id": rid, "messages": msgs})


@app.post("/api/chat/import")
async def api_chat_import(request: Request):
    """One-shot migration of a browser-local transcript into the archive."""
    body = await request.json()
    rid = _rid_for(body.get("race"))
    msgs = body.get("messages") or []
    if not rid:
        return JSONResponse({"ok": False, "reason": "no-race"})
    try:
        n = store.chat_count(rid)
    except Exception as e:
        return JSONResponse({"ok": False, "reason": str(e)[:200]}, status_code=500)
    if n:
        return JSONResponse({"ok": True, "imported": 0, "reason": "already-has-history"})
    added = 0
    for m in msgs:
        if not isinstance(m, dict):
            continue
        role, text = m.get("role"), m.get("text")
        if role not in ("user", "assistant") or not isinstance(text, str) or not text.strip():
            continue
        _chat_add(rid, role, text[:4000], str(m.get("meta") or "")[:300], bool(m.get("err")))
        added += 1
    return JSONResponse({"ok": True, "imported": added})


@app.post("/api/chat")
async def api_chat(request: Request):
    """Live chat. The body of the pipeline lives in chat_request.py.

    Written once and shared with archive.py: two copies of this handler is how the
    live and archive surfaces end up disagreeing about what a scope means (or, as
    actually happened, about whether markdown gets rendered at all).
    """
    body = await request.json()
    race = body.get("race")
    rid = _rid_for(race)
    archived = race not in (None, "", "live")
    live_snap = None
    if not archived:
        live_snap = snapshot()
    out = await chat_request.handle(
        message=body.get("message") or "",
        race_id=rid,
        live_snap=live_snap,
        archived=archived,
        scope=body.get("scope"),
        lap=body.get("lap"),
        reasoning=body.get("reasoning"),
        # Live surface only: resolve questions that point outside this snapshot
        # before the call, deterministically, and attach real stored rows. A tool
        # loop would cost 20-30 s per question and risk invented arguments; these
        # lookups are exact and testable (see routing.py).
        route_extra=_route_extra,
    )
    return JSONResponse(out)


#: How often the answer stream says something while the model is still thinking.
#: Reasoning tokens are not sent as content, so without this the connection would
#: be byte-silent for the whole thinking period -- long enough for any proxy's
#: idle timeout (~100 s is common) to drop it. 5 s gives 20 heartbeats inside
#: that window: cheap (a few bytes each) and far below every such timeout.
CHAT_PING_SECS = 5.0


@app.post("/api/chat/stream")
async def api_chat_stream(request: Request):
    """Stream a chat answer as Server-Sent Events.

    This endpoint exists because of a failure, not a preference. The plain
    /api/chat call returns nothing for 30-110 s while the model thinks; a proxy
    between the browser and this process drops a byte-silent connection at
    around 100 s and answers with its own HTML error page, which the panel
    reported as "HTTP 524 — sunucu JSON yerine HTML döndürdü" (the server
    returned HTML instead of JSON). The answer had been produced (and paid for)
    but never reached the browser.

    Two things keep the connection alive: the provider's tokens, and -- while
    there are none, because reasoning is not streamed as content -- a comment line
    every CHAT_PING_SECS. The comment is ignored by EventSource and by our own
    parser, so it cannot corrupt the answer.

    Event shapes (each `data: {json}`):
        scope  -> which sessions this answer is based on
        thinking -> how many characters of reasoning so far (text is not shown)
        delta  -> a piece of the answer, append it
        done   -> final answer + the cost/quality meta
        error  -> a message to show instead of an answer
    """
    body = await request.json()
    race = body.get("race")
    rid = _rid_for(race)
    archived = race not in (None, "", "live")
    live_snap = None if archived else snapshot()

    async def gen():
        import chat_request as _cr
        agen = _cr.handle_stream(
            message=body.get("message") or "",
            race_id=rid,
            live_snap=live_snap,
            archived=archived,
            scope=body.get("scope"),
            lap=body.get("lap"),
            reasoning=body.get("reasoning"),
            route_extra=_route_extra,
        ).__aiter__()
        pending = asyncio.ensure_future(agen.__anext__())
        try:
            while True:
                done, _ = await asyncio.wait({pending}, timeout=CHAT_PING_SECS)
                if not done:
                    # Nothing yet (the model is thinking). Keep the pipe warm.
                    yield ": ping\n\n"
                    continue
                try:
                    ev = pending.result()
                except StopAsyncIteration:
                    break
                except Exception as e:
                    # An exception inside the pipeline must reach the panel as a
                    # readable message, not kill the stream mid-answer (which the
                    # browser would report as a truncated response). scrub()
                    # before the slice, so a provider body cannot carry the key.
                    import ai_config as _ac
                    ev = {"type": "error",
                          "message": _ac.scrub(
                              f"Sunucu hatası: {type(e).__name__}: {e}", None)[:300]}
                    yield "data: " + json.dumps(ev, default=str) + "\n\n"
                    break
                yield "data: " + json.dumps(ev, default=str) + "\n\n"
                pending = asyncio.ensure_future(agen.__anext__())
        except asyncio.CancelledError:
            # The browser closed the tab (or a proxy gave up). Cancel the model
            # call rather than let it run to completion for nobody.
            pending.cancel()
            raise

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@app.delete("/api/chat")
async def api_chat_clear(race: str = "live"):
    rid = _rid_for(race)
    if not rid:
        return JSONResponse({"ok": False, "reason": "no-race"})
    store.chat_clear(rid)
    return JSONResponse({"ok": True, "race_id": rid})


# ----- provisional championship (live surface) -----
@app.get("/api/standings/provisional")
async def api_provisional(season: int | None = None):
    """What the live panel shows: base table + this race's projection on top.

    Separate from /api/state so the panel can be refreshed on its own, and so
    the numbers can be checked without parsing a whole snapshot.
    """
    snap = snapshot()
    view = snap.get("standings") or {}
    return JSONResponse({"race_id": CURRENT_RACE_ID, "lap": snap.get("lap"),
                         "total_laps": snap.get("total"), "session": snap.get("session"),
                         **view})


@app.post("/api/standings/refresh")
async def api_standings_refresh(request: Request):
    """Force a base-table refresh from the live surface (button / diagnosis)."""
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    meta = _session_meta() or {}
    season = (body.get("season") if isinstance(body, dict) else None) \
        or meta.get("year") or standings.season_now()
    try:
        season = int(season)
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "reason": "geçersiz sezon"}, status_code=400)
    res = await asyncio.to_thread(_standings_refresh_blocking, season)
    _standings_state.update({"at": time.time(), "season": season,
                             "base": res.get("base"), "cons": res.get("cons"),
                             "schedule": res.get("schedule") or [],
                             "err": None if res.get("ok") else _standings_err(res)})
    return JSONResponse({"ok": bool(res.get("ok")), "report": res.get("report"),
                         "error": _standings_state.get("err")})


if __name__ == "__main__":
    # timeout_graceful_shutdown keeps SIGTERM inside systemd's TimeoutStopSec=15:
    # the SSE flag usually ends streams instantly, this is the belt-and-braces bound.
    # F1_HOST widens the bind address for remote surfaces (e.g. a phone on the LAN).
    # Default stays loopback: no auth in this app, so exposure must be opt-in.
    uvicorn.run(app, host=os.environ.get("F1_HOST", "127.0.0.1"),
                port=int(os.environ.get("F1_PORT", "8099")),
                log_level="warning", timeout_graceful_shutdown=8)
