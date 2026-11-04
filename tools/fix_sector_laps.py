#!/usr/bin/env python3
"""Repair sector rows stored against the wrong lap number.

The live writer used `NumberOfLaps` to decide which lap a set of sector times
belonged to. Measured against OpenF1 on Mexico City qualifying, that was off by
one for 275 of 286 laps: the sectors of lap N were written as lap N+1.

That corrupts every number derived from sectors -- IDEAL, LOSS, and any "where did
he lose the lap" answer -- for practice and qualifying sessions captured live.
Races were unaffected (a race's lap counter agrees with the feed), and every
BACKFILLED session is clean because the mirror publishes sectors and lap times on
the same record.

This tool fixes the existing rows using evidence already in the database: for each
sector row, find the lap whose recorded lap time equals s1+s2+s3. That is a
verification, not a guess -- a row that matches no lap time is reported and left
alone, because moving it on a hunch is how the original bug happened.

    .venv/bin/python tools/fix_sector_laps.py --dry-run
    .venv/bin/python tools/fix_sector_laps.py
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import store  # noqa: E402

TOL = 0.05          # sector sum vs lap time, seconds (both are 3-decimal strings)
WINDOW = range(-2, 3)


def audit(rid: int) -> dict:
    secs = store.all_sectors(rid)
    laps = store.all_lap_times(rid)
    fixes, unfixable, ok = [], [], 0
    for num, per in secs.items():
        for lap, tri in per.items():
            if not all(v is not None for v in tri):
                continue
            total = sum(tri)
            lt = laps.get(num) or {}
            if lap in lt and abs(total - lt[lap]) <= TOL:
                ok += 1
                continue
            target = None
            for off in WINDOW:
                cand = lap + off
                v = lt.get(cand)
                if v is not None and abs(total - v) <= TOL:
                    target = cand
                    break
            if target is None:
                unfixable.append((num, lap, round(total, 3)))
            else:
                fixes.append((num, lap, target))
    return {"ok": ok, "fixes": fixes, "unfixable": unfixable}


def apply(rid: int, fixes: list, dry_run: bool) -> int:
    """Move rows. Two-phase: write the destinations first, then drop the sources.

    A single UPDATE per row can collide with an existing row at the destination
    (the correct lap may already hold a partial, sector-by-sector write from
    put_sectors' merge). Writing destinations first and deleting sources second
    means a collision ends up merged rather than lost.
    """
    if dry_run or not fixes:
        return 0
    moved = 0
    with store.db() as c:
        for num, src, dst in fixes:
            row = c.execute("SELECT s1,s2,s3,seg1,seg2,seg3 FROM sectors "
                            "WHERE race_id=? AND num=? AND lap=?", (rid, num, src)).fetchone()
            if row is None:
                continue
            c.execute(
                "INSERT INTO sectors(race_id,num,lap,s1,s2,s3,seg1,seg2,seg3,source) "
                "VALUES(?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(race_id,num,lap) DO UPDATE SET "
                "  s1=COALESCE(sectors.s1,excluded.s1), s2=COALESCE(sectors.s2,excluded.s2), "
                "  s3=COALESCE(sectors.s3,excluded.s3), seg1=COALESCE(sectors.seg1,excluded.seg1), "
                "  seg2=COALESCE(sectors.seg2,excluded.seg2), seg3=COALESCE(sectors.seg3,excluded.seg3)",
                (rid, num, dst, row["s1"], row["s2"], row["s3"],
                 row["seg1"], row["seg2"], row["seg3"], "live"))
            moved += 1
        for num, src, dst in fixes:
            c.execute("DELETE FROM sectors WHERE race_id=? AND num=? AND lap=?",
                      (rid, num, src))
    return moved


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--race-id", type=int, action="append")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    store.init_db()
    races = store.list_races()
    if args.race_id:
        want = set(args.race_id)
        races = [r for r in races if r["id"] in want]

    total_fixed = total_bad = 0
    for r in races:
        rep = audit(r["id"])
        if not rep["fixes"] and not rep["unfixable"]:
            continue
        label = f"#{r['id']:3} {str(r.get('meeting'))[:24]:24} {str(r.get('session'))[:18]:18}"
        print(f"{label} doğru={rep['ok']:5} taşınacak={len(rep['fixes']):4} "
              f"onarılamaz={len(rep['unfixable']):4}")
        if rep["unfixable"][:3]:
            print(f"      onarılamaz örnek: {rep['unfixable'][:3]}")
        moved = apply(r["id"], rep["fixes"], args.dry_run)
        total_fixed += moved
        total_bad += len(rep["unfixable"])

    print(f"\n{'DRY-RUN: ' if args.dry_run else ''}taşınan satır={total_fixed}, "
          f"onarılamaz={total_bad}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
