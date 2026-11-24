# 002 月底回测

新增 `backtests` 表，服务内部分析页的一次月底回测。应用启动时以 SQLAlchemy
模型补建缺失表；已有部署升级只需重启后台与接口进程。

- `name`、`period`：回测名称与目标月份（YYYY-MM）。
- `dataset_id`：**发起时锁定**的预测批次；之后并发补传不会改写它。缺批时为空。
- `status`：`queued` → `running` → `predicted` →（满 30 天）→ `settled`，失败为
  `failed`。
- `prediction`：预测结果 JSON，含圈定学员、样本月、渠道 × 项目估算与缺样本标记。
  一经写入不再重算，重试与续跑都复用它。
- `actual`：实绩核对 JSON，只在目标月末满 30 天后写入；缺批时也写入占位结果。
- `actual_dataset_id`：满 30 天时点前最后入库的实绩批次。
- `attempt`、`lease_until`：与 `runs` 相同的租约与重试；预测成功后重置为 0，
  让实绩结算拥有独立的重试额度。
- `predicted_at`、`settled_at`：预测与结算时间。

实绩批次与预测批次的选择规则在 `src/domain/batches.py`，均可单测：
预测取目标月末前最后入库的有效批次，实绩取目标月末满 30 天时点前最后入库的
批次。因此之后补传的数据只会进入新的回测，不会改写已出的预测或实绩。
