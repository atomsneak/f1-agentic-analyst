#!/usr/bin/env python3
"""Penalties, read out of the race-control feed instead of guessed.

The timing feed has no penalty field. A penalty exists only as a line of text in
`RaceControlMessages`, and that text is where the whole dashboard has to get it
from:

    FIA STEWARDS: 5 SECOND TIME PENALTY FOR CAR 16 (LEC) - LEAVING THE TRACK ...
    FIA STEWARDS: 10 SECOND TIME PENALTY FOR CAR 63 (RUS) - CAUSING A COLLISION
    FIA STEWARDS: PENALTY SERVED - 5 SECOND TIME PENALTY FOR CAR 16 (LEC)
    FIA STEWARDS: TURN 6 INCIDENT INVOLVING CAR 63 (RUS) UNDER INVESTIGATION ...

Why this is a module rather than a regex in the page
----------------------------------------------------
Three things need the same answer: the live table (a badge on the driver's row),
the archive (the same badge, on a stored frame) and the assistant (a question
like "kimin cezası var" must be answered from numbers, not from the model's
memory of the transcript). Written three times, three answers. So the parse lives
here, once, as a pure function over a list of messages.

What is deliberately NOT claimed
--------------------------------
  * `UNDER INVESTIGATION` is NOT a penalty. An investigation may end with no
    action; showing it as a penalty would accuse a driver of something the
    stewards have not decided. It is reported separately, as a state.
  * A penalty's REMAINING time is never computed. Whether a 5-second penalty has
    been served depends on a pit stop we may not have seen, so the parse reports
    "served" only when the feed itself says "PENALTY SERVED".
  * An unparseable stewards message is not silently dropped: it is returned in
    `unparsed` so a new wording shows up as a gap instead of vanishing.
"""
from __future__ import annotations

import re

#: "5 SECOND TIME PENALTY FOR CAR 16 (LEC)" -- the lifetime of the message format
#: has been stable, but the number of seconds and the reason vary.
_PENALTY = re.compile(
    r"(?P<secs>\d+)\s*SECOND\s+TIME\s+PENALTY\s+FOR\s+CAR\s+(?P<car>\d+)"
    r"(?:\s*\((?P<code>[A-Z]{3})\))?"
    r"(?:\s*-\s*(?P<reason>[^(]*))?",
    re.I)

#: "PENALTY SERVED - 5 SECOND TIME PENALTY FOR CAR 16 (LEC)": the serving notice
#: repeats the penalty, so it must be recognised BEFORE the plain pattern or the
#: same message would be counted as a second, unserved penalty.
_SERVED = re.compile(r"PENALTY\s+SERVED.*?CAR\s+(?P<car>\d+)", re.I)

#: "UNDER INVESTIGATION" / "NOTED" -- a review, not a decision.
#:
#: `CARS?` is plural-tolerant on purpose: real messages say both
#: "INVOLVING CAR 63 (RUS) UNDER INVESTIGATION" and
#: "INVOLVING CARS 44 (HAM) AND 16 (LEC) UNDER INVESTIGATION". The first version
#: only matched the singular form, so an incident between two drivers was
#: reported as unparsed -- and "unparsed" means nobody saw it on screen.
_REVIEW = re.compile(
    r"(?P<kind>UNDER\s+INVESTIGATION|NOTED|WILL\s+BE\s+INVESTIGATED)"
    r".*?CARS?\s+(?P<car>\d+)(?:\s*\((?P<code>[A-Z]{3})\))?",
    re.I)
#: The review wording puts the car first and the state last, so try that order too.
_REVIEW_ALT = re.compile(
    r"CARS?\s+(?P<car>\d+)(?:\s*\((?P<code>[A-Z]{3})\))?[^.]*?"
    r"(?P<kind>UNDER\s+INVESTIGATION|WILL\s+BE\s+INVESTIGATED)",
    re.I)
#: Every additional car named in the same message: "AND 16 (LEC)".
_EXTRA_CARS = re.compile(r"\bAND\s+(\d+)(?:\s*\(([A-Z]{3})\))?", re.I)

#: "REVIEWED NO FURTHER INVESTIGATION" / "NO FURTHER ACTION": the stewards looked
#: and decided nothing follows. This is a CLOSED review and must not be shown as
#: an open one -- leaving a driver marked "inceleme" after he has been cleared is
#: the same class of error as calling a review a penalty, just in reverse.
_CLEARED = re.compile(
    r"(?P<kind>NO\s+FURTHER\s+(?:INVESTIGATION|ACTION)|REVIEWED\s+NO\s+FURTHER\s+\w+)"
    r".*?CARS?\s+(?P<car>\d+)(?:\s*\((?P<code>[A-Z]{3})\))?",
    re.I)
_CLEARED_ALT = re.compile(
    r"CARS?\s+(?P<car>\d+)(?:\s*\((?P<code>[A-Z]{3})\))?[^.]*?"
    r"(?P<kind>NO\s+FURTHER\s+(?:INVESTIGATION|ACTION))",
    re.I)

#: Other sanction shapes that are real penalties but carry no seconds.
_OTHER = re.compile(
    r"(?P<kind>DRIVE\s+THROUGH\s+PENALTY|STOP\s+AND\s+GO\s+PENALTY|"
    r"REPRIMAND|GRID\s+PENALTY|DISQUALIF\w+|BLACK\s+AND\s+WHITE\s+FLAG)"
    r"[^.]*?CAR\s+(?P<car>\d+)(?:\s*\((?P<code>[A-Z]{3})\))?",
    re.I)

_KIND_TR = {
    "drive through penalty": "drive-through",
    "stop and go penalty": "stop-go",
    "reprimand": "kınama",
    "grid penalty": "grid cezası",
    "disqualified": "diskalifiye",
    "black and white flag": "siyah-beyaz bayrak",
}


def _clean(s) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip(" .-")


def parse(messages) -> dict:
    """Penalty state per car, from a race-control message list.

    `messages` is the snapshot's own `rc` list: dicts with `msg`, `lap`, `cat`.
    Returns:

        by_car   {car: {"car", "code", "total_secs", "items": [...],
                        "served": bool, "review": str|None}}
        pending  how many cars have an unserved penalty
        unparsed stewards lines we could not read (so a new wording is visible)
    """
    by_car: dict[str, dict] = {}
    unparsed: list[str] = []
    served_cars: set[str] = set()
    # Two passes: serving notices can appear anywhere in the list, and a penalty
    # is only "served" if that notice exists at all.
    for m in messages or []:
        raw = str((m or {}).get("msg") or "")
        sm = _SERVED.search(raw)
        if sm:
            served_cars.add(sm.group("car"))
    for m in messages or []:
        raw = str((m or {}).get("msg") or "")
        if not raw:
            continue
        # Only obvious non-sanctions are skipped here (flags, safety car, track
        # limits chatter). The filter must NOT be a whitelist of known sanction
        # words: a new stewards wording would then be discarded BEFORE the parser
        # could report it as unparsed, which is exactly the silent drop this
        # module exists to prevent. Anything from the stewards falls through.
        low = raw.lower()
        if "steward" not in low and "fia" not in low:
            if not any(k in low for k in ("penalty", "investigation", "reprimand",
                                          "disqualif", "black and white")):
                continue

        # Served notices are recorded as evidence, not as a second penalty.
        if _SERVED.search(raw):
            continue

        mm = _PENALTY.search(raw)
        if mm:
            car = mm.group("car")
            e = by_car.setdefault(car, {"car": car, "code": (mm.group("code") or "").upper(),
                                        "total_secs": 0, "items": [], "served": False,
                                        "review": None})
            secs = int(mm.group("secs"))
            e["total_secs"] += secs
            e["code"] = e["code"] or (mm.group("code") or "").upper()
            e["items"].append({"secs": secs, "reason": _clean(mm.group("reason")),
                               "lap": (m or {}).get("lap")})
            if car in served_cars:
                e["served"] = True
            continue

        mm = _CLEARED.search(raw) or _CLEARED_ALT.search(raw)
        if mm:
            # Checked BEFORE the review patterns: "REVIEWED NO FURTHER
            # INVESTIGATION" contains "INVESTIGATION" and would otherwise be read
            # as a review that is still open.
            car = mm.group("car")
            e = by_car.setdefault(car, {"car": car, "code": (mm.group("code") or "").upper(),
                                        "total_secs": 0, "items": [], "served": False,
                                        "review": None})
            e["code"] = e["code"] or (mm.group("code") or "").upper()
            e["review"] = "kapandı"
            e["cleared"] = True
            continue

        mm = _REVIEW.search(raw) or _REVIEW_ALT.search(raw)
        if mm:
            # An incident can name several cars: they are all under review, so all
            # of them get the state. Only the first is captured by the pattern.
            cars = [(mm.group("car"), mm.group("code"))]
            for extra in _EXTRA_CARS.finditer(raw):
                if extra.group(1) != mm.group("car"):
                    cars.append((extra.group(1), extra.group(2)))
            for car, code in cars:
                e = by_car.setdefault(car, {"car": car, "code": (code or "").upper(),
                                            "total_secs": 0, "items": [], "served": False,
                                            "review": None})
                e["code"] = e["code"] or (code or "").upper()
                e["review"] = _clean(mm.group("kind")).lower()
            continue

        mm = _OTHER.search(raw)
        if mm:
            car = mm.group("car")
            e = by_car.setdefault(car, {"car": car, "code": (mm.group("code") or "").upper(),
                                        "total_secs": 0, "items": [], "served": False,
                                        "review": None})
            kind = _clean(mm.group("kind")).lower()
            e["code"] = e["code"] or (mm.group("code") or "").upper()
            e["items"].append({"secs": None, "reason": _KIND_TR.get(kind, kind),
                               "lap": (m or {}).get("lap")})
            continue

        # A stewards line we could not classify. Surfaced, never swallowed: a
        # dropped penalty is worse than an unparsed one.
        if "steward" in low or "fia" in low:
            unparsed.append(raw)

    for e in by_car.values():
        e["has_penalty"] = bool(e["items"])
        e["label"] = label(e)
    pending = sum(1 for e in by_car.values() if e["has_penalty"] and not e["served"])
    return {"by_car": by_car, "pending": pending, "unparsed": unparsed}


def label(e: dict) -> str:
    """Short badge text for a driver row: "+5s", "+15s", "DT", "İNCELEME"."""
    if not e:
        return ""
    secs = e.get("total_secs") or 0
    bits = []
    if secs:
        bits.append(f"+{secs}s")
    for it in e.get("items") or []:
        if it.get("secs") is None and it.get("reason"):
            short = {"drive-through": "DT", "stop-go": "SG", "kınama": "kınama",
                     "grid cezası": "grid", "diskalifiye": "DSQ",
                     "siyah-beyaz bayrak": "SB"}.get(it["reason"], it["reason"][:6])
            bits.append(short)
    if e.get("review") == "kapandı" and not bits:
        return ""      # cleared: nothing to show, and showing "temiz" is noise
    if e.get("review") and not bits:
        bits.append("inceleme")
    if e.get("served") and bits:
        bits.append("çekildi")
    return " ".join(bits)


def summary(st: dict) -> str:
    """One line for the briefing: who has what, and who is only under review.

    Written for the model, so it states the distinction it must not lose: a
    review is not a penalty.
    """
    by_car = (st or {}).get("by_car") or {}
    if not by_car:
        return "CEZALAR: yarış kontrolünde ceza veya inceleme kaydı yok."
    parts = []
    for car, e in sorted(by_car.items(), key=lambda kv: kv[1].get("code") or kv[0]):
        who = f"CAR {car}" + (f" ({e['code']})" if e.get("code") else "")
        if e.get("has_penalty"):
            why = "; ".join((f"{it['secs']}sn" if it.get("secs") else it.get("reason") or "?")
                            + (f" — {it['reason']}" if it.get("reason") and it.get("secs") else "")
                            for it in e["items"])
            state = "çekildi" if e.get("served") else "BEKLİYOR"
            parts.append(f"{who}: {why} [{state}]")
        elif e.get("review") == "kapandı":
            parts.append(f"{who}: inceleme KAPANDI, işlem yok")
        elif e.get("review"):
            parts.append(f"{who}: sadece İNCELEME ({e['review']}), ceza DEĞİL")
    line = "CEZALAR: " + " · ".join(parts)
    if (st or {}).get("unparsed"):
        line += (f" | OKUNAMAYAN {len(st['unparsed'])} yarış kontrolü satırı var; "
                 "ceza var/yok diye kesin konuşma, önce o satırları aktar.")
    return line
