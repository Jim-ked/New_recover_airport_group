# 本地接口及 V1.4.1 模板审计（2026-10-10）

工作树：`D:\Data\New_recover_airport_group`。本地已有大量未提交修改，包含
`backend/domain/catalog.py`、`backend/storage/database.py`、`backend/storage/airport_repository.py`。
实现读取当前磁盘内容，没有 reset、checkout、还原、覆盖既有文件或远端同步。
第一阶段仅新增工具目录和 `tests/tools/test_scenario_db_tool.py`。

## 当前保存接口

| 对象/入口 | 本地位置 | 调用 |
| --- | --- | --- |
| 空库初始化 | `backend/storage/database.py:91` | `initialize_database(path)`；当前 v001–v027 |
| AircraftType | `backend/domain/catalog.py:53`、`backend/storage/airport_repository.py:326` | `save_aircraft_type(item)` |
| ResourceType | `backend/domain/catalog.py:115`、`backend/storage/airport_repository.py:353` | `save_resource_type(item)` |
| 机型资源消耗 | `backend/domain/catalog.py:147`、`backend/storage/airport_repository.py:367` | 本地名为 AircraftResourceRequirement；`save_aircraft_resource_requirement(item)` |
| 机场静态信息 | `backend/storage/airport_repository.py:72` | `save_airport(base)`，含跑道 |
| AirportOperationalProfile | `backend/storage/airport_repository.py:450` | `save_operational_profile(profile)`，含 aircraft_support/resource_stocks |
| Mission | `backend/storage/mission_repository.py:42` | `MissionRepository(path).save(mission)` |
| Situation、DamageScenario | `backend/storage/situation_repository.py:100` | `save_situation(situation, owner_user_id="admin")`；损毁方案随整个聚合保存 |
| 初始管理员 | `backend/storage/user_repository.py:214` | `bootstrap_admin(login_name="admin", password="hao123456")`，仅空用户表创建 |

仓储明确接收数据库路径；工具传入本次临时文件，不调用 web/app 启动入口。
本地 bootstrap 参数名是 `login_name`，不是 `login`。
领域对象先通过 `from_mapping()` 校验，再进入仓储。

## 当前 V5 资源损毁构造

本地对象为 `backend/domain/damage.py:138` 的 DynamicInventoryResourceDamageEffect。
通过 DamageEvent.from_mapping(effect=...) 中的以下字段触发当前 V5 对象：

```python
{
    "inventory_semantics": "physical_inventory_v1",
    "physical_loss": {"remaining_quantity": {"RS001": 3000000}},
}
```

纯库存损失使用 `recovery_mode="none"`。领域还支持显式 storage_capacity_damage，
但 V1.4.1 没有此输入列，第一阶段不推断或生成存储容量损毁。
Situation.__post_init__ 要求涉及物理损毁的资源已有 storage_capacity，
要求物理剩余量不超过该容量，并检查同一资源损毁区间不能重叠。
普通非燃油库存空容量可以保存，这是损毁聚合的更强约束，不是数据库建表限制。

容量关闭通过 CapacityDamageEffect(remaining_capacity_per_window=0)，instant 恢复；
航空器损失通过 AircraftDamageEffect(aircraft_loss)，一次性 none 恢复。
所有事件仍遵守 `[start_slot,end_slot)` 的领域时间窗。

## 实际模板

读取 `C:\Users\ZJ\Desktop\顽存能力\机场群应用场景数据模板_V1.4.1.xlsx`。
数据表标题行 1、表头行 2；场景说明和字典标题行 3。空白预留行跳过后：

| 数据 | 行数 |
| --- | ---: |
| 机场 / 跑道 | 7 / 5 |
| 机型 / 资源 / 机型资源消耗 | 7 / 15 / 24 |
| 机场保障 / 机型保障 / 资源库存 / 补给 | 7 / 49 / 105 / 27 |
| 任务 / 任务需求 | 8 / 28 |
| 损毁方案 / 启用事件输入行 | 4 / 19 |

所有实际表头匹配读取器。模板补给为“每窗补给量”；无需总量转换。
03_机场保障保留 ID 与可读名称；其余业务子表通过名称解析。
跑道编号为如 `03/21` 的可读标识，工具需生成全局唯一领域 ID 并保留两端名称。
任务区域类别、损毁方案说明没有当前领域对应字段，保持中间值并在使用说明中明确范围。

原模板 validate/build 均返回以下错误（未创建数据库）：

| Sheet | 行 | 字段 | 本地域约束 |
| --- | ---: | --- | --- |
| 05-1_损毁事件 | 7 | 资源名称 | 龙田 / 中距空空导弹（AP004/RS012）缺 storage_capacity |
| 05-1_损毁事件 | 8 | 资源名称 | 龙田 / 制导炸弹（AP004/RS013）缺 storage_capacity |
| 05-1_损毁事件 | 9 | 资源名称 | 霞浦 / 歼-20基础包（AP003/RS002）缺 storage_capacity |
| 05-1_损毁事件 | 10 | 资源名称 | 霞浦 / 歼-20飞机战储器材（AP003/RS006）缺 storage_capacity |

要生成保留这些损毁的正式库，需要在资源库存子表显式提供真实容量数据。
工具不修改原始 Excel，不自行填容量，不绕过 V5 校验。

默认数据库审计前及回归测试后保持：

- SHA256：`438162330F6EA56F990AA93A312B3567697308AE808FA0A4500411F3E554CE85`
- 大小：7,348,224 字节。
- mtime UTC：2026-10-09 13:29:19（测试另比较纳秒精度）。

## 验证结果

26 项新增工具测试通过；65 项相关 catalog、airport_operations、damage、mission、
situation 领域及 database、airport、mission、situation、user 仓储回归测试通过。
工具与测试通过 compileall。实际模板 CLI 检查和生成均定位上述四项输入错误，
没有生成正式或临时数据库。成功路径使用完整 V1.4.1 表头的隔离工作簿验证，
不是声称原始模板已成功生成数据库。

当前项目虚拟环境已安装工具 requirements 中的 openpyxl 3.1.5。
测试需普通本机权限：本会话的 Windows 沙箱阻止访问 Python 创建的私有临时目录，
在正常权限下测试通过。测试数据库均位于隔离临时目录。
Windows 原子 rename 已验证；Linux/macOS 分支未在当前 Windows 主机运行验证。
