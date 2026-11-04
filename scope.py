#!/usr/bin/env python3
"""Which sessions a question is allowed to see -- resolved before the model runs.

The problem this solves
-----------------------
Each chat panel is attached to ONE session, because a chat belongs to a race row.
But "three practices are done, who is favourite for qualifying and the race?" is
a question about the WEEKEND, and answered from Practice 3's snapshot it comes
out wrong in a way that looks confident.

Two designs were considered and rejected:

  * a master chat with every session's full data in the prompt. Six briefings at
    ~4.4k tokens each is ~26k tokens per question, and the session-kind rules
    (sprint vs sprint qualifying vs practice) start colliding: the exact class of
    bug that produced "practice analysed as a race".
  * an LLM tool loop. Measured elsewhere in this project: 20-30 s and ~13k tokens
    per question, on a model with reasoning disabled that will invent argument
    names it has never seen.

What is implemented instead is the pattern `routing.py` already uses for "an
earlier lap" and "another race": decide deterministically, before the call, and
attach only the blocks the question needs. This module adds the missing piece --
WHICH session blocks.

Resolution order (first hit wins, and the source is reported)
-------------------------------------------------------------
  1. an explicit `scope` list from the UI (chips / checkboxes): no guessing at all
  2. `@shortcuts` typed in the message: explicit intent, cheap to parse
  3. nothing -> the owning session only, i.e. exactly today's behaviour

There is deliberately no fourth step ("infer from the sentence"). The welcome
grid exists so the UI can send scope explicitly, and a fourth step would be the
one place a plausible-but-wrong scope could enter. When the user asks for a
session that does not exist, the answer says so instead of substituting another.
"""
from __future__ import annotations

import re

#: Typed shortcut -> session kind. Three characters minimum and no collisions:
#: `@p1` is deliberately absent, because everywhere else in this dashboard "P1"
#: means first place, so `@p1` would be ambiguous in the most confusing way.
SHORTCUTS = {
    "fp1": "FP1",
    "fp2": "FP2",
    "fp3": "FP3",
    "q": "Q",
    "sq": "SQ",
    "sp": "SP",
    "race": "RACE",
}
#: Weekend summary. `@all` and `@w` both mean it; `all` alone would be a nasty
#: prefix of nothing else, so it is safe.
SUMMARY_SHORTCUTS = ("all", "w", "weekend")

#: A shortcut must start a word and be followed by nothing word-like: "@fp1x" is
#: not a shortcut, it is a typo, and silently treating it as @fp1 would hide it.
_TOKEN = re.compile(r"(?<![\w@])@([A-Za-z0-9_]+)\b")

MAX_SCOPES = 8


def kind_label(kind: str) -> str:
    """Short human label for a kind, for prompts and error messages."""
    import store
    return store.kind_label(kind)


def parse_tokens(text: str) -> tuple[list[str], list[str], str]:
    """Split a message into (kinds, summary_flags, cleaned_text).

    The cleaned text is what gets stored in the transcript and what the model
    reads. Keeping the tokens in the stored message would mean a later turn's
    history looks like a scope request again and drags the same blocks in twice.
    """
    kinds: list[str] = []
    summaries: list[str] = []
    if not text:
        return kinds, summaries, ""

    def _strip(m: re.Match) -> str:
        tok = m.group(1).lower()
        if tok in SHORTCUTS:
            if SHORTCUTS[tok] not in kinds:
                kinds.append(SHORTCUTS[tok])
            return " "
        if tok in SUMMARY_SHORTCUTS:
            if "weekend" not in summaries:
                summaries.append("weekend")
            return " "
        return m.group(0)      # not ours: leave the text untouched

    cleaned = _TOKEN.sub(_strip, text)
    # Collapse the spaces the removals left behind, but never touch newlines: a
    # pasted table in the question must survive.
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned).strip()
    return kinds, summaries, cleaned


def unknown_tokens(text: str) -> list[str]:
    """`@`-words that are neither a shortcut nor a summary token.

    Surfaced to the user rather than swallowed: a silent no-op shortcut is a user
    asking about Practice 2 and getting Practice 3's answer with no clue why.
    """
    out = []
    for m in _TOKEN.finditer(text or ""):
        tok = m.group(1).lower()
        if tok not in SHORTCUTS and tok not in SUMMARY_SHORTCUTS:
            out.append("@" + m.group(1))
    return out


def normalize(requested) -> list[str]:
    """Validate a scope list from the UI. Unknown kinds are dropped, order kept."""
    out: list[str] = []
    for k in requested or []:
        if not isinstance(k, str):
            continue
        k2 = k.strip().upper()
        if k2 in set(SHORTCUTS.values()) and k2 not in out:
            out.append(k2)
    return out[:MAX_SCOPES]


def resolve(text: str, owner_kind: str | None = None,
            requested=None) -> dict:
    """Decide the scope for one question.

    Returns a dict, never raises:
      scopes     list[str]  session kinds whose blocks must be attached, in the
                            order they should be presented (weekend order)
      want_summary  bool    attach the weekend summary block
      cleaned    str        the message with shortcut tokens removed
      unknown    list[str]  `@` words we could not interpret
      source     str        "ui" | "tokens" | "owner"
      note       str        a short explanation to show next to the answer
    """
    kinds_tok, summaries, cleaned = parse_tokens(text or "")
    ui = normalize(requested)

    unknown = unknown_tokens(text or "")
    # A token the UI already placed in scope is not "unknown" to the user; keep
    # the message about genuinely uninterpretable words only.
    if ui:
        unknown = [u for u in unknown if u.lower().lstrip("@").upper() not in ui]

    want_summary = bool(summaries)
    if ui:
        scopes, source = ui, "ui"
    elif kinds_tok:
        scopes, source = kinds_tok, "tokens"
    elif want_summary:
        scopes, source = [], "tokens"
    else:
        scopes = [owner_kind] if owner_kind else []
        source = "owner"

    # A summary request implies every session of the weekend: the summary lives
    # in weekend.py and costs one line per driver, not six snapshots, so this is
    # the cheap path -- not the "put everything in the prompt" path.
    note = ""
    if want_summary:
        note = "hafta sonu özeti istendi"
    elif scopes and source != "owner":
        note = "kapsam: " + ", ".join(kind_label(k) for k in scopes)
    return {"scopes": scopes, "want_summary": want_summary, "cleaned": cleaned,
            "unknown": unknown, "source": source, "note": note}


def scope_note(scopes: list[str], missing: list[str] | None = None) -> str:
    """The line shown at the top of an answer: what data it is actually based on.

    Visible on purpose. The whole reason the scope is resolved deterministically
    is that a wrong scope produces a confident wrong answer; if the user can see
    which sessions were read, an unexpected scope is obvious immediately instead
    of three questions later.
    """
    if not scopes:
        return ""
    bits = ", ".join(kind_label(k) for k in scopes)
    out = "Veri: " + bits
    if missing:
        out += " · kayıt yok: " + ", ".join(kind_label(k) for k in missing)
    return out
