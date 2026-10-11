# 2B-R 正式 Docker 结果比较记录部署验收

日期：2026-10-08（北京时间）。仓库：`D:\Data\New_recover_airport_group`。

本轮已完成当前工作树 Docker build、已迁移正式库的初始化复核、真实 UI 保存与幂等、CSRF、audit、历史展示和业务不变量检查。正式保留 **1 条**由 admin 显式保存的 damage_effect 记录。未提交 Git。

**必须保留的事实差异：任务开始时 v027 已 applied，首次应用不发生在本轮。** 首次 migration marker 时间为 `2026-10-08 07:07:23 UTC`，即北京时间 **15:07:23**；同时段日志记录原生 backend 启动。不能把本轮备份称作首次 v027 之前的备份，也不能宣称本轮实际观察了旧表替换过程。

用户后续明确批准使用内置 Chromium，并指示“不用测另外的”账户，以及“注意，解决不了的跳过”。本轮仅使用既有 admin，不再登录 test/operator 或 snapshot/viewer；未新建用户、重置密码或改变权限。无法追补的首次迁移前历史证据明确跳过。其他角色与私有可见性的自动测试证据仍见 2B 实施报告，不冒充本轮真实多账户验收。

证据目录：`runtime/acceptance/comparison-2b-r-20261008/`。本轮未修改产品代码、compose、SQLite journal/synchronous/busy_timeout/mount/retry 或数学内核。

## 1. 部署前 Git／数据库状态

HEAD 始终为 `84ee66f7a9ac398f1d634d80a0c8a84d0e036ca9`。

在服务和数据库操作前保存 `head.txt`、`status.txt`、`diff-stat.txt`、`diff-check.txt`、`working-tree.txt`（完整 binary diff）、`changed-files.txt`（已有修改及未跟踪文件清单）。部署结束确认 tracked diff 与起始文件逐字节一致；九个关键源文件也保持与镜像一致。既有 2A／2A-R1／2B 和本轮开始时已有的其他未提交修改均保留。

正式 authority：`runtime/db/airport_group.sqlite3`，首次读取使用 SQLite `mode=ro`，没有初始化动作。

| 表／状态 | 部署前事实 |
|---|---:|
| runs | 26 |
| run_input_snapshots | 26 |
| run_results | 21 |
| run_events | 199 |
| assessment_result | 126 |
| assessment_run_evaluation | 8 |
| assessment_indicator_value | 32 |
| assessment_airport_risk | 0 |
| assessment_comparison | 0 |
| schema_migrations | 27，已含 v027 |
| succeeded／failed／cancelled | 21／3／2 |
| queued／running | 0／0 |
| quick_check | ok |

完整 schema、count 和全部表的业务行 SHA-256 见 `before-live.json`、停服后的 `before-stopped.json`。本轮以实际 **126** 条容量事实为基准，没有用旧报告的 120 条替代当前值。

## 2. 旧 comparison count 与多消费者检查

本轮首次只读检查的 comparison count 为 **0**，允许继续；但其 schema 已经是 v027 正式 pair 表。**本轮无法直接观察迁移前旧表 count/schema。** v027 非空 fail-closed 的代码／临时库测试仍有效，不能把它写成本轮直接读取了旧表。

发现原生本仓库进程：web launcher PID 20192、实际 web PID 8484；worker launcher PID 36452、实际 worker PID 34472。命令行为本仓库 `.venv/Scripts/python.exe -m backend serve/worker`；15:47:40 启动，父进程为既有 Windows 生命周期脚本。确认正式队列没有 queued/running 后，精确停止这四个进程。

开始时 Docker daemon 不可用。启动已安装的 Docker Desktop 后，其旧容器自动恢复，随后立即停止 Docker web/worker，再备份；期间没有新 Run。未改变永久启动策略。后续部署和结束的原生进程检查均未发现 backend serve/worker。

证据：`processes-before.json`、`native-stopped.json`、`native-after-docker-stop.json`、`native-during-docker.json`、`native-final.json`。

## 3. 正式数据库备份

服务停止后使用 SQLite backup API，源以只读方式打开，目标不覆盖既有备份。备份路径：

`D:\Data\New_recover_airport_group\runtime\db\backups\airport_group_pre-comparison-v027_20261008-155842.sqlite3`

| 项目 | 结果 |
|---|---|
| 创建时间 | 2026-10-08 07:58:43 UTC／15:58:43 北京时间 |
| 源库 SHA-256 | `7f8309927c9c4d020dfd44d6506d2dc03bb706c088a4cbb7a099b0af36fd01bc` |
| 备份 SHA-256 | `960e0f10851b338d9d5491024ec579a4f5b92e44664435cfe9c2c3cea00d9b8d` |
| quick_check | ok |
| 全部表 schema／业务行与停服源一致 | 是 |

文件名遵循任务所需的 pre-comparison-v027 标识；**实际内容已包含先前应用的 v027，是本轮 Docker/UI 保存前备份，不是首次 migration 前备份。** `backup.json` 显式记录 `already_v027_before_backup=true`。SQLite backup 的文件字节布局可以不同，业务一致性另行核验。

## 4. v027 初始化过程

正式入口已核查：`python -m backend serve` → `build_application` → `prepare_runtime` → `initialize_database`。worker 也经过 prepare_runtime，故本轮先只启动 web，worker 保持停止。

当前工作树 build 后执行 `docker compose up -d --no-deps web`；web 经已有初始化入口识别已 applied 的 v027，不再次 DROP/CREATE。随后停止 web，立即只读检查 schema、索引、migration marker、count、quick_check 和全部表指纹，再启动 web/worker。

没有发明第二套迁移机制，没有清空或重建正式表，没有回滚并重放首次 migration。首次 v027 的 marker 未改变。

证据：`web-single-up.log`、`web-single-state.json`、`web-single-stop.log`、`after-initialize.json`、`migration-verification.json`。

## 5. 正式 schema／indexes

assessment_comparison 已为 14 字段正式 schema：

```text
id, comparison_mode, base_run_id, compared_run_id,
base_snapshot_hash, base_metrics_hash,
compared_snapshot_hash, compared_metrics_hash,
comparison_version, created_by_user_id, created_at,
conclusion, result_json, idempotency_key
```

四 mode CHECK、不同 Run CHECK、小写 SHA-256 CHECK、UNIQUE(idempotency_key) 存在。`PRAGMA index_list` 核验 unique 自动索引；CREATE SQL 明确唯一身份列。

- `idx_comparison_creator_page(created_by_user_id, created_at DESC, id DESC)`。
- `idx_comparison_page(created_at DESC, id DESC)`。
- 不存在旧 base_airport_id／base_scenario_type／compared_airport_id／compared_scenario_type。
- 没有 Run FK；初始化复核后 count=0、quick_check=ok。

## 6. 初始化前后业务不变量

停服 baseline 与单 web 初始化／再停服之间，**全部数据库表的 schema、count、完整业务行指纹一致**，包括 comparison 和 migration marker。这证明本轮初始化幂等且没有业务改写。

这份证据覆盖本轮已 v027 状态下的启动复核；**不覆盖任务开始前首次 v027 的旧表→新表逐行对照**。首次迁移前备份和全表指纹无法追补，按用户“解决不了的跳过”指示作为历史证据缺口跳过，不计入 PASS 的覆盖范围。

## 7. Docker image／container authority

执行当前项目 `docker compose build`，成功完成，未复用旧镜像冒充部署。

- 新 image ID：`sha256:2d20c1a9e9d7c8515d8f8cb2103e1d2aae0b7c82aa634043030f4dc3704085a6`。
- image Created：2026-10-08 07:59:08.620975815 UTC／15:59:08 北京时间。
- web container：`20bce70bdd650eec19ff77461c314c621c2838c9b172f97535e577ed43a80815`，healthy。
- worker container：`f4532d0ffdbee71d25350ff3a611990d7d391110995eff8d66c18a53cd69d702`，running。
- 两者 DB：`/app/runtime/db/airport_group.sqlite3`，runtime：`/app/runtime`。
- 两者 bind：宿主 `D:\Data\New_recover_airport_group\runtime` → `/app/runtime`，使用原正式 authority，未复制到容器内部。

九个关键文件容器 SHA-256 全部与宿主相等：domain、v027、repository、service、results_api、assessment_results_api、flask_results、results.js、results.html。完整 digest 见 `image-verification.json`，build 日志 `docker-build.log`，authority 见 `container-authority.json`。

## 8. 基础 HTTP 回归

既有本地验收凭据正常登录真实 Docker，密码／cookie／CSRF token 未写入验收日志或报告。

保存前已在直接 `localhost:8080` 页面读取评估区域，comparison 为空；对应统一 audit 记录四个 assessment GET 200。初始空态和只读正式库 count=0 均已确认，未单独留存初次 total=0 的响应 JSON。

最终 HTTP 再核验均为 200：

| GET | 最终结果 |
|---|---|
| assessment/overview | assessment-visual.v1，容量矩阵正常 |
| assessment/curves?airport_id=AP179 | assessment-visual.v1，total=21，曲线正常 |
| assessment/evaluations?limit=100 | assessment-evaluation.v1，total=8 |
| assessment/comparisons?limit=50 | comparison-record.v1，total=1 |
| comparison-records/CMP… | 保存 JSON 与首条一致，provenance_status=current |

容量行保持 126；评价 8；指标值 32；页面仍显示真实综合能力指数 **64.27**、AHP 与风险“2级 较低”，没有重算。

证据：`http-regression.json`、`final-http-1.json` 至 `final-http-5.json`、`http-observations.jsonl`。

## 9. 实际验收 comparison

仅保存一条有业务意义的无损毁→重度损毁对照，来自当前真实页面唯一合法 damage 三元组候选，不由 manifest 或 SQL 指定生成。

| 项目 | 实际值 |
|---|---|
| ID | `CMP-b1b2eb3fb1434906be63b5aabef1cbc3` |
| mode | damage_effect |
| base Run | `RUN-8e92f4bd64ae4014847f8c3178636a63` |
| compared Run | `RUN-c00f68941cab4079add60903879f4bdc` |
| 严格三元组第三 Run | `RUN-6156e662f5994d0da0baa1986697e515` |
| 创建者 | admin |
| created_at | 2026-10-08T08:09:57.325958+00:00（北京时间16:09:57） |
| comparison_version | strict-focus.v1 |
| envelope | comparison-record.v1 |

结论：履行 123 架次、缺口 0 均未变化；实际出动减少 53 架次；J 下降 39.1997，并保留原内核任务结构结论。

四 provenance hash、idempotency_key、完整 JSON 和全部存储列见 `rows-after-first.json`；五个 envelope 顶层字段、pair scheme 仅含两侧、完整三元组 evidence 均已核验。

## 10. 首次真实 UI 保存

正常登录 → 结果分析 → 损毁与组群 → 选择真实 radio 候选 → 应用比较 → 等待 preview → 点击“保存比较记录 · 损毁影响”。

应用后实际观察 loading 按钮禁用；preview 完成后启用，title 指向完整 R0→R1 ID。首次点击真实 POST 返回 **201、created=true**，正式 count **0→1**。

保存反馈显示原 pair，反馈 title 提供实际 CMP ID 和双方完整 ID。没有使用 API、SQL INSERT、repository 或 dev_validation 替代保存按钮。

## 11. 幂等真实验收

回到同一用户、同一 damage pair，快速双击保存。保存中按钮禁用，观察器仅捕获 **一次**该次 POST；返回 **200、created=false**，同一 CMP ID。

数据库逐列对照 `rows-after-first.json`：ID、created_at、result_json、conclusion、四 hash、version、creator、key 等 **所有列完全一致**；count 仍 1。UI 提示“已有记录 RUN-8e…36a63 → RUN-c0…f4bdc”。

证据：`idempotency-verification.json`、`race-bc-pending.json`、`race-bc-result.json`。

## 12. 权限与私有可见性范围

真实 admin 登录、保存入口、来源访问、保存与全库列表读取通过。admin total=1，与正式全库 count 一致。

用户明确取消其他账户测试，故 **operator/viewer/第二 operator 的真实私有可见性未执行**。现有 21 个成功 Run 均属于 admin；未为验收改 owner、造 Run 或改变角色。此前自动测试涵盖 operator、viewer、降级 viewer、admin、owner 过滤和 total 隔离。

本轮“权限 PASS”仅表示用户收窄后的 admin 路径，不代表重新跑过全角色验收。

## 13. 结果比较记录 UI

评估区域使用“结果比较记录”，无旧“跨场景记录”标题。真实行提供 mode、Run pair、结论、作者 admin、UTC 创建时间、来源有效 (current)。title 与只读详情包含完整 Run ID。

展开“查看保存事实”读取保存的 values、J/F、pair facts、provenance 和 eligibility；该区域没有业务按钮，未提供编辑、删除、分享或重算。

实际数值 0 与 solver_facts 中的 JSON null 保持区别。该 damage 样本所有 J/F 均可比，**本轮没有真实“不可直接比较”组件样本**；此前临时库和 browser 自动测试覆盖该分支。探查可选的另一配置样本时 preview 严格拒绝 422，未保存第二条，不造数据填补边界样本。

证据：`history-ui.json`、`final-http-5.json` 和截图。

## 14. CSRF

真实 UI 保存证明合法浏览器 token 路径。另用既有 admin 正常登录真实 Docker，发送不带 X-CSRF-Token 的持久化请求：**403／CSRF_FAILED**；count **1→1**。未把 cookie／token／认证正文写入报告。

证据：`csrf-verification.json`。

## 15. Audit

首次和幂等操作均各有一条正常统一 hook 审计：

| 项目 | 首次／重复 |
|---|---|
| resource_type | results |
| resource_id | 实际 CMP-b1b2eb3fb1434906be63b5aabef1cbc3 |
| action | POST /api/results/comparison-records（正式标签“保存结果比较”） |
| response_status | 201／200 |
| disposition | created／existing |

details 包含 comparison_id、comparison_mode、base_run_id、compared_run_id、comparison_version、created/disposition；没有 result_json、password、token 或 authorization。无 CSRF 请求有 denied 审计；业务表仍一行。没有手工补写另一套审计。

证据：`audit-verification.json`。

## 16. Canonical／2A 不变量及只读无重算

保存、迟到响应、重复点击、历史展开和详情 GET 后逐表完整行指纹保持一致：

- runs、run_input_snapshots、run_results（Solution／Metrics 原文和 hash）、run_events。
- assessment_result、assessment_run_evaluation、assessment_indicator_value、assessment_airport_risk。
- indicator_sets、indicator_nodes、indicator_weight_config，以及专家评分配置。
- situations 及其 airports／missions／damage／resources／runways 等所有配置子表；airport、mission、aircraft、resource 主表。

comparison 只增一条，audit 正常增加，users 唯一变化为 admin.last_login_at；账号集合、角色、密码等没有修改。没有新优化、Run、canonical 事件、capacity backfill 或指标计算日志。历史 GET 返回原保存 JSON、current，保存行未改变。

证据：`final-facts.json`、`final-verification.json`、`login-user-changes.json`、`new-application.log`。整体 SQLite 文件 hash 不作为业务不变量要求。

## 17. SQLite disk I/O 观察

只检查本轮备份之后新增的 application log、Docker log，以及真实 POST 和 audit 的结果。首次 INSERT、幂等操作、两个成功 audit、403 拒绝及 read 均成功。

**本轮未复现** `sqlite3.OperationalError: disk I/O error`。旧 application.log 中先前 2A 的 I/O traceback 仍保留，不算本轮新异常，也不声称问题已解决。没有为规避它修改任何 SQLite／mount／retry 设置。

## 18. 实际竞态操作

浏览器工具缺少网络控制接口，采用临时 `localhost:8081` 观察器，原样转发真实 UI 请求到 Docker8080；只延迟已收到的保存响应 8 秒，不改响应／请求内容、不重放保存、不记录登录正文或认证头。最终截图和只读展示回到直接 Docker8080。

| 场景 | 真实观察结果 |
|---|---|
| A 保存发出后切 focus | 首次保存 damage_effect 期间切到 cluster；原按钮“保存中…”；新阶段按钮立即可用。迟到反馈仍指向旧 R0→R1，不标记新 pair |
| B 保存发出后切 workspace | 幂等请求期间切方案配置；返回后 configuration 保持 active，当前 focus 不变且保存入口可用；未跳回 damage |
| C 快速重复点击 | 双击只产生一个保存 POST，后台返回 existing，业务仍一条 |

证据：`race-a-pending.json`、`race-a-result.json`、`race-bc-pending.json`、`race-bc-result.json`、`http-observations.jsonl`。临时观察器已精确停止，未改变正式部署策略。

## 19. 最终正式 comparison count

**1**，current，保留 `CMP-b1b2eb3fb1434906be63b5aabef1cbc3` 供正式演示／用户手册。没有删除、回滚、覆盖或批量制造记录。

## 20. 最终截图

全部为 **1920×1440、100% zoom、无 DevTools／认证信息**。页面缩放已用浏览器 Ctrl+0 恢复；中间尺寸不一致的截图已重拍。

1. [合法 focus 与保存入口](D:/Data/New_recover_airport_group/runtime/acceptance/comparison-2b-r-20261008/01-focus-save-entry.jpg)
2. [首次保存成功](D:/Data/New_recover_airport_group/runtime/acceptance/comparison-2b-r-20261008/02-save-success.jpg)
3. [评估完整页面：用户手册候选](D:/Data/New_recover_airport_group/runtime/acceptance/comparison-2b-r-20261008/03-assessment-full.jpg)
4. [比较记录只读详情展开](D:/Data/New_recover_airport_group/runtime/acceptance/comparison-2b-r-20261008/04-record-detail.jpg)

表格及详情按原 UI 使用滚动区域；完整 JSON、hash 和完整 ID 的核查证据另存上述 JSON，截图不替代数据核查。

## 21. 判定与尚未解决事项

| 验收项 | 判定 |
|---|---|
| 2B正式migration | **PASS（已迁移 schema／marker、正式初始化幂等和完整性核验；首次迁移在本轮前）** |
| 2B真实保存链 | **PASS** |
| 2B幂等 | **PASS** |
| 2B权限 | **PASS（仅用户保留的 admin 验收范围）** |
| 2B audit | **PASS** |
| 2B canonical/2A不变量 | **PASS（本轮部署／保存／读取前后）** |

**是否可以冻结2B：是（按用户最终批准的验收范围）。** 功能链与本轮业务不变量已通过；用户已指示无法解决的项跳过，因此任务开始前首次 migration 的备份与业务指纹缺口明确跳过，不再阻塞冻结建议。不能把已 v027 的本轮备份及初始化幂等证据冒充首次迁移前后证据；本报告不回滚正式 schema 或删除真实记录来制造验收过程。

其他明确边界：其他账户真实测试按用户指示取消；正式 J 不可比样本未制造；外部 V5 档案仍是此前自动测试的资产缺失项；SQLite I/O 仅记录本轮未复现。可选配置 preview 的 422 没有业务写入，本轮没有修改比较规则或配置来源来强行通过。

**Git：未提交。** `git diff --check` PASS，HEAD 与产品 working-tree diff 保持本轮开始状态。正式 Docker web／worker 保持运行，临时观察器已停止。本轮结束。
