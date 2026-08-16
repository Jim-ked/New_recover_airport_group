from __future__ import annotations

import uuid
from typing import Any, Mapping, Optional

from backend.auth.principal import Principal
from backend.domain.indicator import Expert, ExpertScore, IndicatorNode, IndicatorSet
from backend.storage.indicator_repository import IndicatorRepository
from backend.web.error_mapping import map_expected_error
from backend.web.http import ApiInputError, ApiResponse, reject_unknown, require_object, required_nonblank_string


def _revision(body: Mapping[str, Any], field: str = "expected_revision") -> int:
    value = body.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ApiInputError(f"{field} must be a nonnegative integer", field=field)
    return value


class IndicatorApi:
    """Frontend-facing Indicator definition/expert-score contract.

    Expert scores use one frozen, explainable weight rule: arithmetic mean across submitted
    experts followed by normalization among enabled L3 siblings under the same L2 parent.
    Draft score sheets never participate.
    """

    def __init__(self, *, repository: IndicatorRepository) -> None:
        self.repo = repository

    @staticmethod
    def _handle(call) -> ApiResponse:
        try:
            return call()
        except Exception as exc:
            mapped = map_expected_error(exc)
            if mapped is not None:
                return mapped
            raise

    def tree(self, *, principal: Principal, indicator_set_id: Optional[str] = None) -> ApiResponse:
        def action() -> ApiResponse:
            principal.require_permission("indicators.read")
            if indicator_set_id:
                return ApiResponse(self.repo.get_tree(indicator_set_id), 200)
            default = self.repo.get_default_set()
            if default is None:
                raise RuntimeError("default published indicator set is missing")
            return ApiResponse(self.repo.get_tree(default["id"]), 200)
        return self._handle(action)

    def list_sets(self, *, principal: Principal) -> ApiResponse:
        def action() -> ApiResponse:
            principal.require_permission("indicators.read")
            return ApiResponse({"items": self.repo.list_sets()}, 200)
        return self._handle(action)

    def create_draft(self, raw_body: Any, *, principal: Principal) -> ApiResponse:
        def action() -> ApiResponse:
            principal.require_permission("indicators.write")
            body = require_object(raw_body)
            reject_unknown(body, {"source_indicator_set_id", "name", "version", "description", "expected_revision"})
            source_id = required_nonblank_string(body, "source_indicator_set_id")
            name = required_nonblank_string(body, "name")
            version = required_nonblank_string(body, "version")
            description = body.get("description")
            if description is not None and not isinstance(description, str):
                raise ApiInputError("description must be string or null", field="description")
            draft_id = f"INDSET-{uuid.uuid4().hex}"
            draft = IndicatorSet(
                id=draft_id, name=name, version=version, is_default=False,
                status="draft", description=description,
            )
            return ApiResponse(
                self.repo.clone_to_draft(source_id, draft, expected_revision=_revision(body)), 201
            )
        return self._handle(action)

    def publish(self, set_id: str, raw_body: Any, *, principal: Principal) -> ApiResponse:
        def action() -> ApiResponse:
            principal.require_permission("indicators.write")
            body = require_object(raw_body)
            reject_unknown(body, {"expected_revision"})
            return ApiResponse(self.repo.publish_draft(set_id, expected_revision=_revision(body)), 200)
        return self._handle(action)

    def create_node(self, raw_body: Any, *, principal: Principal) -> ApiResponse:
        def action() -> ApiResponse:
            principal.require_permission("indicators.write")
            body = require_object(raw_body)
            reject_unknown(body, {"indicator", "expected_set_revision"})
            raw = body.get("indicator")
            if not isinstance(raw, Mapping):
                raise ApiInputError("indicator must be a JSON object", field="indicator")
            node = IndicatorNode.from_mapping(raw)
            return ApiResponse(
                self.repo.save_node(node, expected_set_revision=_revision(body, "expected_set_revision"), create_only=True),
                201,
            )
        return self._handle(action)

    def update_node(self, indicator_id: str, raw_body: Any, *, principal: Principal) -> ApiResponse:
        def action() -> ApiResponse:
            principal.require_permission("indicators.write")
            body = require_object(raw_body)
            reject_unknown(body, {"indicator", "expected_set_revision"})
            raw = body.get("indicator")
            if not isinstance(raw, Mapping):
                raise ApiInputError("indicator must be a JSON object", field="indicator")
            node = IndicatorNode.from_mapping(raw)
            if node.id != indicator_id:
                raise ApiInputError("indicator id must match URL", field="indicator.id")
            return ApiResponse(
                self.repo.save_node(node, expected_set_revision=_revision(body, "expected_set_revision")), 200
            )
        return self._handle(action)

    def delete_node(self, indicator_id: str, raw_body: Any, *, principal: Principal) -> ApiResponse:
        def action() -> ApiResponse:
            principal.require_permission("indicators.write")
            body = require_object(raw_body)
            reject_unknown(body, {"indicator_set_id", "expected_set_revision"})
            set_id = required_nonblank_string(body, "indicator_set_id")
            return ApiResponse(
                self.repo.delete_node(set_id, indicator_id, expected_set_revision=_revision(body, "expected_set_revision")),
                200,
            )
        return self._handle(action)

    def list_experts(self, *, principal: Principal) -> ApiResponse:
        def action() -> ApiResponse:
            principal.require_permission("indicators.read")
            return ApiResponse({"items": self.repo.list_experts()}, 200)
        return self._handle(action)

    def create_expert(self, raw_body: Any, *, principal: Principal) -> ApiResponse:
        def action() -> ApiResponse:
            principal.require_permission("experts.manage")
            expert = Expert.from_mapping(require_object(raw_body))
            return ApiResponse(self.repo.save_expert(expert, create_only=True), 201)
        return self._handle(action)

    def update_expert(self, expert_id: str, raw_body: Any, *, principal: Principal) -> ApiResponse:
        def action() -> ApiResponse:
            principal.require_permission("experts.manage")
            body = require_object(raw_body)
            reject_unknown(body, {"expert", "expected_revision"})
            raw = body.get("expert")
            if not isinstance(raw, Mapping):
                raise ApiInputError("expert must be a JSON object", field="expert")
            expert = Expert.from_mapping(raw)
            if expert.expert_id != expert_id:
                raise ApiInputError("expert_id must match URL", field="expert.expert_id")
            return ApiResponse(self.repo.save_expert(expert, expected_revision=_revision(body)), 200)
        return self._handle(action)

    def delete_expert(self, expert_id: str, raw_body: Any, *, principal: Principal) -> ApiResponse:
        def action() -> ApiResponse:
            principal.require_permission("experts.manage")
            body = require_object(raw_body)
            reject_unknown(body, {"expected_revision"})
            return ApiResponse(self.repo.delete_expert(expert_id, expected_revision=_revision(body)), 200)
        return self._handle(action)

    def get_score_sheet(self, expert_id: str, *, principal: Principal, indicator_set_id: str) -> ApiResponse:
        def action() -> ApiResponse:
            principal.require_permission("indicators.read")
            if not isinstance(indicator_set_id, str) or not indicator_set_id.strip():
                raise ApiInputError("indicator_set_id is required", field="indicator_set_id")
            return ApiResponse(self.repo.get_score_sheet(indicator_set_id, expert_id), 200)
        return self._handle(action)

    def put_score_sheet(self, expert_id: str, raw_body: Any, *, principal: Principal) -> ApiResponse:
        def action() -> ApiResponse:
            principal.require_permission("indicators.score")
            body = require_object(raw_body)
            reject_unknown(body, {"indicator_set_id", "status", "scores", "expected_revision"})
            set_id = required_nonblank_string(body, "indicator_set_id")
            status = required_nonblank_string(body, "status")
            rows = body.get("scores")
            if not isinstance(rows, list):
                raise ApiInputError("scores must be an array", field="scores")
            parsed = []
            for i, row in enumerate(rows):
                if not isinstance(row, Mapping):
                    raise ApiInputError("score row must be object", field=f"scores[{i}]")
                reject_unknown(row, {"indicator_id", "score"})
                parsed.append(ExpertScore.from_mapping({
                    "indicator_set_id": set_id,
                    "indicator_id": row.get("indicator_id"),
                    "expert_id": expert_id,
                    "score": row.get("score"),
                    "status": status,
                }))
            result = self.repo.replace_score_sheet(
                set_id, expert_id, scores=parsed, status=status, expected_revision=_revision(body)
            )
            return ApiResponse(result, 200)
        return self._handle(action)

    def weights(self, *, principal: Principal, indicator_set_id: Optional[str] = None) -> ApiResponse:
        def action() -> ApiResponse:
            principal.require_permission("indicators.read")
            return ApiResponse(self.repo.published_weights(indicator_set_id), 200)
        return self._handle(action)


__all__ = ["IndicatorApi"]
