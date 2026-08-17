# F2 Base Data + Situation 状态

## 范围

F2 只完成 Base Data 与 Situation 两个产品面，不扩展 Indicator。

- Base Data：机场、任务模板、机型、保障资源当前态维护。
- Situation：GIS 优先 Working Copy 编辑、保存、冲突、任务/机场/损毁场景配置。
- 继续沿用 F0 共享 Shell / tokens / components。

## 已冻结的关键口径

1. Base Data 只有一份当前态，不维护可选择的数据版本历史。
2. `revision` 只用于单记录并发冲突检测，不属于业务版本。
3. JSON/CSV 批量导入采用“全量校验后事务覆盖当前数据集”，操作摘要进入统一 append-only 行为日志。
4. 已保存 Situation 与历史 Run 保持值快照，不随 Base Data 后续修改自动变化。
5. Situation 使用一个前端 Working Copy；机场/任务/损毁场景的直接编辑不单独持久化，整份 Situation 保存。
6. 每次“应用到情境”先调用 `/api/situations/working-copy/canonicalize`，用后端同一领域模型立即校验，但不增加用户可见“情境校验”流程。
7. Situation 保存使用 `expected_content_hash` 乐观锁。
8. Base Data 保存使用 `expected_revision` 乐观锁。
9. 编辑器草稿与 Working Copy 未保存状态分层处理，离开时分别保护。
10. 损毁场景分类保持现有 `low / medium / high / custom`。不把“极端、持续”扩展成新 `DamageScenario.category`。
11. 当前损毁编辑仅开放机场级 target；没有可靠对象选择来源的 runway/support_element target_id 不由前端手输伪造。
12. Leaflet、logo、login background 等只保留本地路径契约，不从 GitHub/互联网下载。

## Base Data 已实现

- 机场 / 任务模板 / 机型 / 保障资源四页签。
- 搜索；机场类型、区域筛选；机场和任务服务端分页。
- 机场基础数据、跑道结构、运行配置、支持机型、资源库存维护。
- 任务模板及动态机型需求维护。
- 机型参数及资源消耗关系维护。
- 资源类型维护。
- 新增 / 编辑 / 删除，后端引用保护。
- JSON / CSV 覆盖式导入。
- 当前数据集 JSON 导出。
- 编辑器本地草稿保护：切页签、搜索、筛选、翻页、选其他记录、离开页面均不会静默丢失。
- 支持 `/base-data?tab=...&id=...` 深链定位。

## Situation 已实现

- 一级导航真实入口 `/situations`。
- 新建 / 打开 / 编辑根信息 / 保存 / 删除。
- 未保存 Working Copy 状态与 content-hash 冲突处理。
- 机场基础库候选：名称/编号、类型、区域筛选；地图和候选列表双向选择。
- 从 Base Data 复制机场进入 Working Copy。
- 情境机场配置：容量、保障等级、支持机型、资源库存、实际补给计划。
- “恢复基础配置”由后端复制边界完成，并清除该情境机场的补给安排。
- “查看基础数据”跳转到 Base Data 对应机场。
- 任务：新建、任务模板复制、历史 Run 冻结任务复制、编辑、移除。
- 正式 Leaflet 存在时任务支持地图取点；缺失时手工坐标 + 无底图坐标预览。
- 损毁场景：低/中/高/自定义；机场级容量、资源、导航延迟、航空器损失事件。
- 图层控制、对象搜索、适应范围、底部情境总览。
- viewer 只读；不会出现“能编辑但最后保存才 403”的假交互。
- 右侧编辑器草稿有取消/离开确认；应用后才进入页面级 Working Copy dirty。
- 每次应用通过后端 canonicalize；非法对象不会先进入前端 Working Copy。

## 视觉与响应式

- F2 页面 CSS 只描述各自工作区，继续使用 F0 公共控件。
- F2 显式字号下限 10px。
- Situation 使用全幅 GIS + overlay surface；右侧 inspector 收起时底部 overview 自动扩展。
- Base Data 低宽度下列表/详情改为纵向布局，页面允许自然滚动。
- Disclosure/Modal/Inspector 仍使用 F0 动效变量，不新造另一套动画节奏。

## 验证

- F2 专项契约：通过。
- 全量回归：299 tests passed, 0 failed。
- 7 个 Jinja 页面离线渲染：通过。
- 9 个前端 JS 模块 `node --check`：通过。
- Legacy API scan：0。
- CDN / 网络运行时引用：0；`http://www.w3.org/2000/svg` 仅为 SVG namespace。
- `situations.css` / `base-data.css` 最小显式字号：10px。

## 仍后置

- 真实 Flask/TestClient/浏览器运行环境由 Codex/部署环境完成。
- Leaflet 本地实物与离线瓦片由人工放到固定目录。
- 浏览器 1366×768 / 1672×941 / 1920×1080 三档视觉人工验收仍需真实运行后执行。
- Indicator 属于 F3。
