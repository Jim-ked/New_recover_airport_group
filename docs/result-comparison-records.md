# 结果比较记录 API（2B）

比较记录由用户显式保存当前有向 focus。无自动生成、编辑、覆盖、删除或分享接口。

## 版本与来源

- 比较语义版本：`strict-focus.v1`，覆盖严格资格规则、pair 投影与结论生成。
- 保存 envelope：`comparison-record.v1`。
- 既有预览 payload `comparison.v1` 是另一份输出结构契约。
- Snapshot 按原始冻结文本核对 SHA-256；Metrics 按 canonical JSON 核对 SHA-256；同时核对 Run 状态、owner、Run.snapshot_hash 和 canonical Run ID。Solution 闭包也检查内容完整性。
- 损毁保存校验完整 R0/R1/R2；multi/configuration 保存校验完整 selected set。记录身份及保存后的来源状态仅由当前 pair 决定。

## 预览

已有 `/api/results/damage-comparison`、`scenario-comparison`、`config-comparison` 在既有事实外增加：

```json
{
  "save_context": {
    "comparison_version": "strict-focus.v1",
    "sources": [
      {"run_id": "RUN-A", "snapshot_hash": "64个小写十六进制字符", "metrics_hash": "64个小写十六进制字符"}
    ]
  }
}
```

`sources` 实际包含完整选中上下文，前端只能把它作为 expected provenance 提交，服务端仍重新读取 authority。

## POST /api/results/comparison-records

要求已认证、`results.comparison.write` 和现有 CSRF mutation guard。operator/admin 可保存。

```json
{
  "comparison_mode": "configuration",
  "selection": {
    "run_ids": ["RUN-A", "RUN-B"],
    "baseline_run_id": "RUN-A",
    "comparison_run_id": "RUN-B"
  },
  "comparison_version": "strict-focus.v1",
  "expected_provenance": [
    {"run_id": "RUN-A", "snapshot_hash": "64个小写十六进制字符", "metrics_hash": "64个小写十六进制字符"},
    {"run_id": "RUN-B", "snapshot_hash": "64个小写十六进制字符", "metrics_hash": "64个小写十六进制字符"}
  ]
}
```

damage 模式为 `damage_effect` 或 `cluster_effect`，selection 必须且只能包含 `r0_run_id`、`r1_run_id`、`r2_run_id`。multi 模式为 `multi_scenario`，选 2–6 Run；configuration 选 2–5 Run。两者均显式指定不同的 baseline/comparison，且必须属于 selected set。

拒绝额外字段，包括 creator、time、conclusion、result_json、facts、idempotency_key。expected provenance 必须覆盖完整上下文且使用合法小写 SHA-256。

返回 `{"record": {...}, "created": true}`，新建 201；重复保存返回原记录、`created:false`、200。稳定保留原 ID、时间和 JSON。

幂等身份为 server canonical JSON 的 SHA-256：创建者、mode、有序两侧 Run ID/Snapshot hash/Metrics hash、语义版本。数据库 UNIQUE 保证并发只写一条。同一身份数学事实冲突返回 409，不更新旧行。

| HTTP | 含义 |
|---|---|
| 400 | 输入格式、字段或 cardinality 错误 |
| 401 | 未认证 |
| 403 | 缺少写权限或 CSRF 失败 |
| 404 | 来源不存在、来源无权访问，或详情记录不可见 |
| 409 | provenance/version 改变、来源完整性失败、事务内来源消失或事实 invariant 冲突 |
| 422 | 整体严格比较不合法 |
| 500 | 存储或未预期内部故障 |

J 单独定义不可比仍可保存；其 delta 为 null，recorded、definition_comparable、原因均保留。

## GET /api/results/comparison-records/{id}

要求 `results.read`。非 admin 仅查看本人历史，admin 可查看全部。当前 pair 来源已无权访问时返回 404。

返回保存记录与独立的 `provenance_status`、`provenance_reasons`，不调用比较内核：

| 状态 | 原因 |
|---|---|
| current | 两侧有效、hash 匹配、语义版本当前 |
| stale | snapshot_hash_mismatch、metrics_hash_mismatch、comparison_version_changed |
| unavailable | source_missing、source_not_succeeded、source_integrity_failed、saved_record_unreadable |

unavailable 优先；历史事实继续保留。若仅验证上下文中某个非 focus 来源失去访问权限，读取时隐藏该资格上下文，pair 事实仍遵循 pair 权限。第三个 Run 删除不会影响已保存 pair 的 current 状态。

## GET /api/results/assessment/comparisons

`limit` 默认 50、范围 1–500；`offset` 默认 0、范围 0–1000000。返回 `schema_version`、`items`、`total`、`limit`、`offset`。total 在创建者过滤后计算，按 created_at DESC/id DESC 稳定排序。

若本人记录当前来源转归他人，列表保留只含记录 ID、mode、创建者、时间的隐藏条目，状态 unavailable/source_inaccessible；不返回 pair ID、结论或 JSON。viewer 降级后仍可读自己历史。

## Envelope

`result_json` 在数据库内为不可变 canonical JSON 文本，在 API 内解析为对象：

- `schema_version`：`comparison-record.v1`。
- `focus_comparison`：当前 pair 原内核 values、J/F 组件、structural_changes、timeline、assurance、conclusions、eligible/reasons、relation。
- `pair_facts`：两侧 scheme、run_summaries、原 solver 技术事实；configuration 另包含当前 pair 的 key_configuration_differences。排除依赖第三个 Run 的全组排序及 baseline delta；不新增计算。
- `labels`：两侧完整 Run ID、冻结人类标签及对象标签。
- `eligibility_evidence`：实际 selection、完整 source hashes、eligible/reasons。它不是 membership。

conclusion 只连接 focus conclusions，不使用 multi 的全组选定结论。Run 删除无 comparison FK 阻止或级联删除。

## 研究与部署边界

v027 仅在旧 assessment_comparison 为空时替换；非空旧表（含 9 条 V5 研究记录）拒绝升级。DDL 与 migration marker 共享事务，无 executescript。

V5 验证工具输出独立 `.comparison-research-evidence.json`，保留“12 个候选按不含 mode 的标签 pair 覆盖为 9 条”的历史解释。`persisted_records` 始终为 0；`research_evidence_count` 说明研究证据数。它不写正式比较业务表。

本轮只实施代码与临时数据库自动测试。正式 migration、服务部署与真实 Docker 用户保存验收须在审批后进行。
