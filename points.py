#!/usr/bin/env python3
"""Championship points: the tables, and the projection built on them.

Two questions this module answers, and nothing else in the project may answer
them a second way:

  1. "How many points does finishing P cost?"     -> points_for()
  2. "If the race ended right now, what would the championship table be?"
     -> project()

Why it is its own module: the live panel, the offline standings page and the
briefing all need the same arithmetic. A second copy of 25-18-15-12-10-8-6-4-2-1
somewhere else is not a style problem -- the two copies drift, and then two
surfaces on the same screen disagree about the same number.

What is deliberately NOT here:

  * the fastest-lap point. It existed until 2024 and was dropped for 2025; a
    table that still awards it invents a point that no longer exists. If it ever
    comes back it comes back as a table entry, not as a hard-coded bonus.
  * half points for a shortened race. The live feed cannot tell us yet that a
    race will be shortened, so a halved table would be a guess. The projection
    says "tam puan varsayımı" in the UI instead.
  * tie-breaks. Official order among equal points is a countback of best
    finishes, which needs every result of the season -- data we do not hold.
    Equal totals are therefore marked `tie` and shown with a shared rank, so the
    order on screen is never presented as official.

Pure functions only: no database, no HTTP, no clock. That is what makes the
arithmetic testable (tests/test_points.py).
"""
from __future__ import annotations

import re

# The one place these numbers exist.
RACE_POINTS = (25, 18, 15, 12, 10, 8, 6, 4, 2, 1)
SPRINT_POINTS = (8, 7, 6, 5, 4, 3, 2, 1)

TABLES = {"race": RACE_POINTS, "sprint": SPRINT_POINTS}
KIND_RACE = "race"
KIND_SPRINT = "sprint"


def table(kind: str = KIND_RACE) -> tuple[int, ...]:
    """The points table for a session kind. Unknown kinds are a bug, not a default."""
    try:
        return TABLES[kind]
    except KeyError:
        raise ValueError(f"bilinmeyen puan türü: {kind!r} (beklenen: {sorted(TABLES)})") from None


def points_for(position, kind: str = KIND_RACE) -> int:
    """Points for a finishing position (1-based).

    Out of the points, missing, or nonsense (0, None, -3, "DNF", 27th) score 0.
    A retired driver is not in the running order, so this is reached with no
    position far more often than with a wrong one -- 0 is the honest answer both
    times.
    """
    pts = table(kind)
    try:
        pos = int(position)
    except (TypeError, ValueError):
        return 0
    if pos < 1 or pos > len(pts):
        return 0
    return pts[pos - 1]


def award_for_order(order, kind: str = KIND_RACE) -> dict[str, int]:
    """{key: points} for a finishing order given as an ordered list of keys."""
    out: dict[str, int] = {}
    for i, key in enumerate(order, 1):
        out[str(key)] = points_for(i, kind)
    return out


def session_kind(session_name) -> str:
    """Which table applies to a session name from the live feed.

    "Sprint" and "Sprint Shootout"/"Sprint Qualifying" are sprint sessions and
    pay the sprint table; a Race (or a sprint weekend's Sunday race) pays the
    race table. Anything else -- practice, qualifying -- pays neither, which is
    why the caller must check `pays_points` first.
    """
    s = (session_name or "").strip().lower()
    if not s:
        return KIND_RACE
    if "sprint" in s:
        return KIND_SPRINT
    return KIND_RACE


def pays_points(session_name) -> bool:
    """True only for sessions that actually award championship points."""
    s = (session_name or "").strip().lower()
    if not s:
        return False
    if "race" in s or "sprint" in s:
        # "Sprint Qualifying"/"Sprint Shootout" pays nothing; "Sprint" does.
        if "qualifying" in s or "shootout" in s:
            return False
        return True
    return False


# ---------- session taxonomy ----------
# One place that decides WHAT KIND of session the live feed is showing.
#
# Why this exists: the briefing read the session name only to decide points, and
# treated everything else as a race. Practice data was described as "race pace",
# the session lap counter was read as race distance, and a practice session whose
# counter had reached 56 was reported as a 56-lap grand prix. The taxonomy below
# is the single answer to "what am I looking at", and both the briefing and the
# UI read it instead of re-deriving it from the name at each call site.
KIND_PRACTICE = "practice"
KIND_QUALIFYING = "qualifying"
KIND_SPRINT_QUALIFYING = "sprint_qualifying"
KIND_UNKNOWN = "unknown"

# Qualifying splits into Q1/Q2/Q3; sprint qualifying into SQ1/SQ2/SQ3 (older
# weekends call it "Sprint Shootout" with the same three-part split). Practice
# and races have no segments.
_SEGMENTS = {
    KIND_QUALIFYING: ("Q1", "Q2", "Q3"),
    KIND_SPRINT_QUALIFYING: ("SQ1", "SQ2", "SQ3"),
}

_LABELS = {
    KIND_PRACTICE: "Antrenman (serbest çalışma)",
    KIND_QUALIFYING: "Sıralama turları",
    KIND_SPRINT_QUALIFYING: "Sprint sıralaması",
    KIND_SPRINT: "Sprint yarışı",
    KIND_RACE: "Yarış",
    KIND_UNKNOWN: "Bilinmeyen seans",
}

# What a pace number actually means in each kind. "Yarış temposu" is a claim a
# practice session cannot support, and that exact wording was the visible symptom.
_PACE_LABEL = {
    KIND_PRACTICE: "antrenman / uzun stint temposu",
    KIND_QUALIFYING: "sıralama turu temposu",
    KIND_SPRINT_QUALIFYING: "sprint sıralama turu temposu",
    KIND_SPRINT: "sprint yarış temposu",
    KIND_RACE: "yarış temposu",
    KIND_UNKNOWN: "seans temposu",
}


def session_type(session_name, type_hint=None) -> dict:
    """Full classification of a session name.

    `session_name` is SessionInfo.Name ("Practice 1", "Qualifying", "Sprint
    Qualifying", "Sprint", "Race"). `type_hint` is SessionInfo.Type when the feed
    provides it -- used only as a fallback, because Name is more specific: it
    carries the Q/SQ distinction and the practice number, which Type does not.

    Returns a dict rather than an enum because each caller needs a different
    slice (briefing: wording; UI: segment list; points: `pays_points`). One dict
    keeps them reading the same classification instead of three parallel
    if-chains that can silently disagree.
    """
    name = (session_name or "").strip()
    s = name.lower()
    hint = (type_hint or "").strip().lower()
    if not s and not hint:
        return _stype(KIND_UNKNOWN)

    # Sprint qualifying BEFORE sprint: "Sprint Qualifying" and "Sprint Shootout"
    # both contain "sprint", and both are qualifying sessions worth no points.
    if "shootout" in s or "sq1" in s or "sq2" in s or "sq3" in s or \
            ("sprint" in s and ("qualif" in s or "shootout" in s)):
        return _stype(KIND_SPRINT_QUALIFYING)
    if "sprint" in s:
        return _stype(KIND_SPRINT)
    if "qualif" in s or hint == "qualifying":
        return _stype(KIND_QUALIFYING)
    if "practice" in s or hint == "practice" or re.fullmatch(r"p[123]", s):
        return _stype(KIND_PRACTICE)
    if "race" in s or hint == "race":
        return _stype(KIND_RACE)
    return _stype(KIND_UNKNOWN)


def _stype(kind: str) -> dict:
    return {
        "kind": kind,
        "label": _LABELS.get(kind, kind),
        "pace_label": _PACE_LABEL.get(kind, _PACE_LABEL[KIND_UNKNOWN]),
        "segments": list(_SEGMENTS.get(kind, ())),
        # A lap counter is race distance only where a race distance exists. In
        # practice the counter ticks with the session clock, so calling it "lap
        # 56 of 56" is what made a practice session read as a finished GP.
        "has_race_distance": kind in (KIND_RACE, KIND_SPRINT),
        "is_race": kind == KIND_RACE,
        "is_sprint": kind == KIND_SPRINT,
        "is_qualifying": kind in (KIND_QUALIFYING, KIND_SPRINT_QUALIFYING),
        "is_practice": kind == KIND_PRACTICE,
        "pays_points": kind in (KIND_RACE, KIND_SPRINT),
    }


def project(base_entries, awards, *, tie_notice: bool = True) -> dict:
    """Project the table after adding `awards` to `base_entries`.

    base_entries -- iterable of dicts describing the championship AS IT STANDS
        without this session: {"key", "label", "points", "position" (optional),
        "team"/"color"/"code" (optional pass-through)}.
    awards       -- {key: points} earned in the session being projected.

    Returns {"rows": [...], "unmatched": [...], "tie_groups": [[key, ...], ...]}.

    Each row carries base_points, race_points, total, rank, base_position and
    rank_change (positive = places gained). Ordering: total desc, then the
    championship position it started from, then key -- deterministic, and never
    pretending to be the official tie-break (see module docstring).
    """
    rows: list[dict] = []
    seen: set[str] = set()
    for e in base_entries or []:
        key = str(e.get("key"))
        if key in seen:
            # Two rows for one driver would double-count his points; keep the
            # first and let the caller's data bug show as a missing second row.
            continue
        seen.add(key)
        row = dict(e)
        try:
            base = int(e.get("points") or 0)
        except (TypeError, ValueError):
            base = 0
        try:
            gained = int(awards.get(key, 0) or 0)
        except (TypeError, ValueError):
            gained = 0
        row["key"] = key
        row["base_points"] = base
        row["race_points"] = gained
        row["total"] = base + gained
        try:
            row["base_position"] = int(e["position"]) if e.get("position") is not None else None
        except (TypeError, ValueError):
            row["base_position"] = None
        rows.append(row)

    def _sort(r: dict):
        bp = r["base_position"]
        return (-r["total"], bp if bp is not None else 10 ** 6, r["key"])

    rows.sort(key=_sort)

    # Shared rank inside a group of equal totals, so P3=P3= P3 reads as a tie
    # rather than as three separate verdicts we cannot support.
    for i, r in enumerate(rows, 1):
        r["rank"] = i
        r["tie"] = False
        r["tie_rank"] = i
    groups: list[list[str]] = []
    i = 0
    while i < len(rows):
        j = i + 1
        while j < len(rows) and rows[j]["total"] == rows[i]["total"]:
            j += 1
        if j - i > 1 and tie_notice:
            groups.append([rows[k]["key"] for k in range(i, j)])
            for k in range(i, j):
                rows[k]["tie"] = True
                rows[k]["tie_rank"] = i + 1
        i = j

    for r in rows:
        if r["base_position"] is not None:
            r["rank_change"] = r["base_position"] - r["rank"]
        else:
            r["rank_change"] = None

    got = {str(k) for k, v in (awards or {}).items() if v}
    unmatched = sorted(got - seen)
    return {"rows": rows, "unmatched": unmatched, "tie_groups": groups}


def totals(rows) -> int:
    """Sum of projected totals -- used by tests and the panel's sanity line."""
    return sum(int(r.get("total") or 0) for r in rows or [])


def gained(rows) -> int:
    """Points handed out by this session, all rows together."""
    return sum(int(r.get("race_points") or 0) for r in rows or [])


# ---------- provisional view (a live race, seen as a championship table) ----------
def _entry_from_standing(row: dict, *, upper_key: bool = True) -> dict:
    """One cached standings row -> the base entry shape project() expects.

    upper_key for drivers (their key is the three-letter code, which the live
    feed also sends in caps) but not for constructors: a constructor key is an
    API id like "red_bull", and upper-casing it would stop matching team ids.
    """
    key = str(row.get("key") or row.get("code") or "")
    return {
        "key": key.upper() if upper_key else key,
        "label": row.get("code") or row.get("key") or "",
        "name": row.get("name") or "",
        "team": row.get("team") or "",
        "team_id": row.get("team_id") or "",
        "position": row.get("position"),
        "points": row.get("points") or 0,
        "wins": row.get("wins") or 0,
    }


def running_awards(race_rows, kind: str = KIND_RACE) -> tuple[dict[str, int], list[dict]]:
    """{code: points} from the live running order, plus the rows that scored.

    A retired driver scores nothing: he is not in the running order, whatever
    position he last held. A driver sitting in the pits still holds his place on
    the road, so he keeps his points -- the pit lane is not a retirement.
    """
    awards: dict[str, int] = {}
    scored: list[dict] = []
    for r in race_rows or []:
        if r.get("retired"):
            continue
        pos = r.get("pos")
        try:
            pos = int(pos)
        except (TypeError, ValueError):
            continue
        pts = points_for(pos, kind)
        if not pts:
            continue
        code = str(r.get("code") or r.get("num") or "").upper()
        if not code or code in awards:
            continue
        awards[code] = pts
        scored.append({"key": code, "pos": pos, "points": pts})
    return awards, scored


def provisional(race_rows, base_driver_rows, base_constructor_rows=None, *,
                kind: str = KIND_RACE, total_laps=None, lap=None) -> dict:
    """The championship table IF the race ended in its current order.

    Everything the live panel shows comes from here, so the panel, the API and
    the tests cannot disagree:
      * base_points  -- the cached championship total without this race
      * race_points  -- what the current running order would pay
      * total        -- the sum, and the new order it creates
      * rank_change  -- places gained (+) or lost (-) against the base order
      * unmatched    -- drivers scoring here but absent from the base table
                        (reported, never merged into somebody else's row)
    """
    awards, scored = running_awards(race_rows, kind)
    drivers = project([_entry_from_standing(r) for r in base_driver_rows or []], awards)

    out = {
        "kind": kind,
        "table": list(table(kind)),
        "drivers": drivers["rows"],
        "unmatched": drivers["unmatched"],
        "tie_groups": drivers["tie_groups"],
        "scored": scored,
        "awarded": sum(awards.values()),
        "lap": lap,
        "total_laps": total_laps,
        "provisional": True,
        "constructors": None,
    }

    if base_constructor_rows:
        # Constructor points for this race are the sum of its drivers' points,
        # so the team each scoring driver belongs to is read from the base
        # drivers' table (a driver code is a stable key; team names are not).
        team_of = {str(r.get("key") or r.get("code") or "").upper(): r.get("team_id") or ""
                   for r in base_driver_rows or []}
        team_awards: dict[str, int] = {}
        for code, pts in awards.items():
            tid = team_of.get(code)
            if tid:
                team_awards[tid] = team_awards.get(tid, 0) + pts
        cons = project([_entry_from_standing(r, upper_key=False)
                        for r in base_constructor_rows], team_awards)
        out["constructors"] = cons["rows"]
        out["constructor_unmatched"] = cons["unmatched"]
        out["unmapped_teams"] = sorted({c for c in awards
                                        if not team_of.get(c)})
    return out
