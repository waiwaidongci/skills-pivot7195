"""月底回测的批次选择、租约领取与结算。

* 预测批次在发起时确定并写入任务，之后的上传只影响新任务，不会改动已出的预测。
* 实绩批次取「目标月末满 30 天」时点之前最后入库的批次，因此之后补传的数据
  不会进入实绩；结算只核对预测时圈定的学员。
* 预测时点按目标月末计算，使结果与发起时间无关；提前发起只是用现有数据预演
  月末尚能看到的结果。
"""

import logging
from datetime import timedelta

from sqlalchemy import and_, or_, select

from src.config.database import Session
from src.constants.enums import BacktestState
from src.domain import backtest as engine
from src.domain.backtest import MISSING_BATCH
from src.domain.batches import actual_dataset as choose_actual
from src.domain.batches import prediction_dataset as choose_prediction
from src.models.jobs import Backtest
from src.models.source import Dataset
from src.services.audit import record
from src.utils.time import now

log = logging.getLogger(__name__)

LEASE = timedelta(minutes=3)


def prediction_dataset(db, period):
    """目标月末之前最后入库、且含该月受理记录的批次。"""
    return choose_prediction(db.scalars(select(Dataset)), period)


def actual_dataset(db, period):
    """目标月末满 30 天时点之前最后入库、且含该月受理记录的批次。"""
    return choose_actual(db.scalars(select(Dataset)), period)


def create(db, name, period, actor):
    """发起回测。预测批次在此刻锁定，之后并发补传不影响本任务。"""
    dataset = prediction_dataset(db, period)
    item = Backtest(name=name, period=period, dataset_id=dataset.id if dataset else None,
                    status=BacktestState.QUEUED, author=actor)
    db.add(item)
    db.flush()
    record(db, actor, 'backtest.queued', item.id, period=period,
           dataset_id=item.dataset_id, missing_batch=item.dataset_id is None)
    return item


def _prediction_missing(period):
    from src.domain.backtest import month_end_date
    return [f"{MISSING_BATCH}：{month_end_date(period)} 之前没有含 {period} 受理记录的批次入库，未能圈定学员；之后补传若需纳入，请重新发起回测。"]


def _claimable(stamp):
    return or_(Backtest.status == BacktestState.QUEUED,
               and_(Backtest.status == BacktestState.RUNNING, Backtest.lease_until < stamp))


def claim():
    """领取待预测任务，或已到期待结算的任务。"""
    stamp = now()
    with Session.begin() as db:
        job = db.scalar(select(Backtest).where(_claimable(stamp))
                        .order_by(Backtest.id).limit(1).with_for_update(skip_locked=True))
        if job is None:
            waiting = db.scalars(select(Backtest).where(
                Backtest.status == BacktestState.PREDICTED, Backtest.actual.is_(None))
                .order_by(Backtest.period, Backtest.id).limit(50).with_for_update(skip_locked=True))
            job = next((item for item in waiting if engine.settlement_ready(item.period, stamp)), None)
        if job is None:
            return None
        if job.attempt >= 3:
            job.status = BacktestState.FAILED
            job.error = '后台进程多次中断，请重新发起回测'
            job.lease_until = None
            record(db, 'worker', 'backtest.failed', job.id, reason=job.error)
            return None
        job.status = BacktestState.RUNNING
        job.attempt += 1
        job.lease_until = stamp + LEASE
        return job.id, job.attempt


def execute(lease):
    """预测或结算。预测成功后重置尝试次数，让结算拥有独立的失败额度。"""
    job_id, attempt = lease
    try:
        with Session() as db:
            job = db.get(Backtest, job_id)
            if job is None:
                return
            phase = 'predict' if job.prediction is None else 'settle'
            dataset_id, period, prediction = job.dataset_id, job.period, job.prediction
        if phase == 'predict':
            _predict(job_id, attempt, period, dataset_id)
        else:
            _settle(job_id, attempt, period, prediction)
    except Exception:
        log.exception('Backtest %s failed', job_id)
        _fail(job_id, attempt, '回测执行失败，请检查批次数据后重试')


def _predict(job_id, attempt, period, dataset_id):
    prediction = engine.missing_prediction(period, _prediction_missing(period))
    if dataset_id is not None:
        with Session() as db:
            dataset = db.get(Dataset, dataset_id)
        if dataset is not None:
            prediction = engine.build_prediction(dataset.rows, period)
    if not _finish(job_id, attempt, status=BacktestState.PREDICTED, prediction=prediction,
                   predicted_at=now(), reset_attempt=True):
        return
    record_event(job_id, 'backtest.predicted', cohort=prediction['cohort_size'],
                 missing_batch=prediction['missing_batch'], flags=len(prediction['flags']))


def _settle(job_id, attempt, period, prediction):
    if not engine.settlement_ready(period, now()):
        # 手动催促或租约重放都可能在届满前抵达；此时退回待实绩，不写任何实绩。
        _finish(job_id, attempt, status=BacktestState.PREDICTED)
        return
    if prediction.get('missing_batch'):
        actual = engine.missing_actual(period, list(prediction.get('flags', [])))
        if _finish(job_id, attempt, status=BacktestState.SETTLED, actual=actual, settled_at=now()):
            record_event(job_id, 'backtest.settled', actual=None, error=None, missing_batch=True)
        return
    with Session() as db:
        dataset = actual_dataset(db, period)
        previous_dataset = db.get(Backtest, job_id).dataset_id
    if dataset is None:
        actual = engine.missing_actual(period, [f"{MISSING_BATCH}：满 30 天时没有含 {period} 受理记录的批次。"])
        if _finish(job_id, attempt, status=BacktestState.SETTLED, actual=actual, settled_at=now()):
            record_event(job_id, 'backtest.settled', actual=None, error=None, missing_batch=True)
        return
    actual = engine.build_actual(prediction, dataset.rows, same_batch=dataset.id == previous_dataset)
    if _finish(job_id, attempt, status=BacktestState.SETTLED, actual=actual,
               actual_dataset_id=dataset.id, settled_at=now()):
        record_event(job_id, 'backtest.settled', actual=actual['actual_enrolled'],
                     error=actual['error'], dataset_id=dataset.id)


def _finish(job_id, attempt, **fields):
    with Session.begin() as db:
        job = db.get(Backtest, job_id, with_for_update=True)
        if job is None or job.attempt != attempt or job.status != BacktestState.RUNNING:
            return False
        job.lease_until = None
        job.error = None
        for key, value in fields.items():
            if key == 'reset_attempt':
                if value:
                    job.attempt = 0
            else:
                setattr(job, key, value)
        return True


def _fail(job_id, attempt, message):
    with Session.begin() as db:
        job = db.get(Backtest, job_id, with_for_update=True)
        if job is None or job.attempt != attempt or job.status != BacktestState.RUNNING:
            return
        job.lease_until = None
        job.status = BacktestState.FAILED
        job.error = message[:500]
        record(db, 'worker', 'backtest.failed', job.id, reason=job.error)


def record_event(job_id, event, **info):
    with Session.begin() as db:
        record(db, 'worker', event, job_id, **info)
