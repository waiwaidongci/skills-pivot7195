"""演示种子：回填历史批次，并预置「已结算」和「待实绩」两条回测。

历史批次的入库时间按业务时点回填，用来演示「月末前最后一批圈定学员」和
「满 30 天后用当时最后一批核对实绩」。演示日期固定在 2026-11 下旬：此时
2026-09 已满 30 天可结算，2026-10 要到 11-30 才满 30 天。
"""

from datetime import datetime, timedelta

from sqlalchemy import select

from src.constants.enums import BacktestState
from src.domain import backtest as engine
from src.domain.backtest_demo import august_rows, october_registrations, september_rows

NAMES = ("2026-09 末预测（已结算）", "2026-10 末预测（待实绩）")


def _dataset(db, Dataset, name, rows, created_at, actor="seed"):
    item = Dataset(name=name, filename=f"{name}.csv", checksum=f"demo-{name}", rows=rows,
                   row_count=len(rows), author=actor, created_at=created_at)
    db.add(item)
    db.flush()
    return item


def seed_backtests(db, Dataset, Backtest, main, actor="seed"):
    """幂等：补建历史批次和两条演示回测。``main`` 为演示主批次（10–11 月）。"""
    if db.scalars(select(Backtest).where(Backtest.name.in_(NAMES))).first():
        return {'created': False}

    august = august_rows()
    september = september_rows()
    august_batch = _dataset(db, Dataset, "榆桥职训受理_2026-08归档", august,
                            datetime(2026, 9, 1, 1, 0), actor)
    september_partial = _dataset(db, Dataset, "榆桥职训受理_2026-09截至20日",
                                 august + september_rows(with_confirmations=False),
                                 datetime(2026, 9, 21, 1, 0), actor)
    october_partial = _dataset(db, Dataset, "榆桥职训受理_2026-08至10月_20261021",
                               august + september + october_registrations(),
                               datetime(2026, 10, 21, 1, 0), actor)

    # 9 月：预测批次只有 8 月成熟样本；实绩取 10-21 入库的 9 月归档。
    sep_prediction = engine.build_prediction(september_partial.rows, "2026-09")
    sep_actual = engine.build_actual(sep_prediction, october_partial.rows)
    db.add(Backtest(name=NAMES[0], period="2026-09", dataset_id=september_partial.id,
                    status=BacktestState.SETTLED, prediction=sep_prediction, actual=sep_actual,
                    actual_dataset_id=october_partial.id, author=actor,
                    created_at=datetime(2026, 9, 21, 1, 30),
                    predicted_at=datetime(2026, 9, 21, 1, 31),
                    settled_at=engine.maturity_instant("2026-09") + timedelta(hours=2)))

    # 10 月：预测批次为 10-21 批次，样本含 8、9 月；实绩要等到 11-30 之后。
    oct_prediction = engine.build_prediction(october_partial.rows, "2026-10")
    db.add(Backtest(name=NAMES[1], period="2026-10", dataset_id=october_partial.id,
                    status=BacktestState.PREDICTED, prediction=oct_prediction, actual=None,
                    author=actor, created_at=datetime(2026, 10, 21, 1, 30),
                    predicted_at=datetime(2026, 10, 21, 1, 31)))
    db.flush()
    return {'created': True, 'september_error': sep_actual['error'],
            'august_batch': august_batch.id, 'main': main.id}
