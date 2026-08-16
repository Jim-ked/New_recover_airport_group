from __future__ import annotations

import sqlite3

DEFAULT_SET_ID = "INDSET-V1.1-DEFAULT"

SCHEMA_SQL = r"""
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS indicator_sets (
    indicator_set_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    version TEXT NOT NULL,
    is_default INTEGER NOT NULL CHECK (is_default IN (0,1)),
    status TEXT NOT NULL CHECK (status IN ('draft','published','disabled')),
    description TEXT,
    revision INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_indicator_default_published
ON indicator_sets(is_default) WHERE is_default=1 AND status='published';

CREATE TABLE IF NOT EXISTS indicator_nodes (
    indicator_id TEXT PRIMARY KEY,
    indicator_set_id TEXT NOT NULL,
    parent_id TEXT,
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    level INTEGER NOT NULL CHECK (level IN (1,2,3)),
    node_kind TEXT NOT NULL CHECK (node_kind IN ('CATEGORY','ABSTRACT','DIRECT')),
    unit TEXT,
    direction TEXT CHECK (direction IS NULL OR direction IN ('positive','negative','neutral')),
    weight REAL CHECK (weight IS NULL OR weight >= 0),
    description TEXT,
    is_core INTEGER NOT NULL DEFAULT 0 CHECK (is_core IN (0,1)),
    editable INTEGER NOT NULL DEFAULT 1 CHECK (editable IN (0,1)),
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0,1)),
    display_order INTEGER NOT NULL DEFAULT 0 CHECK (display_order >= 0),
    revision INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (indicator_set_id) REFERENCES indicator_sets(indicator_set_id) ON DELETE CASCADE,
    FOREIGN KEY (parent_id) REFERENCES indicator_nodes(indicator_id) ON DELETE RESTRICT,
    UNIQUE (indicator_set_id, code)
);
CREATE INDEX IF NOT EXISTS ix_indicator_nodes_tree
ON indicator_nodes(indicator_set_id, level, parent_id, display_order, indicator_id);

CREATE TABLE IF NOT EXISTS indicator_experts (
    expert_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS indicator_expert_score_sheets (
    indicator_set_id TEXT NOT NULL,
    expert_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('draft','submitted')),
    revision INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (indicator_set_id, expert_id),
    FOREIGN KEY (indicator_set_id) REFERENCES indicator_sets(indicator_set_id) ON DELETE CASCADE,
    FOREIGN KEY (expert_id) REFERENCES indicator_experts(expert_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS indicator_expert_scores (
    indicator_set_id TEXT NOT NULL,
    indicator_id TEXT NOT NULL,
    expert_id TEXT NOT NULL,
    score REAL NOT NULL CHECK (score >= 0 AND score <= 100),
    status TEXT NOT NULL CHECK (status IN ('draft','submitted')),
    revision INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (indicator_set_id, indicator_id, expert_id),
    FOREIGN KEY (indicator_set_id) REFERENCES indicator_sets(indicator_set_id) ON DELETE CASCADE,
    FOREIGN KEY (indicator_id) REFERENCES indicator_nodes(indicator_id) ON DELETE CASCADE,
    FOREIGN KEY (expert_id) REFERENCES indicator_experts(expert_id) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS ix_indicator_scores_expert
ON indicator_expert_scores(expert_id, indicator_set_id, status, indicator_id);
"""


def _seed_nodes():
    # l1 code/name, [(l2 code/name, [(l3 code/name/kind/description/core), ...]), ...]
    return [
        ("node_capability", "机场保障节点能力", [
            ("infrastructure", "基础设施保障能力", [
                ("runway_support", "跑道保障能力", "ABSTRACT", "综合跑道可用状态、容量及受损恢复条件", False),
                ("stand_support", "机位保障能力", "ABSTRACT", "综合机位可用数量、停放与保障条件", False),
                ("airspace_support", "空域保障能力", "ABSTRACT", "综合空域开放、限制及可达条件", False),
                ("flight_org_support", "飞行组织保障能力", "ABSTRACT", "综合管制、导航、航路与运行组织条件", False),
            ]),
            ("material_support", "物资保障能力", [
                ("fuel_support", "油料保障能力", "ABSTRACT", "综合油料库存、补给与消耗支撑能力", False),
                ("munition_support", "弹药保障能力", "ABSTRACT", "综合弹药库存、保障与消耗支撑能力", False),
                ("spares_support", "航材保障能力", "ABSTRACT", "综合航材库存、补给与保障支撑能力", False),
            ]),
            ("equipment_support", "装备技术保障能力", [
                ("refuel_equipment", "加油保障能力", "ABSTRACT", "综合加油装备可用状态及并行保障能力", False),
                ("power_support", "供电保障能力", "ABSTRACT", "综合供电装备可用状态及保障能力", False),
                ("towing_support", "牵引保障能力", "ABSTRACT", "综合牵引装备可用状态及周转能力", False),
                ("special_equipment", "特种保障装备能力", "ABSTRACT", "综合充氧、充氮、运输等装备保障能力", False),
            ]),
            ("operations_support", "战勤保障能力", [
                ("atc_support", "管制保障能力", "ABSTRACT", "综合飞行管制保障条件", False),
                ("weather_support", "气象保障能力", "ABSTRACT", "综合气象保障条件", False),
                ("comm_nav_support", "通信导航保障能力", "ABSTRACT", "综合通信、导航保障条件", False),
                ("transport_service_support", "运输与勤务保障能力", "ABSTRACT", "综合运输、警卫、卫生等勤务保障条件", False),
            ]),
            ("maintenance_support", "机务保障能力", [
                ("maintenance_type_fit", "机型保障适配能力", "ABSTRACT", "反映机务力量与机型保障需求的适配程度", False),
                ("maintenance_capacity", "机务保障容量", "ABSTRACT", "反映工作组数量、并行保障能力等形成的保障上限", False),
                ("maintenance_timeliness", "机务保障时效", "ABSTRACT", "反映整备、维修及再次出动所需时间", False),
            ]),
        ]),
        ("cluster_capability", "机场群保障能力", [
            ("mission_fit", "任务需求适配能力", [
                ("airport_aircraft_fit", "机场—机型保障适配能力", "ABSTRACT", "反映机场保障条件与机型需求的匹配程度", False),
                ("mission_coverage", "任务保障覆盖能力", "ABSTRACT", "反映机场群对任务需求的可承接范围", False),
                ("aircraft_mission_support", "分机型任务保障能力", "ABSTRACT", "反映不同机型任务的可保障程度", False),
            ]),
            ("node_constraints", "节点保障约束能力", [
                ("capacity_support", "起降容量保障能力", "ABSTRACT", "反映机场起降容量形成的任务保障上限", False),
                ("aircraft_availability", "航空器可用保障能力", "ABSTRACT", "反映分机型可用航空器形成的保障能力", False),
                ("resource_constraint", "资源约束保障能力", "ABSTRACT", "反映油料、航材、弹药等资源约束", False),
                ("maintenance_constraint", "机务约束保障能力", "ABSTRACT", "反映机务保障能力形成的任务执行约束", False),
            ]),
            ("temporal_support", "时序持续保障能力", [
                ("continuous_support_duration", "持续保障时长", "DIRECT", "可由时间窗内持续满足保障条件的时长计算", False),
                ("window_support_margin", "时间窗保障余量", "DIRECT", "可由各时间窗容量、资源及任务需求差额计算", False),
                ("continuous_mission_support", "连续任务保障能力", "ABSTRACT", "反映连续时间窗内保持任务执行能力的程度", False),
            ]),
            ("cluster_coordination", "群内协同调配能力", [
                ("mission_transfer", "任务转移承接能力", "ABSTRACT", "反映受损后群内机场任务接替能力", False),
                ("resource_dispatch", "资源调配能力", "ABSTRACT", "反映群内资源调拨与补充能力", False),
                ("node_substitution", "节点替代能力", "ABSTRACT", "反映关键节点受损后的替代保障能力", False),
                ("support_response", "群内支援响应能力", "ABSTRACT", "反映支援资源和保障力量的响应能力", False),
            ]),
        ]),
        ("regional_resilience", "区域顽存能力", [
            ("mission_execution", "任务出动与执行效率", [
                ("task_completion_rate", "任务完成率", "DIRECT", "合同/V1.1核心指标；已完成任务量占任务需求量的比例", True),
                ("sortie_achievement_rate", "出动架次达成率", "DIRECT", "实际出动架次与计划/需求架次的达成程度", False),
                ("on_time_execution_rate", "准时执行率", "DIRECT", "按规定时间完成或执行的任务比例", False),
                ("average_mission_delay", "平均任务延误", "DIRECT", "任务实际执行相对计划时间的平均延误", False),
                ("mission_recovery_time", "任务执行恢复时间", "DIRECT", "受损后任务执行能力恢复至规定水平所需时间", False),
            ]),
            ("resource_effectiveness", "资源保障效能", [
                ("professional_material_support_rate", "专业物资保障率", "DIRECT", "合同/V1.1核心指标；专业保障物资需求满足程度", True),
                ("critical_resource_satisfaction", "关键资源需求满足率", "DIRECT", "关键资源实际保障量与需求量的比值", False),
                ("resource_timeliness", "资源保障及时率", "DIRECT", "在规定时限内完成资源保障的比例", False),
                ("resource_sustain_duration", "资源持续保障时间", "DIRECT", "资源能够持续支持任务执行的时间", False),
                ("resource_per_effective_mission", "单位有效任务资源消耗", "DIRECT", "单位有效任务对应的资源消耗", False),
                ("external_support_dependency", "外部资源支援依赖度", "DIRECT", "外部支援资源占总体保障资源的比例或程度", False),
            ]),
            ("network_coordination", "节点网络协同效果", [
                ("airport_comprehensive_capability", "机场综合保障能力值", "DIRECT", "由单机场评估结果形成的节点综合状态量", False),
                ("intracluster_airport_distribution_balance", "群内机场分布均衡度", "DIRECT", "合同/V1.1核心指标；反映群内机场空间与任务覆盖均衡程度", True),
                ("node_capability_balance", "节点保障能力均衡度", "DIRECT", "反映群内机场保障能力差异程度", False),
                ("critical_mission_multi_node_coverage", "关键任务多节点覆盖率", "DIRECT", "具有多个可执行机场的关键任务占比", False),
                ("damaged_node_task_substitution_rate", "受损节点任务替代率", "DIRECT", "受损节点原任务被群内其他机场接替完成的比例", False),
                ("cluster_support_response_timeliness", "群内支援响应及时率", "DIRECT", "规定时间内完成群内支援任务的比例", False),
                ("cluster_coordination_gain", "机场群协同增益率", "DIRECT", "比较组群协同前后任务完成、资源保障和节点替代的改善程度", False),
            ]),
            ("protection", "防护能力", [
                ("capability_decline_rate", "保障能力下降速率", "DIRECT", "反映损毁后机场/机场群保障能力下降速度", False),
                ("minimum_capability_retention", "最低能力保持率", "DIRECT", "损毁过程中最低保障能力相对基准能力的保持程度", False),
                ("mission_executable_threshold", "任务可执行阈值", "DIRECT", "满足任务执行所需的最低保障能力条件", False),
                ("below_threshold_duration", "低于任务阈值持续时间", "DIRECT", "保障能力低于任务可执行阈值的持续时间", False),
            ]),
            ("emergency_response", "应急响应能力", [
                ("emergency_response_time", "应急响应时间", "DIRECT", "损毁发生至恢复行动启动或响应完成的时间", False),
                ("recovery_slope", "恢复斜率", "DIRECT", "恢复阶段保障能力随时间提升的速度", False),
                ("recovery_time", "恢复时间", "DIRECT", "保障能力恢复至规定水平所需时间", False),
                ("recovery_probability", "恢复概率", "DIRECT", "V1.1规定的损毁程度—恢复特征指标之一", False),
            ]),
        ]),
    ]


def apply(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    exists = conn.execute("SELECT 1 FROM indicator_sets WHERE indicator_set_id=?", (DEFAULT_SET_ID,)).fetchone()
    if exists is not None:
        return
    conn.execute(
        "INSERT INTO indicator_sets (indicator_set_id,name,version,is_default,status,description) VALUES (?,?,?,?,?,?)",
        (DEFAULT_SET_ID, "默认指标集 V1.1", "V1.1", 1, "published", "技术方案V1.1与指标管理确认说明冻结的系统默认指标体系"),
    )
    order1 = 0
    for l1_code, l1_name, l2s in _seed_nodes():
        l1_id = f"IND:{l1_code}"
        conn.execute(
            """INSERT INTO indicator_nodes
            (indicator_id,indicator_set_id,parent_id,code,name,level,node_kind,is_core,editable,enabled,display_order)
            VALUES (?,?,?,?,?,1,'CATEGORY',0,0,1,?)""",
            (l1_id, DEFAULT_SET_ID, None, l1_code, l1_name, order1),
        )
        order1 += 1
        for order2, (l2_code, l2_name, l3s) in enumerate(l2s):
            l2_id = f"IND:{l1_code}:{l2_code}"
            conn.execute(
                """INSERT INTO indicator_nodes
                (indicator_id,indicator_set_id,parent_id,code,name,level,node_kind,is_core,editable,enabled,display_order)
                VALUES (?,?,?,?,?,2,'CATEGORY',0,0,1,?)""",
                (l2_id, DEFAULT_SET_ID, l1_id, l2_code, l2_name, order2),
            )
            for order3, (l3_code, l3_name, kind, desc, core) in enumerate(l3s):
                l3_id = f"IND:{l1_code}:{l2_code}:{l3_code}"
                conn.execute(
                    """INSERT INTO indicator_nodes
                    (indicator_id,indicator_set_id,parent_id,code,name,level,node_kind,description,is_core,editable,enabled,display_order)
                    VALUES (?,?,?,?,?,3,?,?,?,?,1,?)""",
                    (l3_id, DEFAULT_SET_ID, l2_id, l3_code, l3_name, kind, desc, int(core), 0 if core else 1, order3),
                )
