#!/usr/bin/env python3
"""Scope resolution: which sessions a question may see, decided before the model.

Every case here exists because a plausible alternative is wrong in a way that
produces a confident wrong answer rather than an error.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import scope  # noqa: E402


class TokenTest(unittest.TestCase):
    def test_known_shortcut_selects_its_session(self):
        r = scope.resolve("@fp2 uzun stint", owner_kind="FP3")
        self.assertEqual(r["scopes"], ["FP2"])
        self.assertEqual(r["source"], "tokens")
        self.assertEqual(r["cleaned"], "uzun stint")

    def test_two_shortcuts_keep_their_order(self):
        r = scope.resolve("@q @fp1 kıyasla", owner_kind="FP3")
        self.assertEqual(r["scopes"], ["Q", "FP1"])

    def test_duplicate_shortcut_is_mentioned_once(self):
        r = scope.resolve("@fp1 ve @fp1 aynı mı", owner_kind="FP3")
        self.assertEqual(r["scopes"], ["FP1"])

    def test_all_requests_the_summary_not_every_snapshot(self):
        r = scope.resolve("üç antrenman bitti @all favori kim", owner_kind="FP3")
        self.assertTrue(r["want_summary"])
        self.assertEqual(r["scopes"], [])
        self.assertEqual(r["cleaned"], "üç antrenman bitti favori kim")

    def test_p1_is_not_a_shortcut(self):
        """`@p1` must NOT mean Practice 1.

        Everywhere else in this dashboard "P1" means first place. A shortcut that
        silently reads as a session would turn "kim @p1" into a bid for Practice 1
        data -- and more importantly, a user typing it expecting position gets a
        different answer with no warning. It must be reported as unknown.
        """
        r = scope.resolve("@p1 kim önde", owner_kind="RACE")
        self.assertEqual(r["scopes"], ["RACE"], "sahibi seans kullanılmalı")
        self.assertEqual(r["unknown"], ["@p1"])
        self.assertEqual(r["source"], "owner")

    def test_unknown_token_is_reported_not_swallowed(self):
        r = scope.resolve("@fp9 nedir", owner_kind="RACE")
        self.assertIn("@fp9", r["unknown"])
        self.assertEqual(r["scopes"], ["RACE"])

    def test_unknown_token_text_is_left_in_the_question(self):
        r = scope.resolve("@xyz nedir", owner_kind="RACE")
        self.assertIn("@xyz", r["cleaned"], "tanınmayan token metinden silinmemeli")

    def test_no_shortcut_falls_back_to_the_owning_session(self):
        r = scope.resolve("kim hızlı", owner_kind="FP3")
        self.assertEqual(r["scopes"], ["FP3"])
        self.assertEqual(r["source"], "owner")
        self.assertEqual(r["cleaned"], "kim hızlı")

    def test_message_without_tokens_is_byte_identical(self):
        msg = "LEC'in 2. stint'i neden yavaştı?\nDetay ver."
        r = scope.resolve(msg, owner_kind="RACE")
        self.assertEqual(r["cleaned"], msg, "token yoksa metin bozulmamalı")

    def test_email_like_text_is_not_a_token(self):
        # "user@fp1.com" must not be read as a shortcut: a word character before
        # the @ means it is part of a larger token.
        r = scope.resolve("mail user@fp1.com adresine", owner_kind="RACE")
        self.assertEqual(r["scopes"], ["RACE"])
        self.assertEqual(r["unknown"], [])

    def test_case_insensitive(self):
        self.assertEqual(scope.resolve("@FP2 x", "FP1")["scopes"], ["FP2"])
        self.assertEqual(scope.resolve("@All x", "FP1")["want_summary"], True)


class UiScopeTest(unittest.TestCase):
    def test_ui_scope_wins_over_tokens(self):
        """An explicit UI selection is a human decision; a token is a convenience.

        If they disagree the UI wins, because the tokens may be leftovers from a
        previous question in the box.
        """
        r = scope.resolve("@fp2 soru", owner_kind="FP3", requested=["FP1", "Q"])
        self.assertEqual(r["scopes"], ["FP1", "Q"])
        self.assertEqual(r["source"], "ui")

    def test_invalid_ui_entries_are_dropped(self):
        r = scope.resolve("soru", owner_kind="FP3", requested=["FP1", "bogus", 42, None])
        self.assertEqual(r["scopes"], ["FP1"])

    def test_ui_scope_is_capped(self):
        r = scope.resolve("soru", owner_kind="FP3",
                          requested=["FP1"] * 50)
        self.assertLessEqual(len(r["scopes"]), scope.MAX_SCOPES)


class NoteTest(unittest.TestCase):
    def test_scope_note_names_the_sessions(self):
        n = scope.scope_note(["FP1", "Q"])
        self.assertIn("Antrenman 1", n)
        self.assertIn("Sıralama", n)

    def test_scope_note_reports_missing_sessions(self):
        n = scope.scope_note(["FP1"], ["Q"])
        self.assertIn("kayıt yok", n)

    def test_empty_scope_has_no_note(self):
        self.assertEqual(scope.scope_note([]), "")


if __name__ == "__main__":
    unittest.main()


class SessionBlockTest(unittest.TestCase):
    """The block builder must not invent a session or read the wrong one."""

    def test_missing_session_is_reported_not_substituted(self):
        import tempfile, pathlib
        import store, weekend
        old = (store.DATA_DIR, store.DB_PATH)
        tmp = tempfile.TemporaryDirectory()
        store.DATA_DIR = pathlib.Path(tmp.name)
        store.DB_PATH = os.path.join(tmp.name, "s.db")
        store.init_db()
        weekend.clear_cache()
        try:
            rid = store.upsert_race("p1", meeting="X", session="Practice 1", year=2026,
                                    start_date="2026-05-01T12:00:00", meeting_key=5)
            store.put_lap_times(rid, [("1", 1, 80.0), ("1", 2, 80.1), ("1", 3, 80.2)])
            blocks, missing = chat_blocks(rid, ["FP1", "Q"])
            self.assertEqual(missing, ["Q"], "kaydı olmayan seans 'missing' olmalı")
            self.assertEqual(len(blocks), 1)
            self.assertIn("Antrenman", blocks[0])
        finally:
            store.DATA_DIR, store.DB_PATH = old
            weekend.clear_cache()
            tmp.cleanup()


def chat_blocks(rid, kinds):
    import chat_request
    return chat_request._session_blocks(rid, kinds)


class CardStatesReachThePromptTest(unittest.TestCase):
    """The reported bug, end to end in the briefing the model actually reads.

    Asking a chat attached to the Race session "what is qualifying's card status"
    produced `missing` although the card API called it `done`. The fix attaches
    the grid's own states, so the model reads a stored verdict instead of mapping
    "not in this snapshot" onto the nearest of five words.
    """

    def _db(self):
        import pathlib
        import tempfile
        import store
        import weekend
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        old = (store.DATA_DIR, store.DB_PATH)
        store.DATA_DIR = pathlib.Path(tmp.name)
        store.DB_PATH = os.path.join(tmp.name, "s.db")
        self.addCleanup(lambda: setattr(store, "DATA_DIR", old[0]))
        self.addCleanup(lambda: setattr(store, "DB_PATH", old[1]))
        self.addCleanup(weekend.clear_cache)
        store.init_db()
        weekend.clear_cache()

    def _session(self, session, *, key, frames, final_status=None):
        import store
        rid = store.upsert_race(f"{session}-{key}", meeting="Mexico City Grand Prix",
                                official="Mexico City Grand Prix", session=session,
                                year=2026, start_date="2026-10-31T15:00:00",
                                end_date="2026-10-31T15:00:00", meeting_key=key)
        store.put_lap_times(rid, [("1", i, 80.0 + i / 10) for i in range(1, 6)])
        for lap in range(1, frames + 1):
            store.put_frame(rid, lap, {"rows": [{
                "num": "1", "code": "VER", "pos": 1, "gap": "LEADER", "int": "",
                "laps": lap, "last": 80.0, "best": 80.0, "pit": 0,
                "tyre": "SOFT", "tyre_age": lap, "team": "Red Bull"}]})
        if final_status:
            with store.db() as c:
                c.execute("UPDATE races SET final_status=?, last_seen=? WHERE id=?",
                          (final_status, "2026-10-31T16:30:00Z", rid))
        return rid

    def test_captured_qualifying_reaches_the_briefing_as_done(self):
        import analysis
        import chat_request
        import weekend
        self._db()
        self._session("Qualifying", key=31, frames=26, final_status="Ends")
        race_rid = self._session("Race", key=31, frames=3, final_status="Ends")

        resolved = {"scopes": ["RACE"], "want_summary": False, "cleaned": "durum?"}
        snap, _note = chat_request.build_context(race_rid, live_snap=None,
                                                 archived=True, resolved=resolved,
                                                 question="durum?")
        blocks = [b for b in (snap.get("extra_blocks") or []) if "KART DURUMLARI" in b]
        self.assertEqual(len(blocks), 1, "kart durumları bloğu briefinge eklenmeli")
        self.assertIn("Q (Sıralama) = done", blocks[0])

        prompt = analysis.context_text(snap)
        self.assertIn("HAFTA SONU KART DURUMLARI", prompt)
        self.assertIn("Q (Sıralama) = done", prompt)
        self.assertNotIn("Q (Sıralama) = missing", prompt)
        # The card API is the single producer: whatever it says is what the prompt says.
        q = next(c for c in weekend.overview(race_rid)["cards"] if c["kind"] == "Q")
        self.assertIn(f"Q (Sıralama) = {q['state']}", prompt)

