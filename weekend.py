#!/usr/bin/env python3
"""One weekend, computed once: card grid data + briefing summary.

Why this module exists
----------------------
Two very different surfaces need the same facts about a race weekend:

  * the welcome grid draws one card per session, with a state
    (done / live / scheduled / empty / missing) and a headline number;
  * the chat briefing gets a driver-by-session summary so a question like
    "three practices are done, who is favourite for qualifying and the race"
    can be answered without stuffing six whole snapshots into one prompt.

Written as two separate features, those two would eventually disagree about the
same weekend -- the failure this project keeps paying for (a number computed in
two places drifts). So the numbers are computed HERE, once, and both consumers
render them.

What this module deliberately does not do
-----------------------------------------
  * It does not ask a model to summarise anything. Every number below comes from
    `store` (lap times, frames, race rows). An LLM-written weekend digest would
    invent numbers and cost a call per question; the panel's whole stance is
    "no number is a guess".
  * It does not decide a card's state from the clock. A real row in the archive
    (Mexico City Race) had three captured frames while its `start_date` was still
    a day away, so "future date = not started" is false here. State comes from
    capture evidence: frames, `last_seen` freshness, `final_status`.
  * It does not invent a missing session. A weekend with no Qualifying row says
    "kayıt yok" on that card and lists it as missing in the briefing header.
"""
from __future__ import annotations

import statistics as stats
import time
from datetime import datetime, timedelta, timezone

import points
import store

#: A session counts as live when the writer touched its row within this window.
#: The live writer touches every 15 s (see server.touch_race), so 90 s is six
#: missed ticks: long enough to survive a reconnect, short enough that a card is
#: not still claiming LIVE an hour after the chequered flag.
LIVE_WINDOW_SECS = 90.0

#: `final_status` values that mean the session is over. SessionStatus.Status uses
#: this small vocabulary; matching is by prefix so "ends"/"Ends"/"FINISHED" all
#: land here without a case table that will miss the next spelling.
_FINISHED = ("ends", "finish", "final", "abort", "chequer")

#: A lap slower than this multiple of the driver's best is not pace: out-laps,
#: in-laps, traffic and cool-down laps are all in `lap_times` and would drag a
#: median into meaninglessness. 1.07 keeps real long-run degradation (a stint can
#: lose ~2-3 %) and drops the rest.
_CLEAN_BAND = 1.07
#: A "long run" needs at least this many clean laps. Two laps is not a medium.
_MIN_CLEAN_LAPS = 3

_STATE_LABELS = {
    "done": "tamamlandı",
    "live": "canlı",
    "scheduled": "başlamadı",
    "empty": "veri yok",
    "missing": "kayıt yok",
    # A fifth state, and the reason it exists: `unknown` used to render as
    # "tamamlandı", so a record nobody can classify was presented as a finished
    # session. "Bilmiyorum" is a real answer; "bitti" is a claim.
    "unknown": "belirsiz",
}

#: kind -> what a card's headline means. Practice/qualifying quote a lap time,
#: the race quotes a finishing position; saying "en hızlı tur" on a race card
#: would be true and useless.
_POS_KINDS = ("RACE", "SP")


# ---------- small pure helpers ----------
def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(v) -> datetime | None:
    if not v:
        return None
    try:
        return datetime.strptime(str(v).replace("Z", ""), "%Y-%m-%dT%H:%M:%S").replace(
            tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def is_finished_status(status) -> bool:
    s = str(status or "").strip().lower()
    return any(s.startswith(k) for k in _FINISHED)


#: SessionInfo.StartDate is written in the TRACK's local time, not UTC (Mexico
#: City FP1 is stored as 12:30 while OpenF1 reports 18:30 UTC), so it cannot be
#: compared to a UTC clock directly. Instead of one symmetric margin, the bounds
#: are one-sided and named for what they are -- which is what makes the verdict
#: STABLE: the earlier attempt used a symmetric 12 h window compared against
#: `now`, so as the clock advanced the same unchanged row moved from
#: "başlamadı" to "tamamlandı" with no new evidence. A verdict that drifts is
#: worse than no verdict.
#:
#:   real_start_utc = start_local - offset,  offset in [-12h .. +14h]
#:   => real_start_utc is in [start_local - 14h, start_local + 12h]
_EARLIEST_HRS = 14      # largest positive offset (UTC+14) -> earliest real start
_LATEST_HRS = 12        # largest negative offset (UTC-12) -> latest real start
#: No F1 session runs longer than this. Used only to close the far bound, so the
#: "finished" verdict needs no knowledge of the track's timezone.
_MAX_SESSION_HRS = 6


def capture_counts(row: dict | None) -> tuple[int, int]:
    """(frames, lap_time rows) for one race row.

    `store.list_races()` already carries these as correlated subqueries, so a row
    from the grid costs nothing here. A row from `store.get_race()` (the archive's
    path) has neither, and re-querying is the difference between "this session was
    captured" and "this session looks empty" -- the bug that made a finished race
    read as having no data at all.
    """
    if not row:
        return 0, 0
    if "n_frames" in row and "n_lap_times" in row:
        return int(row.get("n_frames") or 0), int(row.get("n_lap_times") or 0)
    rid = row.get("id")
    if rid is None:
        return 0, 0
    try:
        with store.db() as c:
            f = c.execute("SELECT COUNT(*) FROM frames WHERE race_id=?", (int(rid),)).fetchone()[0]
            l = c.execute("SELECT COUNT(*) FROM lap_times WHERE race_id=?", (int(rid),)).fetchone()[0]
        return int(f), int(l)
    except Exception:
        return 0, 0


def race_phase(row: dict | None, *, max_lap: int | None = None,
               now: datetime | None = None) -> dict:
    """Is the EVENT over, and is OUR RECORD of it complete? Two separate facts.

    This distinction is the whole point of the function. Two real rows from the
    archive, both answered wrongly before it existed:

      * Australian GP (in the past, captured to lap 57 of 58): the race is over
        and the record stops one lap early. The assistant called the session
        "not finished yet" because it only saw `status=Started` and 57/58 --
        true about the file, false about the event.
      * Mexico City race (start time still ~a day away, three stray frames):
        the card said "bitti" because "has frames and is stale" was read as
        "finished". The event had not begun.

    So: `event_over` answers "should I describe this in the past tense?", and
    `record_complete` answers "can this record support a result?". A partial
    record must never be reported as a live race, and a future race must never
    be reported as finished.
    """
    now = now or _now()
    info = {"event_over": False, "record_complete": False, "phase": "no_data",
            "last_lap": max_lap, "total": None, "reason": ""}
    if not row:
        info["reason"] = "bu seans için kayıt satırı yok"
        return info

    kind = store.session_kind(row.get("session"))
    stype = points.session_type(row.get("session"))
    total = row.get("total_laps")
    try:
        total = int(total) if total not in (None, "") else None
    except (TypeError, ValueError):
        total = None
    info["total"] = total

    frames, laps = capture_counts(row)
    start = _parse_iso(row.get("start_date"))
    last_seen = _parse_iso(row.get("last_seen"))
    # Only the LIVE WRITER can make a row fresh. A backfilled row is stamped with
    # the moment we wrote it (store.upsert_race sets last_seen=now), so trusting
    # freshness alone made every restored practice session claim to be running
    # right now -- measured on the United States GP weekend: four backfilled
    # sessions all showed a LIVE badge. `source` is the evidence that separates
    # "being captured" from "restored from an archive".
    live_writer = (row.get("source") or store.SOURCE_LIVE) == store.SOURCE_LIVE
    fresh = (live_writer and last_seen is not None
             and (now - last_seen).total_seconds() <= LIVE_WINDOW_SECS)
    finished_status = is_finished_status(row.get("final_status"))

    # ---- timezone-safe bounds (see _EARLIEST_HRS / _LATEST_HRS) ----
    earliest_start = start - timedelta(hours=_EARLIEST_HRS) if start else None
    latest_start = start + timedelta(hours=_LATEST_HRS) if start else None
    latest_end = (latest_start + timedelta(hours=_MAX_SESSION_HRS)
                  if latest_start else None)

    # 1. NOT STARTED, decided from evidence that cannot drift.
    #
    #    `last_seen` is UTC and was written by US when the capture stopped; the
    #    start time is track-local. If our capture ended before the earliest
    #    instant the session could possibly have begun, then nothing we hold can
    #    be race data -- the session had not started when we stopped watching.
    #
    #    This is what settles the Mexico City race: it was last touched at
    #    2026-10-31T17:43Z while the start cannot be earlier than 2026-11-01T00:00Z.
    #    The conclusion depends only on stored facts, so it stays true forever
    #    instead of flipping as the clock advances (the previous bug).
    if earliest_start is not None:
        if last_seen is not None and last_seen < earliest_start:
            info.update(phase="not_started", event_over=False,
                        reason=("kayıt başlangıçtan önce kesilmiş — eldeki çerçeveler "
                                "seans öncesi, yarış verisi değil"))
            return info
        if now < earliest_start:
            info.update(phase="not_started", event_over=False,
                        reason="başlangıç saati henüz gelmedi")
            return info

    # 2. No capture at all: nothing to describe.
    if frames == 0 and laps == 0:
        info.update(phase="no_data", event_over=False,
                    reason="bu seans için hiç veri yakalanmamış")
        return info

    # 3. A restored session whose scheduled end is past the LATEST possible end
    #    is over: the mirror never updates a finished session.
    end = _parse_iso(row.get("end_date"))
    if not live_writer and end is not None:
        end_ceiling = end + timedelta(hours=_LATEST_HRS + _MAX_SESSION_HRS)
        if now > end_ceiling:
            partial = bool(stype.get("has_race_distance") and total and max_lap is not None
                           and int(max_lap) < total)
            info.update(phase="finished", event_over=True, record_complete=not partial,
                        reason=("arşivden tamamlandı; kayıt son turda kesiliyor" if partial
                                else "arşivden tamamlandı (seans bitmiş)"))
            return info

    # 4. The feed itself said it ended: no clock needed.
    if finished_status:
        info.update(phase="finished", event_over=True, record_complete=True,
                    reason="feed seansın bittiğini söyledi")
        return info

    # 5. Race distance reached: no clock needed either.
    if stype.get("has_race_distance") and total and max_lap is not None \
            and int(max_lap) >= total:
        info.update(phase="finished", event_over=True, record_complete=True,
                    reason=f"yarış mesafesi tamamlandı ({max_lap}/{total})")
        return info

    # 6. Still being written: live, whatever the lap number says.
    if fresh:
        info.update(phase="running", event_over=False,
                    reason="kayıt son 90 saniye içinde güncellendi")
        return info

    # 7. Stale capture past the LATEST possible end: the event is over whatever
    #    we managed to record. One-sided on purpose -- the previous version also
    #    used a margin in the OTHER direction and produced a verdict that
    #    depended on how long the row had been sitting there.
    if latest_end is not None and now > latest_end:
        partial = not stype.get("has_race_distance") or total is None \
            or max_lap is None or int(max_lap) < total
        info.update(phase="finished", event_over=True, record_complete=not partial,
                    reason=("seans bitti; kayıt son turda kesiliyor"
                            if partial else "seans bitti"))
        return info

    # 8. Neither the clock nor the lap count can settle it. Say so; and never
    #    render this as "finished" -- see _STATE_LABELS["unknown"].
    info.update(phase="unknown", event_over=False,
                reason="başlangıç saati kayıttaki zaman dilimiyle belirsiz")
    return info


def session_state(row: dict | None, *, now: datetime | None = None,
                  max_lap: int | None = None) -> str:
    """done | live | scheduled | empty | missing  -- a rendering of race_phase().

    All the judgement lives in `race_phase` so the card, the archive badge and
    the briefing cannot disagree about the same session. This function only
    maps its verdict onto the four words a card can show.

    Two mappings are deliberate:

      * `partial` (event over, record cut short) still renders as "done": the
        session happened. The incompleteness is reported separately, in words,
        because "bitti ama kayıt 57/58'de kesiliyor" is more useful than hiding
        a session that plainly took place.
      * `unknown` renders as its OWN state ("belirsiz"), never as "done". It
        used to map to "done", so a record nobody could classify was shown as a
        finished session -- the exact complaint that produced this state: the
        Mexico City race showing "BİTTİ" before it had run. Claiming completion
        requires positive evidence, and so does claiming a session is live.
    """
    if not row:
        return "missing"
    info = race_phase(row, max_lap=max_lap, now=now)
    phase = info["phase"]
    if phase == "not_started":
        return "scheduled"
    if phase == "running":
        return "live"
    if phase == "unknown":
        return "unknown"
    if phase == "no_data":
        return "empty"
    # finished / partial / unknown with data
    return "done"


def _lap_fmt(sec) -> str:
    if not isinstance(sec, (int, float)) or sec <= 0:
        return "-"
    m, s = divmod(float(sec), 60)
    return f"{int(m)}:{s:06.3f}"


def _pos_fmt(pos) -> str:
    try:
        return f"P{int(pos)}"
    except (TypeError, ValueError):
        return "-"


def clean_run(times: dict, best: float | None = None) -> tuple[float | None, int]:
    """(median of clean laps, laps used) for one driver's lap map.

    `times` is {lap: seconds}. Returns (None, 0) when there is not enough clean
    data -- a median over one lap is not a pace, and reporting it as one is the
    kind of quiet invention this project avoids.
    """
    vals = [v for v in (times or {}).values() if isinstance(v, (int, float)) and v > 0]
    if not vals:
        return None, 0
    b = best if isinstance(best, (int, float)) else min(vals)
    if b <= 0:
        return None, 0
    clean = [v for v in vals if v <= b * _CLEAN_BAND]
    if len(clean) < _MIN_CLEAN_LAPS:
        return None, len(clean)
    return round(stats.median(clean), 3), len(clean)


# ---------- per-session evidence ----------
def session_facts(rid: int, kind: str | None) -> dict:
    """Numbers a card and a briefing cell need, for one captured session.

    Costs: one lap_times read and one frame read per session. Called behind the
    memoised weekend overview, so a weekend of six sessions is read once, not
    once per question.
    """
    out = {"best": None, "best_driver": None, "pos": None, "pos_driver": None,
           "grid": {}, "lap_count": 0, "drivers": 0, "lead": None}
    if not rid:
        return out
    try:
        per = store.all_lap_times(rid)
    except Exception:
        per = {}
    meta = {}
    try:
        meta = store.race_driver_meta(rid)
    except Exception:
        meta = {}

    def code_of(num):
        m = meta.get(str(num)) or {}
        return m.get("code") or str(num)

    # Session best across the field. Out-laps cannot fake this: it is a minimum.
    best, best_num = None, None
    for num, laps in per.items():
        for v in laps.values():
            if isinstance(v, (int, float)) and v > 0 and (best is None or v < best):
                best, best_num = float(v), num
    if best is not None:
        out["best"] = round(best, 3)
        out["best_driver"] = code_of(best_num)
    out["lap_count"] = max((len(v) for v in per.values()), default=0)
    # Highest RACE lap number captured. For a race this is the completion
    # evidence: 57 of 58 means the record stops one lap early, which is a fact
    # about the file and must not be reported as a fact about the race.
    laps_seen = [lap for d in per.values() for lap in d]
    out["last_lap"] = int(max(laps_seen)) if laps_seen else None
    out["drivers"] = len(per)
    # Coverage: median laps per driver. A session captured from one frame has one
    # lap per driver, and the fastest of those is somebody's out-lap, not a pace
    # benchmark. The number is still real, so it is shown -- but labelled, because
    # "FP1 en hızlısı 1:19.200" off one lap per driver reads as a claim it cannot
    # support. Measured on the live archive: Mexico FP1 has 106 lap rows for 22
    # drivers (≈1 each), FP2 has 624 (≈19).
    counts = sorted(len(v) for v in per.values() if v)
    out["coverage"] = int(stats.median(counts)) if counts else 0
    out["thin"] = bool(counts) and out["coverage"] < _MIN_CLEAN_LAPS

    # Result/order from the newest frame that has rows.
    fr = None
    try:
        fr = store.frame_row(rid)
    except Exception:
        fr = None
    rows = ((fr or {}).get("snap") or {}).get("rows") or []
    if rows:
        ordered = sorted((r for r in rows if isinstance(r.get("pos"), int)),
                         key=lambda r: r["pos"])
        if ordered:
            leader = ordered[0]
            out["lead"] = {"code": leader.get("code"), "pos": leader.get("pos"),
                           "tyre": leader.get("tyre"), "pit": leader.get("pit")}
            out["pos"] = leader.get("pos")
            out["pos_driver"] = leader.get("code")
    if kind in _POS_KINDS:
        try:
            out["grid"] = store.all_row_grids(rid)
        except Exception:
            pass
    return out


# ---------- weekend overview (card grid) ----------
def _expected_kinds(sessions: list[dict], season, round_no) -> tuple[tuple[str, ...], str]:
    """Which session kinds this weekend SHOULD have, and how we know.

    Returns (kinds, source) where source is one of:
      'sessions' -- derived from what was actually captured (SQ/SP seen => sprint)
      'calendar' -- curated sprint_rounds table says so
      'default'  -- nothing said sprint, so the normal shape is assumed
    The distinction matters: 'default' is an assumption and the caller must be
    able to present it as one instead of asserting a falsified schedule.
    """
    seen = {store.session_kind(s.get("session")) for s in sessions}
    if "SQ" in seen or "SP" in seen:
        return store.SPRINT_KINDS, "sessions"
    if season and round_no:
        try:
            if int(round_no) in store.sprint_rounds(int(season)):
                return store.SPRINT_KINDS, "calendar"
        except (TypeError, ValueError):
            pass
    return store.NORMAL_KINDS, "default"


def resolve_round(wk: dict) -> int | None:
    """The championship round a weekend is, from the CACHED calendar only.

    Why not `standings.round_for(start_date)` directly: that function matches on
    the RACE date, while a weekend's own earliest stored date is a practice day --
    measured on the real archive, Mexico City FP1 starts 2026-10-30 and its round
    only matched from 2026-11-01. Passing the weekend start there returns None and
    the sprint lookup then silently degrades. So every session date is tried, and
    the meeting name as a last resort.

    Returns None when the calendar does not contain this weekend: a wrong round
    would select the wrong sprint flag, and a wrong sprint flag changes which
    cards exist.
    """
    try:
        import standings  # local import: weekend.py must stay importable without it
        schedule = standings.get_schedule(int(wk.get("year") or 0)) or []
    except Exception:
        return None
    if not schedule:
        return None
    for s in wk.get("sessions") or []:
        r = standings.round_for(s.get("start_date") or "", wk.get("meeting") or "", schedule)
        if r:
            return r
    r = standings.round_for(wk.get("start_date") or "", wk.get("meeting") or "", schedule)
    if r:
        return r
    # Name-only match, exact (case-insensitive) and unique -- never a prefix.
    name = (wk.get("meeting") or "").strip().lower()
    if name:
        hits = [x["round"] for x in schedule
                if (x.get("name") or "").strip().lower() == name]
        if len(hits) == 1:
            return int(hits[0])
    return None


def overview(rid: int, *, round_no: int | None = None, now: datetime | None = None) -> dict | None:
    """Everything the welcome grid needs for one weekend.

    `round_no` is passed in by the caller, which already has the cached calendar
    (weekend.py must not fetch anything). Without it the sprint decision falls
    back to capture evidence and then to the normal weekend shape, and the result
    says so via `shape_source`.
    """
    wk = store.weekend_of(rid)
    if not wk:
        return None
    if round_no is None:
        round_no = resolve_round(wk)
    sessions = wk.get("sessions") or []
    by_kind: dict[str, dict] = {}
    for s in sessions:
        k = store.session_kind(s.get("session"))
        if k and k not in by_kind:
            by_kind[k] = s
    expected, shape_source = _expected_kinds(sessions, wk.get("year"), round_no)

    cards = []
    for kind in expected:
        row = by_kind.get(kind)
        cards.append(_card(kind, row, now=now))
    # Sessions that exist but are not part of the expected shape (a duplicate
    # capture, or a kind the calendar did not predict) are appended rather than
    # dropped: hiding a stored session makes the archive look emptier than it is.
    for kind, row in by_kind.items():
        if kind in expected:
            continue
        card = _card(kind, row, now=now)
        card["label"] += " (beklenmeyen)"
        cards.append(card)

    missing = [c["kind"] for c in cards if c["state"] == "missing"]
    done = [c["kind"] for c in cards if c["state"] in ("done", "live")]
    return {
        "key": wk["key"],
        "meeting": wk["meeting"],
        "official": wk["official"],
        "year": wk["year"],
        "meeting_key": wk["meeting_key"],
        "start_date": wk["start_date"],
        "round": round_no,
        "shape_source": shape_source,
        "cards": cards,
        "missing": missing,
        "done": done,
        "live": next((c["kind"] for c in cards if c["live"]), None),
        # A weekend is "in progress" when at least one session has data and at
        # least one expected session does not. The grid uses this to decide
        # whether to explain itself ("Qualifying was not captured").
        "partial": bool(done) and bool(missing),
    }


def _card(kind: str, row: dict | None, *, now: datetime | None = None) -> dict:
    """One card's payload. Single producer so a missing row and a real row cannot
    grow different key sets -- the grid iterates these without guarding."""
    facts = session_facts(int(row["id"]), kind) if row else {}
    info = race_phase(row, max_lap=facts.get("last_lap"), now=now)
    state = session_state(row, now=now, max_lap=facts.get("last_lap"))
    return {
        "kind": kind,
        "label": store.card_label(kind),
        "rid": int(row["id"]) if row else None,
        "session": (row or {}).get("session") or "",
        "state": state,
        "state_label": _STATE_LABELS.get(state, state),
        # The phase verdict travels with the card: the grid needs to say
        # "bitti, kayıt 57/58'de kesiliyor" without re-deriving completion (and
        # possibly deriving it differently).
        "phase": info["phase"],
        "event_over": info["event_over"],
        "record_complete": info["record_complete"],
        "phase_note": info["reason"],
        "last_lap": info["last_lap"],
        "total": info["total"],
        "live": state == "live",
        "has_data": bool(row and (int(row.get("n_frames") or 0)
                                  or int(row.get("n_lap_times") or 0))),
        "start_date": (row or {}).get("start_date") or "",
        "laps": int((row or {}).get("n_lap_times") or 0),
        "frames": int((row or {}).get("n_frames") or 0),
        "coverage": int(facts.get("coverage") or 0),
        "thin": bool(facts.get("thin")),
        "last_seen": (row or {}).get("last_seen") or "",
        "chat": int((row or {}).get("n_chat") or 0),
        "best": facts.get("best"),
        "best_driver": facts.get("best_driver"),
        "lead": facts.get("lead"),
        "headline": _headline(kind, state, facts),
    }


def _headline(kind: str, state: str, facts: dict) -> str:
    """The one number a card shows. Empty states get no number at all."""
    if state in ("missing", "scheduled", "empty"):
        return ""
    if kind in _POS_KINDS:
        lead = facts.get("lead")
        if lead:
            return f"{_pos_fmt(lead.get('pos'))} {lead.get('code') or ''}".strip()
    if facts.get("best") is not None:
        # The headline stays a bare number: it is the one thing read at a glance
        # and it was being ellipsised in a 168 px card once a "(az tur)" suffix
        # was appended. Thin coverage is carried by the card's sub line instead
        # (see welcome.html), which has room for words.
        return f"{_lap_fmt(facts['best'])} {facts.get('best_driver') or ''}".strip()
    return ""


def session_cells(rid: int, kind: str | None = None) -> str:
    """One session's comparison material: driver | best | clean median (n) | grid.

    Deliberately the SAME numbers `summary_block` puts in a column cell, so a
    single-session block and the weekend matrix can never disagree. This is what
    a cross-session question actually needs -- not a second full snapshot, which
    would cost ~4.4k tokens per session and re-introduce the session-kind
    confusion the briefing rules exist to prevent.
    """
    kind = kind or store.session_kind((store.get_race(rid) or {}).get("session"))
    per = store.all_lap_times(rid)
    if not per:
        return "bu seansta kayıtlı tur süresi yok"
    meta = store.race_driver_meta(rid)
    try:
        grids = store.all_row_grids(rid)
    except Exception:
        grids = {}
    rows = ((store.frame_row(rid) or {}).get("snap") or {}).get("rows") or []
    pos_of = {str(r.get("num")): r.get("pos") for r in rows}

    stype = points.session_type((store.get_race(rid) or {}).get("session"))
    is_pos = kind in _POS_KINDS
    head = ("sürücü | bitiş | en hızlı tur | temiz uzun stint medyanı (tur)"
            if is_pos else "sürücü | en hızlı tur | temiz uzun stint medyanı (tur) | grid")
    out = [head]
    lines = []
    for num, laps in per.items():
        if not laps:
            continue
        best = min(laps.values())
        med, n = clean_run(laps, best)
        code = (meta.get(num) or {}).get("code") or str(num)
        pos = pos_of.get(str(num))
        cells = []
        if is_pos:
            cells.append(_pos_fmt(pos))
        cells.append(_lap_fmt(best))
        cells.append(f"{_lap_fmt(med)} ({n})" if med else "-")
        if not is_pos:
            g = grids.get(str(num))
            cells.append(str(g) if g else "-")
        lines.append((pos if isinstance(pos, int) else 999, code,
                      f"{code} | " + " | ".join(cells)))
    lines.sort(key=lambda t: (t[0], t[1]))
    out.extend(t[2] for t in lines)
    if stype.get("is_practice") and not any("(" in ln.split("|")[-2] for ln in out[1:]):
        out.append("NOT: temiz tur sayısı yetersiz; medyan verilmedi (tek tur tempo kanıtı değildir).")
    return "\n".join(out)


# ---------- briefing summary ----------
# Memoised: the summary is rebuilt whenever any session of the weekend grows, and
# served unchanged in between. The fingerprint is cheap (counts already sit in
# the races row) so a weekend of six sessions is not re-read per question.
_summary_cache: dict[str, tuple[tuple, str]] = {}


def _fingerprint(sessions: list[dict]) -> tuple:
    return tuple(sorted((int(s.get("id") or 0), int(s.get("n_frames") or 0),
                         int(s.get("n_lap_times") or 0), s.get("final_status") or "")
                        for s in sessions))


def summary_block(rid: int, *, round_no: int | None = None, max_drivers: int = 22,
                  max_chars: int = 6000) -> str:
    """Driver-by-session matrix for the briefing.

    Kept to one line per driver so a whole weekend costs a few hundred tokens
    instead of six full snapshot briefings. Every number is a stored lap time or
    a stored position; the caller is told which sessions are missing so the model
    cannot read absence as "nothing happened".
    """
    wk = store.weekend_of(rid)
    if not wk:
        return "HAFTA SONU ÖZETİ: bu yarış kaydı bulunamadı."
    sessions = wk.get("sessions") or []
    if not sessions:
        return "HAFTA SONU ÖZETİ: bu hafta sonu için seans kaydı yok."

    if round_no is None:
        round_no = resolve_round(wk)
    # The round is part of the cache key: it decides the weekend SHAPE (sprint or
    # not), and two callers resolving it differently must not serve each other a
    # summary of the wrong shape.
    key = f"{wk['key']}@r{round_no}"
    fp = _fingerprint(sessions)
    hit = _summary_cache.get(key)
    if hit and hit[0] == fp:
        return hit[1]

    expected, shape_source = _expected_kinds(sessions, wk.get("year"), round_no)
    by_kind = {}
    for s in sessions:
        k = store.session_kind(s.get("session"))
        if k and k not in by_kind:
            by_kind[k] = s

    cols: list[tuple[str, int]] = []
    missing: list[str] = []
    thin: list[str] = []
    for kind in expected:
        row = by_kind.get(kind)
        if row and int(row.get("n_lap_times") or 0) > 0:
            cols.append((kind, int(row["id"])))
            # Coverage is judged per session, from the same helper the cards use,
            # so the grid and the briefing cannot disagree about whether a
            # session's numbers are usable.
            if (session_facts(int(row["id"]), kind) or {}).get("thin"):
                thin.append(kind)
        else:
            missing.append(kind)

    if not cols:
        out = (f"HAFTA SONU ÖZETİ — {wk.get('meeting')} {wk.get('year')}: "
               "kayıtlı seans verisi yok (tur süresi hiçbir seansta yok).")
        _summary_cache[key] = (fp, out)
        return out

    # codes per session + union of drivers, keyed by car number (stable across
    # sessions in one weekend; `code` is what the human reads).
    per_session: dict[str, dict] = {}
    codes: dict[str, str] = {}
    for kind, rid_s in cols:
        per_session[kind] = {
            "laps": store.all_lap_times(rid_s),
            "meta": store.race_driver_meta(rid_s),
            "grid": store.all_row_grids(rid_s),
            "rows": (((store.frame_row(rid_s) or {}).get("snap") or {}).get("rows") or []),
        }
        for num, m in per_session[kind]["meta"].items():
            codes.setdefault(str(num), m.get("code") or str(num))
        for r in per_session[kind]["rows"]:
            if r.get("code"):
                codes.setdefault(str(r.get("num")), r["code"])

    head = [f"HAFTA SONU ÖZETİ — {wk.get('meeting')} {wk.get('year')}"
            + (f", tur {round_no}" if round_no else "")
            + f" (kaynak: arşiv; {len(cols)} seans okundu)"]
    if missing:
        head.append("KAYIT YOK: " + ", ".join(f"{store.kind_label(k)}" for k in missing)
                    + " — bu seanslar koşulmuş olabilir ama kaydı yok; 'koşulmadı' deme, 'kayıt yok' de.")
    if thin:
        head.append("AZ KAYIT: " + ", ".join(f"{store.kind_label(k)}" for k in thin)
                    + " — sürücü başına çok az tur var; bu seansların 'en hızlı turu' "
                      "bir çıkış/kurulum turu olabilir, tempo kanıtı sayma.")
    if shape_source == "default":
        head.append("NOT: bu hafta sonunun sprint olup olmadığı takvimden doğrulanamadı; "
                    "aşağıdaki seans listesi kayıtlı veriden çıkarıldı.")

    col_hdr = " | ".join(k for k, _ in cols)
    head.append("SÜRÜCÜ | " + col_hdr)
    head.append("(antrenman hücresi = en hızlı tur / temiz uzun stint medyanı (temiz tur sayısı); "
                "sıralama = en hızlı tur; yarış/sprint = bitiş sırası / en hızlı tur)")

    # Order drivers by their best result anywhere in the weekend, so a reader
    # sees the contenders first. Ties fall back to code for determinism.
    def driver_line(num: str) -> list[str]:
        cells = []
        for kind, rid_s in cols:
            d = per_session[kind]
            laps = (d["laps"].get(num) or {})
            best = min(laps.values()) if laps else None
            if kind in _POS_KINDS:
                pos = next((r.get("pos") for r in d["rows"] if str(r.get("num")) == num), None)
                cells.append(f"{_pos_fmt(pos)}/{_lap_fmt(best)}" if best else _pos_fmt(pos))
            elif kind in ("Q", "SQ"):
                grid = d["grid"].get(num)
                cells.append(_lap_fmt(best) if best else "-")
                if grid:
                    cells[-1] += f" (grid {grid})"
            else:
                med, n = clean_run(laps, best)
                cells.append(f"{_lap_fmt(best)}/{_lap_fmt(med)}({n})" if med
                             else (_lap_fmt(best) if best else "-"))
        return cells

    lines = []
    for num in sorted(codes, key=lambda n: codes[n]):
        cells = driver_line(num)
        if all(c == "-" for c in cells):
            continue
        # Rank by the best finishing position the driver has anywhere in the
        # weekend; a weekend with no race yet ranks by nothing, and then the code
        # keeps the order deterministic. Sorting on the rendered string would read
        # "P10" as better than "P2" -- the classic lexicographic trap.
        pos = None
        for kind, _ in cols:
            if kind not in _POS_KINDS:
                continue
            p = next((r.get("pos") for r in per_session[kind]["rows"]
                      if str(r.get("num")) == num), None)
            if isinstance(p, int) and (pos is None or p < pos):
                pos = p
        lines.append((pos if pos is not None else 999, codes[num],
                      f"{codes[num]} | " + " | ".join(cells)))
    lines.sort(key=lambda t: (t[0], t[1]))
    lines = [t[2] for t in lines][:max_drivers]
    out = "\n".join(head + lines)
    if len(out) > max_chars:
        out = out[:max_chars] + "\n… (hafta sonu özeti kesildi; tek seans dökümü için o seansı aç)"
    _summary_cache[key] = (fp, out)
    return out


def clear_cache() -> None:
    """Drop the memoised summaries. Called by tests and after a backfill."""
    _summary_cache.clear()
