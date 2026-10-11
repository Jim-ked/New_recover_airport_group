# Native V1.1 构建与 Fresh 验收报告

日期：2026-10-10（Asia/Shanghai）

结论：**READY_FOR_DELIVERY**。

## A. 构建方式

入口：`deploy/native/build_native.ps1`。清单：`deploy/native/package_manifest.json`。辅助：`package_native.py`、`setup_native.ps1`、`README.md`。

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/native/build_native.ps1 -OfflineAssets "D:\Data\New_recover_airport_group\runtime\temp\native-v1.1\assets" -OutputDirectory "D:\新的构建输出目录"
```

构建直接读取 `D:\Data\New_recover_airport_group` 当前本地文件，包括未提交的代码。全部 258 个 backend/frontend/Windows scripts/algorithm overlay/scenario tool 核心文件在最终构建与验收期间保持字节一致。未修改算法、V5 链路、GUI 逻辑或源场景数据。

旧 Native 仅提供原有官方 Python 安装器和 wheel 静态资产；没有取用旧 backend/frontend/数据库、`.python` 或 `.venv`。Tcl/Tk 的官方 MSI 组件与新增 openpyxl/et_xmlfile wheels 单独补齐。没有 PyInstaller、Docker 或开发 venv 复制。

## B. 最终交付物与文件清单

交付目录：`D:\Data\Native_V1.1_Release`。

- `AirportGroupSystem_Native_V1.1.zip`：最终 ZIP。
- `AirportGroupSystem_Native_V1.1/`：未经安装/测试写入的构建目录，和 ZIP 内容相同。
- `AirportGroupSystem_Native_V1.1.release.json`：ZIP 名称、大小、文件数。
- `Native_V1.1_验收报告.md`：本报告的交付副本。

ZIP 含 **304 个文件**。包内 `file_manifest.json` 提供路径、size、来源，清单自身声明不递归记录自己；包内 `build_provenance.json` 提供构建时间、工作树基线及依赖版本。

| 内容 | 文件数 |
| --- | ---: |
| backend | 132 |
| frontend | 96 |
| deploy/windows + deploy/native | 15 |
| original_algorithm_overlay | 9 |
| tools/scenario_db_tool | 11 |
| offline/python + offline/wheels + lock | 23 |
| 默认数据库 + 已有场景库副本 | 3 |
| docs、tests、seed/migrations、根启动器及记录 | 15 |

明确排除 `.venv`、`.python`、`.pytest_cache`、`__pycache__`、`.git`、本机 `.env.local.ps1`、瓦片、日志和运行目录历史文件。fresh 安装后新建 `.venv` 不属于交付 ZIP 内容。

## C. 离线依赖

- Python **3.14.0 amd64**：官方完整安装器及 core/exe/lib/tcltk MSI。
- Tcl/Tk **8.6**：随官方 tcltk MSI 提取。
- openpyxl **3.1.5**、et_xmlfile **2.0.0**。
- PySCIPOpt **6.2.1**、numpy **2.5.2**、Flask **3.1.3**、waitress **3.0.2**、reportlab **4.5.1**。
- 其余完整依赖见包内 `offline/requirements-native.lock.txt`，共 17 个 wheel，包含 pip。

Python 介质验证 Python Software Foundation 的 Authenticode 签名。setup 通过 `/a` 提取到包内 `runtime/python`，不查找/复用本机注册 Python，也不更改全局 PATH。安装依赖全程使用 `--no-index --find-links offline/wheels`，不存在联网安装步骤。

按用户最新要求，不生成或交付逐文件/ZIP SHA256；源数据库 SHA256 仅用于内部保护比对，不将数值写入交付报告或包内清单。

## D. 最终 Fresh 安装

最终目录：`D:\Data\AirportGroupSystem_Native_Test_V1.1_Final`。它由最终 ZIP 新解压产生，没有已有 `.venv` 或 Python。

执行：

```powershell
setup_native.cmd -TileRoot "D:\Data\New_recover_airport_group\resources\tiles" -Port 18994
```

结果：**NATIVE_SETUP_PASS**。

- Python 3.14.0 64 位通过。
- `python -c "import openpyxl"`、`python -c "import tkinter"` 通过。
- `pip check`：No broken requirements found。
- `backend check` 通过，fresh 默认库 3 用户、566 机场，使用 18994 端口。
- `sys.base_prefix` 指向 fresh 包的 `runtime/python`，不指向开发环境。
- 最终 fresh 环境运行 GUI/交付配置 20 项与 scenario_db_tool 26 项，**46/46 PASS**。

初次候选 setup 因 PowerShell 签名模块搜索路径失败；修复为显式加载 Windows PowerShell 自带模块。最终 ZIP 的全新目录重新安装成功，没有复用失败目录。

## E. GUI 验收

`scenario_gui.cmd` 实际打开交付 GUI，已观察标题、场景管理页、列表、日志和布局。

用 fresh Native 自身 Python 3.14/Tcl/Tk 实例化真实窗口，调用窗口操作方法验收：

1. V1.4.3 Excel 验证通过：7 机场、7 机型、15 资源、8 任务、4 损毁方案。
2. 场景库生成至 fresh `runtime/db/scenes`，列表刷新通过。
3. GUI 调用场景启动脚本，Web/Worker PID 与所选数据库显示通过。
4. GUI 停止系统通过，没有主动停止误报启动失败。

没有修改 GUI 逻辑；没有编写鼠标点击单元测试。全流程窗口操作方法驱动与 HTTP 验收结合，不宣称鼠标点击完成全部步骤。

## F. 默认系统启动

fresh GUI service → `start.cmd` → 现有生命周期 → fresh Web/Worker。

默认库副本启动成功，登录成功，Web 页面返回成功，外置 world 瓦片 `/tiles/world/2/0/0.jpg` 返回成功。`stop.cmd` 停止成功，端口释放、Worker 无残留。

## G. 场景系统启动

fresh GUI service → `start_scene.cmd <绝对路径>` → 生命周期 → Web/Worker 共同继承场景数据库路径。

新生成 V1.4.3 库启动成功，登录成功，HTTP 接口核对 **7 机场 / 8 任务 / 4 损毁方案**。页面和外置瓦片可读取，停止成功。最终状态 Web PID、Worker PID 均为空，18994 端口空闲。

瓦片按用户要求不携带。部署时：`setup_native.cmd -TileRoot "部署机已有瓦片根目录"`。根目录直接包含 `tiles_world` 等 source 子目录。

## 数据库保护与并发记录

初始内部检查检测到源项目 Web/Worker 于 17:04:54 并发启动，源默认库随后变化；该次原始基线检查未通过。两个已有场景库始终未变。本轮所有验收启动命令均指向 fresh 目录，未用 fresh 测试操作源库。

用户随后明确授权停止源系统、保留已发生的数据变化并建立新基线。执行源 `stop.cmd` 时系统已退出；没有回滚数据库。最终 ZIP 从新基线重新构建，并重新执行完整 fresh 安装与验收。

最终基线至验收结束，源默认库和两个已有场景库的 **SHA256、size、mtime_ns 全部一致**：保护通过。fresh 副本允许迁移、登录和 Worker 的正常写入；测试后副本没有回写到交付包。测试未提交 Run，没有执行 MIP。

## H / I / J. 大小、校验记录与交付状态

- ZIP：**175,147,277 bytes（约 167.03 MiB）**。
- SHA256：按用户最新要求取消逐文件和 ZIP 输出。
- ZIP CRC 完整性检查通过；排除目录扫描通过；源码与输入复制内部核对通过。
- 最终状态：**READY_FOR_DELIVERY**。
