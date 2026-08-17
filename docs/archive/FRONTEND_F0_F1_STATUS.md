# Frontend F0/F1 重构状态

日期：2026-08-16

## 1. 本阶段定位

本阶段不是最终视觉验收，而是先把现有真实链 `Run → Single Run → GIS Runtime → Results` 从“按页堆叠样式/效果图复刻”重构为可继续扩展的统一前端基础，并消除已确认的 P0/P1 交互与视觉结构问题。

F0：共享设计与交互基础设施。
F1：现有四页的高优先级修复。

## 2. F0 已完成

- 新建 `tokens.css`：Shell 尺寸、颜色、字体、语义间距、行高、圆角、阴影、动效时长。
- 新建 `shell.css`：Topbar、Sidebar、账号区、账号浮层、统一模态框。
- 新建 `components.css`：按钮、输入、字段、segmented、消息、panel、disclosure、dock、表格、empty state、badge。
- 页面 CSS 不再拥有 `.app-shell/.topbar/.sidebar/.nav/.btn/.control` 等全局规则。
- 新建 `shell.js`：账号事实、权限分发、改密、登出、401 全局会话失效。
- `api-client.js` 统一 CSRF、401 事件和文件下载。
- 登录页与 session policy 已接入后端；密码/角色/禁用变更通过 `auth_revision` 使旧会话失效。
- 空闲超时和绝对超时由服务器会话策略执行；页面只消费 401，不自行猜登录有效性。
- 共享间距不再机械统一；字段内部、同组、跨组、页面区域采用不同 spacing token。
- 页面辅助文字下限调整到 10px；常规交互和正文主要为 11–13px。

## 3. F1 Run

- `run.css` 已重写为 Run 工作区专属 CSS，删除旧 Shell/按钮/输入重复定义。
- 页面允许工作区自然滚动；宽屏配置区 sticky，低高度不再强制把三块运行信息压进一屏。
- 高级设置/校验使用平滑 disclosure，header 稳定、body 展开。
- Run 进度不再显示由“已出现阶段数”估算的百分比，也不读取 `algorithm_progress`；显示 `阶段 X / 4 + 阶段名称`。
- 候选生成与快速评估保持一个视觉组，不伪造顺序阶段。
- 运行日志修复重复追加问题。
- 增加自动滚动、回到最新、复制、导出日志。
- 失败 Run 增加基于 immutable snapshot 的真正重试。
- viewer/operator 权限直接影响验证/运行/重试控件；后端仍为最终权限边界。
- validate → submit 必须携带服务器 `validated_input_hash`，外部修改输入后强制重新验证。

## 4. F1 Single Run

- `single-run.css` 已重写，使用共享 segmented/select 等组件。
- 五摘要、三中部区域、四底部区域保持；全机场/全任务/全机型，不做 Top-N。
- 中部旧“组群空间布局”改为“组群与航链结构（非比例示意）”，避免把经纬度分别拉伸的 SVG 误解为真实地图。
- 真正地理关系仍进入 GIS Runtime。
- 底部列表采用内容最小高度 + 内部滚动，不依赖恰好几行示例数据。
- 统一详情 Dock 使用浮动覆盖与动效，不推动主布局。
- “方案关注”仍不创造阈值规则。

## 5. F1 GIS Runtime

- 修复旧 `56px/72px` 自建坐标系，与共享 Shell 统一。
- “对象可见性”和“状态强调”明确分开：机场/任务/航链决定显示；组选/参与/核心/损毁只决定符号强调。
- 默认适应实际 Run 范围；“全部”按钮已真实绑定，可查看全部 Situation 对象。
- 增加播放速度和轨迹强调窗口。
- 航链使用稳定的二次 Bézier 采样曲线，减少重合；同一路径保持确定性曲率。
- 活跃/近期出动返航航段使用虚线流动效果，只表达“该窗存在活动”，不插值伪造航空器实时位置。
- 强连接阈值暂不实现，其业务语义仍未冻结。
- 详情 Dock 打开时速览面板淡出，避免右侧速览与底部详情竞争。

## 6. F1 Results

- `results.css` 已重写，Results 不再借用 Single Run 的 `.segmented/.mini-select` 样式。
- 主比较区使用弹性列宽，不固定 420px 右栏；低宽度下改为纵向布局并允许滚动。
- 比较条件覆盖面板为真正 overlay，不压缩背景主工作区。
- 主时序图增加 Y 轴刻度、时间刻度、hover 垂线和逐系列值 tooltip。
- 图表只做屏幕坐标映射与 hover 索引，不重算业务指标。
- PDF/CSV 导出读取后端 canonical report/export 结果并受 `results.export` 权限控制。
- 全机场、资源和方案结构仍保留完整数据。

## 7. 后端语义同步完成

- 专家三级指标权重：仅 `submitted` 专家评分参与；每个三级指标取专家平均评分，再在同一二级指标子项内归一化；全 0 时等权。草稿不参与。
- Base Data：单一当前态；新增/修改/删除和批量 JSON/CSV 覆盖导入统一写行为审计日志，不建立可选择的数据版本历史。`revision` 只用于并发编辑冲突。
- 批量导入：完整校验通过后单事务替换对应数据集。
- PDF：人读汇总报告；CSV：整理后的运行结果长表，不直接导出内部 JSON。
- 登录/会话：SQLite 用户权威、密码哈希、CSRF、idle/absolute timeout、auth revision 失效。

## 8. 本地资产策略

本阶段不下载、不复制旧 GitHub 静态素材。代码只固定本地槽位；人工/Codex 从现有本地项目材料放入指定目录。见 `LOCAL_ASSET_PLACEMENT.md`。

机场旧 JSON/GeoJSON 不得成为第二业务数据源；新 Base Data 始终是机场事实权威。

## 9. 当前非阻塞待办

- Flask 依赖安装、TestClient 和真实浏览器运行由后续 Codex 环境执行。
- 当前容器 Chromium 被管理策略阻止本地 URL/file 导航，因此本阶段不声称完成真实浏览器视觉验收。
- 本地 Leaflet/瓦片/登录视觉素材需人工按交接文件放置。
- F2：Base Data + Situation 完整前端。
- F3：Indicator 完整前端。
- GIS “强连接阈值”在业务含义冻结后再决定是否加入；当前曲线/动态强调已先解决主要线重合可读性问题。
- 最终仍需 1366×768、1672×941、1920×1080 三档真实浏览器人工验收。
