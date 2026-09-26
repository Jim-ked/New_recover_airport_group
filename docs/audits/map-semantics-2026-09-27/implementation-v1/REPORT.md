# 情境地图显示逻辑统一与第一版视觉修复实施报告

日期：2026-09-27

基线：`review/minimal-model-r1` / `542818e`

验证浏览器：Google Chrome 153（Headless Chromium），1440×900，DPR 1 与 DPR 2

结论：第一版规则已实现并通过回归；等待人工确认视觉参数，不继续第二套图标设计。

## 1. 最终显示规则与实现

| 规则 | 最终实现 | 证据状态 |
|---|---|---|
| 业务身份 | 机场按 `airport_id`、任务按 `mission_id` 排重；不按坐标合并 | 浏览器实测确认 |
| 基础参考 | 缓存与显示组分离；只绘制当前情境和可见候选之外的对象；机场 Pane 580，低于业务 markerPane 600 | 浏览器实测确认 |
| 绘制/命中 | 正式选中 1000、聚焦 700、候选勾选 500、当前模式主对象 300、普通业务对象 100；fallback 使用同一权重 | 浏览器实测确认 |
| 参考交互 | 仅保留 hover 提示，不绑定编辑回调；任务取点期间 `pointer-events:none`，完成/取消后恢复 | 浏览器实测确认 |
| 标签 | 正式选中 100 > 聚焦 90 > 来源预览 88 > 当前模式主对象 85 > 普通机场 80 > 普通任务 75；候选仅聚焦时主动显示提示 | 浏览器实测确认 |
| 状态语义 | `map-state-selected`、`map-state-focused`、`candidate-queued`、`has-damage-config`、`mission-source-preview`、`mission-location-draft` 分离 | 浏览器实测确认 |
| 损毁提示 | 表示当前情境任一损毁场景中存在引用该机场的事件配置；右上角红色菱形徽标，不表达实时损毁或严重程度 | 浏览器实测确认 |
| 机场规格 | 当前主体 13px / 24px 点击容器；候选 11px / 20px；基础参考 8px / 16px | 浏览器实测确认 |
| 机场类别 | 民用蓝色圆形、军用橙色三角形、军民两用紫色六边形；任务继续使用独立菱形 | 浏览器实测确认 |
| 图例 | 原图层面板内补充机场类别、对象来源、待加入和损毁配置说明 | 浏览器实测确认 |

主要实现位置：

- `frontend/static/js/modules/situation-map.js:9`：参考 Pane、排重缓存、状态权重、fallback 权重和任务取点交互。
- `frontend/static/css/situations.css:202`：图例；`:618` 起为三类配色、尺寸及状态装饰。
- `frontend/templates/pages/situations.html:101`：图层面板内图例。
- `frontend/static/js/modules/situation-panels.js:116`：参考层开关与页面销毁清理。

## 2. 四种工作模式、排重与鼠标命中

受控样本包含 3 个当前机场、1 个当前任务、同 ID 基础对象、不同 ID 同坐标对象以及基础独有对象。

| 模式 | 当前机场 / 候选 / 当前任务 | 基础机场 / 基础任务 | 结果 | 证据状态 |
|---|---:|---:|---|---|
| 情境总览 | 3 / 0 / 1 | 5 / 2 | 当前对象常显；同 ID 参考为 0 | 浏览器实测确认 |
| 添加机场 | 3 / 5 / 1 | 0 / 2 | 所有可见机场候选排除对应参考；候选可正常勾选、聚焦 | 浏览器实测确认 |
| 编辑任务 | 批量加入后 6 / 0 / 1 | 2 / 2 | 当前任务同 ID 参考为 0；来源预览规则保持独立 | 浏览器实测确认 |
| 编辑损毁 | 3 / 0 / 1 | 5 / 2 | 当前机场为主对象；损毁徽标不覆盖类别主体 | 浏览器实测确认 |

具体结果：

- 当前机场、当前任务、可见候选与相同 ID 的参考标记数量均为 `0`。
- 不同 ID 同坐标时，`elementFromPoint` 命中当前机场或当前任务；对象仍分别保留，可从列表选择。
- 任务取点期间参考机场计算样式为 `pointer-events:none`；取点完成后恢复为 `auto`。
- 受控批量加入 3 个候选后，当前机场由 3 增至 6，`candidate-queued` 清零，页面提示“已加入 3 个机场，尚未保存。”
- 关闭参考层无遗留 marker；情境切换、候选搜索和模式退出的排重重建均由浏览器回归覆盖。

## 3. 真实渲染与前后对照

修改前证据来自专项审计的相同受控样本；修改后截图和完整计算样式见本目录的 `verification.json`。

| 场景 | 修改前 | 修改后 |
|---|---|---|
| 总览与全部参考 | [01-overview-all-references.png](../01-overview-all-references.png) | [after-overview-references-dpr1.png](after-overview-references-dpr1.png) |
| 候选未勾选/勾选/聚焦 | [02-candidates-unselected-checked-focused.png](../02-candidates-unselected-checked-focused.png) | [after-candidate-states-legend-dpr1.png](after-candidate-states-legend-dpr1.png) |
| 正式选中与损毁配置 | [05-current-airport-selected.png](../05-current-airport-selected.png) | [after-current-states-dpr1.png](after-current-states-dpr1.png) |
| 无底图坐标视图 | [08-fallback-candidate-states.png](../08-fallback-candidate-states.png) | [after-fallback-states-dpr1.png](after-fallback-states-dpr1.png) |

补充证据：

- [DPR 2 当前状态](after-current-states-dpr2.png)
- [任务取点与参考层](after-mission-pick-references-dpr1.png)
- [565 候选 DPR 1](after-dense-565-dpr1.png)
- [565 候选 DPR 2](after-dense-565-dpr2.png)

计算样式确认：

- 民用：`border-radius: 50%`，填充 `rgb(23,121,173)`。
- 军用：三角形 `clip-path`，填充 `rgb(168,95,36)`。
- 军民两用：六边形 `clip-path`，填充 `rgb(114,86,168)`。
- 正式选中为 2px 外部矩形框；聚焦为 1px 虚线外框；损毁为 5×5px 红色徽标。三者均不改变或覆盖主体形状。
- 当前、候选、参考机场分别位于 24、20、16px 容器内，主体为 13、11、8px。

## 4. fallback、标签与性能回归

- fallback 正式选中、聚焦、候选勾选、候选聚焦和损毁配置使用与 Leaflet 相同的业务类名；同坐标状态对象使用相同 z-index 权重。浏览器实测确认。
- 普通候选不显示永久标签；聚焦候选显示 tooltip；正式选中标签强制保留，聚焦次之。浏览器实测确认。
- 565 候选从 565 缩小为 10 时移除 555 个 marker，保留候选 `DENSE-000` 的 DOM 节点实例；恢复时只重建此前移除的 555 个。DPR 1/2 结果一致，无 page error。浏览器实测确认。
- 原审计 fallback 超时用例在实施前单跑 `1 passed in 1.87s`；最终与 565 候选用例合跑 `2 passed in 3.92s`。已排除实际功能缺陷，结论为原等待/环境波动。

## 5. 测试与质量门禁

- 阶段一定向与关联：39 项通过；一次 fixture 初始化超时单独复跑通过。
- 阶段二关联：45 项通过。
- 阶段三关联：48 项通过。
- 完整 `tests/frontend`：`225 passed in 38.97s`。
- `node --check frontend/static/js/modules/situation-map.js`：通过。
- `git diff --check`：无错误，仅工作区 LF/CRLF 提示。
- Chromium 受控验证：DPR 1、DPR 2、正常、密集候选和 fallback 均无 page error。
- 五轴审查：正确性、可读性、架构、安全、性能无阻断项；未新增依赖，动态文本继续使用 `escapeHtml`。

## 6. 提交记录与修改范围

| 提交 | 内容 |
|---|---|
| `9867443` | `fix: prioritize situation map objects over references` |
| `d885ba6` | `fix: separate situation map display states` |
| `e8f4ce6` | `fix: improve situation map marker legibility` |
| `e783319` | `test: align map visual acceptance contracts` |

业务修改仅涉及 `situation-map.js`、`situation-panels.js`、`situations.css` 和 `situations.html`；其余为定向测试与证据。未修改底图、瓦片、算法、领域模型、机场详情、正式数据库或标准数据；未推送、未部署。

## 7. 待人工确认

以下均为“设计规则待确定”，不影响当前语义正确性：

1. 蓝/橙/紫三类颜色在实际投影设备上的饱和度是否需要再降低。
2. 13/11/8px 主体和 24/20/16px 点击容器在超密集真实数据中的最终平衡。
3. 正式选中 2px 实线框、聚焦 1px 虚线框和 5px 损毁徽标是否需要微调明度或间距。
4. fallback 仅同步状态与层级，没有重建 Leaflet 的完整标签避让；是否接受当前简化表现。

第一版到此暂停，等待人工查看截图后再决定是否调整视觉参数。
