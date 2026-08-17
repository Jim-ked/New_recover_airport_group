直接覆盖包（2026-08-17）

使用：
1. 关闭正在运行的 Web/Worker。
2. 将本 ZIP 直接解压到 D:\Data\New_recover_airport_group。
3. 选择“覆盖/替换同名文件”。
4. 新文件会自动落到 tests/algorithm 和 tests/web，不需要运行补丁脚本。

本批实际改动：
- model_builder.py：
  * 删除 build_model() 对 validate_hard_demand_paths() 的调用。
  * 删除原 sum(X_PATH) >= required_sorties 的硬需求语义。
  * 改为 sum(X_PATH) + UNMET >= required_sorties。
  * UNMET 进入 maximize 目标函数负惩罚，默认 1000/架次。
  * 不设置 sum(X_PATH) <= required_sorties，因此满足基准任务后仍可继续出动。
  * 正需求没有可行路径时也能由 UNMET 承担，不再因任务需求本身阻断构模。
- cluster_selector.py：
  * LP/SA 候选评价与最终 MIP 使用同一 UNMET 惩罚，避免 objective drift。
  * leaderboard 增加内部 Unmet 事实；不改变组选的区域语义。
- model_facts.py：
  * 共享 demand_rows 允许正需求对应空路径集合。
  * validate_schedule_base 不再检查 required_sorties，仅检查容量、航空器流、共享资源等物理守恒。
  * 旧 validate_hard_demand_paths 名称仅为兼容保留，不再拒绝短缺。
- solution_dump.py：
  * 继续直接使用共享物理可行性复核；“实际执行 < 基准需求”可以形成合法 Solution。
- flask_audit.py：
  * 成功的 GET /api/runs、GET /api/runs/<id>、GET /api/runs/<id>/events 不再逐次写审计。
  * POST、权限拒绝、错误、solution/metrics/runtime/results 等仍保留审计。
- 新增两个针对性测试。

覆盖后先运行：
python -m pytest -q tests/algorithm/test_soft_demand_contract.py tests/algorithm/test_model_facts_overlay.py tests/algorithm/test_model_builder_overlay.py tests/algorithm/test_cluster_selector_overlay.py tests/algorithm/test_solution_dump_overlay.py tests/web/test_audit_polling_policy.py

通过后再运行：
python -m pytest -q

当前刻意未扩大的内容：
- 没改 selected_cluster 区域语义。
- 没新增“需求上限”。
- 没新增压力测试模型。
- 没改 Metrics/Results schema；当前已有 required_total 与 scheduled_total 可用于后续解释。
- 完全 0 架次的成功业务结果这批暂未放开，避免同时扩大 Solution/Metrics schema；若真实场景需要，再单独处理。
- 典型低/中/高损毁只作为 Situation 损毁编辑器速填模板，下一批处理，不改 DamageProjection。
