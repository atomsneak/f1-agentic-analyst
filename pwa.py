"""PWA wiring shared by the three surfaces.

Why a module instead of three copies: the three apps (`server.py`,
`archive.py`, `standings_app.py`) are separate processes on separate ports, and
each needs the same three things to be installable -- a manifest, a service
worker, and the icon set. Written three times, the manifests drift (one gets a
`display` fix, the other two keep the bug), which is exactly the failure mode
`static/theme.css` was created to end for the chrome.

Everything here is **relative on purpose**. Each surface is reached on its own
port at the root path:

    http://127.0.0.1:8099/           live
    http://127.0.0.1:8100/           archive
    http://127.0.0.1:8101/           standings
    http://127.0.0.1:8103/           weekend

A manifest with `"start_url": "/"` would still be wrong the day someone serves
these under a path prefix, and it costs nothing to be correct: per the manifest
spec, `start_url` and every icon `src` resolve against the manifest's own URL,
so `"./"` means "the directory this manifest was fetched from". Same for the
service worker: the browser registers it relative to the *page*, and its scope
becomes its own directory. No surface needs to know how it is being
reached.

What is cached, and what deliberately is not:

  * the shell (page HTML, theme.css, shell.js, icons) -- precached, so an
    installed app opens with no network at all;
  * `GET /api/...` -- network first, cache as the fallback. Stale JSON is
    acceptable *because every surface renders the snapshot's own timestamp* and
    `static/shell.js` shows an offline badge; it is a labelled old picture, not
    an invented new one;
  * `POST`/`DELETE` (chat, prefs, refresh) -- never cached, never served from
    cache. A queued write the user cannot see failing is worse than an error;
  * `/api/stream` (SSE) -- bypassed entirely. A cached event stream is not a
    thing, and Cache API writes on a response that never ends would hang.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, Response

HERE = Path(__file__).resolve().parent
STATIC = HERE / "static"

#: Cache API name. The activate handler deletes every cache whose name is not
#: this one, so the name is what performs the eviction.
#:
#: It used to be the constant "f1-shell-v1", and that was a trap: the name has to
#: be bumped by hand every time a precached asset changes, and a human will forget
#: -- as happened here. The shell HTML, theme.css and shell.js were all edited,
#: the worker's own bytes never changed, so the browser never reinstalled it and
#: kept serving the old page and old JS. The user's report was exactly that:
#: "değişiklik yok gibi".
#:
#: So the name is now DERIVED from the contents of the precached files. Touching
#: any of them changes the cache name, which changes the worker's bytes, which
#: makes the browser install the new worker and evict the old cache. No step to
#: remember.
#: Surface -> the page file that surface serves at "/". Mixed into the cache
#: version so editing a page changes the worker's bytes and the browser evicts.
SURFACE_PAGES = {
    "live": "index.html",
    "archive": "archive.html",
    "standings": "standings.html",
    "welcome": "welcome.html",
}


def _content_version(surface: str | None = None) -> str:
    """Cache name derived from the bytes that actually get cached.

    A hand-maintained constant failed here exactly once too often, so the name is
    a hash of the precached files plus the surface's own page. Any edit changes
    the name -> changes /sw.js -> the browser installs the new worker -> the
    activate handler deletes the old cache. Nothing to remember at release time.
    """
    h = hashlib.sha256()
    if surface:
        h.update(("surface:" + surface).encode())
    for rel in PRECACHE:
        if rel in _ROUTE_SERVED:
            continue
        try:
            h.update((STATIC.parent / rel).read_bytes())
        except OSError:
            h.update(b"?missing:" + rel.encode())
    page = SURFACE_PAGES.get(surface or "")
    if page:
        try:
            h.update((STATIC.parent / page).read_bytes())
        except OSError:
            h.update(b"?missing:" + page.encode())
    return "f1-shell-" + h.hexdigest()[:12]

#: Precache entries that are served by a ROUTE rather than read from disk
#: (`./` is the surface's page, the manifest is generated). The content hash skips
#: them by path, but the page itself is mixed in below so an HTML edit still
#: changes the cache name.
_ROUTE_SERVED = {"./", "manifest.webmanifest"}

#: Files precached on install, relative to the worker's own directory.
#: `"./"` is the surface's page -- the shell HTML itself.
PRECACHE = (
    "./",
    "manifest.webmanifest",
    "static/theme.css",
    "static/shell.js",
    "static/icons/icon-192.png",
    "static/icons/icon-512.png",
    "static/icons/icon-maskable-512.png",
    "static/icons/apple-touch-icon-180.png",
    "static/icons/favicon-32.png",
)

#: Icon set, shared by all three manifests. `purpose` matters: Android crops a
#: maskable icon to a circle covering the middle 80%, so the maskable file is
#: rendered with the artwork shrunk and the background bleeding to the edges
#: (see tools/make_icons.py). Declaring the normal artwork as maskable would
#: have the gauge's ends eaten on a phone home screen.
ICONS = (
    {"src": "static/icons/icon-192.png", "sizes": "192x192", "type": "image/png"},
    {"src": "static/icons/icon-512.png", "sizes": "512x512", "type": "image/png"},
    {"src": "static/icons/icon-maskable-512.png", "sizes": "512x512",
     "type": "image/png", "purpose": "maskable"},
)

#: Per-surface identity. Each surface installs as its OWN app: they answer
#: different questions and are opened at different times (the live one during a
#: session, the standings one in the off-season), so one icon that always lands
#: on the timing table would be the wrong shortcut twice.
SURFACES: dict[str, dict[str, Any]] = {
    "live": {
        "name": "F1 Analyst — Canlı",
        "short_name": "F1 Canlı",
        "description": "Yarış anında canlı sıralama, farklar, lastik durumu ve AI asistan.",
    },
    "archive": {
        "name": "F1 Analyst — Arşiv",
        "short_name": "F1 Arşiv",
        "description": "Geçmiş yarışlar: strateji şeridi, fark grafiği, tur süresi dağılımı.",
    },
    "standings": {
        "name": "F1 Analyst — Puan Durumu",
        "short_name": "F1 Puan",
        "description": "Sezonun resmî sürücü ve marka sıralaması; ağ olmadan da açılır.",
    },
    # The welcome surface answers a fourth question -- "what is on this weekend,
    # and which session am I in?" -- and it is the one surface worth opening
    # before anything else has happened. Installed as its own app for the same
    # reason the other three are: a shortcut should land on the question it
    # answers, not on a timing table that is empty on a Tuesday.
    "welcome": {
        "name": "F1 Analyst — Hafta Sonu",
        "short_name": "F1 Hafta",
        "description": "Hafta sonunun seansları: antrenman, sıralama, sprint ve yarış kartları.",
    },
}

#: Matches --bg in static/theme.css. The status bar and the splash screen must
#: be the same near-black as the page, or an installed app flashes white.
THEME_COLOR = "#0a0c10"


def manifest(surface: str) -> dict[str, Any]:
    """The manifest body for one surface.

    `start_url` and `scope` are `"./"` / omitted (which defaults to the
    manifest's directory) so the same bytes work on any host or port, and
    would keep working under a path prefix -- see the module docstring.
    """
    meta = SURFACES[surface]
    return {
        "name": meta["name"],
        "short_name": meta["short_name"],
        "description": meta["description"],
        "lang": "tr",
        "dir": "ltr",
        "start_url": "./",
        "display": "standalone",
        "theme_color": THEME_COLOR,
        "background_color": THEME_COLOR,
        "icons": list(ICONS),
        "categories": ["sports", "news"],
    }


def service_worker(surface: str | None = None) -> str:
    """static/sw.js with the precache list and cache name substituted in.

    Substituted rather than duplicated: the list has to match the files that
    actually exist, and a second hand-written copy inside the JS is a second
    place to forget. `%...%` placeholders are used instead of `.format()` so
    the JS can keep its own braces.
    """
    src = (STATIC / "sw.js").read_text(encoding="utf-8")
    return (src
            .replace("%CACHE_VERSION%", _content_version(surface))
            .replace("%PRECACHE%", json.dumps(list(PRECACHE))))


def install(app: FastAPI, surface: str) -> None:
    """Adds the manifest, worker and icon routes to one surface's app.

    Route order note: `/manifest.webmanifest` and `/sw.js` must be registered
    at the *root*, not under /static, because a service worker's scope is
    limited to its own directory -- served from `/static/sw.js` it could only
    control `/static/`, and would never see a page navigation.
    """
    if surface not in SURFACES:
        raise ValueError(f"bilinmeyen yüzey: {surface}")

    @app.get("/manifest.webmanifest")
    async def _manifest():  # noqa: ANN202
        return JSONResponse(
            manifest(surface),
            media_type="application/manifest+json",
            headers={"Cache-Control": "no-cache"},
        )

    @app.get("/sw.js")
    async def _sw():  # noqa: ANN202
        # no-cache, not no-store: the browser must revalidate the worker on
        # every update check, and a stale worker is a stale app.
        return Response(
            service_worker(surface),
            media_type="application/javascript; charset=utf-8",
            headers={"Cache-Control": "no-cache"},
        )

    @app.get("/favicon.ico")
    async def _favicon():  # noqa: ANN202
        return FileResponse(STATIC / "icons" / "favicon-32.png",
                            media_type="image/png")

    # The icons themselves are NOT re-routed: /static/icons/... is already
    # served by the /static mount every surface has, and one URL per file is
    # one thing to get wrong. Only the two scope-sensitive files above needed
    # root routes.
