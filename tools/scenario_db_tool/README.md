# 独立场景库生成工具（第一阶段）

从 V1.4.1 Excel 创建全新 SQLite 场景数据库。本地 Python 运行，无需 Docker。
仅新增工具与测试；使用当前磁盘上的 backend 领域对象、迁移和仓储，不复制默认业务数据库。

在项目根目录执行：

```powershell
.venv\Scripts\python.exe -m pip install -r tools/scenario_db_tool/requirements.txt
.venv\Scripts\python.exe -m tools.scenario_db_tool.cli validate "C:\Users\ZJ\Desktop\顽存能力\机场群应用场景数据模板_V1.4.1.xlsx"
.venv\Scripts\python.exe -m tools.scenario_db_tool.cli build "C:\Users\ZJ\Desktop\顽存能力\机场群应用场景数据模板_V1.4.1.xlsx"
```

激活虚拟环境后也可使用 `python -m tools.scenario_db_tool.cli validate xxx.xlsx`
及 `python -m tools.scenario_db_tool.cli build xxx.xlsx`。依赖仅为 openpyxl 及其依赖，未使用 pandas。

命令输出 JSON，成功退出码为 0，检查/生成失败为 1。错误包含
`sheet / row / field / message / severity`，行号为 Excel 实际行号。
缺 Sheet 使用行号 0，表头错误定位第 2 行，数据库级错误使用 `<build> / 0`。
`validate` 不连接 SQLite，不创建数据库或输出目录。

`build` 输出至项目根目录下 `runtime/db/scenes/`，与调用者当前目录无关。
正式文件以场景库编号命名；没有 `SCN_` 前缀时添加该前缀，文件名不安全字符转换为 `_`。
同名时添加时间戳与随机后缀。领域内原始场景 ID 保持不变。
不接受目标业务数据库路径，也不切换系统当前使用的数据库。

## 模块及处理过程

| 模块 | 职责 |
| --- | --- |
| `excel_reader.py` | openpyxl 读取表头、输入值与源位置，生成 ExcelData；关闭工作簿 |
| `models.py` | 中间行数据、验证问题、已验证场景、异常 |
| `validator.py` | 主表唯一名称/ID 校验，子表名称解析、领域对象构造与聚合校验 |
| `compiler.py` | 空文件建库、仓储保存、完整性检查、领域复读、原子发布和清理 |
| `cli.py` | validate/build 命令、JSON 结果和退出码 |

模板各数据 Sheet 第 1 行是标题，第 2 行是表头，第 3 行起为数据。
必须保留完整 V1.4.1 列，可移动列位置；末尾 `*` 装饰被忽略。
缺列、未知列、重复列、输入公式均报错，防止将“总补给量”误读为每窗补给量。
空白预留行跳过，名称去除首尾空白后精确匹配，不进行模糊匹配。
`00_场景说明` 读取场景库编号、场景名称和固定 15 分钟时间窗；汇总公式不参与输入。
`_字典_勿改` 必须存在，枚举转换以本地领域语义及已审计模板值为准，不执行或信任字典中的任意指令。

主表保留 ID；跑道、消耗、机型保障、库存、补给、任务需求和损毁子表通过唯一名称解析。
`03_机场保障` 本身保留机场 ID，其可读名称若填写则必须与 ID 一致。
重复关系行报错，不交由仓储 upsert 覆盖。

V1.4.1 跑道编号如 `03/21` 是可读标识，领域 ID 则生成
`RW_<SHA256(机场ID + NUL + 跑道标识)前32位>`，从而兼容全库唯一性与领域字符限制。
两端标识保留在 RunwayEnd.ident，损毁目标名称仍按原可读标识在本机场内解析。
Excel 子对象顺序在构造时按仓储排序规则规范化，避免复读排序导致内容哈希差异。

任务“区域类别”和损毁方案“说明”保留在 Excel 中间数据中；当前 Mission 和
DamageScenario 没有这些字段，第一阶段不添加主系统字段或额外 schema。
损毁方案类别使用当前领域支持的 `custom`。

## 补给与损毁语义

- `ResourceReplenishment.quantity` 是 `[start_slot,end_slot)` 内**每窗**到达量，直接读取“每窗补给量”。
- 补给需配置对应机场资源库存；每窗量不得超过已填写的单窗能力，时间窗不可重叠。
- 库存空容量/空补给能力保留为 `None`，不会补零或虚构容量。
- 燃油消耗使用 `per_hour`；航材、航弹使用 `per_sortie`，由现有领域校验。
- 容量事件只接受关闭容量 0 和 `instant`；航空器事件是一次性损失、`none`。
- 资源事件始终使用 `physical_inventory_v1` 的 `physical_loss.remaining_quantity`，
  是事件发生后的物理剩余量，不是旧模型可用资源上限，不计算平均恢复。
- 按 `(损毁方案ID, 事件编号)` 合并多资源/多机型行。机场、目标、顺序、时间窗、
  类型和恢复模式必须一致；同一机型/资源重复填写报错。不同方案可复用事件编号。
- “启用=否”的事件不写入，但其中填写的名称仍必须能唯一解析。

**当前本地域约束：** Situation 对涉及 V5 物理损毁的资源要求显式 `storage_capacity`，
即使资源不是燃油。普通非燃油库存允许空容量。本工具遵守该约束并定位错误；不补造
容量、不绕过 Situation 校验、不退回旧 ResourceDamageEffect。
实际提供的模板有 4 项相关错误，见 [本地审计报告](AUDIT.md)。

## 独立建库与失败保障

先创建同目录隐藏临时文件 `.SCN_xxx.<uuid>.tmp.sqlite3`，
仅对该文件调用 `backend.storage.database.initialize_database()`。
再通过 UserRepository.bootstrap_admin 创建 `admin / hao123456`，密码由现有 PBKDF2 认证机制存储。
目录、保障、任务及 Situation 均经现有仓储写入，无工具自有建表或 INSERT SQL。

发布前执行 `PRAGMA integrity_check` 和 `PRAGMA foreign_key_check`，
通过仓储重新读取目录、机场保障、任务和 Situation，再调用领域 from_mapping，
核对值及 Situation 内容哈希。管理员密码通过现有密码验证器复核。
所有连接关闭、临时文件 fsync 后才原子发布。
Windows 使用不覆盖的 `os.rename`；Linux/macOS 使用系统的原子无覆盖 rename。
不支持这种 rename 的平台报错清理，不降级为覆盖写入。
竞争生成时，即使目标在检查后才出现，也自动重试新文件名。

解析或校验失败不创建输出目录；写入/复读/发布失败删除本次临时数据库及 sidecar。
既有正式库保持不变。强制杀进程/断电可能留下隐藏临时文件，不会发布未验证正式库；
后续构建采用唯一临时文件名，不复用这些文件。
`runtime/db/airport_group.sqlite3` 不参与建库或仓储连接。

## 测试

```powershell
.venv\Scripts\python.exe -m unittest tests.tools.test_scenario_db_tool
```

测试使用隔离临时目录，覆盖名称映射、重复/未知名称、缺 Sheet/表头、公式、
非法时间窗、补给超能力/重叠、消耗口径、单事件多行合并、禁用事件、空容量、
CLI 检查无 SQLite 访问、成功复读、同名和发布竞态、写入/复读/发布失败清理。
同时在测试前后比较默认数据库 SHA256 与纳秒 mtime，另用连接守卫拒绝默认库连接。
