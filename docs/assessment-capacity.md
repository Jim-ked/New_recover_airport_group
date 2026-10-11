# 正式 Run 容量派生产物

## 数据及身份

canonical Run succeeded、Solution/Metrics 原子落库后，正式 Worker 分别生成容量事实和既有指标综合评价。派生失败及事件保存失败只记录日志/RunEvent，不进入 canonical 失败转换。

容量记录 ID 为 `capacity:{run_id}:{airport_id}`，来源为冻结 Snapshot 和 canonical Metrics。生成前验证 Run ID、Snapshot hash、Solution/Metrics canonical JSON hash 和完整机场集。一个 Run 的全部容量记录在同一事务内插入；相同事实跳过，冲突拒绝覆盖，部分已有且完全相同的记录可补齐。

`assessment_result.scenario_type` 是显示语义。损毁场景 ID 不能唯一识别评估实例：同一损毁可有不同组群或优化配置。API 新增：

- `scenario_keys`：以 `source_run_id` 为权威的列 key。
- `scenario_labels`：从 immutable Snapshot 的情境名、损毁名、组群配置和优化偏好生成。
- `run_cells[airport_id][scenario_key]`：保留各 Run 的单元格及 provenance。

旧 `scenario_types` 和 `cells` 继续提供原先按标签取最新记录的视图。没有 Run 来源的历史记录使用 `legacy:{scenario_type}` key。逐窗 series 新增 `scenario_key`、`scenario_label` 并保留 `source_run_id`。前端优先使用新结构，重复显示标签不影响实例身份。

## 指标口径

`metric_kind=capacity_availability_v1`：

- 曲线：本窗可用容量 / 冻结基准容量。
- 矩阵：有效窗使用容量之和 / 同期可用容量之和。
- 无有效容量分母时矩阵值为 null，不能补为 0。

不派生风险分数，不改变优化模型或正式指标综合评价。冻结 V5 开发重建通过显式 `result_id_prefix="v5cap"` 保留既有 ID；正式 Worker 和维护命令不依赖开发脚本或实验资源。

## 历史补算

先备份 SQLite authority，再运行：

```powershell
python -m backend assessment backfill-capacity --all-succeeded --dry-run
python -m backend assessment backfill-capacity --all-succeeded
python -m backend assessment backfill-capacity --all-succeeded
```

指定 Run：

```powershell
python -m backend assessment backfill-capacity --run-id RUN_ID --dry-run
```

可重复指定 `--run-id`，与 `--all-succeeded` 互斥。数据库路径使用既有 `AIRPORT_GROUP_DB_PATH` 设置。命令不初始化数据库、迁移 schema 或重新优化；dry-run 零业务写入。

JSON 统计：`scanned`、`eligible`、`generated_runs`、`generated_rows`、`skipped`、`conflicts`、`failed`，均按 Run 统计，唯 `generated_rows` 按行统计。dry-run 新增 `would_generate_rows`；`items` 保留每 Run 结果和来源。显式选择的非 succeeded/不存在 ID 在 `not_succeeded` 中列出。存在失败、冲突或无效选择时退出码为 1。

RunEvent 区分容量 persisted/skipped/failed 和指标 persisted/unavailable/failed。旧 `post_success_hook` 接口保留兼容，正式 Worker 使用独立的派生产物 hook。

## 本轮边界

无需 schema migration。`assessment_comparison` 的正式保存规则、用户意图及双方 provenance 属于后续任务，本轮不生成 comparison。

自动测试覆盖原子性、内容 hash、冲突、无副作用重跑、正式 Worker、backfill、多 Run API 和 JS 实际渲染函数。容器和浏览器人工验收需运行交付环境后检查页面截图。
