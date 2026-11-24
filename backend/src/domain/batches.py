"""批次选择、圈定与结算的纯逻辑（不依赖数据库）。

真正的数据库查询在 ``services/backtests.py``；这里只保留可直接单测的筛选规则。
"""

from src.domain import backtest as engine


def contains_period(rows, period):
    return any(engine.row_period(row) == period for row in (rows or []))


def _key(item):
    return (item.created_at, item.id or 0)


def select_dataset(candidates, period, cutoff, inclusive=True):
    """从带 ``rows``、``created_at``、``id`` 的候选中挑出 cutoff 前最后入库的一批。

    同时按入库时间与 id 打破并列，保证「最后入库」在并发补传时稳定。
    """
    def eligible(item):
        created = item.created_at
        within = created <= cutoff if inclusive else created < cutoff
        return within and contains_period(item.rows, period)

    selected = [item for item in candidates if eligible(item)]
    return max(selected, key=_key) if selected else None


def prediction_dataset(candidates, period):
    """目标月末之前最后入库、且含该月受理记录的批次。"""
    return select_dataset(candidates, period, engine.prediction_cutoff(period), inclusive=False)


def actual_dataset(candidates, period):
    """目标月末满 30 天时点之前最后入库、且含该月受理记录的批次。"""
    return select_dataset(candidates, period, engine.maturity_instant(period), inclusive=True)

