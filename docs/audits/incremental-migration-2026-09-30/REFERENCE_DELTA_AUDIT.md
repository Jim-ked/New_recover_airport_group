# 参考版本定向差异审计

日期：2026-09-30  
目标工程：`D:\Data\New_recover_airport_group`  
只读参考：`D:\Data\移植\安装程序\AirportGroup`  
审计分支：`codex/airport-group-incremental-migration`

## 范围与方法

仅比较用户指定目录中的源代码：

- `backend/web/`
- `backend/services/`
- `backend/storage/`
- `backend/analysis/`
- `frontend/static/gis/`
- `frontend/static/js/`

排除了安装程序、运行数据、历史报告、虚拟环境、缓存和生成文件。先按相对路径及 SHA-256 建立差异清单，再对共同文件做逐行差异检查。参考版独有的迁移文件没有直接复制；目标工程当前顺序为 `v021_assessment_visualization`、`v022_security_management`。

## 结论

未发现可在不改变模型、指标口径或数据库语义的前提下，证明为“参考版已修复、目标版仍存在”的独立缺陷。因此本阶段不修改生产代码。下列差异均已分类，避免为了形成代码改动而回退目标版能力或引入未经授权的新功能。

## 已在目标版解决或目标版更完整

| 差异 | 证据 | 决议 |
| --- | --- | --- |
| 会话生命周期 | 目标版 `backend/web/flask_auth.py` 区分公开静态资源与被动 Run 轮询，失效会话同步清理 CSRF，并使用浏览器会话 Cookie；参考版缺少这些处理且设置 `session.permanent = True` | 保留目标版，不回退 |
| 情境并发写入 | 目标版 `backend/storage/situation_repository.py` 在更新和删除前执行 `BEGIN IMMEDIATE`，避免两个延迟事务同时通过旧哈希检查 | 保留目标版并发保护 |
| 标识迁移 | 目标版 `backend/storage/identifier_migration.py` 使用 `derive_configuration=False`，只迁移 ID，不重算历史内部完整性状态 | 保留目标版历史语义 |
| 退出与改密 | 目标版 `frontend/static/js/modules/shell.js` 调用 `requestCurrentWorkspaceLeave()`，处理未保存工作区、网络不确定状态和账户缓存；参考版会直接退出或改密 | 保留目标版交互保护 |
| API 认证通知 | 目标版 `frontend/static/js/modules/api-client.js` 支持 `notifyAuthRequired`，可避免主动退出等流程重复触发全局认证跳转 | 保留目标版 |
| 账户上下文 | 目标版设置页复用 `getAccount()`，参考版直接重复请求 `/api/me` | 保留共享上下文 |
| GIS 源码 | `frontend/static/gis/` 中没有内容差异 | 无需迁移 |

## 本轮已安全迁移

| 能力 | 目标实现 | 与参考版的关键差别 |
| --- | --- | --- |
| 评估结果展示 | 新增只读评估领域对象、仓储、API、数据库表及结果页工作区；提供热力矩阵、恢复曲线、风险分级、跨场景记录 | 未引入评估计算、动态修正或新的评估模型 |
| 异常预警 | 新增持久化、权限受控 API 与设置页显示 | 只管理正式记录，不伪装为自动检测模型 |
| 数据库备份/恢复 | 仅支持真实 SQLite 全量在线备份；恢复前执行 `PRAGMA quick_check` | 明确拒绝伪增量与未配置的加密请求 |
| 审计报告 | 复用现有审计仓储，支持 CSV/PDF | 服务端固定受限目录，客户端下载禁止指定路径 |
| 文件访问 | 下载使用 `private, no-store`，恢复和下载都验证归一化路径位于受控根目录 | 不允许 cwd 回退、绝对路径或目录穿越 |

## 参考版存在风险，明确不迁移

1. 参考版备份接口可接受客户端路径；目标版仅允许 `runtime/security/backups`。
2. 参考版可把普通 SQLite 副本记录为 `encrypted=true`；目标版拒绝该请求并记录 `encrypted=false`。
3. 参考版所谓增量备份仍执行全量复制；目标版明确返回“不支持”。
4. 参考版报告读取存在工作目录回退；目标版只允许 `runtime/security/reports` 内的归一化相对路径。
5. 参考版认证和 shell 逻辑会回退近期完成的会话与未保存工作区保护。

## 涉及模型、指标或数据库语义，仅报告

| 参考差异 | 影响 | 决议 |
| --- | --- | --- |
| `assessment_api.py`、`flask_assessment.py`、`assessment_service.py` | 引入评估计算及动态修正 | 超出“只迁移展示”的范围 |
| `damage_projection_service.py` 的 `special_pattern` | 改变容量、资源损毁深度和恢复时长 | 属于优化输入/损毁语义变化，不迁移 |
| `v025`—`v028` 及相关仓储字段 | 新增军事指标、保护/应急要素、特殊损毁模式 | 改变数据库与业务语义，不迁移 |
| 参考版 `situation_repository.py` | 使用另一套机场、补给与损毁字段结构，同时缺少目标版现有字段与并发锁 | 不能局部复制，也不能覆盖目标结构 |
| `data_codec_service.py` 和 catalog 多格式/SM4 路由 | 是新增导入导出功能及密码学依赖，不是已证实缺陷修复 | 不纳入本轮 |

## 可选工程观测扩展，尚无缺陷证据

参考版的 `run_queue_repository.count_queued()`、`RunTimingCollector`、Worker 心跳 `queue_depth`/阶段耗时属于运行观测扩展。当前目标版没有相应产品需求或故障证据；迁移它们会扩展 Worker 状态合同，因此本轮只记录。

参考版将 `AuditRepository` 的 SQLite `INSERT ... RETURNING` 改为 `lastrowid` 后再次查询，可兼容不支持 `RETURNING` 的旧 SQLite，但当前运行时支持 `RETURNING`。该变化还会把一次原子返回拆成后续读取，未证明对目标环境有收益，因此不迁移。

## 后续条件

- 若要引入特殊损毁模式、军事指标或多格式加密导入，应分别形成模型、数据库迁移和密码学威胁模型评审，不能作为隐性修复附带进入。
- 若需要 Worker 阶段耗时和队列深度，应先版本化 Worker 状态合同并明确监控消费者。
- 若要支持加密备份，需要明确密钥来源、轮换、恢复授权和密钥丢失策略；在此之前 UI 与 API 必须继续显示“未加密”。
