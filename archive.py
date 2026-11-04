#!/usr/bin/env python3
"""F1 archive browser: the second surface, for races that already happened.

This is deliberately NOT the live dashboard with a different skin. The live app
answers "what is happening right now" and a timing table is the right shape for
that. Here the question changes to "what happened, and why", which wants charts:
tyre strategy, gap evolution, pit windows, lap-time pace.

It shares three things with the live app and nothing else:
  * store.py     -- one database, so the two surfaces never disagree
  * analysis.py  -- MED8/TREND, provenance handling, the LLM briefing
  * the chat table, keyed by race, so a race keeps one conversation no matter
    which surface you opened it from

Read-only except for chat. It never touches the live feed, so a slow or failed
request here can never cost us a recording.

Run:  .venv/bin/python archive.py           (port 8100 by default)
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

import analysis
import ai_config
import chat_request
import points
import pwa
import store
import weekend

HERE = Path(__file__).resolve().parent
COMPOUND_ORDER = ("SOFT", "MEDIUM", "HARD", "INTERMEDIATE", "WET")


# ---------- derived series (all from stored data, nothing invented) ----------
def _parse_gap(v) -> float | None:
    """'+11.634' -> 11.634 ; '' -> 0.0 ; '+1 LAP' -> None (not a seconds gap)."""
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return 0.0
    if "LAP" in s.upper():
        return None
    try:
        return float(s.lstrip("+"))
    except ValueError:
        return None


def build_series(rid: int) -> dict:
    """Everything the charts need, computed once per race load."""
    race = store.get_race(rid)
    if race is None:
        return {"error": "yarış bulunamadı"}
    laps_meta = store.race_laps(rid)
    lap_numbers = [x["lap"] for x in laps_meta]
    if not lap_numbers:
        return {"race": race, "laps": [], "drivers": [], "sources": laps_meta}

    frames: dict[int, dict] = {}
    for lapno in lap_numbers:
        fr = store.frame_row(rid, lapno)
        if fr and fr["lap"] == lapno:
            frames[lapno] = fr

    # driver identity: prefer the richest (latest) frame that lists everyone
    identity: dict[str, dict] = {}
    for lapno in sorted(frames, reverse=True):
        for r in frames[lapno]["snap"].get("rows") or []:
            num = str(r.get("num"))
            if num not in identity:
                identity[num] = {
                    "num": num,
                    "code": r.get("code") or num,
                    "name": r.get("name") or "",
                    "team": r.get("team") or "",
                    "color": r.get("color") or "888888",
                    "grid": r.get("grid"),
                }

    # per-lap tyre (for the strategy strip) and stored position/gap
    tyre: dict[str, dict[int, dict]] = {}
    stored_gap: dict[str, dict[int, float | None]] = {}
    pos: dict[str, dict[int, int]] = {}
    pit_at: dict[str, list[int]] = {}
    last_pit: dict[str, int] = {}
    for lapno in lap_numbers:
        for r in frames[lapno]["snap"].get("rows") or []:
            num = str(r.get("num"))
            tyre.setdefault(num, {})[lapno] = {"compound": r.get("tyre"),
                                               "age": r.get("tyre_age")}
            stored_gap.setdefault(num, {})[lapno] = _parse_gap(r.get("gap"))
            if r.get("pos"):
                pos.setdefault(num, {})[lapno] = int(r["pos"])
            p = r.get("pit")
            if isinstance(p, int) and p > last_pit.get(num, 0):
                last_pit[num] = p
                pit_at.setdefault(num, []).append(lapno)

    # exact gaps from cumulative lap times: independent of the stored strings,
    # consistent across every lap, and pit time is inside a lap duration anyway
    times = store.all_lap_times(rid)
    cum: dict[str, dict[int, float]] = {}
    for num, hist in times.items():
        acc, out = 0.0, {}
        for lapno in sorted(hist):
            acc += hist[lapno]
            out[lapno] = acc
        cum[num] = out

    gap_series: dict[str, list] = {}
    for lapno in lap_numbers:
        present = [(num, cum[num][lapno]) for num in cum if lapno in cum.get(num, {})]
        if not present:
            continue
        best = min(c for _, c in present)
        for num, c in present:
            gap_series.setdefault(num, []).append([lapno, round(c - best, 3)])

    # stints, collapsed from the per-lap tyre map
    stints: dict[str, list] = {}
    for num, per_lap in tyre.items():
        seq: list[dict] = []
        for lapno in sorted(per_lap):
            comp = per_lap[lapno].get("compound")
            if comp is None:
                continue
            if seq and seq[-1]["compound"] == comp and seq[-1]["end"] == lapno - 1:
                seq[-1]["end"] = lapno
                seq[-1]["laps"] += 1
            else:
                seq.append({"compound": comp, "start": lapno, "end": lapno, "laps": 1,
                            "age_at_start": per_lap[lapno].get("age") or 1})
        stints[num] = seq

    # The page has to know WHAT KIND of session it is showing. Without this the
    # qualifying view drew a "tyre strategy" grid -- a wall of red blocks with no
    # meaning, because in qualifying everyone runs short sequences on several
    # sets -- and labelled session-clock ticks as if they were race laps.
    stype = points.session_type(race.get("session"))

    final_order = []
    last_lap = max(lap_numbers)
    for r in frames[last_lap]["snap"].get("rows") or []:
        final_order.append(str(r.get("num")))

    drivers = []
    for num in sorted(identity, key=lambda n: (identity[n].get("grid") or 99)):
        d = dict(identity[num])
        d["final_pos"] = final_order.index(num) + 1 if num in final_order else None
        d["stints"] = stints.get(num, [])
        d["pit_laps"] = pit_at.get(num, [])
        drivers.append(d)
    drivers.sort(key=lambda d: (d["final_pos"] or 99))

    lap_time_series = {num: [[l, round(s, 3)] for l, s in sorted(h.items())]
                       for num, h in times.items()}

    total = race.get("total_laps") or last_lap
    return {
        "race": race,
        "laps": lap_numbers,
        "total_laps": total,
        "drivers": drivers,
        "gap_series": gap_series,
        "lap_time_series": lap_time_series,
        "stored_gap": stored_gap,
        "pos_by_lap": pos,
        "sources": laps_meta,
        # What kind of session this is. The page uses it to relabel the tyre grid
        # and to stop calling the session counter a race lap.
        "session_type": stype,
        "has_openf1": any(x["source"] == store.SOURCE_OPENF1 for x in laps_meta),
        "missing_union": sorted({m for x in laps_meta for m in x["missing"]}),
    }


# ---------- app ----------
store.init_db()
app = FastAPI(title="F1 Archive")
# Same shell chrome as the other two surfaces (one copy in static/).
app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")
# PWA: manifest + service worker at the surface root. Shared so the three
# manifests cannot drift apart (see pwa.py for why start_url is relative).
pwa.install(app, "archive")
# AI provider settings: both chat surfaces must be able to read AND write the
# same profile rows, because they are separate processes over one database.
ai_config.install(app)


@app.get("/", response_class=HTMLResponse)
async def index():
    return (HERE / "archive.html").read_text(encoding="utf-8")


@app.get("/api/scope")
async def api_scope(race: str = ""):
    """Same contract as the live surface: the chip row's source of truth."""
    try:
        rid = int(race)
    except (TypeError, ValueError):
        return JSONResponse({"owner": None, "sessions": []})
    try:
        ov = weekend.overview(rid)
    except Exception as e:
        return JSONResponse({"owner": None, "sessions": [],
                             "error": f"{type(e).__name__}: {e}"[:200]})
    if not ov:
        return JSONResponse({"owner": None, "sessions": []})
    owner = store.session_kind((store.get_race(rid) or {}).get("session"))
    return JSONResponse({
        "owner": owner,
        "meeting": ov.get("meeting"),
        "sessions": [{"kind": c["kind"], "label": c["label"],
                      "has_data": bool(c.get("laps")), "state": c["state"],
                      "rid": c.get("rid")} for c in ov.get("cards") or []],
    })


@app.get("/api/races")
async def api_races():
    races = store.list_races()
    return JSONResponse({"races": races})


@app.get("/api/race/{rid}/series")
async def api_series(rid: int):
    try:
        return JSONResponse(build_series(rid))
    except Exception as e:
        return JSONResponse({"error": f"{type(e).__name__}: {e}"[:300]}, status_code=500)


@app.get("/api/race/{rid}/lap")
async def api_lap(rid: int, lap: int | None = None):
    snap = analysis.archived_snapshot(rid, lap)
    if snap is None:
        return JSONResponse({"error": "bu yarış için kayıt yok"}, status_code=404)
    return JSONResponse(snap)


@app.get("/api/prefs")
async def get_prefs():
    """Learned output preferences: server-side so both surfaces agree."""
    p = analysis.prefs()
    return JSONResponse({"prefs": p, "summary": analysis.prefs_summary(p)})


@app.post("/api/prefs")
async def set_prefs(request: Request):
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
async def get_usage():
    """Same cost numbers as the live surface -- one shared table, not a copy."""
    return JSONResponse(store.usage_summary())


@app.get("/api/chat")
async def chat_list(race: str = ""):
    try:
        rid = int(race)
    except (TypeError, ValueError):
        return JSONResponse({"messages": [], "race_id": None})
    return JSONResponse({"race_id": rid, "messages": store.chat_all(rid)})


@app.post("/api/chat")
async def chat_post(request: Request):
    """Archive chat. Shares the pipeline with the live surface (chat_request.py).

    The duplicate that used to live here had already drifted: it ignored
    `_route_extra`, so a scoped question in the archive could never see a sibling
    session while the same question on the live page could.
    """
    body = await request.json()
    rid = body.get("race")
    if not rid:
        return JSONResponse({"answer": "Eksik istek.", "meta": {}})
    try:
        rid = int(rid)
    except (TypeError, ValueError):
        return JSONResponse({"answer": "Geçersiz yarış.", "meta": {"error": True}})
    out = await chat_request.handle(
        message=body.get("message") or "",
        race_id=rid,
        live_snap=None,
        archived=True,
        scope=body.get("scope"),
        lap=body.get("lap"),
        reasoning=body.get("reasoning"),
    )
    return JSONResponse(out)


#: See server.CHAT_PING_SECS for why this heartbeat exists.
CHAT_PING_SECS = 5.0


@app.post("/api/chat/stream")
async def chat_stream(request: Request):
    """Streamed chat for the archive surface. Same contract as the live one.

    Both surfaces need their own endpoint (separate processes), but the pipeline
    is the same `chat_request.handle_stream`, so the two cannot drift.
    """
    body = await request.json()
    rid = body.get("race")
    if not rid:
        return JSONResponse({"answer": "Eksik istek.", "meta": {}})
    try:
        rid = int(rid)
    except (TypeError, ValueError):
        return JSONResponse({"answer": "Geçersiz yarış.", "meta": {"error": True}})

    async def gen():
        agen = chat_request.handle_stream(
            message=body.get("message") or "",
            race_id=rid,
            live_snap=None,
            archived=True,
            scope=body.get("scope"),
            lap=body.get("lap"),
            reasoning=body.get("reasoning"),
        ).__aiter__()
        pending = asyncio.ensure_future(agen.__anext__())
        try:
            while True:
                done, _ = await asyncio.wait({pending}, timeout=CHAT_PING_SECS)
                if not done:
                    yield ": ping\n\n"
                    continue
                try:
                    ev = pending.result()
                except StopAsyncIteration:
                    break
                except Exception as e:
                    # scrub() before the slice: an exception text can carry the
                    # provider's response body, and a truncated key is no longer
                    # matchable by value alone.
                    import ai_config as _ac
                    ev = {"type": "error",
                          "message": _ac.scrub(
                              f"Sunucu hatası: {type(e).__name__}: {e}", None)[:300]}
                    yield "data: " + json.dumps(ev, default=str) + "\n\n"
                    break
                yield "data: " + json.dumps(ev, default=str) + "\n\n"
                pending = asyncio.ensure_future(agen.__anext__())
        except asyncio.CancelledError:
            pending.cancel()
            raise

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@app.delete("/api/chat")
async def chat_clear(race: str = ""):
    try:
        rid = int(race)
    except (TypeError, ValueError):
        return JSONResponse({"ok": False}, status_code=400)
    store.chat_clear(rid)
    return JSONResponse({"ok": True, "race_id": rid})


if __name__ == "__main__":
    port = int(os.environ.get("F1_ARCHIVE_PORT", "8100"))
    # F1_HOST mirrors server.py: loopback unless a remote surface is opted in.
    uvicorn.run(app, host=os.environ.get("F1_HOST", "127.0.0.1"), port=port,
                log_level="warning", timeout_graceful_shutdown=8)
