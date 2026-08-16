from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Sequence

from backend.domain.indicator import Expert, ExpertScore, IndicatorNode, IndicatorSet
from backend.storage.database import initialize_database


class IndicatorRepositoryError(RuntimeError):
    pass


class IndicatorNotFoundError(IndicatorRepositoryError):
    pass


class IndicatorConflictError(IndicatorRepositoryError):
    pass


class IndicatorProtectionError(IndicatorRepositoryError):
    pass


class IndicatorStateError(IndicatorRepositoryError):
    pass


class _ClosingConnection(sqlite3.Connection):
    def __enter__(self):
        return super().__enter__()

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            return super().__exit__(exc_type, exc_val, exc_tb)
        finally:
            self.close()


class IndicatorRepository:
    """Persistence boundary for indicator definitions and expert score sheets.

    Published sets are immutable definitions. Editing happens only on a draft cloned from
    a published source. Expert scoring is saved as one aggregate sheet per expert/set so
    draft/submit and optimistic concurrency cannot become partially applied row states.
    """

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, factory=_ClosingConnection)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def init_schema(self) -> None:
        initialize_database(self.db_path)

    @staticmethod
    def _set_from_row(row: sqlite3.Row) -> IndicatorSet:
        return IndicatorSet(
            id=row["indicator_set_id"], name=row["name"], version=row["version"],
            is_default=bool(row["is_default"]), status=row["status"], description=row["description"],
        )

    @staticmethod
    def _node_from_row(row: sqlite3.Row) -> IndicatorNode:
        return IndicatorNode(
            id=row["indicator_id"], indicator_set_id=row["indicator_set_id"], parent_id=row["parent_id"],
            code=row["code"], name=row["name"], level=int(row["level"]), node_kind=row["node_kind"],
            unit=row["unit"], direction=row["direction"], weight=row["weight"], description=row["description"],
            is_core=bool(row["is_core"]), editable=bool(row["editable"]), enabled=bool(row["enabled"]),
            display_order=int(row["display_order"]),
        )

    @staticmethod
    def _set_payload(conn: sqlite3.Connection, row: sqlite3.Row) -> Dict[str, Any]:
        item = IndicatorRepository._set_from_row(row).to_dict()
        item.update({
            "revision": int(row["revision"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        })
        return item

    @staticmethod
    def _node_payload(row: sqlite3.Row) -> Dict[str, Any]:
        item = IndicatorRepository._node_from_row(row).to_dict()
        item.update({
            "revision": int(row["revision"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        })
        return item

    def list_sets(self) -> list[Dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM indicator_sets ORDER BY is_default DESC, updated_at DESC, indicator_set_id"
            ).fetchall()
            return [self._set_payload(conn, r) for r in rows]

    def get_set(self, set_id: str) -> Optional[Dict[str, Any]]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM indicator_sets WHERE indicator_set_id=?", (set_id,)).fetchone()
            return None if row is None else self._set_payload(conn, row)

    def get_default_set(self) -> Optional[Dict[str, Any]]:
        with self.connect() as conn:
            row = conn.execute(
                """SELECT * FROM indicator_sets
                   WHERE is_default=1 AND status='published'
                   ORDER BY updated_at DESC, indicator_set_id LIMIT 1"""
            ).fetchone()
            return None if row is None else self._set_payload(conn, row)

    def get_tree(self, set_id: str) -> Dict[str, Any]:
        with self.connect() as conn:
            set_row = conn.execute("SELECT * FROM indicator_sets WHERE indicator_set_id=?", (set_id,)).fetchone()
            if set_row is None:
                raise IndicatorNotFoundError(f"indicator set not found: {set_id}")
            rows = conn.execute(
                """SELECT * FROM indicator_nodes WHERE indicator_set_id=?
                   ORDER BY level, display_order, indicator_id""", (set_id,)
            ).fetchall()
            return {
                "indicator_set": self._set_payload(conn, set_row),
                "nodes": [self._node_payload(r) for r in rows],
            }

    @staticmethod
    def _assert_set_revision(conn: sqlite3.Connection, set_id: str, expected_revision: int) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM indicator_sets WHERE indicator_set_id=?", (set_id,)).fetchone()
        if row is None:
            raise IndicatorNotFoundError(f"indicator set not found: {set_id}")
        if int(row["revision"]) != expected_revision:
            raise IndicatorConflictError(
                f"indicator set revision changed: expected {expected_revision}, current {int(row['revision'])}"
            )
        return row

    @staticmethod
    def _bump_set(conn: sqlite3.Connection, set_id: str) -> None:
        conn.execute(
            "UPDATE indicator_sets SET revision=revision+1, updated_at=CURRENT_TIMESTAMP WHERE indicator_set_id=?",
            (set_id,),
        )


    @staticmethod
    def _recalculate_submitted_weights(conn: sqlite3.Connection, set_id: str) -> Dict[str, Any]:
        """Recalculate L3 weights from submitted expert score sheets.

        Rule v1 is intentionally simple and explainable for review: for every enabled
        level-3 indicator, take the arithmetic mean across submitted experts; then
        normalize those means among siblings under the same level-2 parent so sibling
        weights sum to 1. If a sibling group has submitted scores but every mean is 0,
        use equal weights for that group. Draft score sheets never participate.
        """
        submitted = int(conn.execute(
            "SELECT COUNT(*) FROM indicator_expert_score_sheets WHERE indicator_set_id=? AND status='submitted'",
            (set_id,),
        ).fetchone()[0])
        nodes = conn.execute(
            """SELECT indicator_id,parent_id FROM indicator_nodes
               WHERE indicator_set_id=? AND level=3 AND enabled=1
               ORDER BY parent_id,display_order,indicator_id""",
            (set_id,),
        ).fetchall()
        # No submitted expert facts means weights are intentionally unavailable.
        if submitted == 0:
            conn.execute(
                "UPDATE indicator_nodes SET weight=NULL WHERE indicator_set_id=? AND level=3",
                (set_id,),
            )
            return {"submitted_expert_count": 0, "weighted_indicator_count": 0}

        means = {
            r["indicator_id"]: float(r["mean_score"] or 0.0)
            for r in conn.execute(
                """SELECT n.indicator_id, AVG(s.score) AS mean_score
                   FROM indicator_nodes n
                   LEFT JOIN indicator_expert_scores s
                     ON s.indicator_set_id=n.indicator_set_id
                    AND s.indicator_id=n.indicator_id
                    AND s.expert_id IN (
                        SELECT expert_id FROM indicator_expert_score_sheets
                        WHERE indicator_set_id=? AND status='submitted'
                    )
                   WHERE n.indicator_set_id=? AND n.level=3 AND n.enabled=1
                   GROUP BY n.indicator_id""",
                (set_id, set_id),
            ).fetchall()
        }
        by_parent: Dict[str, list[str]] = {}
        for row in nodes:
            by_parent.setdefault(str(row["parent_id"]), []).append(str(row["indicator_id"]))

        weights: Dict[str, float] = {}
        for ids in by_parent.values():
            total = sum(max(0.0, means.get(indicator_id, 0.0)) for indicator_id in ids)
            if total > 0:
                for indicator_id in ids:
                    weights[indicator_id] = max(0.0, means.get(indicator_id, 0.0)) / total
            else:
                equal = 1.0 / len(ids) if ids else 0.0
                for indicator_id in ids:
                    weights[indicator_id] = equal

        conn.execute(
            "UPDATE indicator_nodes SET weight=NULL WHERE indicator_set_id=? AND level=3 AND enabled=0",
            (set_id,),
        )
        for indicator_id, weight in weights.items():
            conn.execute(
                "UPDATE indicator_nodes SET weight=? WHERE indicator_set_id=? AND indicator_id=?",
                (weight, set_id, indicator_id),
            )
        return {
            "submitted_expert_count": submitted,
            "weighted_indicator_count": len(weights),
        }

    def clone_to_draft(self, source_set_id: str, draft: IndicatorSet, *, expected_revision: int) -> Dict[str, Any]:
        if draft.status != "draft" or draft.is_default:
            raise IndicatorStateError("cloned indicator set must be a non-default draft")
        with self.connect() as conn:
            source = self._assert_set_revision(conn, source_set_id, expected_revision)
            if source["status"] != "published":
                raise IndicatorStateError("only published indicator set can be cloned to draft")
            if conn.execute("SELECT 1 FROM indicator_sets WHERE indicator_set_id=?", (draft.id,)).fetchone():
                raise IndicatorConflictError(f"indicator set already exists: {draft.id}")
            conn.execute(
                """INSERT INTO indicator_sets
                   (indicator_set_id,name,version,is_default,status,description)
                   VALUES (?,?,?,?,?,?)""",
                (draft.id, draft.name, draft.version, 0, "draft", draft.description),
            )
            rows = conn.execute(
                "SELECT * FROM indicator_nodes WHERE indicator_set_id=? ORDER BY level, display_order, indicator_id",
                (source_set_id,),
            ).fetchall()
            id_map = {r["indicator_id"]: f"{draft.id}:{r['code']}" for r in rows}
            for r in rows:
                conn.execute(
                    """INSERT INTO indicator_nodes
                       (indicator_id,indicator_set_id,parent_id,code,name,level,node_kind,unit,direction,weight,
                        description,is_core,editable,enabled,display_order)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        id_map[r["indicator_id"]], draft.id,
                        None if r["parent_id"] is None else id_map[r["parent_id"]],
                        r["code"], r["name"], r["level"], r["node_kind"], r["unit"], r["direction"], r["weight"],
                        r["description"], r["is_core"], r["editable"], r["enabled"], r["display_order"],
                    ),
                )
        return self.get_tree(draft.id)

    def publish_draft(self, set_id: str, *, expected_revision: int) -> Dict[str, Any]:
        """Publish a user-created definition set without replacing the system default.

        ``is_default`` is reserved for the seeded V1.1 baseline.  A custom draft becomes
        an independently selectable published set; opening Indicator Management without an
        explicit set id still resolves to the protected system default.
        """
        with self.connect() as conn:
            row = self._assert_set_revision(conn, set_id, expected_revision)
            if row["status"] != "draft":
                raise IndicatorStateError("only draft indicator set can be published")
            if bool(row["is_default"]):
                raise IndicatorProtectionError("system default indicator set cannot be republished as a custom draft")
            conn.execute(
                """UPDATE indicator_sets SET status='published',is_default=0,revision=revision+1,
                   updated_at=CURRENT_TIMESTAMP WHERE indicator_set_id=?""", (set_id,)
            )
            self._recalculate_submitted_weights(conn, set_id)
        return self.get_tree(set_id)

    def save_node(self, node: IndicatorNode, *, expected_set_revision: int, create_only: bool = False) -> Dict[str, Any]:
        if node.level != 3:
            raise IndicatorProtectionError("only level-3 extension indicators are mutable")
        with self.connect() as conn:
            set_row = self._assert_set_revision(conn, node.indicator_set_id, expected_set_revision)
            if set_row["status"] != "draft":
                raise IndicatorStateError("indicator definitions can only be edited in a draft set")
            parent = conn.execute(
                "SELECT * FROM indicator_nodes WHERE indicator_id=? AND indicator_set_id=?",
                (node.parent_id, node.indicator_set_id),
            ).fetchone()
            if parent is None or int(parent["level"]) != 2:
                raise IndicatorStateError("level-3 indicator parent must be a level-2 node in the same set")
            existing = conn.execute("SELECT * FROM indicator_nodes WHERE indicator_id=?", (node.id,)).fetchone()
            if create_only and existing is not None:
                raise IndicatorConflictError(f"indicator node already exists: {node.id}")
            if existing is not None and (bool(existing["is_core"]) or not bool(existing["editable"])):
                raise IndicatorProtectionError("protected/core indicator node cannot be modified")
            if existing is None:
                conn.execute(
                    """INSERT INTO indicator_nodes
                       (indicator_id,indicator_set_id,parent_id,code,name,level,node_kind,unit,direction,weight,
                        description,is_core,editable,enabled,display_order)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (node.id, node.indicator_set_id, node.parent_id, node.code, node.name, node.level, node.node_kind,
                     node.unit, node.direction, node.weight, node.description, int(node.is_core), int(node.editable),
                     int(node.enabled), node.display_order),
                )
            else:
                if existing["indicator_set_id"] != node.indicator_set_id:
                    raise IndicatorStateError("indicator node cannot move between sets")
                conn.execute(
                    """UPDATE indicator_nodes SET parent_id=?,code=?,name=?,node_kind=?,unit=?,direction=?,weight=?,
                       description=?,enabled=?,display_order=?,revision=revision+1,updated_at=CURRENT_TIMESTAMP
                       WHERE indicator_id=?""",
                    (node.parent_id, node.code, node.name, node.node_kind, node.unit, node.direction, node.weight,
                     node.description, int(node.enabled), node.display_order, node.id),
                )
            self._bump_set(conn, node.indicator_set_id)
        tree = self.get_tree(node.indicator_set_id)
        return next(x for x in tree["nodes"] if x["id"] == node.id)

    def delete_node(self, set_id: str, indicator_id: str, *, expected_set_revision: int) -> Dict[str, Any]:
        with self.connect() as conn:
            set_row = self._assert_set_revision(conn, set_id, expected_set_revision)
            if set_row["status"] != "draft":
                raise IndicatorStateError("indicator definitions can only be edited in a draft set")
            row = conn.execute(
                "SELECT * FROM indicator_nodes WHERE indicator_id=? AND indicator_set_id=?", (indicator_id, set_id)
            ).fetchone()
            if row is None:
                raise IndicatorNotFoundError(f"indicator node not found: {indicator_id}")
            if int(row["level"]) != 3 or bool(row["is_core"]) or not bool(row["editable"]):
                raise IndicatorProtectionError("protected/core indicator node cannot be deleted")
            conn.execute("DELETE FROM indicator_nodes WHERE indicator_id=?", (indicator_id,))
            self._bump_set(conn, set_id)
        return {"indicator_set_id": set_id, "indicator_id": indicator_id, "deleted": True}

    def list_experts(self) -> list[Dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM indicator_experts ORDER BY name, expert_id").fetchall()
            return [dict(r) for r in rows]

    def save_expert(self, expert: Expert, *, expected_revision: Optional[int] = None, create_only: bool = False) -> Dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM indicator_experts WHERE expert_id=?", (expert.expert_id,)).fetchone()
            if create_only and row is not None:
                raise IndicatorConflictError(f"expert already exists: {expert.expert_id}")
            if row is None:
                conn.execute("INSERT INTO indicator_experts (expert_id,name) VALUES (?,?)", (expert.expert_id, expert.name))
            else:
                if expected_revision is None or int(row["revision"]) != expected_revision:
                    raise IndicatorConflictError("expert revision changed or expected_revision missing")
                conn.execute(
                    "UPDATE indicator_experts SET name=?, revision=revision+1, updated_at=CURRENT_TIMESTAMP WHERE expert_id=?",
                    (expert.name, expert.expert_id),
                )
        with self.connect() as conn:
            return dict(conn.execute("SELECT * FROM indicator_experts WHERE expert_id=?", (expert.expert_id,)).fetchone())

    def delete_expert(self, expert_id: str, *, expected_revision: int) -> Dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM indicator_experts WHERE expert_id=?", (expert_id,)).fetchone()
            if row is None:
                raise IndicatorNotFoundError(f"expert not found: {expert_id}")
            if int(row["revision"]) != expected_revision:
                raise IndicatorConflictError("expert revision changed")
            submitted = conn.execute(
                "SELECT COUNT(*) FROM indicator_expert_score_sheets WHERE expert_id=? AND status='submitted'",
                (expert_id,),
            ).fetchone()[0]
            if submitted:
                raise IndicatorProtectionError("expert with submitted score sheet cannot be deleted")
            conn.execute("DELETE FROM indicator_expert_scores WHERE expert_id=?", (expert_id,))
            conn.execute("DELETE FROM indicator_expert_score_sheets WHERE expert_id=?", (expert_id,))
            conn.execute("DELETE FROM indicator_experts WHERE expert_id=?", (expert_id,))
        return {"expert_id": expert_id, "deleted": True}

    def get_score_sheet(self, set_id: str, expert_id: str) -> Dict[str, Any]:
        with self.connect() as conn:
            if conn.execute("SELECT 1 FROM indicator_sets WHERE indicator_set_id=?", (set_id,)).fetchone() is None:
                raise IndicatorNotFoundError(f"indicator set not found: {set_id}")
            if conn.execute("SELECT 1 FROM indicator_experts WHERE expert_id=?", (expert_id,)).fetchone() is None:
                raise IndicatorNotFoundError(f"expert not found: {expert_id}")
            sheet = conn.execute(
                "SELECT * FROM indicator_expert_score_sheets WHERE indicator_set_id=? AND expert_id=?",
                (set_id, expert_id),
            ).fetchone()
            rows = conn.execute(
                """SELECT s.* FROM indicator_expert_scores s
                   JOIN indicator_nodes n ON n.indicator_id=s.indicator_id
                   WHERE s.indicator_set_id=? AND s.expert_id=?
                   ORDER BY n.display_order, n.indicator_id""",
                (set_id, expert_id),
            ).fetchall()
            return {
                "indicator_set_id": set_id,
                "expert_id": expert_id,
                "status": "draft" if sheet is None else sheet["status"],
                "revision": 0 if sheet is None else int(sheet["revision"]),
                "updated_at": None if sheet is None else sheet["updated_at"],
                "scores": [
                    {"indicator_id": r["indicator_id"], "score": float(r["score"])} for r in rows
                ],
            }

    def replace_score_sheet(
        self,
        set_id: str,
        expert_id: str,
        *,
        scores: Sequence[ExpertScore],
        status: str,
        expected_revision: int,
    ) -> Dict[str, Any]:
        if status not in ("draft", "submitted"):
            raise IndicatorStateError("score sheet status must be draft or submitted")
        with self.connect() as conn:
            set_row = conn.execute("SELECT * FROM indicator_sets WHERE indicator_set_id=?", (set_id,)).fetchone()
            if set_row is None:
                raise IndicatorNotFoundError(f"indicator set not found: {set_id}")
            if set_row["status"] not in ("published", "draft"):
                raise IndicatorStateError("disabled indicator set cannot be scored")
            if conn.execute("SELECT 1 FROM indicator_experts WHERE expert_id=?", (expert_id,)).fetchone() is None:
                raise IndicatorNotFoundError(f"expert not found: {expert_id}")
            sheet = conn.execute(
                "SELECT * FROM indicator_expert_score_sheets WHERE indicator_set_id=? AND expert_id=?",
                (set_id, expert_id),
            ).fetchone()
            current_revision = 0 if sheet is None else int(sheet["revision"])
            if current_revision != expected_revision:
                raise IndicatorConflictError(
                    f"score sheet revision changed: expected {expected_revision}, current {current_revision}"
                )
            valid_l3 = {
                r[0] for r in conn.execute(
                    "SELECT indicator_id FROM indicator_nodes WHERE indicator_set_id=? AND level=3 AND enabled=1",
                    (set_id,),
                ).fetchall()
            }
            score_ids = [x.indicator_id for x in scores]
            if len(score_ids) != len(set(score_ids)):
                raise IndicatorStateError("score sheet contains duplicate indicator_id")
            unknown = sorted(set(score_ids) - valid_l3)
            if unknown:
                raise IndicatorStateError(f"score sheet contains non-scoreable indicator: {unknown[0]}")
            if status == "submitted" and set(score_ids) != valid_l3:
                missing = sorted(valid_l3 - set(score_ids))
                raise IndicatorStateError(f"submitted score sheet must cover every enabled level-3 indicator; missing: {missing[:3]}")
            conn.execute(
                "DELETE FROM indicator_expert_scores WHERE indicator_set_id=? AND expert_id=?", (set_id, expert_id)
            )
            for score in scores:
                if score.indicator_set_id != set_id or score.expert_id != expert_id:
                    raise IndicatorStateError("score identity must match score sheet")
                conn.execute(
                    """INSERT INTO indicator_expert_scores
                       (indicator_set_id,indicator_id,expert_id,score,status)
                       VALUES (?,?,?,?,?)""",
                    (set_id, score.indicator_id, expert_id, score.score, status),
                )
            previous_status = None if sheet is None else str(sheet["status"])
            if sheet is None:
                conn.execute(
                    """INSERT INTO indicator_expert_score_sheets
                       (indicator_set_id,expert_id,status,revision) VALUES (?,?,?,1)""",
                    (set_id, expert_id, status),
                )
            else:
                conn.execute(
                    """UPDATE indicator_expert_score_sheets SET status=?,revision=revision+1,updated_at=CURRENT_TIMESTAMP
                       WHERE indicator_set_id=? AND expert_id=?""",
                    (status, set_id, expert_id),
                )
            # Draft-only edits do not affect published weights.  Recalculate only when a
            # sheet enters submitted state or a previously submitted sheet is withdrawn
            # back to draft.  This keeps the expert-edit UI honest: saving a draft never
            # changes a published fact.
            should_recalculate = status == "submitted" or previous_status == "submitted"
            weight_result = (
                self._recalculate_submitted_weights(conn, set_id)
                if should_recalculate
                else None
            )
        result = self.get_score_sheet(set_id, expert_id)
        result["weights_recalculated"] = bool(should_recalculate)
        result["weight_rule"] = "submitted_expert_mean_sibling_normalization_v1"
        if weight_result is not None:
            result.update(weight_result)
        return result

    def published_weights(self, set_id: Optional[str] = None) -> Dict[str, Any]:
        with self.connect() as conn:
            if set_id is None:
                set_row = conn.execute(
                    "SELECT * FROM indicator_sets WHERE is_default=1 AND status='published' LIMIT 1"
                ).fetchone()
            else:
                set_row = conn.execute("SELECT * FROM indicator_sets WHERE indicator_set_id=?", (set_id,)).fetchone()
            if set_row is None:
                raise IndicatorNotFoundError("published indicator set not found")
            if set_row["status"] != "published":
                raise IndicatorStateError("weights can only be read from a published set")
            rows = conn.execute(
                """SELECT indicator_id,code,name,weight FROM indicator_nodes
                   WHERE indicator_set_id=? AND level=3 AND enabled=1
                   ORDER BY display_order, indicator_id""",
                (set_row["indicator_set_id"],),
            ).fetchall()
            items = [
                {"indicator_id": r["indicator_id"], "code": r["code"], "name": r["name"], "weight": r["weight"]}
                for r in rows
            ]
            complete = all(x["weight"] is not None for x in items)
            return {
                "indicator_set_id": set_row["indicator_set_id"],
                "version": set_row["version"],
                "status": "available" if complete else "unavailable",
                "calculation_rule": "submitted_expert_mean_sibling_normalization_v1",
                "normalization_scope": "enabled_level3_siblings_under_each_level2_parent",
                "items": items,
                "message": None if complete else "No submitted expert score sheet is available for the published indicator set.",
            }
