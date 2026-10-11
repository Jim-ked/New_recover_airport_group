# AirportGroupSystem Native V1.2

## 离线安装与启动

解压到可写目录（不要放在 Program Files）。Python 3.14.0 amd64、Tcl/Tk 和所有 wheels 已随包提供，无需联网、Docker 或预先安装 Python。

```powershell
setup_native.cmd -TileRoot "D:\已有瓦片目录"
scenario_gui.cmd
```

瓦片目录应直接包含 `tiles_world`、`tiles_china_east` 等 source 子目录；本包不携带瓦片。不传 TileRoot 时使用包内空的 `resources/tiles`，地图需要随后配置 `.env.local.ps1` 的 `AIRPORT_GROUP_TILE_ROOT`。可用 `-Port 18994` 做独立端口验收。已有 `.env.local.ps1` 会保持不变，后续参数不会覆盖它。

setup 从官方签名的 core/exe/lib/tcltk MSI 使用 `/a` 提取 Python 至 `runtime/python`，创建全新 `.venv`，通过 `--no-index` 从 `offline/wheels` 安装锁定依赖，验证 openpyxl/tkinter，并执行 backend check。不会复制开发 `.venv` 或修改本机 Python 注册/全局 PATH。

数据库为当前工作树的副本。首次 check 会应用当前迁移；登录、后台 Worker 和业务操作会正常写部署副本。保护源工作树数据库不变，不将测试后的运行目录回写到交付 ZIP。

默认启动：`deploy/windows/start.cmd`。场景启动：GUI 或 `deploy/windows/start_scene.cmd <sqlite绝对路径>`。停止：`deploy/windows/stop.cmd`。状态：生命周期 `-Action status -StatusJson`。切换场景前先停止当前系统。

## 构建

从当前工作树执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File deploy/native/build_native.ps1 -OfflineAssets "D:\Native离线资产" -OutputDirectory "D:\交付目录"
```

资产目录需要 `python/python-3.14.0-amd64.exe`、`python/msi/{core,exe,lib,tcltk}.msi` 和 `wheels/*.whl`。本轮补充 openpyxl 3.1.5、et_xmlfile 2.0.0；后台 PySCIPOpt 6.2.1 保持已验证版本。官方介质来自 `https://www.python.org/ftp/python/3.14.0/amd64/`，运行时组件经过 Python Software Foundation 签名校验。

`package_manifest.json` 控制源码纳入和排除范围，所有代码直接来自本地文件（包括未提交更新）。`file_manifest.json` 记录逐文件路径、size 和来源；`build_provenance.json` 记录构建时间、工作树来源和离线依赖。按用户要求不记录逐文件或 ZIP SHA256。构建内部仅以数据库指纹和复制校验保证源数据不变。

交付包排除 `.venv`、`.python`、`.pytest_cache`、`__pycache__`、`.git`、本机配置、日志、运行历史目录和瓦片。安装后创建的 `.venv` 属于 fresh 部署环境，不是复制的开发环境。输出目录或 ZIP 已存在时拒绝覆盖，请使用新输出目录。

## 交付验收

在全新目录解压本次 ZIP，执行 setup，检查 Python 版本、pip check、backend check、GUI、Excel 验证/生成、默认及场景 Web/Worker 启动、登录和停止。禁止提交算法任务。源码/源数据库保护及详细测试结果另见工作树 `docs/native-v1.1-validation.md`。

## V1.2 场景校准

福建场景 `runtime/db/scenes/SCN_FUJIAN_001.sqlite3` 与同名 `.f2-calibration.json` 必须一同部署。场景校准由当前 RunSnapshotService 加载并冻结，机型权重兼容修复来自当前开发主线。未修改模型公式或约束。

当前指定福建库的 physical_inventory_v1 求解存在既有补给容量缺失，运行条件校验通过不等于求解通过。完整验证结果见发布目录 Native_V1.2_验收报告.md。
