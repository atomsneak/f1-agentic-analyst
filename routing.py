"""Deterministic routing for questions that point outside the current snapshot.

Why not a tool loop: one LLM call is measured at 6-10 s, and the archive briefing
is ~4.4k tokens. A three-hop loop costs 20-30 s and ~13k tokens per question, and
the model this runs on has reasoning disabled -- it will happily invent argument
names it never saw. The routing facts below are cheap, exact, and testable, so the
lookup happens *before* the call and the model only ever reads real numbers.

Two intents matter to a live agent:

  past lap   -- "tur 20'de ne olmuştu"        -> same race, an earlier frame
  other race -- "geçen yarış", "Austin'de"    -> a different race in the archive

Both are answered by attaching a small, extra block to the live snapshot. The
attached data is marked so the briefing never labels a live race's own earlier
lap as "a past race".
"""
from __future__ import annotations

import re

import store

# A bare lap reference: "tur 20", "20. turda", "lap 20".
_LAP = re.compile(r"\b(?:tur|lap)\s*[:\-]?\s*(\d{1,3})\b", re.I)
_LAP_TR = re.compile(r"\b(\d{1,3})\s*\.?\s*tur(?:da|de|a|e|un|ün|daki|deki)?\b", re.I)

# Words that mean "not this race, not right now".
_PAST_HINT = re.compile(
    r"\b(geçen|gecen|önceki|onceki|son\s+yarış|son\s+yaris|bitmiş|bitmis|arşiv\w*|arsiv\w*|"
    r"geçmiş\w*|gecmis\w*|hatırla|hatirla|dünkü|dunku|evvelsi)\b", re.I)

# "şu an", "canlı", "bu tur" -- an explicit request for the live picture wins.
_NOW_HINT = re.compile(r"\b(şu\s*an|su\s*an|canlı|canli|anlık|anlik|hemen|simdi|şimdi)\b", re.I)

# A past-hint only means "another race" when race vocabulary is present. Without
# this, "geçen turda tempo düştü mü" -- which is about this race's previous lap --
# would be answered from a different race.
_RACE_CTX = re.compile(r"\b(yarış\w*|yaris\w*|grand\s*prix|gp|sezon|arşiv\w*|arsiv\w*|"
                       r"geçmiş\w*|gecmis\w*|hatırla\w*|hatirla\w*|sonuç\w*|sonuc\w*|"
                       r"podyum|kazan\w*|kazandı\w*)\b", re.I)


def lap_ref(text: str) -> int | None:
    """The lap number a question points at, if any."""
    for rx in (_LAP, _LAP_TR):
        m = rx.search(text or "")
        if m:
            try:
                n = int(m.group(1))
            except (TypeError, ValueError):
                continue
            if 1 <= n <= 200:
                return n
    return None


def wants_past_lap(text: str) -> bool:
    """True when the question names a lap that is not the current one.

    The caller still has to check that the lap is actually in the past; a
    question about the current lap must stay on the live path.
    """
    return lap_ref(text) is not None and not _NOW_HINT.search(text or "")


def wants_other_race(text: str) -> bool:
    """True when the question points at a race other than the one being captured.

    A past-hint is only trusted when the sentence is not plainly about something
    that happened inside a lap ("geçen turda lastik düştü" is still this race).
    """
    t = text or ""
    if not _PAST_HINT.search(t) or _NOW_HINT.search(t):
        return False
    return bool(_RACE_CTX.search(t))


def _race_terms(race: dict) -> list[str]:
    """Searchable names for a race, longest first so 'United States' wins."""
    terms = []
    for key in ("meeting", "official"):
        v = (race.get(key) or "").strip()
        if not v:
            continue
        terms.append(v)
        # "United States Grand Prix" -> also match "United States", "Austin"
        base = re.sub(r"\b(grand\s+prix|gp)\b", "", v, flags=re.I).strip(" -")
        if base and base.lower() != v.lower():
            terms.append(base)
    return sorted({t.lower() for t in terms if len(t) >= 3}, key=len, reverse=True)


def available_races() -> list[dict]:
    """Archived races, newest first -- used when a question names none of them."""
    races = store.list_races()
    return sorted(races, key=lambda r: (r.get("start_date") or ""), reverse=True)


def race_menu() -> str:
    """The archive's contents, so the model asks instead of guessing a race."""
    races = available_races()
    if not races:
        return "ARŞİV: kayıtlı yarış yok."
    bits = [f"#{r.get('id')} {r.get('meeting')} {r.get('year')} ({r.get('n_frames')} tur kayıtlı)"
            for r in races[:12]]
    return ("ARŞİVDEKİ YARIŞLAR: " + " · ".join(bits) +
            ". Soru bunlardan birini açıkça adlandırmıyorsa hangisi olduğunu sor; tahmin etme.")


def find_race(text: str, current_rid: int | None = None) -> int | None:
    """Pick the archived race a question names, or None if it names no race.

    Matching is by name only. There is no round-number or date guessing: a wrong
    race silently answered as if it were the right one is worse than saying
    "hangi yarışı kastediyorsun".
    """
    t = (text or "").lower()
    if not t:
        return None
    hits = []
    for race in store.list_races():
        if current_rid is not None and race.get("id") == current_rid:
            continue
        for term in _race_terms(race):
            if term in t:
                hits.append((len(term), race.get("id")))
                break
    if not hits:
        return None
    # Two different races named in one question: refuse rather than pick one.
    ids = {rid for _, rid in hits}
    if len(ids) > 1:
        return None
    return max(hits)[1]


def past_lap_block(rid: int, lap: int, max_rows: int = 22) -> str | None:
    """A compact table for one earlier lap of the same race.

    Deliberately smaller than the live briefing: this is an extra reference block,
    not a second full snapshot, so a follow-up question does not cost another 4k
    tokens. Returns None when that lap was never captured.
    """
    snap = store.frame_row(rid, lap)
    if not snap:
        return None
    s = snap.get("snap") or {}
    rows = s.get("rows") or []
    if not rows:
        return None
    secs, _ = store.sectors_for(rid, lap)
    # IDEAL/LOSS come from the race's best sectors, so the block can say where a
    # driver lost time against his own best rather than only against rivals.
    best = store.sector_bests(rid)
    per_driver = best.get("per_driver") or {}
    out = [f"GEÇMİŞ TUR {lap} (aynı yarış, o anın kaydı; kaynak={snap.get('source')}):",
           "POS | DRV | GAP | INT | LAST | TYRE | PIT | S1 | S2 | S3 | IDEAL | LOSS"]

    def _f(v):
        return f"{v:.3f}" if isinstance(v, (int, float)) else "-"

    def _secs(v):
        """'1:41.230' -> 101.23, so LOSS can be computed against IDEAL."""
        if isinstance(v, (int, float)):
            return float(v)
        try:
            t = str(v or "").strip()
            if ":" in t:
                m, rest = t.split(":", 1)
                return int(m) * 60 + float(rest)
            return float(t) if t else None
        except ValueError:
            return None

    for r in rows[:max_rows]:
        num = str(r.get("num"))
        sec = secs.get(num) or {}
        own = per_driver.get(num) or [None, None, None]
        ideal = round(sum(own), 3) if all(v is not None for v in own) else None
        last = _secs(r.get("last"))
        loss = round(last - ideal, 3) if (ideal and last) else None
        tyre = f"{r.get('tyre')}({r.get('tyre_age')})" if r.get("tyre") else "-"
        out.append(f"{r.get('pos')} | {r.get('code')} | {r.get('gap') or '-'} | "
                   f"{r.get('int') or '-'} | {r.get('last') or '-'} | {tyre} | "
                   f"{r.get('pit')} | {_f(sec.get('s1'))} | {_f(sec.get('s2'))} | "
                   f"{_f(sec.get('s3'))} | {_f(ideal)} | {_f(loss)}")
    return "\n".join(out)


def other_race_block(rid: int) -> str | None:
    """A one-block summary of another race: who won, podium, key stops.

    Not a full frame dump -- enough to answer "geçen yarışta ne oldu" without
    dragging a second 4k-token briefing into the request.
    """
    race = store.get_race(rid)
    if not race:
        return None
    laps = store.race_lap_numbers(rid) or []
    last_lap = max(laps) if laps else None
    fr = store.frame_row(rid, last_lap) if last_lap else None
    if not fr:
        return None
    s = fr.get("snap") or {}
    rows = s.get("rows") or []
    if not rows:
        return None
    missing = fr.get("missing") or []
    out = [f"GEÇMİŞ YARIŞ: {race.get('meeting')} / {race.get('session')} "
           f"({race.get('year')}), son tur {last_lap}, kaynak={fr.get('source')}"
           + (f", eksik alan: {', '.join(missing)}" if missing else ""),
           "POS | DRV | TEAM | GAP | PIT | LAST | EN İYİ TUR"]
    for r in rows[:10]:
        out.append(f"{r.get('pos')} | {r.get('code')} | {r.get('team') or '-'} | "
                   f"{r.get('gap') or '-'} | {r.get('pit')} | {r.get('last') or '-'} | "
                   f"{r.get('best') or '-'}")
    out.append("NOT: bu özet son turdan; tur tur detay, sektör ve strateji için "
               "arşiv arayüzünde o yarışı aç.")
    return "\n".join(out)
