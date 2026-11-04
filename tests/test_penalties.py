#!/usr/bin/env python3
"""Penalties parsed from race control, and the two lines that must not be confused.

Every case here comes from a real Mexico City 2026 race-control stream. The
distinctions are the whole point:

  * a TIME PENALTY is a sanction; "UNDER INVESTIGATION" is not. Treating a review
    as a penalty accuses a driver of something the stewards have not decided.
  * "PENALTY SERVED" repeats the original penalty, so a naive parser counts the
    same sanction twice and reports +10s where +5s was given.
  * an incident can name two cars ("CARS 44 (HAM) AND 16 (LEC)"), and a
    singular-only pattern dropped the whole line as unparseable -- which means
    nobody saw it on screen.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import penalties  # noqa: E402


def msg(text, lap=1, cat="Other"):
    return {"lap": lap, "cat": cat, "msg": text}


class TimePenaltyTest(unittest.TestCase):
    def test_five_second_penalty_is_read_with_its_reason(self):
        st = penalties.parse([msg(
            "FIA STEWARDS: 5 SECOND TIME PENALTY FOR CAR 16 (LEC) - "
            "LEAVING THE TRACK AND GAINING AN ADVANTAGE (14:03:57)", lap=12)])
        e = st["by_car"]["16"]
        self.assertEqual(e["total_secs"], 5)
        self.assertEqual(e["code"], "LEC")
        self.assertTrue(e["has_penalty"])
        self.assertFalse(e["served"])
        self.assertIn("LEAVING THE TRACK", e["items"][0]["reason"])

    def test_two_penalties_on_one_car_add_up(self):
        st = penalties.parse([
            msg("FIA STEWARDS: 5 SECOND TIME PENALTY FOR CAR 16 (LEC) - A", lap=12),
            msg("FIA STEWARDS: 10 SECOND TIME PENALTY FOR CAR 16 (LEC) - B", lap=30),
        ])
        self.assertEqual(st["by_car"]["16"]["total_secs"], 15)
        self.assertEqual(len(st["by_car"]["16"]["items"]), 2)

    def test_served_notice_does_not_double_count(self):
        """"PENALTY SERVED - 5 SECOND ..." repeats the sanction, so it must not
        create a second one. Measured failure mode: this turned +5s into +10s."""
        st = penalties.parse([
            msg("FIA STEWARDS: 5 SECOND TIME PENALTY FOR CAR 16 (LEC) - X", lap=12),
            msg("FIA STEWARDS: PENALTY SERVED - 5 SECOND TIME PENALTY FOR CAR 16 (LEC)", lap=20),
        ])
        e = st["by_car"]["16"]
        self.assertEqual(e["total_secs"], 5, "çekildi bildirimi ikinci ceza sayılmamalı")
        self.assertTrue(e["served"])

    def test_pending_counts_only_unserved_penalties(self):
        st = penalties.parse([
            msg("FIA STEWARDS: 5 SECOND TIME PENALTY FOR CAR 16 (LEC) - X", lap=12),
            msg("FIA STEWARDS: PENALTY SERVED - 5 SECOND TIME PENALTY FOR CAR 16 (LEC)", lap=20),
            msg("FIA STEWARDS: 10 SECOND TIME PENALTY FOR CAR 63 (RUS) - Y", lap=13),
        ])
        self.assertEqual(st["pending"], 1)


class ReviewIsNotAPenaltyTest(unittest.TestCase):
    def test_investigation_is_marked_as_review_with_no_seconds(self):
        st = penalties.parse([msg(
            "FIA STEWARDS: TURN 6 INCIDENT INVOLVING CAR 63 (RUS) UNDER INVESTIGATION - "
            "CAUSING A COLLISION (14:04:08)", lap=11)])
        e = st["by_car"]["63"]
        self.assertFalse(e["has_penalty"], "inceleme ceza değil")
        self.assertEqual(e["total_secs"], 0)
        self.assertEqual(e["label"], "inceleme")

    def test_review_that_becomes_a_penalty_is_both(self):
        st = penalties.parse([
            msg("FIA STEWARDS: TURN 6 INCIDENT INVOLVING CAR 63 (RUS) UNDER INVESTIGATION", lap=11),
            msg("FIA STEWARDS: 10 SECOND TIME PENALTY FOR CAR 63 (RUS) - CAUSING A COLLISION", lap=13),
        ])
        e = st["by_car"]["63"]
        self.assertTrue(e["has_penalty"])
        self.assertEqual(e["total_secs"], 10)
        self.assertIn("+10s", e["label"])

    def test_multi_car_incident_marks_every_car(self):
        st = penalties.parse([msg(
            "FIA STEWARDS: TURN 4 INCIDENT INVOLVING CARS 44 (HAM) AND 16 (LEC) "
            "UNDER INVESTIGATION (14:03:57)", lap=4)])
        self.assertEqual(sorted(st["by_car"]), ["16", "44"])
        for car in ("16", "44"):
            self.assertFalse(st["by_car"][car]["has_penalty"])
            self.assertEqual(st["by_car"][car]["review"], "under investigation")
        self.assertEqual(st["unparsed"], [], "çoklu araç satırı okunamamış sayılmamalı")


class OtherSanctionsTest(unittest.TestCase):
    def test_drive_through_and_reprimand_are_penalties_without_seconds(self):
        st = penalties.parse([
            msg("FIA STEWARDS: DRIVE THROUGH PENALTY FOR CAR 4 (NOR) - X", lap=5),
            msg("FIA STEWARDS: REPRIMAND FOR CAR 11 (PER) - Y", lap=6),
        ])
        self.assertTrue(st["by_car"]["4"]["has_penalty"])
        self.assertEqual(st["by_car"]["4"]["total_secs"], 0)
        self.assertIn("DT", st["by_car"]["4"]["label"])
        self.assertIn("kınama", st["by_car"]["11"]["label"])

    def test_unrelated_flags_are_ignored_entirely(self):
        st = penalties.parse([
            msg("YELLOW IN TRACK SECTOR 4", lap=7, cat="Flag"),
            msg("TRACK CLEAR", lap=8, cat="Flag"),
            msg("VSC ENDING", lap=8, cat="SafetyCar"),
        ])
        self.assertEqual(st["by_car"], {})
        self.assertEqual(st["unparsed"], [])

    def test_unknown_stewards_wording_is_reported_not_dropped(self):
        """A new wording must appear as a gap, not vanish.

        This is the failure mode that matters: a dropped penalty looks exactly
        like no penalty at all.
        """
        st = penalties.parse([msg("FIA STEWARDS: SOMETHING NEW FOR CAR 5 (BOT)", lap=5)])
        self.assertEqual(len(st["unparsed"]), 1)
        self.assertIn("SOMETHING NEW", st["unparsed"][0])


class SummaryTest(unittest.TestCase):
    def test_summary_states_served_and_pending_and_forbids_confusing_review(self):
        st = penalties.parse([
            msg("FIA STEWARDS: 5 SECOND TIME PENALTY FOR CAR 16 (LEC) - X", lap=12),
            msg("FIA STEWARDS: PENALTY SERVED - 5 SECOND TIME PENALTY FOR CAR 16 (LEC)", lap=20),
            msg("FIA STEWARDS: 10 SECOND TIME PENALTY FOR CAR 63 (RUS) - Y", lap=13),
            msg("FIA STEWARDS: INCIDENT INVOLVING CAR 44 (HAM) UNDER INVESTIGATION", lap=14),
        ])
        s = penalties.summary(st)
        self.assertIn("BEKLİYOR", s)
        self.assertIn("çekildi", s)
        self.assertIn("ceza DEĞİL", s)

    def test_empty_summary_says_no_record(self):
        self.assertIn("kaydı yok", penalties.summary({"by_car": {}, "unparsed": []}))


if __name__ == "__main__":
    unittest.main()


class ClearedReviewTest(unittest.TestCase):
    """A closed review must not keep a driver marked as under investigation.

    Real line: "INCIDENT INVOLVING CAR 10 (GAS) REVIEWED NO FURTHER INVESTIGATION
    - LEAVING PIT EXIT ON RED LIGHT". It contains the word INVESTIGATION, so the
    review pattern read it as an OPEN review -- and the driver stayed flagged
    after being cleared. That is the same class of error as calling a review a
    penalty, just in the other direction.
    """

    def test_no_further_investigation_closes_the_review(self):
        st = penalties.parse([msg(
            "FIA STEWARDS: INCIDENT INVOLVING CAR 10 (GAS) REVIEWED NO FURTHER "
            "INVESTIGATION - LEAVING PIT EXIT ON RED LIGHT (15:06:01)", lap=40)])
        e = st["by_car"]["10"]
        self.assertEqual(e["review"], "kapandı")
        self.assertFalse(e["has_penalty"])
        self.assertEqual(e["label"], "", "temiz sürücüye rozet basılmamalı")
        self.assertEqual(st["unparsed"], [])

    def test_no_further_action_also_closes(self):
        st = penalties.parse([msg(
            "FIA STEWARDS: TURN 6 INCIDENT INVOLVING CAR 63 (RUS) NO FURTHER ACTION",
            lap=20)])
        self.assertEqual(st["by_car"]["63"]["review"], "kapandı")

    def test_summary_says_the_review_closed(self):
        st = penalties.parse([msg(
            "FIA STEWARDS: INCIDENT INVOLVING CAR 10 (GAS) REVIEWED NO FURTHER "
            "INVESTIGATION", lap=40)])
        self.assertIn("inceleme KAPANDI", penalties.summary(st))
