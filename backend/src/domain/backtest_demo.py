"""回测演示数据：按月构造的登记与确认记录。

只放纯数据构造，入库与预置回测在 ``services/backtest_seed.py``。
"""

from src.domain.demo import demo_rows

# 历史归档：每格为（去重受理数、确认报名数）。8 月与 9 月刻意不同，便于展示估算。
_AUGUST = (
    ((18, 10), (9, 4), (7, 5)),
    ((14, 6), (11, 7), (5, 2)),
    ((9, 5), (12, 4), (8, 6)),
)
_SEPTEMBER = (
    ((20, 13), (10, 6), (8, 5)),
    ((15, 9), (12, 7), (6, 4)),
    ((11, 6), (9, 5), (7, 3)),
)
CHANNELS = ("社区转介", "公众号", "就业服务站")
PROGRAMS = ("养老照护", "电工基础", "烘焙实训")


def monthly_rows(period, grid, with_confirmations=True):
    """按月生成受理记录：登记在第 6 日，确认回执在第 18 日。"""
    rows = []
    for channel_index, channel in enumerate(CHANNELS):
        for program_index, program in enumerate(PROGRAMS):
            total, confirmed = grid[channel_index][program_index]
            for index in range(total):
                intake = {"learner_id": f"YQ-{period}-{channel_index + 1}{program_index + 1}-{index + 1:03d}",
                          "intake_date": f"{period}-06", "channel": channel,
                          "program": program, "enrolled": False}
                rows.append(intake)
                if with_confirmations and index < confirmed:
                    rows.append({**intake, "intake_date": f"{period}-18", "enrolled": True})
    return rows


def august_rows():
    return monthly_rows("2026-08", _AUGUST)


def september_rows(with_confirmations=True):
    return monthly_rows("2026-09", _SEPTEMBER, with_confirmations)


def october_registrations():
    """10 月截至 20 日的登记；当月确认尚未回传。"""
    return [{**row, "intake_date": "2026-10" + row["intake_date"][7:]}
            for row in demo_rows()
            if row["intake_date"].startswith("2026-10") and not row["enrolled"]]
