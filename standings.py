#!/usr/bin/env python3
"""Championship standings: fetch once, cache in SQLite, serve offline.

The standings page has to work with the network down -- that is the point of it
being a separate surface. So the read path and the write path are separate:

  read   get_standings_view(season, kind)   -> newest cached rows + their age
  write  refresh(season)                    -> fetch from the public API, cache

Reads never touch the network. A page load with no network therefore still
renders, and says exactly how old the picture is (fetched_at, plus the last
failed attempt if there was one). Nothing is interpolated to fill a gap: a
season with no cache renders "veri yok", not zeros.

Source: Jolpica, an Ergast-compatible mirror of the F1 results API. Response
shape is Ergast's (MRData.StandingsTable.StandingsLists[0]) -- if that ever
changes, the parser below fails loudly and the previous snapshot keeps serving
instead of the page quietly going blank.

No new dependency: urllib from the stdlib, as the project already does for the
timing feed.
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

import store

# Data source: Jolpica F1, the maintained successor to the Ergast API, licensed
# under Apache-2.0 (https://github.com/jolpica/jolpica-f1). That licence covers
# the API software; it is not a licence to the underlying Formula 1 data, which
# remains subject to F1's own terms. See README, "Lisans, kaynaklar ve yasal notlar".
# Overridable so an operator can point at a mirror (Jolpica has moved host
# before) and so a test can run with no network at all.
API_BASE = (os.environ.get("F1_STANDINGS_API") or "https://api.jolpi.ca/ergast/f1").rstrip("/")
USER_AGENT = "f1-agentic-analyst/1.0 (local dashboard; +standings cache)"
SOURCE_NAME = "Jolpica"
SOURCE_NOTE = "Jolpica (Ergast uyumlu)"

# How old a snapshot may be before the page calls it stale. The table itself
# only moves on race weekends, so a day-old copy is normal, not suspicious --
# what must never happen is claiming freshness we cannot support.
FRESH_FOR = timedelta(hours=6)
# A failed fetch is not retried on every single page load: with the network
# down, each attempt costs a DNS timeout and the page would crawl.
RETRY_AFTER = timedelta(minutes=2)

_ENDPOINT = {
    store.STANDING_KIND_DRIVER: "driverStandings.json",
    store.STANDING_KIND_CONSTRUCTOR: "constructorStandings.json",
    store.STANDING_KIND_SCHEDULE: ".json",
}

# One refresh at a time per process: two tabs opening together must not produce
# two concurrent fetches (and two writers) for the same snapshot.
_refresh_lock = threading.Lock()
_last_try: dict[tuple[int, str], float] = {}


class FetchError(RuntimeError):
    """Network or payload problem; the caller falls back to the cache."""


def url_for(season: int, kind: str) -> str:
    if kind == store.STANDING_KIND_SCHEDULE:
        return f"{API_BASE}/{int(season)}.json?limit=100"
    return f"{API_BASE}/{int(season)}/{_ENDPOINT[kind]}?limit=100"


def season_now(now: datetime | None = None) -> int:
    """The season a calendar date most likely belongs to.

    Seasons start in March, so January and February still belong to the previous
    one. Used only as a default when the caller names no season and nothing is
    cached yet; an explicit season always wins.
    """
    d = now or datetime.now(timezone.utc)
    return d.year - 1 if d.month < 3 else d.year


# ---------- parsing ----------
def _num(v, default=None):
    """'365' -> 365, '12.5' -> 12.5, '0' -> 0, ''/None -> default."""
    if v is None or v == "":
        return default
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return int(f) if f.is_integer() else f


def parse_payload(payload: dict, kind: str) -> tuple[int | None, list[dict]]:
    """Ergast-shaped JSON -> (round, normalised rows).

    Raises FetchError on anything unexpected: a parser that guesses at a changed
    shape is how invented standings happen.
    """
    if not isinstance(payload, dict):
        raise FetchError("beklenmeyen yanıt: JSON nesnesi değil")
    mrdata = payload.get("MRData")
    if not isinstance(mrdata, dict):
        raise FetchError("beklenmeyen yanıt: MRData yok")
    table = mrdata.get("StandingsTable")
    if not isinstance(table, dict):
        raise FetchError("beklenmeyen yanıt: StandingsTable yok")
    lists = table.get("StandingsLists")
    if not isinstance(lists, list):
        raise FetchError("beklenmeyen yanıt: StandingsLists yok")
    if not lists:
        # A season that has not started yet is an honest empty, not an error.
        return None, []
    entry = lists[0]
    if not isinstance(entry, dict):
        raise FetchError("beklenmeyen yanıt: StandingsLists[0] nesne değil")
    round_raw = entry.get("round")
    round_no = _num(round_raw)
    key_name = ("DriverStandings" if kind == store.STANDING_KIND_DRIVER
                else "ConstructorStandings")
    items = entry.get(key_name)
    if not isinstance(items, list):
        raise FetchError(f"beklenmeyen yanıt: {key_name} listesi yok")

    rows: list[dict] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        if kind == store.STANDING_KIND_DRIVER:
            d = it.get("Driver") or {}
            cons = it.get("Constructors") or []
            team = ""
            team_id = ""
            if cons and isinstance(cons[0], dict):
                team = cons[0].get("name") or ""
                team_id = cons[0].get("constructorId") or ""
            code = (d.get("code") or "").upper()
            driver_id = d.get("driverId") or ""
            key = code or driver_id.upper()
            if not key:
                continue
            name = " ".join(x for x in (d.get("givenName"), d.get("familyName")) if x)
            rows.append({
                "key": key,
                "position": _num(it.get("position")),
                "points": _num(it.get("points"), 0),
                "wins": _num(it.get("wins"), 0),
                "code": code or driver_id.upper()[:3],
                "name": name,
                "team": team,
                "team_id": team_id,
                "number": d.get("permanentNumber") or "",
                "nationality": d.get("nationality") or "",
            })
        else:
            c = it.get("Constructor") or {}
            cid = c.get("constructorId") or ""
            if not cid:
                continue
            rows.append({
                "key": cid,
                "position": _num(it.get("position")),
                "points": _num(it.get("points"), 0),
                "wins": _num(it.get("wins"), 0),
                "name": c.get("name") or cid,
                "nationality": c.get("nationality") or "",
            })
    # Position is the API's own order; keep it, and never re-sort by points here
    # (a tie's official order comes from a countback we do not hold).
    return round_no, rows


# ---------- fetch ----------
def fetch(season: int, kind: str, *, timeout: float = 15.0) -> tuple[int | None, list[dict], str]:
    """Network fetch + parse. Raises FetchError; never returns invented rows."""
    url = url_for(season, kind)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                               "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        raise FetchError(f"HTTP {e.code}") from None
    except urllib.error.URLError as e:
        raise FetchError(f"ağ hatası: {e.reason}") from None
    except TimeoutError:
        raise FetchError("zaman aşımı") from None
    except OSError as e:
        raise FetchError(f"ağ hatası: {e}") from None
    try:
        payload = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise FetchError(f"JSON çözülemedi: {type(e).__name__}") from None
    round_no, rows = parse_payload(payload, kind)
    return round_no, rows, url


def parse_schedule(payload: dict) -> list[dict]:
    """Ergast season JSON -> [{"round", "name", "date"}], nothing invented."""
    if not isinstance(payload, dict):
        raise FetchError("beklenmeyen yanıt: JSON nesnesi değil")
    races = ((payload.get("MRData") or {}).get("RaceTable") or {}).get("Races")
    if not isinstance(races, list):
        raise FetchError("beklenmeyen yanıt: RaceTable.Races yok")
    out = []
    for r in races:
        if not isinstance(r, dict):
            continue
        rnd = _num(r.get("round"))
        date = (r.get("date") or "").strip()
        if rnd is None or not date:
            continue
        out.append({"round": rnd, "name": (r.get("raceName") or "").strip(),
                    "date": date})
    return out


def fetch_schedule(season: int, *, timeout: float = 15.0) -> tuple[list[dict], str]:
    url = url_for(season, store.STANDING_KIND_SCHEDULE)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                               "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        raise FetchError(f"HTTP {e.code}") from None
    except urllib.error.URLError as e:
        raise FetchError(f"ağ hatası: {e.reason}") from None
    except OSError as e:
        raise FetchError(f"ağ hatası: {e}") from None
    try:
        payload = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise FetchError(f"JSON çözülemedi: {type(e).__name__}") from None
    return parse_schedule(payload), url


def get_schedule(season: int) -> list[dict]:
    snap = store.get_standings(season, store.STANDING_KIND_SCHEDULE)
    return (snap or {}).get("rows") or []


def ensure_schedule(season: int, *, max_age=timedelta(days=7)) -> list[dict]:
    """The cached calendar, fetched when missing or a week old. Never raises."""
    rows = get_schedule(season)
    view = store.get_standings(season, store.STANDING_KIND_SCHEDULE)
    age = _age_seconds((view or {}).get("fetched_at"))
    if rows and age is not None and age <= max_age.total_seconds():
        return rows
    try:
        rows, url = fetch_schedule(season)
        store.put_standings(season, store.STANDING_KIND_SCHEDULE, rows,
                            source=SOURCE_NAME, url=url)
        return rows
    except FetchError:
        # No calendar is not fatal: callers fall back to the date rule, and say so.
        return rows


def round_for(date_iso: str, meeting: str = "", schedule=None) -> int | None:
    """Which round a race is, from the cached calendar.

    Matched on the calendar DATE (a race date is unique within a season). The
    meeting name is only used to double-check, never to guess: if two rounds
    share a date we return None rather than pick one.
    """
    day = (date_iso or "")[:10]
    if not day or not schedule:
        return None
    hits = [r["round"] for r in schedule if (r.get("date") or "")[:10] == day]
    if len(hits) == 1:
        return int(hits[0])
    if len(hits) > 1:
        # Two rounds on one date would be a data problem; narrow by name and
        # give up unless exactly one matches.
        m = (meeting or "").strip().lower()
        named = [r["round"] for r in schedule
                 if (r.get("date") or "")[:10] == day
                 and m and m.split()[0] in (r.get("name") or "").lower()]
        return int(named[0]) if len(named) == 1 else None
    return None


def refresh(season: int, kinds=None, *, timeout: float = 15.0,
            force: bool = False) -> dict:
    """Fetch and cache the standings for `season`. Returns a per-kind report.

    One kind failing does not stop the other: a broken constructor call must not
    cost us a good driver table.
    """
    kinds = list(kinds or store.STANDING_KINDS)
    out = {"season": int(season), "fetched_at": store.now_iso(), "results": {}}
    with _refresh_lock:
        for kind in kinds:
            if kind not in _ENDPOINT:
                out["results"][kind] = {"ok": False, "error": f"bilinmeyen tür: {kind}"}
                continue
            try:
                if kind == store.STANDING_KIND_SCHEDULE:
                    rows, url = fetch_schedule(season, timeout=timeout)
                    round_no = None
                else:
                    round_no, rows, url = fetch(season, kind, timeout=timeout)
            except FetchError as e:
                msg = str(e)
                store.log_standings_attempt(season, kind, False, msg,
                                            url_for(season, kind))
                _last_try[(season, kind)] = time.time()
                out["results"][kind] = {"ok": False, "error": msg}
                continue
            store.put_standings(season, kind, rows, round_no=round_no,
                                source=SOURCE_NAME, url=url)
            store.log_standings_attempt(season, kind, True, "", url)
            _last_try[(season, kind)] = time.time()
            out["results"][kind] = {"ok": True, "rows": len(rows), "round": round_no}
    out["ok"] = all(r.get("ok") for r in out["results"].values())
    return out


# ---------- read path (never touches the network) ----------
def _age_seconds(fetched_at: str | None) -> float | None:
    if not fetched_at:
        return None
    try:
        t = datetime.strptime(fetched_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None
    return max(0.0, (datetime.now(timezone.utc) - t).total_seconds())


def get_standings_view(season: int, kind: str) -> dict:
    """What the page renders for one season+kind, with full provenance.

    Keys:
      season, kind, rows, round, source, url, fetched_at, age_seconds,
      stale (bool), has_data (bool), last_error (str|None, from the attempt log)
    """
    snap = store.get_standings(season, kind)
    attempt = store.last_standings_attempt(season, kind)
    last_error = None
    if attempt and not attempt.get("ok"):
        last_error = attempt.get("err") or "bilinmeyen hata"
    if snap is None:
        return {"season": int(season), "kind": kind, "rows": [], "round": None,
                "source": None, "url": None, "fetched_at": None, "age_seconds": None,
                "stale": True, "has_data": False, "last_error": last_error,
                "source_note": SOURCE_NOTE}
    age = _age_seconds(snap.get("fetched_at"))
    stale = age is None or age > FRESH_FOR.total_seconds()
    return {"season": snap["season"], "kind": kind, "rows": snap.get("rows") or [],
            "round": snap.get("round"), "source": snap.get("source"),
            "url": snap.get("url"), "fetched_at": snap.get("fetched_at"),
            "age_seconds": None if age is None else round(age),
            "stale": stale, "has_data": True, "last_error": last_error,
            "source_note": SOURCE_NOTE}


def should_retry(season: int, kind: str, now: float | None = None) -> bool:
    """False while a recent failure is still warm, so a dead network is cheap."""
    t = _last_try.get((int(season), kind))
    if t is None:
        return True
    return (now or time.time()) - t > RETRY_AFTER.total_seconds()


def ensure_refreshed(season: int, kinds=None, *, max_age=FRESH_FOR) -> dict | None:
    """Refresh only if the cache is older than `max_age` and a retry is due.

    This is what the reader calls before rendering: it makes the page
    self-healing when the network is back, without hammering a dead one.
    """
    kinds = list(kinds or store.STANDING_KINDS)
    due = []
    for kind in kinds:
        view = get_standings_view(season, kind)
        age = view.get("age_seconds")
        if age is None or age > max_age.total_seconds():
            if should_retry(season, kind):
                due.append(kind)
    if not due:
        return None
    return refresh(season, due)


def base_rows_for_projection(season: int, *, race_round: int | None = None,
                            race_date: str | None = None,
                            meeting: str | None = None) -> dict:
    """The table to treat as "before this race" when projecting live points.

    This is the one place where a projection could silently become a lie, so the
    check is conservative. Two ways the base could already include the race on
    screen (which would count its points twice):

      * the cached snapshot says it is from round R >= the round being raced;
      * or, with no calendar, the snapshot was fetched on/after the race date.

    Either way we refuse and say why. The caller then shows no projection rather
    than a table that is quietly wrong by one race.
    """
    view = get_standings_view(season, store.STANDING_KIND_DRIVER)
    out = {"season": int(season), "rows": view.get("rows") or [],
           "fetched_at": view.get("fetched_at"), "round": view.get("round"),
           "race_round": race_round,
           "has_data": bool(view.get("has_data")), "stale": view.get("stale"),
           "last_error": view.get("last_error"), "source": view.get("source"),
           "unsafe": False, "reason": None, "check": None,
           "source_note": SOURCE_NOTE}
    if not out["has_data"]:
        out["reason"] = "sıralama önbelleği boş"
        out["check"] = "no-cache"
        return out

    cached_round = out["round"]
    if race_round is not None and cached_round is not None:
        out["check"] = "round"
        if int(cached_round) >= int(race_round):
            out["unsafe"] = True
            out["reason"] = (f"önbellekteki tablo {cached_round}. tur sonrası, "
                             f"bu yarış {race_round}. tur: puanlar iki kez sayılırdı")
        return out

    # No usable round on one side: fall back to the clock, which is coarser but
    # never optimistic -- a base fetched after the race began may include it.
    day = (race_date or "")[:10]
    fetched = (out["fetched_at"] or "")[:10]
    if day and fetched:
        out["check"] = "date"
        if fetched >= day:
            out["unsafe"] = True
            out["reason"] = (f"önbellekteki tablo {fetched} tarihli; yarış {day}. "
                             f"Bu yarışı zaten içeriyor olabilir")
        return out

    # Nothing to compare: project, but label it unverified.
    out["check"] = "unverified"
    return out
