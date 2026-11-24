"""端到端：接口发起 → worker 预测 → 到期结算 → 误差留存。

测试以「当前日期」为准：2026-09 已满 30 天，可以走完整结算；2026-10 尚未满
30 天，应停在「预测已出、待实绩」。
"""

import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_TMP = tempfile.TemporaryDirectory()
os.environ.setdefault('DATABASE_URL', f"sqlite:///{_TMP.name}/flow.db")
os.environ.setdefault('JWT_SECRET', 'test-secret')
os.environ.setdefault('SEED_DEMO', 'false')
os.environ.setdefault('ARTIFACT_DIR', f"{_TMP.name}/artifacts")

from fastapi.testclient import TestClient  # noqa: E402

from src.config.database import Base, Session, engine  # noqa: E402
from src.constants.enums import BacktestState, Role  # noqa: E402
from src.domain import backtest as bt  # noqa: E402
from src.domain.backtest_demo import august_rows, monthly_rows, september_rows  # noqa: E402
from src.main import app  # noqa: E402
from src.models.identity import User  # noqa: E402
from src.models.jobs import Backtest  # noqa: E402
from src.models.source import Dataset  # noqa: E402
from src.services import backtests  # noqa: E402
from src.utils.security import hash_password  # noqa: E402

SEPTEMBER_GRID = (((20, 13), (10, 6), (8, 5)),
                  ((15, 9), (12, 7), (6, 4)),
                  ((11, 6), (9, 5), (7, 3)))


def make_users():
    with Session.begin() as db:
        for role in (Role.ADMIN, Role.ANALYST, Role.VIEWER):
            if not db.get(User, role.value):
                db.add(User(username=role.value, role=role,
                            password_hash=hash_password(f'{role.value}-demo-2611')))


def add_dataset(name, rows, created_at):
    with Session.begin() as db:
        item = Dataset(name=name, filename=f'{name}.csv', checksum=name, rows=rows,
                       row_count=len(rows), author='test', created_at=created_at)
        db.add(item)
        db.flush()
        return item.id


class FlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Base.metadata.create_all(engine)
        cls.client = TestClient(app)
        make_users()

    def setUp(self):
        with Session.begin() as db:
            for item in db.query(Backtest).all():
                db.delete(item)
            for item in db.query(Dataset).all():
                db.delete(item)

    def token(self, username):
        response = self.client.post('/auth/login', json={'username': username,
                                                         'password': f'{username}-demo-2611'})
        self.assertEqual(response.status_code, 200, response.text)
        return {'Authorization': f"Bearer {response.json()['token']}"}

    def run_worker(self, rounds=4):
        for _ in range(rounds):
            lease = backtests.claim()
            if lease:
                backtests.execute(lease)

    def seed_history(self):
        august = august_rows()
        august_id = add_dataset('august', august, datetime(2026, 9, 1, 1))
        partial = add_dataset('sep-partial', august + september_rows(with_confirmations=False),
                              datetime(2026, 9, 21, 1))
        backfill = add_dataset('sep-backfill', august + september_rows(), datetime(2026, 10, 21, 1))
        return august_id, partial, backfill

    def test_full_cycle(self):
        _, partial, backfill = self.seed_history()
        admin, analyst, viewer = self.token('admin'), self.token('analyst'), self.token('viewer')
        self.assertEqual(self.client.get('/backtests', headers=viewer).status_code, 403)

        created = self.client.post('/backtests', headers=analyst,
                                   json={'name': '流程九月', 'period': '2026-09'})
        self.assertEqual(created.status_code, 202, created.text)
        job = created.json()
        # 9 月末前最后入库且含 9 月数据的批次：sep-partial，不是更晚的 backfill。
        self.assertEqual(job['dataset_id'], partial)
        self.assertEqual(job['status'], BacktestState.QUEUED)

        self.run_worker()
        detail = self.client.get(f"/backtests/{job['id']}", headers=analyst).json()
        self.assertEqual(detail['status'], BacktestState.SETTLED)
        self.assertEqual(detail['prediction']['cohort_size'], 98)
        self.assertEqual(detail['prediction']['sample_months'], ['2026-08'])
        # 实绩批次取满 30 天前最后入库的一批：sep-backfill。
        self.assertEqual(detail['actual_dataset_id'], backfill)
        self.assertEqual(detail['actual']['actual_enrolled'], 58)
        self.assertAlmostEqual(detail['actual']['error'], 58 - detail['prediction']['estimated_enrolled'], places=3)
        self.assertAlmostEqual(detail['prediction']['estimated_enrolled'], 52.0959, places=3)

        again = self.client.post(f"/backtests/{job['id']}/settle", headers=admin)
        self.assertEqual(again.status_code, 409)

        listing = self.client.get('/backtests', headers=admin).json()
        self.assertEqual(listing[0]['dataset_name'], 'sep-partial')
        self.assertEqual(listing[0]['actual_dataset_name'], 'sep-backfill')

    def test_october_waits_for_maturity(self):
        _, partial, _ = self.seed_history()
        analyst = self.token('analyst')
        october = add_dataset('october-partial', august_rows() + september_rows() +
                              [dict(row, intake_date='2026-10-06')
                               for row in september_rows() if not row['enrolled']],
                              datetime(2026, 10, 21, 1))
        created = self.client.post('/backtests', headers=analyst,
                                   json={'name': '流程十月', 'period': '2026-10'}).json()
        self.assertEqual(created['dataset_id'], october)
        self.run_worker()
        detail = self.client.get(f"/backtests/{created['id']}", headers=analyst).json()
        # 当前日期尚未满 30 天，只能停在预测。
        self.assertEqual(detail['status'], BacktestState.PREDICTED)
        self.assertFalse(detail['settlement_ready'])
        self.assertIsNone(detail['actual'])
        self.assertEqual(detail['prediction']['cohort_size'], 98)
        self.assertEqual(detail['prediction']['sample_months'], ['2026-08', '2026-09'])
        self.assertTrue(partial)

        forced = self.client.post(f"/backtests/{created['id']}/settle", headers=analyst)
        self.assertEqual(forced.status_code, 409)
        self.assertIn('满 30 天', forced.json()['detail'])

    def test_missing_batch_flag(self):
        analyst = self.token('analyst')
        created = self.client.post('/backtests', headers=analyst,
                                   json={'name': '缺批月份', 'period': '2024-01'})
        self.assertEqual(created.status_code, 202, created.text)
        self.assertIsNone(created.json()['dataset_id'])
        self.run_worker()
        detail = self.client.get(f"/backtests/{created.json()['id']}", headers=analyst).json()
        self.assertTrue(detail['prediction']['missing_batch'])
        self.assertEqual(detail['prediction']['cohort_size'], 0)
        self.assertTrue(any('缺批' in flag for flag in detail['flags']))

    def test_prediction_immutable_after_backfill(self):
        analyst = self.token('analyst')
        created = self.client.post('/backtests', headers=analyst,
                                   json={'name': '待定十二月', 'period': '2026-12'}).json()
        self.assertIsNone(created['dataset_id'])
        self.run_worker()
        before = self.client.get(f"/backtests/{created['id']}", headers=analyst).json()
        self.assertTrue(before['prediction']['missing_batch'])

        add_dataset('december-backfill', [dict(row, intake_date='2026-12-03')
                                          for row in september_rows()],
                    datetime(2026, 12, 4, 1))
        after = self.client.get(f"/backtests/{created['id']}", headers=analyst).json()
        self.assertTrue(after['prediction']['missing_batch'])
        self.assertEqual(after['prediction']['cohort_size'], 0)
        self.assertEqual(after['prediction']['as_of'], before['prediction']['as_of'])

    def test_manual_settle_requeues_without_changing_prediction(self):
        _, _, backfill = self.seed_history()
        analyst = self.token('analyst')
        job = self.client.post('/backtests', headers=analyst,
                               json={'name': '手动结算', 'period': '2026-09'}).json()
        # 只跑预测。
        lease = backtests.claim()
        backtests.execute(lease)
        predicted = self.client.get(f"/backtests/{job['id']}", headers=analyst).json()
        self.assertEqual(predicted['status'], BacktestState.PREDICTED)
        self.assertIsNone(predicted['actual'])

        queued = self.client.post(f"/backtests/{job['id']}/settle", headers=analyst)
        self.assertEqual(queued.status_code, 202, queued.text)
        self.assertEqual(queued.json()['status'], BacktestState.QUEUED)
        # 预测原样保留。
        self.assertEqual(queued.json()['prediction'], predicted['prediction'])

        lease = backtests.claim()
        backtests.execute(lease)
        settled = self.client.get(f"/backtests/{job['id']}", headers=analyst).json()
        self.assertEqual(settled['status'], BacktestState.SETTLED)
        self.assertEqual(settled['actual_dataset_id'], backfill)
        self.assertEqual(settled['prediction'], predicted['prediction'])

    def test_validation(self):
        admin = self.token('admin')
        bad = self.client.post('/backtests', headers=admin, json={'name': '坏月份', 'period': '2026-13'})
        self.assertEqual(bad.status_code, 422)
        self.assertIn('YYYY-MM', bad.json()['detail'])
        self.assertEqual(self.client.get('/backtests').status_code, 401)


if __name__ == '__main__':
    unittest.main()
