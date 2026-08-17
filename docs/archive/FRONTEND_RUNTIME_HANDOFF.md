# Flask / 前端运行环境后续交接

本文件只记录当前代码已经预留好的运行位置，供后续 Codex/部署阶段使用；不改变业务/API 契约。

## 1. Python Web 运行依赖

`requirements.txt` 已声明：

```text
Flask>=2.3,<4
```

当前网页会话不强制安装 Flask。后续环境安装后再执行真实 `create_app` / TestClient / 浏览器 smoke。

PySCIPOpt / SCIP 仍保持独立部署任务，不在这里为了 Web 检查临时 pin 版本。

## 2. Leaflet 本地静态资产

固定放置位置：

```text
frontend/static/vendor/leaflet/
├─ leaflet.js
├─ leaflet.css
└─ images/              # 当前 divIcon 不依赖；未来若用默认 marker 再放
```

GIS Runtime 浏览器只加载：

```text
/static/vendor/leaflet/leaflet.js
/static/vendor/leaflet/leaflet.css
```

禁止 CDN fallback。

## 3. 离线瓦片

GIS 页面从 Flask 配置读取：

```text
GIS_TILE_TEMPLATE
```

例如，若后续将离线瓦片作为 Flask static 内容提供，可配置成：

```text
/static/tiles/{z}/{x}/{y}.png
```

也可以指向项目批准的专用本地 tile route。JS 不关心具体存储位置，只接收同源 URL template。

未配置 `GIS_TILE_TEMPLATE` 时不创建 tile layer，但机场、任务、损毁和航链矢量逻辑不变。

## 4. 已有页面路由

```text
/run
/runs/{run_id}
/runs/{run_id}/runtime
/results
```

对应前端模块：

```text
api-client.js
run.js
single-run.js
gis-runtime.js
results.js
```

## 5. 后续真实运行检查范围

Codex/部署依赖补齐后再执行：

- Flask app 创建；
- Session / RBAC / CSRF 的真实 HTTP 请求；
- Run validate/submit/list/events/cancel；
- Single Run；
- GIS Runtime Leaflet 初始化、瓦片加载、时间窗播放、图层开关和详情 Dock；
- Results 三工作区条件选择、比较请求、完整机场表和浮层交互；
- 目标浏览器尺寸/缩放下的母版视觉检查。

运行环境检查不得通过加入旧 Scene/dispatch/results fallback 来“修复”页面。
