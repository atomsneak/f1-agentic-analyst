#!/usr/bin/env python3
"""F1 welcome surface: pick a weekend, then pick a session.

The fourth surface, and the only one that is read-mostly in a different sense
from the archive: it does not show a race, it shows WHICH races exist and what
state each one is in. That question has to be answerable before a session starts
(so an installed shortcut lands somewhere useful on a Friday morning), which is
why it is a separate process on its own port rather than the root of the live
surface: the live page is a timing table, and a timing table with no feed is an
empty screen with a chat box under it.

Views
-----
  /                 weekend list on the left (or a picker on a phone), the
                    session cards of the selected weekend on the right
  /api/weekends     every weekend in the archive, newest first
  /api/weekend/KEY  the card grid for one weekend (weekend.overview)

Honesty rules inherited from the rest of the project
---------------------------------------------------
  * Every card's state comes from `weekend.session_state`, which reads capture
    evidence. A session that was never captured says "kayıt yok" -- it does not
    say "yapılmadı", because the archive cannot know that.
  * A card with no data has no link. A card whose session is live links to the
    live surface; a finished one links to the archive at that exact race id.
  * The expected session list is an assumption when nothing proves otherwise, and
    `shape_source` travels to the page so it can say so instead of asserting a
    schedule it does not have.

Run:  .venv/bin/python welcome_app.py     (port 8103 by default)
"""
from __future__ import annotations

import os
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import pwa
import store
import weekend

HERE = Path(__file__).resolve().parent

store.init_db()
app = FastAPI(title="F1 Weekend")
app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")
pwa.install(app, "welcome")

#: Ports the page needs in order to link onward. Sent to the client as data so
#: the JS does not hard-code them a second time -- `shell.js` owns the mapping
#: from a port to a URL.
LIVE_PORT = os.environ.get("F1_LIVE_PORT", "8099")
ARCHIVE_PORT = os.environ.get("F1_ARCHIVE_PORT", "8100")
STANDINGS_PORT = os.environ.get("F1_STANDINGS_PORT", "8101")


@app.get("/", response_class=HTMLResponse)
async def index():
    return (HERE / "welcome.html").read_text(encoding="utf-8")


def _weekend_summary(rid: int, *, rows: list[dict] | None = None) -> dict | None:
    """One weekend, reduced to what the list needs (no per-session lap reads).

    `rows` is threaded through from the caller's single `list_races()` call: the
    grouping used to re-query the whole table once per race, so the weekend list
    got slower the more of the season was archived (2.3 s at 95 rows).
    """
    wk = store.weekend_of(rid, rows=rows)
    if not wk:
        return None
    sessions = wk.get("sessions") or []
    # The representative race id: the live session if there is one, else the
    # newest captured session. This is what the page opens by default and what a
    # list entry points at.
    live, best = None, None
    for s in sessions:
        state = weekend.session_state(s)
        if state == "live":
            live = s
            break
        if state in ("done", "empty"):
            best = s
    chosen = live or best or (sessions[-1] if sessions else None)
    if chosen is None:
        return None
    kinds = [store.session_kind(s.get("session")) for s in sessions]
    return {
        "rid": int(chosen["id"]),
        "key": wk["key"],
        "meeting": wk["meeting"],
        "official": wk["official"],
        "year": wk["year"],
        "start_date": wk["start_date"],
        "n_sessions": len(sessions),
        "kinds": [k for k in kinds if k],
        "live_kind": store.session_kind(live.get("session")) if live else None,
        "live_rid": int(live["id"]) if live else None,
        # Newest evidence of activity, so a list can sort/group honestly.
        "last_seen": max((s.get("last_seen") or "" for s in sessions), default=""),
        "has_frames": any(int(s.get("n_frames") or 0) for s in sessions),
    }


@app.get("/api/weekends")
async def api_weekends():
    """Every weekend we hold at least one race row for, newest first.

    Grouped by `weekend.weekend_key` through `store.weekend_of`, so the list and
    the card grid can never disagree about which row belongs where. Rows with no
    session name (should not happen) are skipped rather than shown as a mystery
    card.
    """
    try:
        races = store.list_races()
    except Exception as e:
        return JSONResponse({"weekends": [], "error": str(e)[:200]})
    seen: dict[str, dict] = {}
    for r in races:
        try:
            w = _weekend_summary(int(r["id"]), rows=races)
        except Exception:
            w = None
        if not w:
            continue
        prev = seen.get(w["key"])
        # Keep the entry whose representative is most recently touched: a weekend
        # with a live session must not be represented by its Friday practice.
        if prev is None or (w.get("last_seen") or "") > (prev.get("last_seen") or ""):
            seen[w["key"]] = w
    out = sorted(seen.values(),
                 key=lambda w: (w.get("start_date") or "", w.get("rid") or 0), reverse=True)
    return JSONResponse({
        "weekends": out,
        "current": out[0]["rid"] if out else None,
        "ports": {"live": LIVE_PORT, "archive": ARCHIVE_PORT, "standings": STANDINGS_PORT},
    })


@app.get("/api/weekend/{rid}")
async def api_weekend(rid: int):
    """The card grid for the weekend `rid` belongs to."""
    try:
        ov = weekend.overview(rid)
    except Exception as e:
        return JSONResponse({"error": f"{type(e).__name__}: {e}"[:300]}, status_code=500)
    if not ov:
        return JSONResponse({"error": "bu yarış kaydı bulunamadı"}, status_code=404)
    ov["ports"] = {"live": LIVE_PORT, "archive": ARCHIVE_PORT, "standings": STANDINGS_PORT}
    return JSONResponse(ov)


@app.get("/api/health")
async def api_health():
    return JSONResponse({"ok": True, "db": str(store.DB_PATH),
                         "weekends": len(store.list_races())})


if __name__ == "__main__":
    port = int(os.environ.get("F1_WELCOME_PORT", "8103"))
    uvicorn.run(app, host=os.environ.get("F1_HOST", "127.0.0.1"), port=port,
                log_level="warning", timeout_graceful_shutdown=8)
