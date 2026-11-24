from enum import StrEnum

class Role(StrEnum):
    ADMIN = 'admin'
    ANALYST = 'analyst'
    VIEWER = 'viewer'

class Status(StrEnum):
    QUEUED = 'queued'
    RUNNING = 'running'
    COMPLETE = 'complete'
    FAILED = 'failed'

class Review(StrEnum):
    PENDING = 'pending'
    ACCEPTED = 'accepted'
    RETURNED = 'returned'

class BacktestState(StrEnum):
    QUEUED = 'queued'
    RUNNING = 'running'
    PREDICTED = 'predicted'   # 预测已出，等待目标月满 30 天
    SETTLED = 'settled'       # 实绩与误差已记录
    FAILED = 'failed'
