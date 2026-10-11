# 2B 第二阶段：结果比较记录代码实施报告

日期：2026-10-08。仓库：`D:\Data\New_recover_airport_group`。

基线 HEAD：`84ee66f7a9ac398f1d634d80a0c8a84d0e036ca9`。依据：已人工批准的 `assessment-comparison-2b-design-2026-10-08.md` 及第二阶段任务。当前工作树保留既有未提交的 2A／2A-R1 修改。

本报告覆盖代码与自动测试。所有数据库变更测试使用临时数据库；浏览器验证使用隔离 localhost 与测试响应。没有执行正式库 migration、Docker 重建／部署或真实页面保存。

## 1. 实际修改文件

本阶段新增 12 个文件（含本报告）：

```text
backend/domain/run_comparison.py
backend/storage/migrations/v027_run_comparison_records.py
backend/storage/run_comparison_repository.py
backend/services/run_comparison_record_service.py
tests/storage/test_run_comparison_records.py
tests/services/test_run_comparison_record_service.py
tests/web/test_run_comparison_records_api.py
tests/frontend/test_comparison_save_browser.py
tests/frontend/comparison_records.test.mjs
tests/tools/test_v5_comparison_research_boundary.py
docs/result-comparison-records.md
docs/audits/assessment-comparison-2b-implementation-2026-10-08.md
```

本阶段修改 20 个已有文件：

```text
backend/auth/principal.py
backend/storage/assessment_repository.py
backend/storage/audit_repository.py
backend/storage/database.py
backend/web/app.py
backend/web/assessment_results_api.py
backend/web/error_mapping.py
backend/web/flask_assessment_results.py
backend/web/flask_audit.py
backend/web/flask_results.py
backend/web/results_api.py
frontend/static/css/results.css
frontend/static/js/modules/results.js
frontend/templates/pages/results.html
tests/frontend/test_assessment_workspace_contract.py
tests/tools/test_complete_v5_assessment_acceptance.py
tests/tools/test_rebuild_v5_comprehensive_assessment.py
tests/tools/test_rebuild_v5_experiment.py
tests/web/test_assessment_results_api.py
tools/dev_validation/rebuild_v5_experiment.py
```

机器清单：`runtime/acceptance/comparison-2b-20261008/implementation-files.json`。该目录另保存测试日志、起始 diff 和冻结边界核验结果，属于验收证据。

上述清单表示本阶段增量。`results.js` 等共享文件保留既有 2A／2A-R1 实现。工作树中的 `backend/__main__.py`、RunWorker、容量 projection、容量／derived／labels 服务及其原测试、2A 文档、初始化浏览器测试等既有修改不归入本阶段新增；它们没有被重写或丢弃。

## 2. Migration 设计与非空保护

现有编号到 v026，新增并登记 `v027_run_comparison_records`；v021 未修改。

- 在同一事务中检查旧表及 COUNT；非空立即报 `v027 refused: nonempty legacy assessment_comparison`。
- 空旧表才 DROP／CREATE 正式结构；无 `executescript`，DDL 与 migration marker 共用事务。
- UNIQUE(idempotency_key)，创建者分页索引 `(created_by_user_id, created_at DESC, id DESC)`，admin 全库分页索引 `(created_at DESC, id DESC)`。没有当前查询不需要的 pair 索引。
- 没有 Run 外键，Run 删除不会级联删除或阻止保留历史记录。

临时库测试分别模拟 1 条和 9 条旧记录：拒绝后旧完整行、旧 CREATE SQL 均不变，v027 未标已应用。空表升级和重复 initialize 成功；模拟 DDL 后异常，schema、索引和 marker 全部回滚。测试中的旧行清空仅用于随后验证空表路径，不是 migration 行为。

## 3. RunComparisonRecord 最终 schema

`@dataclass(frozen=True)`；JSON 使用字符串，避免内部可变对象绕过冻结。

| 字段 | 正式含义／校验 |
|---|---|
| id | 服务端生成 CMP UUID，主键 |
| comparison_mode | damage_effect／cluster_effect／multi_scenario／configuration |
| base_run_id、compared_run_id | 完整、不同的有向 Run ID |
| base_snapshot_hash、base_metrics_hash | 基准方冻结 provenance，小写 SHA-256 |
| compared_snapshot_hash、compared_metrics_hash | 对照方冻结 provenance，小写 SHA-256 |
| comparison_version | 比较语义版本 |
| created_by_user_id | authenticated principal，由服务端确定 |
| created_at | 服务端 UTC 创建时间，重复保存不更新 |
| conclusion | 保存 focus conclusions 以中文分号连接 |
| result_json | 经过 domain 校验的 comparison-record.v1 envelope |
| idempotency_key | 服务端有向身份 canonical JSON 的 SHA-256，UNIQUE |

domain 校验非空字段、合法 mode/hash、pair 与 focus／labels／evidence 一致、完整选中上下文、结论和 identity 一致。数据库保留 mode、不同 Run 和 hash CHECK。新链路不使用旧机场／scenario_type 身份字段。

## 4. 比较语义版本

`COMPARISON_VERSION = strict-focus.v1`，代表 eligibility、pair facts 投影和结论生成语义。它与原预览输出结构 `comparison.v1`、保存 envelope `comparison-record.v1` 分开。版本改变会产生新的幂等身份；旧记录读为 stale。

## 5. result_json Envelope

仅包含五个正式顶层字段：

```text
schema_version = comparison-record.v1
focus_comparison
pair_facts
labels
eligibility_evidence
```

- focus 原样保留当前 pair 的 values、objective_components、structural_changes、timeline、assurance、conclusions、eligible、reasons 和内核方向／relation 信息。
- pair_facts 从内核输出摘取两侧 scheme、run_summaries、solver facts；configuration 另保留当前 pair 的 key_configuration_differences。排除依赖第三 Run 的组排序、全组 extrema／conclusion 和非 pair 的 baseline delta。
- damage scheme 的 R0／R1／R2 角色键映射成完整 Run ID；长 ID 回归证明保存事实没有丢失。
- labels 冻结两侧完整 ID、人类标签及内核对象标签；不保存 D0、方案序号、颜色、主题、tab 或滑块。
- eligibility_evidence 保存完整三元组／selected set 和全部实际来源 hash，仅作为资格证据，不变成 comparison members。

null、0、recorded、definition_comparable 与原因保持区别；未新增公式或补零。

## 6. 四 mode 保存路径

| mode | 严格输入／复用内核 | 保存 pair |
|---|---|---|
| damage_effect | 完整 R0／R1／R2，build_r0_r1_r2_comparison 及既有三元组检查 | R0→R1，comparisons.damage |
| cluster_effect | 同上 | R1→R2，comparisons.cluster |
| multi_scenario | 当前完整 2–6 Run selection，build_multi_scenario_comparison | 当前 baseline→comparison focus |
| configuration | 当前完整 2–5 Run selection，build_configuration_comparison | 当前 baseline→comparison focus 及 pair 配置差异 |

只显式保存当前 focus。整体资格失败返回 422、零写入；合法 configuration 中 J 定义不可比仍保存，J.delta=null 并保留原因。

## 7. 来源内容与 provenance 验证

服务端重新读取成功 Run、owner、Snapshot、Metrics、Solution：Snapshot 验证原始冻结文本 SHA-256；核对其保存列及 Run.snapshot_hash；Metrics／Solution 按已有 canonical JSON hash 规范验证真实内容，核对 canonical Run ID。expected provenance 必须覆盖完整选择并与实际来源匹配。

客户端只提交 mode、selection、expected_provenance 和 comparison_version；creator、time、facts、conclusion、JSON、identity 或额外字段返回 400。

计算在读事务获得的完整来源上完成；插入前短 `BEGIN IMMEDIATE` 事务再核对全部来源状态、权限、hash。计算后第三 Run 删除的测试证明返回 409 且零插入。初始缺失／无权来源 404；来源漂移／完整性／版本冲突 409。

## 8. 历史 current／stale／unavailable

读取仅解析保存记录并检查当前 provenance，绝不调用内核。API 测试把 `_compute` 替换为失败函数，详情／列表仍通过。

| 状态 | 原因 |
|---|---|
| current | pair 两侧当前有效且 hash／语义版本一致 |
| stale | snapshot_hash_mismatch、metrics_hash_mismatch、comparison_version_changed |
| unavailable | source_missing、source_not_succeeded、source_integrity_failed、saved_record_unreadable |

unavailable 优先。历史事实不清空、不覆盖；损坏 JSON 的原文保留并标 unavailable。保存后的非 focus 第三来源删除不改变 pair current 状态。

权限检查在解析／返回事实前：详情无权来源返回 404；列表仅显示记录元数据，隐藏 pair ID、结论和 JSON，标 source_inaccessible。非 focus 资格上下文失去权限时对该上下文作 redaction。

## 9. 幂等与并发

canonical identity 严格包括创建者、mode、有序两侧 `{run_id,snapshot_hash,metrics_hash}` 和语义版本。第三来源／selected set 顺序不属于身份。

repository 使用 `INSERT ... ON CONFLICT(idempotency_key) DO NOTHING`；数据库 UNIQUE 决定并发唯一性。重复返回第一次 ID／created_at／JSON；creator、mode、方向或版本不同产生不同记录。相同 identity 的 focus／pair_facts 不一致抛 invariant conflict 409，不覆盖。context／label 差异保留首条。

repository 8 次并发插入及 service 4 次并发保存都只有一次 created。

## 10. 权限与可见性

新增 `results.comparison.write` 给 operator／admin；viewer 不可保存。非 admin 仅能保存自己可访问 Run、读取自己记录；admin 可访问全部正式记录。降级 viewer 仍可只读本人历史。

repository 创建者过滤直接使用 `WHERE created_by_user_id=?`，分页与 total 同一 scope；其他用户 total=0 的隔离测试通过。无编辑、删除、覆盖或分享 API。

## 11. CSRF／mutation guard

app 显式把既有 mutation_guard 注入 results blueprint，新持久化 POST 执行该 guard。浏览器通过已有 apiFetch 提交 CSRF。HTTP 测试证明无 token 403、有 token 新建 201、viewer 403、匿名 401。

## 12. Audit

继续统一 after_request hook；成功保存设置窄范围的 `g.comparison_audit_resource_id` 与操作 details。resource_type 保持 results，resource_id 是实际 CMP ID，动作标签为“保存结果比较”。details 包含 ID、mode、pair、版本、created／existing，不包含完整 JSON、请求正文或认证信息。

测试包含一次 CSRF 拒绝与两次合法保存：只有统一 hook 的三条操作审计；两次成功均使用相同真实 CMP ID，业务表仍一条。既有 best-effort 审计边界保持；未改 journal、retry、挂载或 I/O 处理。

## 13. 前端保存按钮状态机

damage／multi／configuration 顶部新增保存入口；只向有写权限者显示。damage 文案随 activeDamageStage 显示损毁影响／组群调整。

当前 preview loading、缺 payload／focus／provenance、整体资格失败、当前捕获身份保存中时禁用；J 单独不可比不禁用。

点击时冻结 workspace、stage、selection、pair、expected provenance、版本与 comparison generation。pending 按捕获状态管理；切换 focus 后新的 preview 完成即可独立保存。preview 自身用 generation 拒绝过期响应。迟到保存只反馈捕获 pair，不设置“当前 focus 已保存”状态、不跳回工作区。

## 14. 前端竞态与历史展示

9 项隔离真实 Chromium 测试：damage stage 切换、workspace 切换、focus 切换与 preview 乱序、selected Run 切换、缺 provenance、整体不合法、viewer、J 不可比可保存、响应式宽度／键盘操作／只读详情。已有 2A-R1 初始化竞态 4 项继续通过。

记录区改为“结果比较记录”，空态为“暂无结果比较记录，可在结果分析中保存当前重点对照”。列表显示 mode、pair、结论、作者、时间和状态，title／详情提供完整 ID。只读 details 展示保存值、J/F 组件、pair facts、provenance 和 eligibility；动态文本转义。JavaScript 测试覆盖 0／null、不可访问、不可读及空态。

## 15. V5 研究边界

`validate_experiment_comparisons(..., persist_records=True)` 保留调用兼容，参数现表示输出独立 `.comparison-research-evidence.json`，不再写业务表。`persisted_records=0`；单独返回 research_evidence_count。

保留旧“12 个候选按不含 mode 的标签 pair 覆盖为 9 条”解释，不伪造正式 creator／provenance／version。冻结 manifest 和 result index 没有修改。synthetic 验证实际输出 9 条研究 artifact、正式表零行；非空旧表 9 行 migration 保护实际执行通过。

8 项外部 V5 档案测试因缺少外部 archive／rebuilt database 跳过。不能据此声称真实外部档案兼容已经运行通过。

## 16. 新增测试与设计第 18 节映射

新测试：storage 9、service 17、HTTP API 9、保存 browser 9、V5 边界 1；加既有初始化 browser 4，最终定向合计 49 passed。新 JavaScript history 测试 2 项包含在全量 31 项中。

| 设计类目 | 证据／完成状态 |
|---|---|
| 1 四 mode 合法 pair | service 四 mode 参数化；damage stage browser |
| 2 严格拒绝／三元组 | service overall_strict_rejection；HTTP 422／零写入 |
| 3 内容与预期 provenance | service 实际 Snapshot／Metrics tampering、版本／hash；HTTP 客户端字段拒绝 |
| 4 重复／并发 | repository 8 次、service 4 次；HTTP 201→200、原记录相同 |
| 5 mode 身份 | cluster_effect 与 configuration 同 pair 两条 |
| 6 direction 身份 | 合法 multi 反向产生新记录 |
| 7 stale | Metrics 内容与 hash 一致更新、Run snapshot hash、语义版本漂移 |
| 8 缺失／非成功 | 临时库修改状态／delete_terminal；历史保留 unavailable |
| 9 权限／total | 匿名、viewer、跨 owner、admin、降级 viewer、来源改 owner 的隐藏输出 |
| 10 audit／CSRF | HTTP 真 CMP ID、两次操作、CSRF 403；既有审计回归 |
| 11 list／detail | limit／offset／total、空分页、owner get／list、禁止 compute |
| 12 按钮状态 | browser 三种 guard、focus loading、J 不可比允许 |
| 13 保存后展示 | workspace_switch_and_history；JavaScript 只读历史 |
| 14 六 Run focus | full_six_multi、非 focus 成员／顺序变化返回原记录 |
| 15 canonical 不变量 | 四 mode 比较保存前后 runs／snapshots／results／events 全行相等 |
| 16 2A 不变量 | 非空 capacity／evaluation／indicator values、权重与风险全行相等 |
| 17 migration | 空表、repeat initialize、1／9 非空保护、DDL 故障回滚 |
| 18 Docker 真实链 | 按用户实施边界延期，未执行 |
| 19 J／F 边界 | 四 mode 中 configuration J 不可比；F1=0 与缺 F2 的保存区分 |
| 20 stale 历史展示 | JSON 保留、无权 facts 隐藏、损坏 JSON unavailable；JS 历史状态 |
| 21 damage 上下文／竞态 | 插入前第三 Run 删除冲突；保存后第三 Run 删除仍 current；stage 迟到响应 |
| 22 版本／无重算 | v2 新 identity、旧行 stale、API read compute 哨兵 |

用户第二阶段列出的 22 项自动测试要求覆盖在上述 storage／service／HTTP／browser 测试中。设计另列的真实 Docker 链留待授权部署验收，不用自动测试替代。

## 17. Python 全量结果

最终工作树全量命令：

```powershell
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider -rs
```

最终结果：**878 passed、8 skipped、160 subtests passed，261.35s**，exit 0。日志：`runtime/acceptance/comparison-2b-20261008/python-full-current-tree.log`。该轮在最后一次指标 fixture 强化后启动，包含全部最终代码与测试。

8 项 skip 全部是第 15 节列出的外部 V5 资产缺失，不属于本轮已执行兼容证据。定向最终：49 passed in 26.97s，日志 `focused-final.log`。

## 18. JavaScript 全量结果

`node --test tests/frontend/*.test.mjs tests/frontend/*.test.cjs`：31 passed、0 failed、0 skipped。日志 `runtime/acceptance/comparison-2b-20261008/js-full.log`。其后没有修改 JavaScript。

## 19. Git diff 检查

`git diff --check` 通过（exit 0）；Git 的 LF／CRLF 提示不属于 whitespace error。HEAD 保持基线，无提交／reset／checkout／clean。

## 20. Canonical／2A 与冻结代码不变量

四 mode 保存前后逐行比较以下临时库表：runs、run_input_snapshots、run_results（含 Solution／Metrics 原文和 hash）、run_events、assessment_result、assessment_run_evaluation、assessment_indicator_value、indicator_nodes、indicator_sets、indicator_weight_config、assessment_airport_risk。全部完全一致。

fixture 真正生成 capacity 和 available 的四指标评价；三类产物表明确非空，避免空表相等造成虚假不变量证据。

冻结文件证据：`runtime/acceptance/comparison-2b-20261008/frozen-boundary-check.json`。RunWorker／projection 等六个文件与起始 diff 对应段一致；capacity／derived／labels 服务和 comparison.py 与 2A-R1 存档 SHA-256 一致；resources、canonical Run／Snapshot domain、Run repository、RunResultService 没有本阶段 diff。未声称存在完整 resources 全树起始 hash 清单。

没有 Worker hook、自动生成、backfill、全 Run 扫描或 manifest 导入正式记录。overview／curves／evaluations 仍走原 2A 契约；仅 comparison 模型与读取协议替换。

## 21. 尚未实施与停止边界

代码与自动测试完成后停在此处。下一阶段需人工批准后才进行正式 migration、服务部署与真实 Docker 登录／预览／显式保存／重复点击／列表验收。外部 V5 档案兼容还需资产就绪后运行；SQLite I/O 若再现按独立问题记录。

**正式数据库 migration：未执行**

**真实 Docker 用户保存验收：未执行**

**Git：未提交**
