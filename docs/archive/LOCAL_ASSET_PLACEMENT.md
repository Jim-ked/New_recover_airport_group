# 本地静态素材人工放置说明

本阶段不从 GitHub 或互联网下载素材。以下文件均从现有本地旧项目材料人工复制；不存在时页面应使用降级状态，不允许 CDN fallback。

## 1. Leaflet

旧本地来源候选：
`interface/app/static/libs/leaflet/`

新工程目标：
`frontend/static/vendor/leaflet/`

至少放置：
- `leaflet.js`
- `leaflet.css`
- `images/layers.png`
- `images/layers-2x.png`
- `images/marker-icon.png`
- `images/marker-icon-2x.png`
- `images/marker-shadow.png`

建议直接复制完整旧 `leaflet/` 目录内容，保持 CSS 相对图片路径不变。

GIS Runtime 固定浏览器路径：
- `/static/vendor/leaflet/leaflet.js`
- `/static/vendor/leaflet/leaflet.css`

未放置时页面显示“本地地图内核未装载”，不会访问网络。

## 2. 可复用视觉素材

旧本地来源候选：
`interface/app/static/images/`

人工审查后可放：
- `logo.png` → `frontend/static/assets/legacy/logo.png`
- `login_bg.png` → `frontend/static/assets/legacy/login_bg.png`
- `airforce-emblem.png` → `frontend/static/assets/legacy/airforce-emblem.png`

是否实际使用由最终视觉验收决定。`airforce-emblem.png` 必须先确认交付环境是否适宜使用。

## 3. GIS 辅助素材

旧项目中的 `china_provinces.geojson`、瓦片覆盖检查脚本、离线瓦片等可作为 GIS/部署辅助素材，放入：
- `resources/gis/`
- `resources/tiles/`
- 或部署专用瓦片目录。

旧项目的 `airports_compact.geojson / airports_internal.json / airports_refined.json` **不得作为浏览器业务事实源**。它们最多用于初始化转换、数据迁移核对或人工检查；机场事实必须读取新系统 Base Data API / Run Snapshot。

## 4. 后续 SVG 图标

目标目录：
`frontend/static/icons/`

后续缺少的功能图标可由 image2 生成 SVG 后人工筛选，再放入该目录。原则：
- 重要操作保留文字标签，不能只依赖图标。
- 同一语义只保留一个图标版本。
- 图标尺寸、stroke 和视觉重量在最终设计系统中统一。
- 当前阶段不批量生成、不预塞装饰图标。

## 5. F2 Situation 复用

`/situations` 与 `/runs/<run_id>/runtime` 共用同一 Leaflet 本地目录：

- `/static/vendor/leaflet/leaflet.css`
- `/static/vendor/leaflet/leaflet.js`

不需要复制两份 Leaflet。Situation 缺失 Leaflet 时显示明确的“无底图坐标视图”；GIS Runtime 保持其只读降级提示。两页都禁止 CDN fallback。
