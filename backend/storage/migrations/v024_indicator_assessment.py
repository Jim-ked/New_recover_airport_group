from __future__ import annotations

import json
import sqlite3


FORMAL_SET_ID = "INDSET-RUN-ASSESSMENT-V1"
FORMAL_SET_VERSION = "run-assessment.v1"


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _add_indicator_columns(conn: sqlite3.Connection) -> None:
    declarations = {
        "category": "TEXT",
        "calculation_type": "TEXT",
        "weight_source": "TEXT",
        "calculation_status": "TEXT NOT NULL DEFAULT 'unavailable'",
    }
    existing = _columns(conn, "indicator_nodes")
    for name, declaration in declarations.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE indicator_nodes ADD COLUMN {name} {declaration}")

    rows = conn.execute(
        "SELECT indicator_id,parent_id,code,level,node_kind FROM indicator_nodes ORDER BY level"
    ).fetchall()
    by_id = {str(row[0]): row for row in rows}
    for row in rows:
        indicator_id = str(row[0])
        level = int(row[3])
        ancestor = row
        while int(ancestor[3]) > 1 and ancestor[1] in by_id:
            ancestor = by_id[str(ancestor[1])]
        category = str(ancestor[2]) if level > 1 else str(row[2])
        calculation_type = (
            "category" if level < 3 else ("composite" if row[4] == "ABSTRACT" else "unmapped")
        )
        conn.execute(
            "UPDATE indicator_nodes SET category=COALESCE(category,?), "
            "calculation_type=COALESCE(calculation_type,?) WHERE indicator_id=?",
            (category, calculation_type, indicator_id),
        )


def _create_weight_config(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS indicator_weight_config (
            indicator_set_id TEXT PRIMARY KEY REFERENCES indicator_sets(indicator_set_id) ON DELETE CASCADE,
            weight_source TEXT NOT NULL CHECK(weight_source IN ('expert','ahp')),
            indicator_ids_json TEXT NOT NULL,
            comparison_matrix_json TEXT,
            trace_json TEXT NOT NULL,
            revision INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    conn.execute(
        """INSERT OR IGNORE INTO indicator_weight_config
           (indicator_set_id,weight_source,indicator_ids_json,comparison_matrix_json,trace_json)
           SELECT indicator_set_id,'expert','[]',NULL,? FROM indicator_sets""",
        (json.dumps({"rule": "submitted_expert_mean_normalization_v1"}, ensure_ascii=False),),
    )


def _seed_formal_run_set(conn: sqlite3.Connection) -> None:
    if conn.execute("SELECT 1 FROM indicator_sets WHERE indicator_set_id=?", (FORMAL_SET_ID,)).fetchone():
        return
    conn.execute(
        """INSERT INTO indicator_sets
           (indicator_set_id,name,version,is_default,status,description)
           VALUES (?,?,?,?,?,?)""",
        (
            FORMAL_SET_ID,
            "正式运行综合评价指标集",
            FORMAL_SET_VERSION,
            0,
            "published",
            "仅使用可由冻结 Run、Solution 与 Metrics 严格复算的四项无量纲指标。",
        ),
    )
    root_id = "IND:run_assessment"
    group_id = "IND:run_assessment:formal_metrics"
    common = (FORMAL_SET_ID,)
    conn.execute(
        """INSERT INTO indicator_nodes
           (indicator_id,indicator_set_id,parent_id,code,name,level,node_kind,description,is_core,
            editable,enabled,display_order,category,calculation_type,weight_source,calculation_status)
           VALUES (?,?,?,?,?,1,'CATEGORY',?,0,0,1,0,?,'category',NULL,'available')""",
        (root_id, *common, None, "run_assessment", "运行结果综合评价", "以正式运行结果为唯一输入的评价体系", "run_assessment"),
    )
    conn.execute(
        """INSERT INTO indicator_nodes
           (indicator_id,indicator_set_id,parent_id,code,name,level,node_kind,description,is_core,
            editable,enabled,display_order,category,calculation_type,weight_source,calculation_status)
           VALUES (?,?,?,?,?,2,'CATEGORY',?,0,0,1,0,?,'category',NULL,'available')""",
        (group_id, *common, root_id, "formal_metrics", "正式运行指标", "四项指标均为 [0,1] 正向量，缺一项时不形成综合评分。", "run_assessment"),
    )
    leaves = (
        ("task_completion_rate", "任务履行率", "mission", "metrics.summary.completion_ratio", "已履行需求架次 / 正式需求架次；直接使用 Metrics.summary.completion_ratio。"),
        ("capacity_availability_rate", "运行期容量可用率", "capacity", "metrics.capacity.available_over_frozen_baseline_v1", "全部机场有效时间窗可用容量之和 / 同期冻结基线容量之和。"),
        ("aircraft_availability_rate", "运行期航空器可用率", "aircraft", "metrics.aircraft_inventory.available_over_initial_v1", "全部机场机型逐窗出动前可用量之和 / 同期冻结初始可用量之和。"),
        ("resource_reserve_retention_rate", "关键资源最低剩余率", "resource", "metrics.resources.category_min_remaining_ratio_v1", "正式资源类别最低剩余率中的最小值；先转为无量纲比率，不跨单位求和。"),
    )
    indicator_ids: list[str] = []
    for order, (code, name, category, calculation_type, description) in enumerate(leaves):
        indicator_id = f"{group_id}:{code}"
        indicator_ids.append(indicator_id)
        conn.execute(
            """INSERT INTO indicator_nodes
               (indicator_id,indicator_set_id,parent_id,code,name,level,node_kind,unit,direction,weight,
                description,is_core,editable,enabled,display_order,category,calculation_type,
                weight_source,calculation_status)
               VALUES (?,?,?,?,?,3,'DIRECT','比例','positive',0.25,?,0,0,1,?,?,?,'ahp','available')""",
            (indicator_id, FORMAL_SET_ID, group_id, code, name, description, order, category, calculation_type),
        )
    matrix = [[1.0 for _ in indicator_ids] for _ in indicator_ids]
    trace = {"method": "normalized_column_mean", "consistency_ratio": 0.0, "consistent": True, "matrix_order": indicator_ids}
    conn.execute(
        """INSERT OR REPLACE INTO indicator_weight_config
           (indicator_set_id,weight_source,indicator_ids_json,comparison_matrix_json,trace_json,revision)
           VALUES (?,?,?,?,?,1)""",
        (
            FORMAL_SET_ID,
            "ahp",
            json.dumps(indicator_ids, ensure_ascii=False, separators=(",", ":")),
            json.dumps(matrix, ensure_ascii=False, separators=(",", ":")),
            json.dumps(trace, ensure_ascii=False, separators=(",", ":")),
        ),
    )


def apply(conn: sqlite3.Connection) -> None:
    _add_indicator_columns(conn)
    _create_weight_config(conn)
    _seed_formal_run_set(conn)


__all__ = ["FORMAL_SET_ID", "FORMAL_SET_VERSION", "apply"]
