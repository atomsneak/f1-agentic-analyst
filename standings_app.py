#!/usr/bin/env python3
"""F1 standings surface: drivers' and constructors' championship, offline-first.

The third surface. Live answers "what is happening", the archive answers "what
happened and why", and this one answers "where does the season stand" -- a
question that must have an answer even when the network does not, which is why
the data is cached in the same SQLite file the other two surfaces use.

Read path: SQLite only. A page load with no network renders the last snapshot it
has and says how old it is. Write path: `standings.refresh()`, called from the
refresh button or from a periodic attempt.

Read-only with respect to the live capture: it never touches `frames`, and the
only tables it writes are the two standings cache tables. A slow or failing
standings refresh therefore cannot cost us a recording.

Run:  .venv/bin/python standings_app.py        (port 8101 by default)
"""
from __future__ import annotations

import os
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import points
import pwa
import standings
import store

HERE = Path(__file__).resolve().parent

store.init_db()
app = FastAPI(title="F1 Standings")
app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")
# PWA: manifest + service worker at the surface root. Shared so the three
# manifests cannot drift apart (see pwa.py for why start_url is relative).
pwa.install(app, "standings")


def _season_param(raw) -> int | None:
    if raw in (None, "", "current", "now"):
        return None
    try:
        y = int(raw)
    except (TypeError, ValueError):
        return None
    return y if 1950 <= y <= 2100 else None


def _payload(season: int, *, refreshed: dict | None = None) -> dict:
    """Everything the page needs, assembled from the cache only."""
    driver = standings.get_standings_view(season, store.STANDING_KIND_DRIVER)
    cons = standings.get_standings_view(season, store.STANDING_KIND_CONSTRUCTOR)

    # Which seasons we can offer: those with a cache, plus the one asked for.
    seasons = sorted({season, *store.standings_seasons()}, reverse=True)

    # A driver table without a constructor table is a partial picture, and the
    # page must be able to say which half is missing.
    missing = [k for k, v in ((store.STANDING_KIND_DRIVER, driver),
                              (store.STANDING_KIND_CONSTRUCTOR, cons))
               if not v.get("has_data")]

    view = {
        "season": season,
        "seasons": seasons,
        "driver": driver,
        "constructor": cons,
        "missing": missing,
        "source_note": standings.SOURCE_NOTE,
        # The gap between P1 and P2, only when both exist as real numbers.
        "gap_p1_p2": _gap(driver.get("rows") or []) if len(driver.get("rows") or []) > 1 else None,
        "has_data": bool(driver.get("has_data") or cons.get("has_data")),
    }
    if refreshed is not None:
        view["refreshed"] = refreshed
    return view


def _gap(rows: list[dict]) -> int | None:
    """P1 points minus P2 points, from rows already ordered by the API."""
    try:
        a = rows[0].get("points")
        b = rows[1].get("points")
    except (IndexError, AttributeError):
        return None
    if a is None or b is None:
        return None
    try:
        return int(a) - int(b)
    except (TypeError, ValueError):
        return None


@app.get("/", response_class=HTMLResponse)
async def index():
    return (HERE / "standings.html").read_text(encoding="utf-8")


@app.get("/api/standings")
async def api_standings(season: str | None = None, refresh: int = 0):
    """The cached picture, optionally retried first when it is old.

    `refresh=1` means "if the cache is older than the freshness window and the
    network is worth trying, try once". It is deliberately not an unconditional
    fetch: a page that hammers a dead network is a page that takes 15 s to load
    while offline.
    """
    y = _season_param(season)
    if y is None:
        cached = store.standings_seasons()
        y = cached[0] if cached else standings.season_now()
    refreshed = None
    if refresh:
        try:
            refreshed = standings.ensure_refreshed(y)
        except Exception as e:                      # never 500 the page for this
            refreshed = {"ok": False, "error": f"{type(e).__name__}: {e}"[:200]}
    return JSONResponse(_payload(y, refreshed=refreshed))


@app.post("/api/standings/refresh")
async def api_refresh(request: Request):
    """Force a fetch now (the refresh button). Reports failures; keeps the cache."""
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    season_raw = body.get("season") if isinstance(body, dict) else None
    y = _season_param(season_raw)
    if y is None:
        cached = store.standings_seasons()
        y = cached[0] if cached else standings.season_now()
    report = standings.refresh(y)
    return JSONResponse(_payload(y, refreshed=report))


@app.get("/api/provisional")
async def api_provisional(race: int | None = None, season: str | None = None):
    """The projected table for a live race, if the live surface has one.

    Kept here as well as on the live surface so a bookmarked standings page can
    explain the projection without the live snapshot.
    """
    y = _season_param(season) or standings.season_now()
    if race is None:
        races = store.list_races()
        race = races[0]["id"] if races else None
    if race is None:
        return JSONResponse({"error": "yarış yok", "provisional": None}, status_code=404)
    fr = store.frame_row(int(race))
    if not fr:
        return JSONResponse({"error": "bu yarış için kayıt yok", "provisional": None},
                            status_code=404)
    snap = fr.get("snap") or {}
    base = standings.base_rows_for_projection(y)
    cons = standings.get_standings_view(y, store.STANDING_KIND_CONSTRUCTOR)
    if not base["has_data"] or base["unsafe"]:
        return JSONResponse({"race_id": int(race), "season": y, "provisional": None,
                             "reason": base["reason"], "base": base})
    proj = points.provisional(snap.get("rows") or [], base["rows"],
                              cons.get("rows") or [],
                              kind=points.session_kind(snap.get("session")),
                              lap=snap.get("lap"), total_laps=snap.get("total"))
    return JSONResponse({"race_id": int(race), "season": y, "provisional": proj,
                         "base": base})


@app.get("/api/health")
async def api_health():
    """Cheap health for the launcher and for a curl after a deploy."""
    return JSONResponse({"ok": True, "db": str(store.DB_PATH),
                         "seasons": store.standings_seasons(),
                         "source": standings.SOURCE_NOTE})


if __name__ == "__main__":
    port = int(os.environ.get("F1_STANDINGS_PORT", "8101"))
    uvicorn.run(app, host=os.environ.get("F1_HOST", "127.0.0.1"), port=port,
                log_level="warning", timeout_graceful_shutdown=8)
