import sys
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.domain import backtest as bt


def row(learner, day, channel, program, enrolled):
    return {"learner_id": learner, "intake_date": day, "channel": channel,
            "program": program, "enrolled": enrolled}


def sample_rows():
    rows = []
    # 2026-08 与 2026-09 两个成熟样本月，社区转介 × 养老照护：8 月 5 人 3 确认，9 月 3 人 3 确认。
    for learner in ("A1", "A2", "A3", "A4", "A5"):
        rows.append(row(learner, "2026-08-05", "社区转介", "养老照护", False))
    for learner in ("A1", "A2", "A3"):
        rows.append(row(learner, "2026-08-20", "社区转介", "养老照护", True))
    for learner in ("B1", "B2", "B3"):
        rows.append(row(learner, "2026-09-05", "社区转介", "养老照护", False))
    for learner in ("B1", "B2", "B3"):
        rows.append(row(learner, "2026-09-18", "公众号", "养老照护", True))
    # 10 月目标月：社区转介 × 养老照护 4 人，公众号 × 电工基础 2 人（无成熟样本）。
    for learner in ("C1", "C2", "C3", "C4"):
        rows.append(row(learner, "2026-10-06", "社区转介", "养老照护", False))
    for learner in ("D1", "D2"):
        rows.append(row(learner, "2026-10-06", "公众号", "电工基础", False))
    return rows


class PredictionTests(unittest.TestCase):
    def test_cohort_and_pooled_rate(self):
        result = bt.build_prediction(sample_rows(), "2026-10")
        self.assertEqual(result["cohort_size"], 6)
        cells = {(c["channel"], c["program"]): c for c in result["cells"]}
        care = cells["社区转介", "养老照护"]
        # 8 月 3/5 + 9 月 3/3 合并 => 6/8 = 0.75；当月 4 人 => 3.0
        self.assertEqual(care["sample_enrolled"], 6)
        self.assertEqual(care["sample_intakes"], 8)
        self.assertEqual(care["rate"], 0.75)
        self.assertEqual(care["estimated_enrolled"], 3.0)
        self.assertEqual(result["estimated_enrolled"], 3.0)
        self.assertEqual(result["covered_cohort"], 4)
        self.assertEqual(result["missing_sample_cohort"], 2)

    def test_missing_sample_is_flagged_not_zeroed(self):
        result = bt.build_prediction(sample_rows(), "2026-10")
        cells = {(c["channel"], c["program"]): c for c in result["cells"]}
        electric = cells["公众号", "电工基础"]
        self.assertIsNone(electric["estimated_enrolled"])
        self.assertEqual(electric["flag"], bt.MISSING_SAMPLE)
        self.assertTrue(any(bt.MISSING_SAMPLE in flag for flag in result["flags"]))
        # 缺样本的 2 人不计入预估合计
        self.assertEqual(result["estimated_enrolled"], 3.0)

    def test_immature_month_excluded(self):
        rows = sample_rows() + [
            row("E1", "2026-10-06", "社区转介", "养老照护", False),
            # 同为目标月 10 月，不进入样本；11 月更晚也不进入。
            row("E9", "2026-11-06", "社区转介", "养老照护", False),
        ]
        # 用目标月末作为 as_of 时，10 月尚未满 30 天，绝不能当样本。
        result = bt.build_prediction(rows, "2026-10", as_of=bt.prediction_cutoff("2026-10"))
        self.assertNotIn("2026-10", result["sample_months"])

    def test_month_not_mature_at_cutoff_is_skipped(self):
        rows = [
            row("F1", "2026-09-06", "社区转介", "养老照护", False),
            row("F1", "2026-09-28", "社区转介", "养老照护", True),
            row("G1", "2026-10-06", "社区转介", "养老照护", False),
        ]
        # 2026-09 要到 2026-10-30 才满 30 天；以 10-15 为观察点，9 月还不算样本。
        early = bt.build_prediction(rows, "2026-10", as_of=datetime(2026, 10, 15))
        self.assertEqual(early["sample_months"], [])
        self.assertIsNone(early["cells"][0]["estimated_enrolled"])
        # 到 10-31 已满 30 天，9 月可以进入样本。
        late = bt.build_prediction(rows, "2026-10", as_of=datetime(2026, 10, 31))
        self.assertEqual(late["sample_months"], ["2026-09"])
        self.assertEqual(late["cells"][0]["estimated_enrolled"], 1.0)

    def test_same_channel_different_program_pooled_separately(self):
        rows = [
            row("H1", "2026-09-06", "社区转介", "养老照护", False),
            row("H1", "2026-09-20", "社区转介", "养老照护", True),
            row("H2", "2026-09-06", "社区转介", "烘焙实训", False),
            row("J1", "2026-10-06", "社区转介", "养老照护", False),
            row("J2", "2026-10-06", "社区转介", "烘焙实训", False),
        ]
        result = bt.build_prediction(rows, "2026-10")
        cells = {(c["channel"], c["program"]): c for c in result["cells"]}
        self.assertEqual(cells["社区转介", "养老照护"]["rate"], 1.0)
        self.assertEqual(cells["社区转介", "烘焙实训"]["rate"], 0.0)
        self.assertEqual(result["estimated_enrolled"], 1.0)

    def test_cross_channel_confirmation_counts_for_assignment(self):
        rows = [
            row("K1", "2026-09-06", "社区转介", "养老照护", False),
            row("K1", "2026-09-20", "公众号", "养老照护", True),
            row("L1", "2026-10-06", "社区转介", "养老照护", False),
        ]
        result = bt.build_prediction(rows, "2026-10")
        cell = result["cells"][0]
        self.assertEqual(cell["rate"], 1.0)
        self.assertEqual(cell["estimated_enrolled"], 1.0)

    def test_empty_period_flags_missing_cohort(self):
        result = bt.build_prediction(
            [row("M1", "2026-09-06", "社区转介", "养老照护", False)], "2026-10")
        self.assertEqual(result["cohort_size"], 0)
        self.assertTrue(any(bt.MISSING_COHORT in flag for flag in result["flags"]))

    def test_missing_batch_placeholder(self):
        result = bt.missing_prediction("2026-10", [f"{bt.MISSING_BATCH}：没有含 2026-10 的批次入库。"])
        self.assertTrue(result["missing_batch"])
        self.assertEqual(result["cohort_size"], 0)
        self.assertTrue(result["flags"])


class ActualTests(unittest.TestCase):
    def setUp(self):
        self.prediction = bt.build_prediction(sample_rows(), "2026-10")

    def test_actual_only_checks_cohort(self):
        actual_rows = sample_rows() + [
            row("C1", "2026-10-25", "社区转介", "养老照护", True),
            row("C2", "2026-10-26", "社区转介", "养老照护", True),
            # 新学员不计入分母，也不计入实绩。
            row("Z9", "2026-10-27", "社区转介", "养老照护", True),
        ]
        actual = bt.build_actual(self.prediction, actual_rows)
        cells = {(c["channel"], c["program"]): c for c in actual["cells"]}
        care = cells["社区转介", "养老照护"]
        self.assertEqual(care["actual_enrolled"], 2)
        self.assertEqual(care["error"], round(2 - 3.0, 4))
        self.assertEqual(actual["cohort_size"], 6)
        self.assertEqual(actual["actual_enrolled"], 2)
        self.assertEqual(actual["error"], round(2 - 3.0, 4))
        self.assertEqual(actual["error_rate"], round(1.0 / 2, 6))

    def test_actual_new_assignment_ignored(self):
        # 圈定学员在实绩里跑到别的渠道，仍按其原归属核对确认。
        actual_rows = sample_rows() + [
            row("C1", "2026-10-25", "公众号", "养老照护", True),
        ]
        actual = bt.build_actual(self.prediction, actual_rows)
        cells = {(c["channel"], c["program"]): c for c in actual["cells"]}
        self.assertEqual(cells["社区转介", "养老照护"]["actual_enrolled"], 1)
        self.assertEqual(cells["公众号", "电工基础"]["actual_enrolled"], 0)

    def test_missing_learner_counted_unconfirmed(self):
        # 实绩批次里完全没有 C4 的当月记录。
        actual_rows = [r for r in sample_rows() if r["learner_id"] != "C4"] + [
            row("C1", "2026-10-25", "社区转介", "养老照护", True),
        ]
        actual = bt.build_actual(self.prediction, actual_rows)
        self.assertEqual(actual["missing_learners"], 1)
        self.assertEqual(actual["matched_learners"], 5)
        self.assertTrue(any(bt.MISSING_ACTUAL in flag for flag in actual["flags"]))

    def test_same_batch_flag(self):
        actual = bt.build_actual(self.prediction, sample_rows(), same_batch=True)
        self.assertTrue(any(bt.SAME_BATCH in flag for flag in actual["flags"]))

    def test_missing_prediction_actual_handled(self):
        # 缺批预测没有圈定学员，实绩为空且保留缺批标记。
        prediction = bt.missing_prediction("2026-10", [bt.MISSING_BATCH])
        actual = bt.build_actual(prediction, sample_rows())
        self.assertEqual(actual["cohort_size"], 0)
        self.assertEqual(actual["actual_enrolled"], 0)
        self.assertTrue(actual["missing_batch"] or bt.MISSING_BATCH in actual["flags"][0])


class MaturityTests(unittest.TestCase):
    def test_dates(self):
        self.assertEqual(bt.month_end_date("2026-02"), date(2026, 2, 28))
        self.assertEqual(bt.maturity_date("2026-10"), date(2026, 11, 30))
        self.assertEqual(bt.prediction_cutoff("2026-10"), datetime(2026, 10, 31, 16))
        # 10 月月末 10-31 北京时间零点起满 30 天 => 11-30 北京时间零点。
        self.assertEqual(bt.maturity_instant("2026-10"), datetime(2026, 11, 29, 16))
        self.assertFalse(bt.settlement_ready("2026-10", datetime(2026, 11, 29, 15)))
        self.assertTrue(bt.settlement_ready("2026-10", datetime(2026, 11, 29, 16)))

    def test_december_rollover(self):
        self.assertEqual(bt.month_end_date("2026-12"), date(2026, 12, 31))
        self.assertEqual(bt.maturity_date("2026-12"), date(2027, 1, 30))

    def test_maturity_date_matches_instant(self):
        for period in ("2026-01", "2026-02", "2026-09", "2026-11", "2026-12"):
            instant = bt.maturity_instant(period)
            self.assertEqual(bt.maturity_date(period), bt.beijing_date(instant))
            self.assertTrue(bt.settlement_ready(period, instant))
            self.assertFalse(bt.settlement_ready(period, instant - timedelta(seconds=1)))

    def test_parse_period_rejects_bad_values(self):
        for bad in ("2026-13", "2026-1", "", None, 202610):
            with self.assertRaises(ValueError):
                bt.parse_period(bad)


if __name__ == "__main__":
    unittest.main()
