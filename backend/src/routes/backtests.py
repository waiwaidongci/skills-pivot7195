"""内部分析：月底报名确认回测。"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from src.config.database import connection
from src.constants.enums import BacktestState, Role
from src.controllers.auth import allow
from src.domain.backtest import maturity_date, parse_period, settlement_ready
from src.models.identity import User
from src.models.jobs import Backtest
from src.models.source import Dataset
from src.schemas.requests import BacktestRequest
from src.services import backtests
from src.utils import views
from src.utils.time import now
from src.utils.wakeup import wake

router = APIRouter(prefix='/backtests')


def dataset_names(db, items):
    ids = {item.dataset_id for item in items if item.dataset_id}
    ids |= {item.actual_dataset_id for item in items if item.actual_dataset_id}
    if not ids:
        return {}
    return dict(db.execute(select(Dataset.id, Dataset.name).where(Dataset.id.in_(ids))).all())


@router.get('')
def index(db: Session = Depends(connection), actor: User = Depends(allow(Role.ADMIN, Role.ANALYST))):
    items = list(db.scalars(select(Backtest).order_by(Backtest.id.desc()).limit(100)))
    names = dataset_names(db, items)
    return [views.backtest(item, names=names) for item in items]


@router.post('', status_code=202)
def create(body: BacktestRequest, db: Session = Depends(connection),
           actor: User = Depends(allow(Role.ADMIN, Role.ANALYST))):
    item = backtests.create(db, body.name, parse_period(body.period), actor.username)
    db.commit()
    wake()
    return views.backtest(item, detail=True, names=dataset_names(db, [item]))


@router.get('/{backtest_id}')
def detail(backtest_id: int, db: Session = Depends(connection),
           actor: User = Depends(allow(Role.ADMIN, Role.ANALYST))):
    item = db.get(Backtest, backtest_id)
    if not item:
        raise HTTPException(404, '找不到该回测记录')
    return views.backtest(item, detail=True, names=dataset_names(db, [item]))


@router.post('/{backtest_id}/settle', status_code=202)
def settle(backtest_id: int, db: Session = Depends(connection),
           actor: User = Depends(allow(Role.ADMIN, Role.ANALYST))):
    """满 30 天后手动催促结算。预测结果与圈定学员保持不变。

    后台每轮都会自动结算已满 30 天的预测；这里只把待结算任务重新置为可领取，
    唤醒后台立即处理，不改变预测、圈定学员或实绩批次的选择规则。
    """
    item = db.get(Backtest, backtest_id, with_for_update=True)
    if not item:
        raise HTTPException(404, '找不到该回测记录')
    if item.actual is not None:
        raise HTTPException(409, '该回测已记录实绩，不能重复结算')
    if not item.prediction:
        raise HTTPException(409, '预测尚未生成，请等待后台完成')
    if not settlement_ready(item.period, now()):
        raise HTTPException(409, f'{maturity_date(item.period)} 之后满 30 天，暂不能核对实绩')
    if item.status in (BacktestState.PREDICTED, BacktestState.FAILED):
        item.status = BacktestState.QUEUED
        item.attempt = 0
        item.lease_until = None
        db.commit()
        wake()
        db.refresh(item)
    return views.backtest(item, detail=True, names=dataset_names(db, [item]))
