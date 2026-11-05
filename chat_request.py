#!/usr/bin/env python3
"""One chat request, one implementation, two surfaces.

Why this module exists
----------------------
`POST /api/chat` was written twice -- once in `server.py` (live) and once in
`archive.py` (archive) -- and the two copies had already started to differ
(history limits, `_route_extra`, archived flags). Adding scope on top of a
duplicated handler guarantees the live page and the archive page disagree about
what "scope" means, and this project has paid for that exact mistake before: the
markdown formatter lived only in `index.html`, so the archive printed literal
asterisks for months and nobody noticed because the live page looked right.

So the handler body lives here, once. Each surface keeps its routing and its
`CURRENT_RACE_ID`, and calls `handle()`.

What a request carries
----------------------
    message   the question (may contain @shortcuts)
    race      "live" | archived race id | "weekend:<key>"
    scope     optional list of session kinds chosen in the UI
    lap       optional archived lap
    reasoning optional per-request thinking level

What comes back
---------------
    answer, meta (cost + scope), race_id, archived, saved, scope_note

The scope note is part of the ANSWER, not only of the meta: "Veri: Antrenman 1,
Sıralama" at the top of a reply is what makes a wrong scope obvious immediately
instead of three questions later.
"""
from __future__ import annotations

import analysis
import scope as scope_mod
import store
import weekend


def _owner_kind(rid: int | None) -> str | None:
    if not rid:
        return None
    try:
        row = store.get_race(int(rid))
    except Exception:
        return None
    if not row:
        return None
    return store.session_kind(row.get("session"))


def _first_held(rid: int | None, kinds: list[str]) -> int | None:
    """Race id of the first requested session we actually hold lap data for."""
    if not rid:
        return None
    wk = store.weekend_of(int(rid))
    if not wk:
        return None
    by_kind = {}
    for s in wk.get("sessions") or []:
        k = store.session_kind(s.get("session"))
        if k and k not in by_kind:
            by_kind[k] = s
    for kind in kinds:
        row = by_kind.get(kind)
        if not row:
            continue
        try:
            if store.all_lap_times(int(row["id"])):
                return int(row["id"])
        except Exception:
            continue
    return None


def _session_blocks(rid: int, kinds: list[str], *, exclude: list[str] | None = None) -> tuple[list[str], list[str]]:
    """Blocks for the requested session kinds of this race's weekend.

    Returns (blocks, missing). A requested kind with no captured row is reported
    in `missing` -- never substituted by another session, and never silently
    dropped: "I have no record of Qualifying" is a useful answer, "nothing
    happened" is not.
    """
    wk = store.weekend_of(int(rid))
    if not wk:
        return [], list(kinds)
    by_kind = {}
    for s in wk.get("sessions") or []:
        k = store.session_kind(s.get("session"))
        if k and k not in by_kind:
            by_kind[k] = s

    blocks: list[str] = []
    missing: list[str] = []
    for kind in kinds:
        if exclude and kind in exclude:
            continue
        row = by_kind.get(kind)
        if not row:
            missing.append(kind)
            continue
        try:
            has_laps = bool(store.all_lap_times(int(row["id"])))
        except Exception:
            has_laps = False
        if not has_laps:
            missing.append(kind)
            continue
        # One session's own briefing would be ~4.4k tokens. What a cross-session
        # question needs is the comparison material, which is exactly the weekend
        # summary's per-session cells -- so a single-session block is the summary
        # restricted to that session, not a second full snapshot. The full
        # snapshot of the OWNING session is already the base briefing.
        blocks.append(_one_session_block(int(row["id"]), kind))
    return blocks, missing


def _one_session_block(rid: int, kind: str) -> str:
    """A compact block for one session of the weekend (never a full snapshot)."""
    row = store.get_race(rid) or {}
    stype = analysis.points.session_type(row.get("session"))
    head = (f"— SEANS BLOĞU: {store.kind_label(kind)} "
            f"({row.get('session') or kind}, {row.get('start_date') or ''}"
            f"{', ' + str(row.get('year')) if row.get('year') else ''}) "
            f"— seans_tipi={stype['label']}")
    body = weekend.session_cells(rid, kind)
    note = ""
    if stype.get("is_practice"):
        note = ("NOT: antrenmanda yarış mesafesi, grid ve puan yoktur; tempo "
                "'antrenman / uzun stint temposu'dur, 'yarış temposu' değil.")
    elif stype.get("is_qualifying"):
        note = ("NOT: bu bir sıralama seansıdır; puan verilmez, grid belirlenir, "
                "stint/yarış temposu analizi yapılmaz.")
    return "\n".join([head, body] + ([note] if note else []))


def build_context(rid: int | None, *, live_snap: dict | None, archived: bool,
                  resolved: dict, question: str, lap=None) -> tuple[dict, str]:
    """The snapshot dict the model sees, plus a scope note for the reply.

    Three shapes:
      * weekend summary / multi-session -> a synthetic snapshot whose base is the
        owning session (so POS/session rules still come from a real frame) with
        the extra blocks attached.
      * owner only -> the plain snapshot, byte for byte today's behaviour.
      * archived -> the stored frame at `lap`.
    """
    scopes = resolved.get("scopes") or []
    want_summary = resolved.get("want_summary")
    owner = _owner_kind(rid)

    # When the user names sessions that do not include the one this chat belongs
    # to, the OWNER must stop being the subject of the answer. Measured: asking
    # "@fp1 @fp2 tempo kıyasla" inside the US GP race chat produced a race-pace
    # answer with the practice blocks attached -- the model read the base frame's
    # POS table as the question's subject, because it came first.
    #
    # So the base becomes the FIRST named session that we actually hold. The
    # owner's frame is only kept when it is in scope (or when nothing is scoped).
    base_rid = int(rid) if rid else None
    if scopes and owner not in scopes:
        picked = _first_held(rid, scopes)
        if picked:
            base_rid = picked
            # Nothing is "live" about a sibling session: an archived read keeps
            # the frame semantics honest (no countdown, no live clock).
            archived = True

    snap = None
    if archived and base_rid:
        snap = analysis.archived_snapshot(int(base_rid), lap if base_rid == rid else None)
    if snap is None and live_snap is not None and base_rid == rid:
        snap = live_snap
    if snap is None and base_rid:
        snap = analysis.archived_snapshot(int(base_rid), lap if base_rid == rid else None)

    extra: list[str] = []
    missing: list[str] = []

    if want_summary and rid:
        try:
            extra.append(weekend.summary_block(int(rid)))
        except Exception as e:
            extra.append(f"HAFTA SONU ÖZETİ üretilemedi: {type(e).__name__}: {e}"[:300])

    # The OTHER sessions of this weekend, as the grid already classifies them.
    # Without this a question about a sibling session ("was qualifying captured?")
    # had no stored verdict in the prompt, and the model mapped "not in this
    # snapshot" onto `missing` while the card API said `done`. Always attached,
    # not only for summary questions: "did qualifying get captured" is exactly
    # the question that arrives at a chat attached to the race session.
    if rid:
        try:
            states = weekend.card_states_block(int(rid))
            if states:
                extra.append(states)
        except Exception as e:
            extra.append(f"HAFTA SONU KART DURUMLARI üretilemedi: "
                         f"{type(e).__name__}: {e}"[:300])

    if scopes:
        blocks, missing = _session_blocks(int(rid) if rid else 0, scopes,
                                          exclude=[owner] if owner in scopes else None)
        extra.extend(blocks)
    if not snap:
        snap = {}

    if extra:
        snap = dict(snap)
        snap["extra_blocks"] = list(snap.get("extra_blocks") or []) + extra
        # Say why there is more than one session in the prompt. Without this the
        # model sees several "SESSION:"-like blocks and reads the first one as
        # the subject of the question.
        snap["multi_session"] = True

    note = ""
    if missing or scopes or want_summary:
        note = scope_mod.scope_note(scopes, missing)
        if want_summary:
            note = ("Veri: hafta sonu özeti" + (f" + {scope_mod.scope_note(scopes, missing)[6:]}" if scopes else ""))
    return snap, note


async def handle_stream(*, message: str, race_id: int | None, live_snap: dict | None,
                        archived: bool, scope=None, lap=None, reasoning=None,
                        route_extra=None):
    """Like handle(), but yields events as the answer is written.

    Same preparation (scope, context, history, stored question); only the LLM call
    differs. Everything after the call -- saving the answer, the meta that the
    panel shows -- is written once here so the streaming and non-streaming paths
    cannot leave the transcript in different states.

    Yields dicts shaped for the SSE endpoint in server.py / archive.py.
    """
    resolved = scope_mod.resolve(message, _owner_kind(race_id), scope)
    q = resolved["cleaned"] or (message or "").strip()
    if not q:
        yield {"type": "error", "message": "Boş soru."}
        return

    snap, note = build_context(race_id, live_snap=live_snap, archived=archived,
                               resolved=resolved, question=q, lap=lap)
    if route_extra is not None and snap and not archived:
        try:
            route_extra(snap, race_id, q)
        except Exception as e:
            print("routing eklentisi başarısız:", e)

    try:
        learned = analysis.learn_prefs(q)
    except Exception:
        learned = None

    hist = []
    if race_id:
        try:
            hist = store.chat_history(int(race_id), 24)
        except Exception:
            hist = []
        try:
            store.chat_add(int(race_id), "user", q[:4000])
        except Exception:
            race_id = None

    yield {"type": "scope", "note": note, "scope": resolved["scopes"],
           "unknown": resolved["unknown"], "summary": bool(resolved.get("want_summary"))}

    answer = ""
    meta: dict = {}
    failed = False
    async for ev in analysis.llm_chat_stream(hist, q, reasoning, snap, archived, race_id):
        if ev.get("type") == "error":
            # Do NOT fall through to `done`: a failed call must leave no answer in
            # the transcript. Storing "(boş cevap)" makes later turns believe the
            # assistant answered, and the user sees an empty reply instead of the
            # error that actually happened.
            failed = True
            yield ev
            continue
        if ev.get("type") == "done":
            answer, meta = ev.get("answer") or "", ev.get("meta") or {}
            continue
        yield ev
    if failed or not answer.strip() or answer == "(boş cevap)":
        yield {"type": "error", "message": answer or "Boş cevap döndü.",
               "race_id": race_id, "archived": archived}
        return

    if learned:
        meta["learned"] = analysis.prefs_summary()
    meta["prefs"] = analysis.prefs_summary()
    meta["scope"] = resolved["scopes"]
    meta["scope_source"] = resolved["source"]
    meta["summary"] = bool(resolved.get("want_summary"))
    if note:
        meta["scope_note"] = note
    if resolved["unknown"]:
        meta["scope_unknown"] = resolved["unknown"]

    if race_id:
        try:
            store.chat_add(int(race_id), "assistant", answer,
                           analysis._meta_text(meta),
                           isinstance(answer, str) and answer.startswith("LLM"))
        except Exception:
            pass
    yield {"type": "done", "answer": answer, "meta": meta, "race_id": race_id,
           "archived": archived, "saved": bool(race_id), "scope_note": note}


async def handle(*, message: str, race_id: int | None, live_snap: dict | None,
                 archived: bool, scope=None, lap=None, reasoning=None,
                 route_extra=None) -> dict:
    """Run one question end to end. Both surfaces call this and nothing else.

    `route_extra` is the surface's own deterministic pre-routing hook (today:
    `server._route_extra`, which attaches an earlier lap or another race). Passed
    in rather than imported so the archive -- which has no live state -- does not
    pull the live writer's module in.
    """
    resolved = scope_mod.resolve(message, _owner_kind(race_id), scope)
    q = resolved["cleaned"] or (message or "").strip()
    if not q:
        return {"answer": "Boş soru.", "meta": {}, "race_id": race_id,
                "archived": archived, "saved": False, "scope_note": ""}

    snap, note = build_context(race_id, live_snap=live_snap, archived=archived,
                               resolved=resolved, question=q, lap=lap)
    if route_extra is not None and snap and not archived:
        try:
            route_extra(snap, race_id, q)
        except Exception as e:
            print("routing eklentisi başarısız:", e)

    # Learn before answering: a preference stated in this message must shape the
    # answer the user is waiting for, not the next one.
    try:
        learned = analysis.learn_prefs(q)
    except Exception:
        learned = None

    hist = []
    if race_id:
        try:
            hist = store.chat_history(int(race_id), 24)
        except Exception:
            hist = []
        try:
            # The CLEANED question is stored: keeping "@fp2" in the transcript
            # would make a later turn's history look like a scope request and
            # attach the same blocks twice.
            store.chat_add(int(race_id), "user", q[:4000])
        except Exception:
            race_id = None

    ans, meta = await analysis.llm_chat(hist, q, reasoning, snap, archived, race_id)
    if learned:
        meta["learned"] = analysis.prefs_summary()
    meta["prefs"] = analysis.prefs_summary()
    meta["scope"] = resolved["scopes"]
    meta["scope_source"] = resolved["source"]
    meta["summary"] = bool(resolved.get("want_summary"))
    if note:
        meta["scope_note"] = note
    if resolved["unknown"]:
        meta["scope_unknown"] = resolved["unknown"]

    if race_id:
        try:
            store.chat_add(int(race_id), "assistant", ans, analysis._meta_text(meta),
                           isinstance(ans, str) and ans.startswith("LLM"))
        except Exception:
            pass
    return {"answer": ans, "meta": meta, "race_id": race_id, "archived": archived,
            "saved": bool(race_id), "scope_note": note}
