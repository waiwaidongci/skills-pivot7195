import sys
import unittest
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.domain import backtest as bt
from src.domain import batches
from test_backtest import row


@dataclass
class Candidate:
    id: int
    rows: list
    created_at: datetime


def batch(identifier, created_at, period="2026-10", learners=1):
    rows = [row(f"{identifier}-{index}", f"{period}-06", "社区转介", "养老照护", False)
            for index in range(learners)]
    return Candidate(identifier, rows, created_at)


class BatchSelectionTests(unittest.TestCase):
    def test_prediction_picks_last_before_month_end(self):
        candidates = [
            batch(1, datetime(2026, 10, 10)),
            batch(2, datetime(2026, 10, 25)),
            batch(3, datetime(2026, 10, 31, 16)),   # 恰在次月一日北京零点，属于下月入库
            batch(4, datetime(2026, 10, 30)),
        ]
        self.assertEqual(batches.prediction_dataset(candidates, "2026-10").id, 4)

    def test_prediction_ignores_batches_without_period_rows(self):
        candidates = [
            batch(1, datetime(2026, 10, 20)),
            Candidate(2, [row("Z", "2026-09-06", "社区转介", "养老照护", False)], datetime(2026, 10, 28)),
        ]
        self.assertEqual(batches.prediction_dataset(candidates, "2026-10").id, 1)

    def test_actual_uses_batch_at_maturity_not_later_backfill(self):
        candidates = [
            batch(1, datetime(2026, 10, 21)),
            batch(2, datetime(2026, 11, 20)),       # 满 30 天前
            batch(3, datetime(2026, 12, 5)),        # 之后补传，不能进入实绩
        ]
        chosen = batches.actual_dataset(candidates, "2026-10")
        self.assertEqual(chosen.id, 2)

    def test_actual_tie_broken_by_id(self):
        stamp = bt.maturity_instant("2026-10")
        candidates = [batch(5, stamp), batch(7, stamp)]
        self.assertEqual(batches.actual_dataset(candidates, "2026-10").id, 7)

    def test_actual_cutoff_is_inclusive(self):
        stamp = bt.maturity_instant("2026-10")
        candidates = [batch(1, stamp)]
        self.assertEqual(batches.actual_dataset(candidates, "2026-10").id, 1)

    def test_prediction_cutoff_is_exclusive(self):
        stamp = bt.prediction_cutoff("2026-10")
        candidates = [batch(1, stamp)]
        self.assertIsNone(batches.prediction_dataset(candidates, "2026-10"))

    def test_no_candidate_returns_none(self):
        self.assertIsNone(batches.prediction_dataset([], "2026-10"))


if __name__ == "__main__":
    unittest.main()
