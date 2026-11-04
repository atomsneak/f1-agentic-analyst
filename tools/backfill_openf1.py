#!/usr/bin/env python3
"""Backfill a finished race from the OpenF1 archive into the shared store.

Why this exists
---------------
F1's own static archive (livetiming.formula1.com/static/...) is behind an auth
wall now: every <topic>.jsonStream returns 401. So a race we did not capture
live is gone as far as the official feed is concerned. OpenF1 keeps an
independent copy, and it is rich enough to rebuild most of a lap frame.

Two phases, deliberately separate:

  --phase laps    only lap_times. Small, certain, immediately fixes MED8/TREND.
  --phase sectors sector splits + mini-sector STATUS codes (never durations).
  --phase speeds  i1/i2/st speeds -- real measurements at sector boundaries.
  --phase frames  rebuild per-lap frames (position, gaps, tyre, pit, sectors,
                  race control, weather). Bigger, and marked as derived.

Provenance
----------
Everything written here is tagged source='openf1' and carries a `missing` list
of fields this archive cannot supply, so the archive UI and the LLM briefing
show "unknown" instead of inventing numbers. By default the script never
overwrites a frame the live capture produced: INSERT OR IGNORE, because live
data is strictly richer (turbo-detailed feed vs reconstructed).

Usage
-----
  tools/backfill_openf1.py --list
  tools/backfill_openf1.py --race-id 1 --phase laps
  tools/backfill_openf1.py --race-id 1 --phase frames --dry-run
  tools/backfill_openf1.py --all --phase both
  tools/backfill_openf1.py --ingest-season 2026 --phase both

The --ingest-season path exists because every other mode needs the race row to
already be in the archive, and a race we never captured live has none. It
discovers the season's Race sessions on OpenF1, skips cancelled and future
ones, creates the rows we are missing (same path shape the live writer uses,
`<yıl>/<tarih>_<Yarış>/<tarih>_Race/`), then runs the same phases on them.

OpenF1 is a free community mirror (not official F1), so requests are paced and
429/Retry-After is respected.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import store  # noqa: E402

# Data source: OpenF1 (https://openf1.org/), licensed CC BY-NC-SA 4.0:
# NON-COMMERCIAL only, share-alike. The MIT licence on this repository covers the
# code, not the data it fetches -- anything you retrieve from here and then
# redistribute must keep that licence and stay non-commercial. Also mind the
# free tier: 3 req/s, 30 req/min (PAUSE below is deliberately slower than that).
# See README, "Lisans, kaynaklar ve yasal notlar".
API = "https://api.openf1.org/v1"
UA = "f1-agentic-analyst/1.0 (personal archive)"
PAUSE = 1.2          # seconds between requests; the mirror is free and shared
FRAME_BATCH = 12     # frames per transaction so live capture is never blocked long

_session_cache: dict[str, list] = {}


def _get(path: str, **params) -> list:
    url = f"{API}/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    if url in _session_cache:
        return _session_cache[url]
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=90) as r:
                data = json.loads(r.read().decode("utf-8", "replace"))
            _session_cache[url] = data
            time.sleep(PAUSE)
            return data
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = float(e.headers.get("Retry-After") or 10)
                print(f"    rate limited, waiting {wait:.0f}s")
                time.sleep(wait)
                continue
            if e.code == 404:
                _session_cache[url] = []
                return []
            if attempt == 3:
                raise
            time.sleep(3 * (attempt + 1))
        except Exception:
            if attempt == 3:
                raise
            time.sleep(3 * (attempt + 1))
    return []


# ---------- matching ----------
def find_session(race: dict) -> dict | None:
    """Locate the OpenF1 session matching an archived row -- ANY session type.

    Primary key is F1's own meeting_key, which both sides share -- no guessing
    from round numbers. Date matching is only a fallback for races archived
    before we started storing the key.

    This used to ask only for `session_name="Race"`, and callers carried a guard
    that skipped practice/qualifying with a message claiming OpenF1 "has no lap
    data" for them. That was false -- the mirror publishes complete practice and
    qualifying laps -- and it meant `--race-id` silently did nothing for exactly
    the sessions that needed repairing most.
    """
    year = race.get("year") or (str(race.get("start_date") or "")[:4])
    name = race.get("session") or ""
    if not year:
        return None
    sessions = _get("sessions", year=int(year))
    if not sessions:
        return None

    wanted = archive_session_name(name) or name
    same_kind = [s for s in sessions
                 if archive_session_name(s.get("session_name")) == wanted]

    mk = race.get("meeting_key")
    if mk:
        hit = [s for s in same_kind if s.get("meeting_key") == mk]
        if hit:
            return hit[0]

    day = str(race.get("start_date") or "")[:10]
    if day:
        hit = [s for s in same_kind if str(s.get("date_start") or "")[:10] == day]
        if hit:
            return hit[0]
        # The archived start_date is the session's own; a one-day slip is
        # possible when the capture began before midnight, so try neighbours.
        hit = [s for s in same_kind
               if abs(_day_delta(str(s.get("date_start") or "")[:10], day)) <= 1]
        if len(hit) == 1:
            return hit[0]
    return None


def _day_delta(a: str, b: str) -> int:
    try:
        da = datetime.strptime(a, "%Y-%m-%d")
        db_ = datetime.strptime(b, "%Y-%m-%d")
        return (da - db_).days
    except (TypeError, ValueError):
        return 99


def _is_race(race: dict) -> bool:
    return "race" in (race.get("session") or "").lower()


#: OpenF1 session_name -> the name F1's own SessionInfo uses, which is what the
#: archive stores and what points.session_type() classifies. The mapping is
#: explicit rather than a substring rule because "Sprint Qualifying" must not
#: become "Sprint" -- that exact confusion produced a wrong answer once.
_OPENF1_SESSION_NAMES = {
    "practice 1": "Practice 1",
    "practice 2": "Practice 2",
    "practice 3": "Practice 3",
    "qualifying": "Qualifying",
    "sprint": "Sprint",
    "sprint qualifying": "Sprint Qualifying",
    "sprint shootout": "Sprint Qualifying",
    "race": "Race",
}


def archive_session_name(openf1_name) -> str | None:
    """OpenF1 session name -> archive session name (None if not a session we hold)."""
    key = str(openf1_name or "").strip().lower()
    return _OPENF1_SESSION_NAMES.get(key)


def sessions_of_meeting(meeting_key: int, year: int | None = None) -> list[dict]:
    """Every OpenF1 session of one meeting, in chronological order.

    Needed because the archive is session-grained: a weekend is up to six rows,
    and a weekend whose practices were never captured live has no rows for them
    at all. Deriving the missing rows from OpenF1 is the only way to answer
    "why is there no practice data for past races" with data instead of an
    apology.
    """
    params = {"meeting_key": int(meeting_key)}
    if year:
        params["year"] = int(year)
    sessions = _get("sessions", **params)
    out = []
    for s in sessions or []:
        name = archive_session_name(s.get("session_name"))
        if not name:
            continue
        out.append({**s, "_archive_name": name})
    out.sort(key=lambda x: str(x.get("date_start") or ""))
    return out


def session_path_for(year: int, day: str, meeting: str, session_name: str) -> str:
    """Same shape SessionInfo.Path produces, so links and grouping look identical."""
    slug = session_name.replace(" ", "_")
    return f"{year}/{day}_{meeting.replace(' ', '_')}/{day}_{slug}/"


# ---------- formatting helpers ----------
def _fmt_lap(sec: float | None) -> str:
    if not sec or sec <= 0:
        return ""
    m, s = divmod(sec, 60)
    return f"{int(m)}:{s:06.3f}"


def _fmt_gap(diff: float, lap_ref: float) -> str:
    if diff <= 0:
        return ""
    if lap_ref and diff >= lap_ref:
        n = int(diff // lap_ref)
        return f"+{n} LAP" if n == 1 else f"+{n} LAPS"
    return f"+{diff:.3f}"


# ---------- phase A: lap times ----------
def backfill_laps(rid: int, session_key: int, dry_run: bool = False) -> dict:
    laps = _get("laps", session_key=session_key)
    items = []
    for l in laps:
        if l.get("lap_duration") is None:
            continue
        items.append((str(l["driver_number"]), int(l["lap_number"]), float(l["lap_duration"])))
    if dry_run:
        return {"lap_time_rows": len(items)}
    inserted = store.put_lap_times(rid, items)
    return {"lap_time_rows": len(items), "lap_times_inserted": inserted}


# ---------- phase A2: sector splits ----------
def backfill_sectors(rid: int, session_key: int, dry_run: bool = False) -> dict:
    """Sector times + mini-sector status codes.

    OpenF1 publishes real sector durations (duration_sector_1..3) for ~99% of
    laps. The segments_sector_* arrays are status codes, not durations -- the
    same codes the live feed sends -- so they ride along and stay honest about
    what they are.
    """
    laps = _get("laps", session_key=session_key)
    items = []
    for l in laps:
        items.append((
            str(l["driver_number"]), int(l["lap_number"]),
            l.get("duration_sector_1"), l.get("duration_sector_2"), l.get("duration_sector_3"),
            l.get("segments_sector_1"), l.get("segments_sector_2"), l.get("segments_sector_3"),
        ))
    full = sum(1 for it in items if it[2] and it[3] and it[4])
    if dry_run:
        return {"sector_rows": len(items), "all_three_sectors": full}
    written = store.put_sectors(rid, items, source=store.SOURCE_OPENF1)
    return {"sector_rows": len(items), "all_three_sectors": full, "written": written}


# ---------- phase A3: speeds at sector boundaries ----------
def backfill_speeds(rid: int, session_key: int, dry_run: bool = False) -> dict:
    """i1/i2/st speeds: the real comparison material at sector boundaries.

    OpenF1 publishes three speeds per lap -- i1 at the end of sector 1, i2 at the
    end of sector 2, st at the speed trap. They were in every /laps response and
    never stored, so "how did LEC's best lap differ from HAM's" could only be
    answered in seconds, never in speed. Unlike the mini-sector arrays these are
    genuine measurements, not status codes.
    """
    laps = _get("laps", session_key=session_key)
    items = []
    for l in laps:
        i1, i2, st = l.get("i1_speed"), l.get("i2_speed"), l.get("st_speed")
        if i1 is None and i2 is None and st is None:
            continue
        items.append((str(l["driver_number"]), int(l["lap_number"]), i1, i2, st))
    full = sum(1 for it in items if it[2] is not None and it[3] is not None and it[4] is not None)
    if dry_run:
        return {"speed_rows": len(items), "all_three_speeds": full}
    written = store.put_speeds(rid, items, source=store.SOURCE_OPENF1)
    return {"speed_rows": len(items), "all_three_speeds": full, "written": written}


# ---------- phase B: rebuild frames ----------
def backfill_frames(rid: int, session_key: int, total: int, *, dry_run=False,
                    overwrite=False, session_ctx: dict | None = None) -> dict:
    """Rebuild per-lap frames from the mirror.

    `session_ctx` carries the session's identity (meeting, official, session
    name) so a practice frame says "Practice 1", not "Race". Defaults to the
    archived race row, which is the authoritative source.
    """
    if session_ctx is None:
        row = store.get_race(rid) or {}
        session_ctx = {"meeting": row.get("meeting"), "official": row.get("official"),
                       "session": row.get("session")}
    laps = _get("laps", session_key=session_key)
    drivers = _get("drivers", session_key=session_key)
    stints = _get("stints", session_key=session_key)
    pits = _get("pit", session_key=session_key)
    rc = _get("race_control", session_key=session_key)
    weather = _get("weather", session_key=session_key)

    drv = {d["driver_number"]: d for d in drivers}
    comp_by_drv: dict[int, list[dict]] = {}
    for s in stints:
        comp_by_drv.setdefault(s["driver_number"], []).append(s)
    for v in comp_by_drv.values():
        v.sort(key=lambda s: s.get("lap_start") or 0)

    pits_by_drv: dict[int, list[int]] = {}
    for p in pits:
        lapno = p.get("lap_number")
        if lapno:
            pits_by_drv.setdefault(p["driver_number"], []).append(int(lapno))
    for v in pits_by_drv.values():
        v.sort()

    rc_by_lap: dict[int, list[dict]] = {}
    for m in rc:
        rc_by_lap.setdefault(int(m.get("lap_number") or 0), []).append(m)

    # lap -> driver -> lap record
    by_lap: dict[int, dict[int, dict]] = {}
    for l in laps:
        by_lap.setdefault(int(l["lap_number"]), {})[int(l["driver_number"])] = l

    # cumulative race time per driver (sum of own lap durations up to N)
    cum: dict[int, dict[int, float]] = {}
    for num in drv:
        run, acc = 0.0, {}
        for lapno in range(1, total + 1):
            rec = by_lap.get(lapno, {}).get(num)
            if rec and rec.get("lap_duration"):
                run += float(rec["lap_duration"])
                acc[lapno] = run
        cum[num] = acc

    # Grid is a static property of the race and OpenF1 has no endpoint for it,
    # but a live-captured frame of this same race may already carry it (and the
    # live frame is the authority). Only if nothing anywhere has it do we mark
    # the field missing, so the UI shows "-" instead of a guess.
    grid_by_num: dict[str, int] = {}
    for num, g in (store.all_row_grids(rid) or {}).items():
        grid_by_num[str(num)] = int(g)
    missing = [] if grid_by_num else ["grid"]
    frames_written = 0
    written_laps = []

    for lapno in range(1, total + 1):
        entries = by_lap.get(lapno) or {}
        if not entries:
            continue
        # order by cumulative race time at the end of this lap
        order = [(num, cum[num][lapno]) for num in entries if lapno in cum.get(num, {})]
        if not order:
            continue
        order.sort(key=lambda x: x[1])
        leader_cum = order[0][1]
        leader_lap = float(entries[order[0][0]].get("lap_duration") or 0) or None

        # track status: latest flag-ish race control message at or before this lap
        track = None
        for back in range(lapno, 0, -1):
            for m in rc_by_lap.get(back, []):
                if m.get("category") in ("Flag", "SafetyCar") and m.get("message"):
                    track = m["message"]
                    break
            if track:
                break

        w = _nearest_weather(weather, entries[order[0][0]].get("date_start"))

        rows = []
        for idx, (num, c) in enumerate(order, start=1):
            rec = entries[num]
            info = drv.get(num) or {}
            own = cum[num]
            diff = c - leader_cum
            ahead = order[idx - 2][1] if idx > 1 else None

            # tyre: stint covering this lap
            tyre, age, stint_list = None, None, []
            for s in comp_by_drv.get(num, []):
                lst, lend = s.get("lap_start") or 0, s.get("lap_end") or 0
                stint_list.append({"compound": (s.get("compound") or "UNKNOWN").upper(),
                                   "laps": (lend - lst + 1) if lend >= lst else None,
                                   "age_base": s.get("tyre_age_at_start") or 0,
                                   "new": (s.get("tyre_age_at_start") or 0) == 0})
                if lst <= lapno <= lend:
                    tyre = (s.get("compound") or "UNKNOWN").upper()
                    age = (s.get("tyre_age_at_start") or 0) + (lapno - lst)

            # best lap so far = min of this driver's own lap durations up to here
            best = None
            for k in range(1, lapno + 1):
                r2 = by_lap.get(k, {}).get(num)
                if r2 and r2.get("lap_duration"):
                    v = float(r2["lap_duration"])
                    best = v if best is None else min(best, v)

            rows.append({
                "pos": idx, "num": str(num),
                "code": info.get("name_acronym") or str(num),
                "grid": grid_by_num.get(str(num)),
                "name": info.get("full_name") or "",
                "team": info.get("team_name") or "",
                "gap": _fmt_gap(diff, leader_lap) if idx > 1 else "",
                "int": _fmt_gap(c - ahead, leader_lap) if ahead is not None else "",
                "laps": lapno,
                "last": _fmt_lap(rec.get("lap_duration")),
                "best": _fmt_lap(best),
                "pit": len([p for p in pits_by_drv.get(num, []) if p <= lapno]),
                "inpit": bool(rec.get("is_pit_out_lap")),
                "retired": False,
                "tyre": tyre, "tyre_age": age,
                "stints": stint_list,
                "med8": None, "trend": None,      # filled by _decorate from lap_times
                "color": info.get("team_colour") or "",
                # No "sectors": _compact() below drops them exactly like the live
                # writer does, so both sources store an identical row shape.
            })

        msgs = []
        for back in range(1, lapno + 1):
            for m in rc_by_lap.get(back, []):
                msgs.append({"utc": m.get("date"), "lap": m.get("lap_number"),
                             "cat": m.get("category"), "msg": m.get("message")})

        snap = {
            "ok": True, "err": None, "archived": True,
            "updated": entries[order[0][0]].get("date_start"),
            "server_time": None,
            # Not hard-coded "Race": this builder is now used for practice and
            # qualifying as well. Writing "Race" into a practice frame is exactly
            # the identity confusion the whole briefing-rules design exists to
            # prevent -- measured: a backfilled FP1 frame told the model
            # "seans_tipi=Yarış (race) | tur=26/26", i.e. a 26-lap grand prix.
            "meeting": session_ctx.get("meeting"),
            "official": session_ctx.get("official"),
            "session": session_ctx.get("session") or "Race",
            "status": "Finished" if lapno >= total else "Started",
            "lap": lapno, "total": total,
            "track": track,
            "air": w.get("air_temperature"), "track_temp": w.get("track_temperature"),
            "humidity": w.get("humidity"), "rain": w.get("rainfall"),
            "wind": w.get("wind_speed"),
            "rows": rows, "rc": msgs[-25:],
        }
        if not dry_run:
            store.put_frame(rid, lapno, _compact(snap), source=store.SOURCE_OPENF1,
                            ts=snap["updated"] or store.now_iso(), missing=missing,
                            overwrite=overwrite)
            frames_written += 1
            if frames_written % FRAME_BATCH == 0:
                time.sleep(0.05)
        written_laps.append(lapno)

    return {"frames": len(written_laps), "frames_written": frames_written,
            "lap_range": [min(written_laps), max(written_laps)] if written_laps else None,
            "missing": missing}


ARCHIVE_ROW_FIELDS = ("pos", "num", "code", "grid", "name", "team", "gap", "int", "laps",
                      "last", "best", "pit", "inpit", "retired", "tyre", "tyre_age",
                      "stints", "color")


def _compact(s: dict) -> dict:
    """Same slimming server.compact() applies, so both writers store identical shapes."""
    return {
        "ok": True, "err": None,
        "updated": s.get("updated"), "server_time": s.get("server_time"),
        "meeting": s.get("meeting"), "official": s.get("official"), "session": s.get("session"),
        "status": s.get("status"), "lap": s.get("lap"), "total": s.get("total"),
        "track": s.get("track"), "air": s.get("air"), "track_temp": s.get("track_temp"),
        "humidity": s.get("humidity"), "rain": s.get("rain"), "wind": s.get("wind"),
        "rows": [{k: r.get(k) for k in ARCHIVE_ROW_FIELDS} for r in (s.get("rows") or [])],
        "rc": s.get("rc") or [],
    }


def _nearest_weather(weather: list, when: str | None) -> dict:
    if not weather:
        return {}
    if not when:
        return weather[-1]
    best, best_d = weather[0], None
    try:
        t = datetime.fromisoformat(when.replace("Z", "+00:00"))
    except Exception:
        return weather[-1]
    for w in weather:
        try:
            d = abs((datetime.fromisoformat(str(w.get("date")).replace("Z", "+00:00")) - t).total_seconds())
        except Exception:
            continue
        if best_d is None or d < best_d:
            best, best_d = w, d
    return best


# ---------- season discovery: create the rows a live capture never made ----------
def _season_names(year: int) -> dict[str, str]:
    """date -> Jolpica raceName, so a created row carries the championship name.

    OpenF1 knows the place ("Austin", country "United States"), not the race name
    ("United States Grand Prix"). Jolpica's calendar is the naming authority here.
    If it is unreachable we fall back to "<ülke> Grand Prix" and print that we did,
    rather than inventing an "official" name.
    """
    try:
        import standings  # parent dir is already on sys.path
        rows, _url = standings.fetch_schedule(int(year))
    except Exception as e:
        print(f"  takvim alınamadı ({type(e).__name__}); adlar ülke adından türetilecek")
        return {}
    out: dict[str, str] = {}
    for r in rows:
        day, name = str(r.get("date") or "")[:10], (r.get("name") or "").strip()
        if day and name:
            out[day] = name
    return out


def _default_meeting_name(sess: dict) -> str:
    country = (sess.get("country_name") or sess.get("location") or "").strip()
    return f"{country} Grand Prix" if country else f"Round {sess.get('meeting_key')}"


def race_path_for(year: int, day: str, meeting: str) -> str:
    """Same shape F1's SessionInfo.Path produces, so the UI links look identical."""
    return f"{year}/{day}_{meeting.replace(' ', '_')}/{day}_Race/"


def plan_season(year: int) -> list[dict]:
    """Race sessions of `year` that passed and are not in the archive yet."""
    sessions = _get("sessions", year=int(year), session_name="Race")
    if not sessions:
        print(f"OpenF1'de {year} için Race seansı yok")
        return []
    races = store.list_races()
    by_key = {r["meeting_key"]: r for r in races if r.get("meeting_key")}
    by_day = {str(r.get("start_date") or "")[:10]: r for r in races if r.get("start_date")}
    names = _season_names(year)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

    todo, skipped = [], []
    for s in sorted(sessions, key=lambda x: str(x.get("date_start") or "")):
        day = str(s.get("date_start") or "")[:10]
        start = str(s.get("date_start") or "")[:19]
        label = f"{(names.get(day) or _default_meeting_name(s))} ({day})"
        if s.get("is_cancelled"):
            skipped.append((label, "iptal edilmiş (is_cancelled)"))
            continue
        if not day or start >= now:
            skipped.append((label, "henüz koşulmadı"))
            continue
        hit = by_key.get(s.get("meeting_key")) or by_day.get(day)
        if hit:
            skipped.append((label, f"zaten arşivde (id={hit['id']})"))
            continue
        todo.append({**s, "_day": day, "_meeting": names.get(day) or _default_meeting_name(s),
                     "_label": label})

    for label, why in skipped:
        print(f"  atlandı: {label}: {why}")
    return todo


def ingest_race(sess: dict, *, phase: str, dry_run: bool, overwrite: bool) -> None:
    """Create the missing race row, then run the normal phases on it."""
    year = int(sess.get("year") or sess["_day"][:4])
    day, meeting = sess["_day"], sess["_meeting"]
    path = race_path_for(year, day, meeting)
    print(f"  {sess['_label']} -> yeni kayıt: {path}")

    if dry_run:
        # Count what the mirror would give us without writing anything.
        sk = sess["session_key"]
        print(f"    (dry-run) laps: {backfill_laps(-1, sk, True)}")
        print(f"    (dry-run) sectors: {backfill_sectors(-1, sk, True)}")
        print(f"    (dry-run) speeds: {backfill_speeds(-1, sk, True)}")
        return

    rid = store.upsert_race(
        path, meeting=meeting, official=None, session="Race", year=year,
        start_date=str(sess.get("date_start") or "")[:19],
        end_date=str(sess.get("date_end") or "")[:19],
        meeting_key=sess.get("meeting_key"),
        source=store.SOURCE_OPENF1,
    )
    race = store.get_race(rid)
    laps = _get("laps", session_key=sess["session_key"])
    # Race distance = last lap somebody actually completed (has a duration), not
    # the highest lap_number seen. Spa 2026 has a stray lap 45 record with a null
    # duration; counting it claimed a 45-lap race while only 44 frames exist.
    timed = [int(l["lap_number"]) for l in laps if l.get("lap_number") and l.get("lap_duration")]
    total = max(timed or [int(l["lap_number"]) for l in laps if l.get("lap_number")] or [0])
    if total:
        store.update_race_state(rid, total_laps=total)
    run_race(race, phase=phase, dry_run=False, overwrite=overwrite)


def ingest_season(year: int, *, phase: str, dry_run: bool, overwrite: bool) -> int:
    todo = plan_season(year)
    if not todo:
        print(f"{year}: eklenecek yarış yok")
        return 0
    print(f"\n{len(todo)} yarış eklenecek, faz={phase}"
          f"{' (dry-run)' if dry_run else ''}")
    ok = 0
    for sess in todo:
        try:
            ingest_race(sess, phase=phase, dry_run=dry_run, overwrite=overwrite)
            ok += 1
        except Exception as e:
            print(f"  {sess['_label']}: HATA {type(e).__name__}: {e}")
    print(f"\n{year}: {ok}/{len(todo)} yarış işlendi")
    return 0 if ok == len(todo) else 1


# ---------- CLI ----------
def _print_races() -> None:
    races = store.list_races()
    if not races:
        print("arşivde yarış yok")
        return
    print(f"{'id':>3}  {'tarih':<11} {'session':<8} {'frames':>6} {'laps':>5} {'chat':>4}  meeting")
    for r in races:
        print(f"{r['id']:>3}  {str(r.get('start_date') or r.get('first_seen') or '')[:10]:<11} "
              f"{str(r.get('session') or '')[:8]:<8} {r.get('n_frames') or 0:>6} "
              f"{r.get('n_lap_times') or 0:>5} {r.get('n_chat') or 0:>4}  "
              f"{r.get('meeting') or r.get('path')}  [meeting_key={r.get('meeting_key')}]")


def run_race(race: dict, *, phase: str, dry_run: bool, overwrite: bool) -> None:
    rid = race["id"]
    label = f"#{rid} {race.get('meeting')} / {race.get('session')}"
    sess = find_session(race)
    if not sess:
        print(f"  {label}: OpenF1'de eşleşen seans bulunamadı "
              f"(meeting_key={race.get('meeting_key')}, yıl={race.get('year')})")
        return
    sk = sess["session_key"]
    print(f"  {label} -> OpenF1 session_key={sk} meeting_key={sess.get('meeting_key')} "
          f"({sess.get('date_start')})")

    total = int(race.get("total_laps") or 0)
    if not total:
        laps_seen = store.race_lap_numbers(rid)
        total = max(laps_seen) if laps_seen else 0
    if not total:
        # ask OpenF1 for it
        total = _session_total(sk, name)
    # `total` is only needed to build frames. Lap times, sectors and speeds do
    # not need it, and a practice session legitimately has no race distance --
    # refusing to run at all here is what made "--race-id" a no-op for exactly
    # the sessions whose sector data was corrupt.
    if not total and phase in ("frames", "both", "all"):
        print("    toplam tur bilinmiyor, frames atlandı (laps/sectors sürebilir)")
        phase = "both" if phase == "all" else phase
        if phase in ("frames",):
            return

    if phase in ("laps", "both"):
        r = backfill_laps(rid, sk, dry_run)
        print(f"    laps: {r}")
    if phase in ("sectors", "both"):
        r = backfill_sectors(rid, sk, dry_run)
        print(f"    sectors: {r}")
    if phase in ("speeds", "both"):
        r = backfill_speeds(rid, sk, dry_run)
        print(f"    speeds: {r}")
    if phase in ("frames", "both") and total:
        r = backfill_frames(rid, sk, total, dry_run=dry_run, overwrite=overwrite)
        print(f"    frames: {r}")


# ---------- whole weekend: fill the sessions the live capture missed ----------
def ingest_weekend(meeting_key: int, *, year: int | None = None, phase: str = "both",
                   dry_run: bool = False, overwrite: bool = False,
                   only_missing: bool = True) -> dict:
    """Create archive rows for a meeting's sessions that have none, then fill them.

    Why this exists: the live writer only creates a row for the session that is
    running while it is running. Any practice or qualifying session captured
    while the writer was down (or before it was installed) is simply absent, so
    the weekend grid showed "kayıt yok" for sessions that OpenF1 has complete
    data for. This is the repair path, and it writes ONLY rows that do not
    exist -- an existing row (live capture, richer) is never overwritten unless
    `overwrite` is explicit.

    Returns a per-session report so a caller can print what actually happened
    instead of claiming success.
    """
    sessions = sessions_of_meeting(meeting_key, year)
    if not sessions:
        return {"meeting_key": meeting_key, "sessions": [], "note": "OpenF1'de seans yok"}

    existing = {}
    for r in store.list_races():
        if r.get("meeting_key") == int(meeting_key):
            existing[str(r.get("session") or "").strip().lower()] = r

    report = []
    for sess in sessions:
        name = sess["_archive_name"]
        key = name.strip().lower()
        row = existing.get(key)
        rec = {"session": name, "session_key": sess.get("session_key"),
               "action": "", "rid": None, "result": {}}
        if row and not overwrite:
            rec["action"] = "zaten var"
            rec["rid"] = row["id"]
            report.append(rec)
            continue

        day = str(sess.get("date_start") or "")[:10]
        meeting = ""
        if row:
            meeting = row.get("meeting") or ""
        if not meeting:
            # Same meeting label the archive already uses for this weekend's
            # other sessions; falling back to OpenF1's own label only when the
            # meeting has no rows at all.
            others = [r for r in existing.values() if r.get("meeting")]
            meeting = (others[0]["meeting"] if others
                       else _default_meeting_name(sess))
        year_i = int(sess.get("year") or (day[:4] if day[:4].isdigit() else 0) or 0)
        path = session_path_for(year_i, day, meeting, name)

        if dry_run:
            rec["action"] = "eklenecek (dry-run)"
            if sess.get("session_key"):
                rec["result"] = {
                    "lap_time_rows": backfill_laps(-1, sess["session_key"], True),
                    "sectors": backfill_sectors(-1, sess["session_key"], True),
                    "speeds": backfill_speeds(-1, sess["session_key"], True),
                }
            report.append(rec)
            continue

        rid = store.upsert_race(
            path, meeting=meeting, official=sess.get("circuit_short_name") and None,
            session=name, year=year_i,
            start_date=str(sess.get("date_start") or "")[:19],
            end_date=str(sess.get("date_end") or "")[:19],
            meeting_key=sess.get("meeting_key"),
            # Provenance must not claim "live": nothing was captured live here.
            source=store.SOURCE_OPENF1,
        )
        rec["rid"] = rid
        rec["action"] = "eklendi" if not row else "güncellendi"
        existing[key] = store.get_race(rid) or {"id": rid, "meeting": meeting}

        sk = sess.get("session_key")
        if not sk:
            rec["result"] = {"error": "session_key yok"}
            report.append(rec)
            continue
        total = _session_total(sk, name)
        if total:
            # Practice/qualifying have no race distance; storing one would make
            # the briefing claim a race that does not exist.
            import points
            if points.session_type(name)["has_race_distance"]:
                store.update_race_state(rid, total_laps=total)
        try:
            rec["result"] = run_session_fill(rid, sk, total, phase=phase,
                                             overwrite=overwrite)
        except Exception as e:
            rec["result"] = {"error": f"{type(e).__name__}: {e}"[:200]}
        report.append(rec)
    return {"meeting_key": meeting_key, "sessions": report}


def _session_total(session_key: int, name: str) -> int:
    """Last timed lap of a session; 0 when OpenF1 has no laps yet."""
    laps = _get("laps", session_key=session_key)
    timed = [int(l["lap_number"]) for l in laps
             if l.get("lap_number") and l.get("lap_duration")]
    if timed:
        return max(timed)
    return max([int(l["lap_number"]) for l in laps if l.get("lap_number")] or [0])


def run_session_fill(rid: int, session_key: int, total: int, *, phase: str,
                     overwrite: bool = False) -> dict:
    """Run the requested phases for one session. Returns what was written."""
    out = {}
    if phase in ("laps", "both", "all"):
        out["laps"] = backfill_laps(rid, session_key)
    if phase in ("sectors", "both", "all"):
        out["sectors"] = backfill_sectors(rid, session_key)
    if phase in ("speeds", "both", "all"):
        out["speeds"] = backfill_speeds(rid, session_key)
    if phase in ("frames", "both", "all") and total:
        out["frames"] = backfill_frames(rid, session_key, total, overwrite=overwrite)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true", help="arşivdeki yarışları göster")
    ap.add_argument("--race-id", type=int, action="append", help="hedef yarış id (tekrarlanabilir)")
    ap.add_argument("--all", action="store_true", help="arşivdeki tüm yarışlar")
    ap.add_argument("--ingest-season", type=int, metavar="YIL",
                    help="o sezonun koşulmuş yarışlarını OpenF1'den bul, eksik olanları "
                         "arşive ekle (kayıt + fazlar); iptal ve gelecek yarışlar atlanır")
    ap.add_argument("--weekend", type=int, action="append", metavar="MEETING_KEY",
                    help="bu hafta sonunun EKSİK seanslarını (antrenman/sıralama/sprint) "
                         "OpenF1'den ekle ve doldur; var olan satırlara dokunmaz")
    ap.add_argument("--weekends-season", type=int, metavar="YIL",
                    help="arşivdeki tüm hafta sonları için --weekend'i uygula")
    ap.add_argument("--phase",
                    choices=("laps", "sectors", "speeds", "frames", "both"), default="laps",
                    help="laps = sadece tur süreleri (varsayılan, düşük risk); "
                         "sectors = sektör süreleri + mini sektör kodları; "
                         "frames = tur görüntülerini yeniden kur; both = üçü")
    ap.add_argument("--season", type=int, help="sadece bu yılın yarışları")
    ap.add_argument("--dry-run", action="store_true", help="hiçbir şey yazma")
    ap.add_argument("--overwrite", action="store_true",
                    help="canlı kaydın üzerine yaz (ÖNERİLMEZ: canlı veri daha zengin)")
    args = ap.parse_args()

    store.init_db()

    if args.list:
        _print_races()
        return 0

    if args.weekend or args.weekends_season:
        keys = list(args.weekend or [])
        if args.weekends_season:
            seen = []
            for r in store.list_races():
                mk = r.get("meeting_key")
                if mk and str(r.get("year") or "")[:4] == str(args.weekends_season) \
                        and mk not in seen:
                    seen.append(mk)
            keys.extend(seen)
        if not keys:
            print("hafta sonu anahtarı yok")
            return 1
        rc = 0
        for mk in keys:
            rep = ingest_weekend(mk, phase=args.phase, dry_run=args.dry_run,
                                 overwrite=args.overwrite,
                                 year=args.weekends_season)
            print(f"\nmeeting_key={mk}")
            for rec in rep.get("sessions") or []:
                res = rec.get("result") or {}
                bits = ", ".join(f"{k}={v}" for k, v in res.items()
                                 if isinstance(v, (int, str)))
                print(f"  {rec['session']:<18} {rec['action']:<18} "
                      f"rid={rec.get('rid')} {bits}")
            if rep.get("note"):
                print("  " + rep["note"])
        if not args.dry_run:
            print("\nsonuç:")
            _print_races()
        return rc

    if args.ingest_season:
        rc = ingest_season(args.ingest_season, phase=args.phase,
                           dry_run=args.dry_run, overwrite=args.overwrite)
        if not args.dry_run:
            print("\nsonuç:")
            _print_races()
        return rc

    races = store.list_races()
    if args.race_id:
        want = set(args.race_id)
        races = [r for r in races if r["id"] in want]
        missing = want - {r["id"] for r in races}
        if missing:
            print(f"bulunamayan yarış id: {sorted(missing)}")
    if args.season:
        races = [r for r in races if str(r.get("year") or "")[:4] == str(args.season)]
    if not races:
        print("hedef yarış yok. --list ile bak, ya da --race-id/--all ver.")
        return 1

    print(f"{len(races)} yarış, faz={args.phase}"
          f"{' (dry-run)' if args.dry_run else ''}{' (overwrite)' if args.overwrite else ''}")
    for race in races:
        try:
            run_race(race, phase=args.phase, dry_run=args.dry_run, overwrite=args.overwrite)
        except Exception as e:
            print(f"  #{race['id']}: HATA {type(e).__name__}: {e}")

    if not args.dry_run:
        print("\nsonuç:")
        _print_races()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
