# 评估结果可视化 2A 实施与验收

起点：`84ee66f7a9ac398f1d634d80a0c8a84d0e036ca9`。本轮没有 Git 提交。

## 1. 根因与修复点

| 根因/缺口 | 修复 |
|---|---|
| 正式 Worker 未调用容量 projection | `RunDerivedArtifactsService` 接入正式运行时装配 |
| 单行 upsert 缺乏 Run 级原子性/来源保护 | `persist_run_capacity_results` 事务插入、相同事实跳过、冲突拒绝 |
| projection 只复制来源 hash | 检查 Run ID、Snapshot 内容/hash、Solution/Metrics canonical JSON hash、机场完整性 |
| 多 Run 共用 scenario_type 被矩阵折叠 | 增加 Run key、冻结标签和 `run_cells`；前端按 Run key 展示 |
| 历史 Run 缺少产品维护入口 | backend CLI backfill，支持 dry-run 和显式选择 |

## 2. 实际修改文件

新增：

- `backend/services/run_capacity_assessment.py`
- `backend/services/run_derived_artifacts.py`
- `backend/services/run_assessment_labels.py`
- `tests/services/test_run_capacity_assessment.py`
- `tests/services/test_capacity_production_chain.py`
- `tests/frontend/assessment_rendering.test.mjs`
- `docs/assessment-capacity.md`
- 本验收记录。

修改：

- `backend/__main__.py`
- `backend/services/run_assessment_projection.py`
- `backend/services/run_worker.py`
- `backend/services/run_worker_runtime.py`
- `backend/storage/assessment_repository.py`
- `backend/web/assessment_results_api.py`
- `frontend/static/js/modules/results.js`
- `tests/services/test_run_assessment_projection.py`
- `tests/frontend/test_assessment_workspace_contract.py`
- `tests/test_backend_worker_cli.py`
- `tools/dev_validation/rebuild_v5_experiment.py`：仅增加显式 V5 ID 前缀参数，正式链不依赖此文件。

## 3. Migration

无 schema migration；使用既有 v021/v023 容量字段。普通 Run 使用 `capacity:{run_id}:{airport_id}`，冻结实验显式保留 `v5cap` 前缀。

## 4. 场景身份

Run 实例 key = `source_run_id`，`scenario_type` 不承担唯一身份。显示标签由冻结情境名、损毁场景名、组群开关和优化偏好构成，允许显示标签重复。

overview 在保留旧 `cells`/`scenario_types` 的同时新增 `scenario_keys`/`scenario_labels`/`run_cells`。曲线保留 Run provenance 并增加冻结标签。矩阵仍为运行期容量占用率；曲线仍为起降容量可用比例。

## 5. Worker 链

canonical succeeded + Solution/Metrics 提交 → 独立容量派生 → 独立既有指标综合评价。派生逻辑位于 canonical 失败处理范围外。

事件区分容量 persisted/skipped/failed 与指标 persisted/unavailable/failed；一个派生失败不阻断另一个；事件保存失败有日志，canonical 状态保持 succeeded。

## 6. 维护入口

```powershell
python -m backend assessment backfill-capacity --all-succeeded --dry-run
python -m backend assessment backfill-capacity --all-succeeded
python -m backend assessment backfill-capacity --run-id RUN_ID --dry-run
```

`--run-id` 可重复，数据库路径沿用 `AIRPORT_GROUP_DB_PATH`。本地验收使用仓库 `.venv/Scripts/python.exe`。命令不初始化 schema、不调用求解器、不补综合评价、不生成 comparison。详见 [维护说明](../assessment-capacity.md)。

## 7. 自动测试

- 默认 Miniconda Python 的全仓检查发现缺少 pytest/reportlab，不作为代码验收结论。
- 使用仓库现有虚拟环境：**829 passed、8 skipped、160 subtests passed**；耗时 127.79 秒。8 项跳过依赖本机缺失的外部实验验收资产。
- 最终来源完整性修改后的相关回归：**73 passed、8 skipped、3 subtests passed**。
- 新增 JS 实际渲染函数测试：**3 passed**，覆盖同名标签多 Run、旧字段/空数据、逐窗 series 及来源标签。
- 新增 Python 测试 26 项，新增 Node 测试 3 项。生产链测试不依赖 tools/dev_validation 或 resources/experiments。
- `git diff --check` 通过。

Python 测试日志：`runtime/acceptance/assessment-2a-20261008/tests-pytest.log`、`tests-focused.log`。Node 测试结果已记录于本轮执行输出。

## 8. 本地数据库验收

authority：`runtime/db/airport_group.sqlite3`。

先用 SQLite backup 备份至 `runtime/db/backups/pre-capacity-2a-20261008-aaaa8575.sqlite3`，备份 quick_check=ok，并核对备份与原库业务状态一致。

| 步骤 | scanned | eligible | generated_runs | generated_rows | skipped | conflicts | failed |
|---|---:|---:|---:|---:|---:|---:|---:|
| dry-run | 19 | 19 | 0 | 0 | 0 | 0 | 0 |
| 首次补算 | 19 | 19 | 19 | 114 | 0 | 0 | 0 |
| 第二次补算 | 19 | 19 | 0 | 0 | 19 | 0 | 0 |

dry-run 的 `would_generate_rows=114`，数据库文件 SHA-256 未变。第二次补算后文件 SHA-256 与首次补算后相同：`939b6b0caaf833f200274d7994f55a8d99c5a3f1d6680e167131fc7d18cbb73c`。

assessment_result：0 → 114；114 行 provenance 均与 canonical Run/结果相符。

## 9. API 验收

本机 Docker 引擎未运行，8080 无运行服务。因此使用现有 Flask blueprint、实际 SQLite、只读仓储和注入的审计管理员 principal 完成路由测试，不宣称已完成交付容器的实际网络/认证验收。

| GET | 路由状态 | 内容 |
|---|---:|---|
| assessment/overview | 200 | total_results=114；6 机场；19 Run keys；114 run_cells |
| assessment/curves?airport_id=AP179 | 200 | total=19；series=19；19 个不同 source_run_id |
| assessment/evaluations?limit=100 | 200 | 6 条、24 指标；全部 provenance=current |
| assessment/comparisons?limit=50 | 200 | total=0、items=0 |

完整只读结果及冻结标签保存于 `runtime/acceptance/assessment-2a-20261008/api.json`。

## 10. canonical 不变量证明

逐表读取完整存储行，包括原始 payload_json、solution_json、metrics_json 字符串，对有序完整行编码计算 SHA-256。补算前后下列表指纹全部相同：

- runs：23 行；19 succeeded、3 failed、1 cancelled。
- run_input_snapshots：23 行。
- run_results：19 行。
- assessment_run_evaluation：6 行。
- assessment_indicator_value：24 行。
- assessment_comparison：0 行。

另外逐 Run 比较 snapshot_hash、solution_hash、metrics_hash 列，全部一致。完整证据保存于 `runtime/acceptance/assessment-2a-20261008/before.json` 和 `after.json`。

SQLite 文件本身因插入容量记录发生变化；上述 canonical 及既有评价的每一项存储业务事实均保持原值。

## 11. 待检查事项

assessment_comparison 正式生产链留待独立任务。当前没有创建 comparison。

代码、自动测试和本地补算完成后停在未提交状态。交付镜像重建、容器真实 HTTP 与人工页面截图检查尚未完成，需要运行 Docker 交付环境后检查。现有用户未跟踪的部署指南 docx 未修改。
