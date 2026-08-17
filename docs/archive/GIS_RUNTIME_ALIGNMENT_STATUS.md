# GIS Runtime 对齐状态

## 结论

GIS Runtime 业务与前端绑定切片已完成。当前实现以一个成功 Run 的冻结 RunSnapshot、canonical Solution 与 Metrics 为唯一事实来源，不读取旧 Scene、dispatch cache、结果目录或 split operations，不建立第二套空间/运行状态权威。

## 后端运行态势投影

新增 `backend/services/run_runtime_service.py`，输出 `runtime.v1`。该投影仅用于展示，由 RunResultService 读取同一成功 Run 的冻结 Situation、Solution、Metrics 后确定性生成：

- 全部冻结机场与任务；
- 最终组选、实际参与机场、核心机场角色；
- canonical sortie chain 对应的完整 `origin -> mission -> return` 路径，保留唯一 `path_id`；
- 每个绝对时间窗的 departures / returns；
- 当前窗机场容量使用、资源余量、航空器占用；
- DamageEvent 当前阶段与 damage projection 结果。

前端不解释 DamageEvent，不重新计算资源库存、容量、航空器递推，也不插值“飞行中位置”。

新增只读端点：

`GET /api/runs/{run_id}/runtime`

要求 `runs.read`，仅 succeeded Run 可读取，并继承 Run owner/admin 权限边界。

## 前端 GIS Runtime

新增：

- `frontend/templates/pages/gis_runtime.html`
- `frontend/static/css/gis-runtime.css`
- `frontend/static/js/modules/gis-runtime.js`

页面结构按照冻结母版实现为：

- Situation 同系全幅 GIS；
- READ ONLY；
- 覆盖式显示控制；
- 图例；
- 对象速览；
- 底部时间轴与播放；
- 统一详情 Dock。

显示对象包括：全部机场、最终组群、实际参与机场、核心机场、任务、损毁状态、全部航链、出动腿、返航腿。

Single Run 的“打开运行态势”已指向真实 `/runs/{run_id}/runtime` 页面。

## GIS 一致性门禁

已自动验证：

1. Runtime 中机场数量与冻结 Snapshot 中机场数量一致；
2. Runtime route 的 `path_id` 唯一，且集合与 canonical Solution `sortie_chains.path_id` 完全一致；
3. 每个时间窗 Runtime departures/returns 总量与 canonical Metrics timeline 完全一致；
4. 出动和返航事件在各自真实窗口出现，不进行位置插值；
5. Damage active/recovering phase 与后端 damage projection 对齐；
6. queued/running/failed/cancelled Run 不伪造运行态势；owner 越权被拒绝。

## 前端边界

GIS Runtime 的 JavaScript 只消费正式 Run API。没有以下回退或业务重算：

- `/api/dispatch`
- `/api/scenes`
- `/api/runtime`
- `scene_file`
- legacy `operations`
- 前端 Damage 公式
- 前端资源递推
- 航链 pairing 猜测

地图适配器只尝试本地：

- `/static/vendor/leaflet/leaflet.css`
- `/static/vendor/leaflet/leaflet.js`

当前工程已经固定 Leaflet 本地资产槽位：

- `frontend/static/vendor/leaflet/leaflet.js`
- `frontend/static/vendor/leaflet/leaflet.css`
- 对应浏览器路径 `/static/vendor/leaflet/...`

实际 vendor 文件按当前人工决定留给 Codex/部署依赖阶段安装；工程内 `README.md` 已说明放置要求。地图内核缺失时页面会明确提示，不使用在线 CDN 伪装正式 GIS。

离线底图 URL 由 Flask 配置 `GIS_TILE_TEMPLATE` 注入，不在 JavaScript 中硬编码。未配置底图时仍可渲染 Leaflet 矢量机场、任务和航链；后续可以把离线瓦片放入批准的静态/专用 tile 路由，再设置该模板。禁止迁入旧 dispatch 业务链。

## 验证结果

当前 GIS + Results 收口后的统一回归：251 tests passed，0 failed。

同时通过：

- Python compileall；
- Jinja 模板解析；
- `api-client.js / run.js / single-run.js / gis-runtime.js` Node syntax check；
- 前端旧 Scene/dispatch/operations API 静态扫描。

## 下一阶段

GIS Runtime 本身收口。下一阶段才进入：

1. Codex/部署环境安装 Flask / Werkzeug 及项目依赖，并执行 TestClient HTTP smoke；
2. 将批准的 Leaflet vendor 文件放入既定本地槽位，并配置离线瓦片 `GIS_TILE_TEMPLATE`；
3. Chromium/目标浏览器执行真实页面、时间窗播放、图层切换和详情 Dock 检查。

Results 三工作区前端已经完成，不再作为 GIS 后续依赖。

以上环境验证不改变 GIS Runtime 的业务事实或 API 契约。
