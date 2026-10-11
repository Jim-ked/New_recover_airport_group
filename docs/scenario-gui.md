# 机场群场景库管理工具

## 启动

在项目/native 根目录使用自带 Python：

```powershell
.\.venv\Scripts\python.exe -m tools.scenario_db_tool.gui
```

运行时需要 `openpyxl`、`tkinter` 和配套 Tcl/Tk 文件；无需 PyInstaller。当前开发工作树的 Python 3.14 venv 没有 tkinter，可用现有 Codex Python 3.12 运行 GUI 源码：

```powershell
& 'C:\Users\ZJ\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m tools.scenario_db_tool.gui
```

Web/Worker 继续由 Windows 生命周期脚本使用项目 `.venv` 启动。GUI 所用 Python 与后台 Python 可以不同。

## 功能与调用链

- 场景管理：选择 xlsx、调用已有 reader/validator 检查、调用 compiler.build 生成数据库并刷新列表。
- 场景库：`runtime/db/scenes/*.sqlite3`，只读列表；重复生成由已有 compiler 使用新文件名发布。
- 默认启动：GUI → Popen → `deploy/windows/start.cmd` → `web_lifecycle.ps1`。
- 场景启动：GUI → Popen → `start_scene.cmd <sqlite绝对路径>` → 生命周期 `-SceneDatabase` → Web/Worker 共同继承 `AIRPORT_GROUP_DB_PATH`。
- 场景参数在环境加载后应用，不修改 `.env.local.ps1`；默认脚本仍使用 canonical 数据库。
- 状态：生命周期 `-Action status -StatusJson` 返回本项目 Web/Worker PID、端口及数据库。旧进程缺少有效生命周期状态时数据库显示 `<unknown>`。
- 停止：GUI → `stop.cmd` → 生命周期，只停止该项目进程；不按 Python 镜像名批量终止。
- 详细日志：`runtime/logs/scenario_gui.log`，轮转并隐藏敏感内容。

部署目录必须可写；当前设计不支持直接写入受保护的 Program Files 目录。切换场景前先停止当前系统。关闭管理工具不会主动停止后台系统。

## 后续 Native 重建条件

保留完整 `tools/scenario_db_tool`、backend、frontend、Windows deploy 脚本以及已有依赖；确认 Python 可 `import openpyxl`、`import tkinter` 并创建 Tk 窗口。native 必须包含 `start_scene.cmd` 和本轮更新的生命周期脚本。无需制作 exe；本轮未修改打包清单或生成 ZIP。

## 2026-10-10 回归

- GUI 服务/交付配置测试：20 项通过。
- 现有 scenario_db_tool 测试：26 项通过，源码与测试复制到隔离目录运行。此前原工作树默认 Worker 的并发写入使文件指纹保护检查失败，因此使用隔离目录验证保护检查。
- V1.4.3 实际模板：7 机场、7 机型、15 资源、8 任务、4 损毁方案。
- 隔离目录、18993 端口真实 Web/Worker：启动、登录、HTTP 核对 7/8/4、停止通过；运行中切换场景拒绝；端口占用拒绝；无效启动配置清理 Worker 与状态文件通过。
- Tkinter 真实窗口：检查 Excel、生成并刷新列表、场景启动、PID/数据库状态、停止通过。采用窗口对象方法驱动操作及截图观察，不声称鼠标点击自动化完成全流程。
- 修复停止竞态、PID 复用保护、状态残留、启动失败子进程清理及主动停止误报启动失败。
- compiler/validator/excel_reader 与项目 `.env.local.ps1` SHA256 和本轮基线一致；隔离默认库与配置在正常验收前后指纹不变。

结论：`READY_FOR_NATIVE_REBUILD`（GUI 源码与启动适配已完成；重建时必须补齐并验证 Tcl/Tk 运行时）。未运行 MIP、PyInstaller 或 native 打包。
