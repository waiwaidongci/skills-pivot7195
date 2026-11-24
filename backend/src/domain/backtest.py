"""月底报名确认回测：圈定学员、按历史成熟月份估算、到期补齐实绩。

口径说明
--------
* 目标月份 ``period``（YYYY-MM）按北京时间划分自然月。受理日期自带的月份即
  业务月份，和公开交叉表一致。
* 预测批次为「目标月末之前最后入库、且含目标月受理记录」的批次。批次在发起
  时写入任务，后台重试与中断续跑都沿用同一批次。
* 估算样本为早于目标月、且在目标月末已「满 30 天」的月份。按渠道 × 项目把
  样本各月的确认人数与受理人数合并后求转化率，再乘以当月经圈定的受理人数。
  没有样本的单元格标记为「缺估算样本」，不按 0 参与预估合计。
* 实绩在目标月末满 30 天后补齐，取该时点前最后入库的批次，只核对发起预测时
  圈定学员在目标月的确认记录；不重新归属、不纳入新学员。
* 发起时若找不到含当月数据的批次，仍建立任务并标记「缺批」，便于查看和补做。
"""

from collections import defaultdict
from datetime import date, datetime, time, timedelta

from .analysis import dedupe_intakes
from .spec import CHANNELS, PROGRAMS

MATURITY_DAYS = 30
BEIJING_OFFSET = timedelta(hours=8)

MISSING_BATCH = "缺批"
MISSING_SAMPLE = "缺估算样本"
MISSING_COHORT = "缺当月学员"
MISSING_ACTUAL = "实绩缺学员"
SAME_BATCH = "实绩与预测同批"


def parse_period(period):
    """校验并返回 YYYY-MM 字符串。"""
    try:
        if not isinstance(period, str) or len(period) != 7:
            raise ValueError
        first = date.fromisoformat(f"{period}-01")
        if first.isoformat()[:7] != period:
            raise ValueError
    except ValueError:
        raise ValueError("回测月份须为有效的 YYYY-MM。") from None
    return period


def _next_month(first):
    return date(first.year + (first.month == 12), 1 if first.month == 12 else first.month + 1, 1)


def month_end_date(period):
    first = date.fromisoformat(f"{parse_period(period)}-01")
    return _next_month(first) - timedelta(days=1)


def _beijing_midnight(day):
    """北京时间某日零点的 UTC 表示。"""
    return datetime.combine(day, time.min) - BEIJING_OFFSET


def prediction_cutoff(period):
    """目标月末（北京时间次月一日零点）对应的 UTC 时点。"""
    first = date.fromisoformat(f"{parse_period(period)}-01")
    return _beijing_midnight(_next_month(first))


def maturity_instant(period):
    """月末末日（北京时间零点）起满 30 天对应的 UTC 时点。"""
    return _beijing_midnight(month_end_date(period)) + timedelta(days=MATURITY_DAYS)


def maturity_date(period):
    """实绩可核对的最早北京时间日期，与 ``settlement_ready`` 判定一致。"""
    return beijing_date(maturity_instant(period))


def beijing_date(stamp):
    return (stamp + BEIJING_OFFSET).date()


def is_mature(month, as_of):
    """样本月在 as_of 时点是否已满 30 天。"""
    return maturity_instant(month) <= as_of


def settlement_ready(period, stamp):
    return stamp >= maturity_instant(period)


def row_period(row):
    day = row.get("intake_date") if isinstance(row, dict) else None
    return str(day)[:7] if isinstance(day, str) and len(str(day)) >= 7 else None


def _prediction_flags(period, cohort_size, missing_cells):
    flags = []
    if not cohort_size:
        flags.append(f"{MISSING_COHORT}：所选批次没有 {period} 的受理记录。")
    for channel, program in missing_cells:
        flags.append(f"{MISSING_SAMPLE}：{channel} × {program} 在已满 30 天的历史月份中没有受理记录。")
    return flags


def build_prediction(rows, period, as_of=None):
    """按预测批次 rows 生成月末估算。返回可原样留存的字典。

    ``as_of`` 默认取目标月末时点，使结果与发起时间无关，便于回看和复核。
    """
    parse_period(period)
    if as_of is None:
        as_of = prediction_cutoff(period)
    _, intakes = dedupe_intakes(rows)

    cohort = sorted(
        (item for item in intakes if item["period"] == period),
        key=lambda item: (item["channel"], item["program"], item["learner_id"]),
    )
    sample = defaultdict(lambda: {"enrolled": 0, "intakes": 0, "months": set()})
    for item in intakes:
        month = item["period"]
        if month >= period or not is_mature(month, as_of):
            continue
        bucket = sample[item["channel"], item["program"]]
        bucket["enrolled"] += int(item["converted"])
        bucket["intakes"] += 1
        bucket["months"].add(month)

    cells = []
    estimated_total = 0.0
    covered = 0
    missing_cells = []
    for channel in CHANNELS:
        for program in PROGRAMS:
            members = [item for item in cohort if item["channel"] == channel and item["program"] == program]
            if not members:
                continue
            bucket = sample.get((channel, program))
            if bucket and bucket["intakes"]:
                rate = bucket["enrolled"] / bucket["intakes"]
                estimated = rate * len(members)
                estimated_total += estimated
                covered += len(members)
                cells.append({
                    "channel": channel, "program": program, "cohort": len(members),
                    "sample_enrolled": bucket["enrolled"], "sample_intakes": bucket["intakes"],
                    "sample_months": sorted(bucket["months"]), "rate": round(rate, 6),
                    "estimated_enrolled": round(estimated, 4), "flag": None,
                })
            else:
                missing_cells.append((channel, program))
                cells.append({
                    "channel": channel, "program": program, "cohort": len(members),
                    "sample_enrolled": 0, "sample_intakes": 0, "sample_months": [],
                    "rate": None, "estimated_enrolled": None, "flag": MISSING_SAMPLE,
                })

    sample_months = sorted({month for bucket in sample.values() for month in bucket["months"]})
    return {
        "period": period,
        "as_of": as_of.isoformat(timespec="seconds"),
        "cohort_size": len(cohort),
        "cohort": [{"learner_id": item["learner_id"], "channel": item["channel"],
                    "program": item["program"]} for item in cohort],
        "cells": cells,
        "sample_months": sample_months,
        "estimated_enrolled": round(estimated_total, 4),
        "covered_cohort": covered,
        "missing_sample_cohort": len(cohort) - covered,
        "flags": _prediction_flags(period, len(cohort), missing_cells),
        "missing_batch": False,
    }


def missing_prediction(period, flags):
    """无法生成预测时的占位结果，仍保留在任务上便于查看。"""
    return {
        "period": period, "as_of": None, "cohort_size": 0, "cohort": [], "cells": [],
        "sample_months": [], "estimated_enrolled": 0.0, "covered_cohort": 0,
        "missing_sample_cohort": 0, "flags": list(flags), "missing_batch": True,
    }


def build_actual(prediction, rows, same_batch=False):
    """按实绩批次核对已圈定学员在目标月的确认，返回误差结果。

    只认预测时圈定的学员和归属；实绩批次的新学员和归属变化不改变分母。
    """
    period = prediction["period"]
    confirmed = defaultdict(set)
    present = set()
    for row in rows:
        if row_period(row) != period:
            continue
        learner = str(row.get("learner_id", "")).strip()
        present.add(learner)
        if row.get("enrolled") is True:
            confirmed[learner].add(row.get("program"))

    cell_stats = {(cell["channel"], cell["program"]): {
        "channel": cell["channel"], "program": cell["program"], "cohort": cell["cohort"],
        "predicted_enrolled": cell["estimated_enrolled"], "flag": cell["flag"],
        "actual_enrolled": 0,
    } for cell in prediction["cells"]}

    actual_total = 0
    unmatched = 0
    for member in prediction["cohort"]:
        learner, channel, program = member["learner_id"], member["channel"], member["program"]
        stat = cell_stats.get((channel, program))
        if stat is None:
            continue
        if learner not in present:
            unmatched += 1
        if program in confirmed.get(learner, set()):
            stat["actual_enrolled"] += 1
            actual_total += 1

    cells = list(cell_stats.values())
    for cell in cells:
        predicted = cell["predicted_enrolled"]
        cell["error"] = None if predicted is None else round(cell["actual_enrolled"] - predicted, 4)

    predicted_total = prediction["estimated_enrolled"]
    error = round(actual_total - predicted_total, 4)
    flags = list(prediction["flags"])
    if unmatched:
        flags.append(f"{MISSING_ACTUAL}：{unmatched} 名已圈定学员在实绩批次中无 {period} 记录，按未确认计。")
    if same_batch:
        flags.append(f"{SAME_BATCH}：满 30 天前没有更新批次，实绩可能未含后续确认。")
    return {
        "period": period,
        "cohort_size": prediction["cohort_size"],
        "matched_learners": prediction["cohort_size"] - unmatched,
        "missing_learners": unmatched,
        "cohort": prediction["cohort"],
        "cells": cells,
        "predicted_enrolled": predicted_total,
        "actual_enrolled": actual_total,
        "error": error,
        "abs_error": round(abs(error), 4),
        "error_rate": None if not actual_total else round(abs(error) / actual_total, 6),
        "covered_cohort": prediction["covered_cohort"],
        "missing_sample_cohort": prediction["missing_sample_cohort"],
        "flags": flags,
        "missing_batch": False,
    }


def missing_actual(period, flags):
    return {
        "period": period, "cohort_size": 0, "matched_learners": 0, "missing_learners": 0,
        "cohort": [], "cells": [], "predicted_enrolled": None, "actual_enrolled": None,
        "error": None, "abs_error": None, "error_rate": None,
        "covered_cohort": 0, "missing_sample_cohort": 0,
        "flags": list(flags), "missing_batch": True,
    }
