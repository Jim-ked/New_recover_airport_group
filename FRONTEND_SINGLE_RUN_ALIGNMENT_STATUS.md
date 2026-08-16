# Single Run 前端对齐状态

## 本轮范围

本轮只实现成功 Run 的单次运行仪表盘，并启用算法运行页历史记录中的“查看结果”。不提前实现 GIS Runtime、Results 主页面、Situation 编辑页或 Indicator 页面。

## 权威事实源

Single Run 页面只读取：

- `GET /api/runs/{run_id}`
- `GET /api/runs/{run_id}/situation`
- `GET /api/runs/{run_id}/solution`
- `GET /api/runs/{run_id}/metrics`

页面仅接受 `status=succeeded`。不读取旧 Scene、runtime、result directory、旧 summary/results，也不从日志或旧 operations 重建结果。

## 已实现

- 路由：`GET /runs/{run_id}`
- 5 个顶部摘要：最终组群、任务规模、出动情况、机场协同、资源保障
- 任务调度时序：全部 / 机场 / 任务 / 机型；非“全部”模式使用完整对象选择器，不做 Top-N
- 组群空间布局：直接使用冻结 Situation 经纬度 + canonical `sortie_chains` 的 origin→mission→return 完整链
- 资源余量时序：使用后端 Metrics 的类别逐窗最低余量率
- 全机场承接：保留所有机场，包括零承接机场
- 全任务调度结构
- 全机型投入结构
- 统一详情 Dock：机场承接 / 任务调度 / 机型投入 / 资源保障 / 技术信息
- 算法运行页成功历史 Run 的“查看结果”已导航至 Single Run
- GIS Runtime 按钮保持禁用，未建立假页面/假数据
- 方案关注保持空规则态，不自行创建阈值、等级或原因性判断

## Metrics v1 非破坏性扩展

为避免前端重算资源业务聚合，新增：

- `tasks[*].required_total`
- `resources.category_min_remaining_ratio_timeline`

`category_min_remaining_ratio_timeline` 完全复用当前冻结口径：

- scope = actual participating airports
- denominator = pre-damage initial stock
- category = fuel / material / munition
- initial stock = 0 时不产生 ratio
- 每个时间窗只在可比资源中取最小 ratio
- 不跨不同资源单位直接求和

前端只格式化百分比和绘图坐标，不重新计算类别最小值。

## 明确未实现

- GIS Runtime 正式 Leaflet 运行态势
- 方案关注阈值和告警规则
- 自动“最佳方案”或原因性建议
- 真实 Flask/TestClient/浏览器运行 smoke（当前容器没有 Flask 运行依赖）

## 测试

`tests/frontend/__init__.py` 已补齐，因此前端契约测试正式纳入统一 `unittest discover -s tests`。

当前统一回归：230 passed, 0 failed。

此外通过：

- Jinja：`base.html`、`pages/run.html`、`pages/single_run.html`
- Node syntax：`api-client.js`、`run.js`、`single-run.js`
- Python compileall
- 无旧 `/api/runtime`、`/api/scenes`、`scene_file`、`run_params_path`、`result_root` 前端引用
- 无在线 CDN/网络资源
