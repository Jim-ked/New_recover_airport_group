from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape


PDF_SYSTEM_NAME = "机场群顽存能力模拟仿真验证系统"


class ResultExportError(ValueError):
    pass


@dataclass(frozen=True)
class RenderedExport:
    content: bytes
    mimetype: str
    filename: str


CSV_COLUMNS = (
    "report_kind",
    "section",
    "entity_type",
    "entity_id",
    "metric",
    "series",
    "time_window",
    "value",
    "unit",
)

SUPPORTED_FORMATS = ("pdf", "csv", "json", "xml")


def _scalar(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float, str)):
        return str(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _pdf_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, float):
        if abs(value) < 0.0000005:
            return "0"
        return f"{value:.4f}".rstrip("0").rstrip(".")
    return _scalar(value)


def _pdf_table_value(header: str, value: Any) -> str:
    if value is None:
        return "-"
    label = str(header)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if abs(float(value)) < 0.0000005:
            return "0"
        if any(token in label for token in ("完成率", "占比", "比例", "综合能力指数", "风险值", "原始值", "归一化", "权重")):
            return f"{float(value) * 100:.2f}%"
        if any(token in label for token in ("架次", "需求", "履行", "缺口", "出动", "返航", "机场数", "组群数")):
            return str(int(round(float(value))))
        if label in {"F1", "F2", "F3", "J", "目标函数 J", "参照", "对照", "差值"}:
            return f"{float(value):.4f}".rstrip("0").rstrip(".")
    return _pdf_scalar(value) or "-"


def _percent(value: Any) -> Any:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{float(value) * 100:.2f}%"
    return value


def _chart_value_label(value: Any, value_type: str) -> str:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return ""
    numeric = float(value)
    if abs(numeric) < 0.0000005:
        return ""
    if value_type == "percent":
        return f"{numeric * 100:.2f}%"
    if numeric.is_integer():
        return str(int(numeric))
    return f"{numeric:.4f}".rstrip("0").rstrip(".")


def _stacked_bar_label_array(
    values: Sequence[Sequence[Any]], value_type: str
) -> list[list[str]]:
    if not values:
        return []
    column_count = max((len(series) for series in values), default=0)
    labels = [["" for _ in range(column_count)] for _ in values]
    for column in range(column_count):
        total = sum(
            float(series[column])
            for series in values
            if column < len(series)
            and isinstance(series[column], (int, float))
            and not isinstance(series[column], bool)
        )
        labels[-1][column] = _chart_value_label(total, value_type)
    return labels


def _configuration_value(key: str, value: Any, labels: Mapping[str, Mapping[str, str]]) -> Any:
    if key == "preference_mode":
        return {
            "balanced": "均衡",
            "resource_min": "资源保障优先",
            "sortie_max": "出动能力优先",
        }.get(str(value), value)
    if key == "core_airports" and isinstance(value, (list, tuple)):
        return "、".join(labels.get("airports", {}).get(str(item), str(item)) for item in value) or "-"
    return value


def _validate_report_data(report_data: Mapping[str, Any]) -> None:
    if report_data.get("schema_version") != "report-data.v1":
        raise ResultExportError("report_data schema_version must be report-data.v1")
    if not isinstance(report_data.get("data"), Mapping):
        raise ResultExportError("report_data.data must be an object")


def _filename_suffix(report_data: Mapping[str, Any]) -> str:
    return "_".join(str(x) for x in (report_data.get("source_run_ids") or [])[:3]) or "results"


def _append_xml_value(element: ET.Element, value: Any) -> None:
    if isinstance(value, Mapping):
        element.set("type", "object")
        for key, child_value in value.items():
            child = ET.SubElement(element, "field", {"name": str(key)})
            _append_xml_value(child, child_value)
    elif isinstance(value, (list, tuple)):
        element.set("type", "array")
        for child_value in value:
            child = ET.SubElement(element, "item")
            _append_xml_value(child, child_value)
    elif value is None:
        element.set("type", "null")
    elif isinstance(value, bool):
        element.set("type", "boolean")
        element.text = "true" if value else "false"
    elif isinstance(value, int):
        element.set("type", "integer")
        element.text = str(value)
    elif isinstance(value, float):
        element.set("type", "float")
        element.text = str(value)
    elif isinstance(value, str):
        element.set("type", "string")
        element.text = value
    else:
        raise ResultExportError(f"unsupported XML value type: {type(value).__name__}")


def _indent_xml(element: ET.Element, level: int = 0) -> None:
    """Pretty-print without depending on ElementTree.indent (Python 3.9+)."""
    indentation = "\n" + level * "  "
    if len(element):
        if not element.text or not element.text.strip():
            element.text = indentation + "  "
        for child in element:
            _indent_xml(child, level + 1)
        if not child.tail or not child.tail.strip():
            child.tail = indentation
    if level and (not element.tail or not element.tail.strip()):
        element.tail = indentation


def _row(
    kind: str,
    section: str,
    metric: str,
    value: Any,
    *,
    entity_type: str = "",
    entity_id: str = "",
    series: str = "",
    time_window: Any = "",
    unit: str = "",
) -> Dict[str, str]:
    return {
        "report_kind": kind,
        "section": section,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "metric": metric,
        "series": series,
        "time_window": _scalar(time_window),
        "value": _scalar(value),
        "unit": unit,
    }


def _flatten_scalar_map(kind: str, section: str, mapping: Mapping[str, Any], *, entity_type: str = "", entity_id: str = ""):
    for key, value in mapping.items():
        if isinstance(value, Mapping):
            for sub_key, sub_value in value.items():
                if not isinstance(sub_value, (Mapping, list, tuple)):
                    yield _row(kind, section, f"{key}.{sub_key}", sub_value, entity_type=entity_type, entity_id=entity_id)
        elif not isinstance(value, (list, tuple)):
            yield _row(kind, section, str(key), value, entity_type=entity_type, entity_id=entity_id)


def _scalar_items(value: Any, prefix: str = ""):
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            yield from _scalar_items(child, child_prefix)
    elif not isinstance(value, (list, tuple)):
        yield prefix, value


def build_tidy_rows(report_data: Mapping[str, Any]) -> list[Dict[str, str]]:
    _validate_report_data(report_data)
    kind = str(report_data.get("kind") or "")
    data = report_data.get("data")
    rows: list[Dict[str, str]] = []
    for run_id in report_data.get("source_run_ids") or []:
        rows.append(_row(kind, "source", "run_id", run_id, entity_type="run", entity_id=str(run_id)))

    if kind == "single_run":
        run = data.get("run") or {}
        if isinstance(run, Mapping):
            rows.extend(_flatten_scalar_map(kind, "run", run, entity_type="run", entity_id=str(run.get("run_id") or "")))
        metrics = data.get("metrics") or {}
        if isinstance(metrics, Mapping):
            summary = metrics.get("summary") or {}
            if isinstance(summary, Mapping):
                rows.extend(_flatten_scalar_map(kind, "summary", summary))
            axis = metrics.get("time_axis") or {}
            timeline = metrics.get("timeline") or {}
            windows = list(axis.get("windows") or []) if isinstance(axis, Mapping) else []
            if isinstance(timeline, Mapping):
                for metric, values in timeline.items():
                    if isinstance(values, list) and len(values) == len(windows):
                        for t, value in zip(windows, values):
                            rows.append(_row(kind, "timeline", metric, value, time_window=t))
            for section, entity_type in (("airports", "airport"), ("tasks", "task"), ("aircraft", "aircraft")):
                entities = metrics.get(section) or {}
                if isinstance(entities, Mapping):
                    for entity_id, payload in entities.items():
                        if isinstance(payload, Mapping):
                            rows.extend(_flatten_scalar_map(kind, section, payload, entity_type=entity_type, entity_id=str(entity_id)))
            resources = metrics.get("resources") or {}
            if isinstance(resources, Mapping):
                rows.extend(_flatten_scalar_map(kind, "resources", resources))
        solution = data.get("solution") or {}
        if isinstance(solution, Mapping):
            chains = solution.get("sortie_chains") or []
            if isinstance(chains, list):
                for idx, chain in enumerate(chains):
                    if isinstance(chain, Mapping):
                        rows.extend(_flatten_scalar_map(kind, "sortie_chains", chain, entity_type="sortie_chain", entity_id=str(chain.get("path_id") or idx)))
    else:
        # comparison.v1: keep every comparison number tied to role/series/time window.
        focus = data.get("focus_comparison") or {}
        if isinstance(focus, Mapping):
            for key in ("baseline_run_id", "comparison_run_id", "relation", "eligible"):
                rows.append(_row(kind, "focus", key, focus.get(key)))
            values = focus.get("values") or {}
            if isinstance(values, Mapping):
                for metric, facts in values.items():
                    if isinstance(facts, Mapping):
                        for series in ("baseline", "current", "delta"):
                            rows.append(_row(kind, "focus_values", str(metric), facts.get(series), series=series))
            components = focus.get("objective_components") or {}
            if isinstance(components, Mapping):
                for component, facts in components.items():
                    if not isinstance(facts, Mapping):
                        continue
                    for field in ("baseline", "current", "delta", "definition_comparable", "recorded"):
                        rows.append(_row(kind, "objective_components", f"{component}.{field}", facts.get(field)))
            focus_timeline = focus.get("timeline") or {}
            focus_windows = list(focus_timeline.get("windows") or []) if isinstance(focus_timeline, Mapping) else []
            if isinstance(focus_timeline, Mapping):
                for metric in ("departures", "returns"):
                    fact = focus_timeline.get(metric) or {}
                    if not isinstance(fact, Mapping):
                        continue
                    for series in ("baseline", "current", "delta"):
                        values = fact.get(series)
                        if isinstance(values, list) and len(values) == len(focus_windows):
                            for t, value in zip(focus_windows, values):
                                rows.append(_row(kind, "focus_timeline", metric, value, series=series, time_window=t))
                    cumulative = fact.get("cumulative") or {}
                    if isinstance(cumulative, Mapping):
                        for series in ("baseline", "current", "delta"):
                            values = cumulative.get(series)
                            if isinstance(values, list) and len(values) == len(focus_windows):
                                for t, value in zip(focus_windows, values):
                                    rows.append(_row(kind, "focus_cumulative", metric, value, series=series, time_window=t))
            for index, conclusion in enumerate(focus.get("conclusions") or [], start=1):
                rows.append(_row(kind, "conclusions", f"conclusion_{index}", conclusion))
        for section in ("difference_overview", "summary", "collaboration"):
            payload = data.get(section) or {}
            if isinstance(payload, Mapping):
                for metric, value in payload.items():
                    if isinstance(value, Mapping):
                        for series, scalar in value.items():
                            if not isinstance(scalar, (Mapping, list, tuple)):
                                rows.append(_row(kind, section, str(metric), scalar, series=str(series)))
                    elif not isinstance(value, (list, tuple)):
                        rows.append(_row(kind, section, str(metric), value))
        timeline = data.get("timeline") or {}
        windows = list(timeline.get("windows") or []) if isinstance(timeline, Mapping) else []
        if isinstance(timeline, Mapping):
            for metric in ("departures", "returns"):
                series_map = timeline.get(metric) or {}
                if isinstance(series_map, Mapping):
                    for series, values in series_map.items():
                        if isinstance(values, list) and len(values) == len(windows):
                            for t, value in zip(windows, values):
                                rows.append(_row(kind, "timeline", metric, value, series=str(series), time_window=t))
        for section, entity_type in (("airports", "airport"), ("tasks", "task"), ("aircraft", "aircraft")):
            entities = data.get(section) or {}
            if isinstance(entities, Mapping):
                roles = data.get("roles") or {}
                run_series = set(str(item) for item in (data.get("run_ids") or []))
                if isinstance(roles, Mapping):
                    run_series.update(str(item) for item in roles)
                    run_series.update(str(item) for item in roles.values())
                for entity_id, payload in entities.items():
                    if not isinstance(payload, Mapping):
                        continue
                    if run_series.intersection(str(key) for key in payload):
                        for series, metrics in payload.items():
                            if not isinstance(metrics, Mapping):
                                continue
                            for metric, scalar in _scalar_items(metrics):
                                rows.append(_row(kind, section, metric, scalar, entity_type=entity_type, entity_id=str(entity_id), series=str(series)))
                    else:
                        for metric, values in payload.items():
                            if isinstance(values, Mapping):
                                for series, scalar in values.items():
                                    if not isinstance(scalar, (Mapping, list, tuple)):
                                        rows.append(_row(kind, section, str(metric), scalar, entity_type=entity_type, entity_id=str(entity_id), series=str(series)))
                            elif not isinstance(values, (list, tuple)):
                                rows.append(_row(kind, section, str(metric), values, entity_type=entity_type, entity_id=str(entity_id)))
        for section in ("run_summaries", "summary_deltas_vs_baseline"):
            run_rows = data.get(section) or {}
            if isinstance(run_rows, Mapping):
                for run_id, payload in run_rows.items():
                    if not isinstance(payload, Mapping):
                        continue
                    for metric, scalar in _scalar_items(payload):
                        rows.append(_row(kind, section, metric, scalar, entity_type="run", entity_id=str(run_id), series=str(run_id)))
    return rows


_REPORT_KIND_LABELS = {
    "single_run": "单次运行报告",
    "damage_comparison": "损毁与组群比较报告",
    "scenario_comparison": "多损毁比较报告",
    "configuration_comparison": "方案配置比较报告",
}

_METRIC_LABELS = {
    "required_sorties_total": "任务需求",
    "fulfilled_sorties_total": "实际履行",
    "unmet_sorties_total": "任务缺口",
    "additional_sorties_total": "额外调度",
    "scheduled_sorties_total": "实际出动",
    "completion_ratio": "任务完成率",
    "participating_airport_count": "参与机场数",
    "selected_cluster_count": "入选组群数",
    "peak_window": "高峰时间窗",
    "peak_sorties": "高峰出动架次",
    "objective": "目标函数 J",
}

_SUMMARY_METRICS = (
    "required_sorties_total",
    "fulfilled_sorties_total",
    "unmet_sorties_total",
    "additional_sorties_total",
    "scheduled_sorties_total",
    "completion_ratio",
    "participating_airport_count",
    "objective",
)


def _report_runs(kind: str, data: Mapping[str, Any], source_run_ids: Sequence[Any]) -> list[tuple[str, str]]:
    roles = data.get("roles") or {}
    if isinstance(roles, Mapping) and roles:
        if kind == "damage_comparison":
            role_labels = {
                "R0": "无损毁基准",
                "R1": "损毁未组群",
                "R2": "损毁后组群优化",
            }
            ordered_roles = [role for role in ("R0", "R1", "R2") if role in roles]
            ordered_roles.extend(role for role in roles if role not in ordered_roles)
            return [
                (role_labels.get(str(role), str(role)), str(roles[role]))
                for role in ordered_roles
            ]
        return [(str(role), str(run_id)) for role, run_id in roles.items()]
    run_ids = data.get("run_ids") or source_run_ids
    if not isinstance(run_ids, (list, tuple)):
        run_ids = []
    baseline = data.get("baseline_run_id")
    comparison = data.get("comparison_run_id")
    rows = []
    for index, run_id in enumerate(run_ids, start=1):
        label = "基准" if run_id == baseline else "对照" if run_id == comparison else f"运行{index}"
        rows.append((label, str(run_id)))
    if kind == "single_run" and not rows and source_run_ids:
        rows.append(("本次运行", str(source_run_ids[0])))
    return rows


def _series_value(mapping: Any, *, label: str, run_id: str) -> Any:
    if not isinstance(mapping, Mapping):
        return None
    if run_id in mapping:
        return mapping.get(run_id)
    return mapping.get(label)


def _comparison_data_label(data: Mapping[str, Any], *, label: str, run_id: str) -> str:
    roles = data.get("roles") or {}
    if isinstance(roles, Mapping):
        for role, role_run_id in roles.items():
            if str(role_run_id) == str(run_id):
                return str(role)
    return label


def _comparison_summary_value(
    data: Mapping[str, Any], *, label: str, run_id: str, metric: str
) -> Any:
    summary = data.get("summary") or {}
    if isinstance(summary, Mapping):
        run_summary = summary.get(run_id)
        if isinstance(run_summary, Mapping) and metric in run_summary:
            return run_summary.get(metric)
        value = _series_value(summary.get(metric), label=label, run_id=run_id)
        if value is not None:
            return value
    run_summaries = data.get("run_summaries") or {}
    if isinstance(run_summaries, Mapping):
        run_summary = run_summaries.get(run_id)
        if isinstance(run_summary, Mapping) and metric in run_summary:
            return run_summary.get(metric)
    comparison_summary = data.get("comparison_summary") or {}
    if isinstance(comparison_summary, Mapping):
        data_label = _comparison_data_label(data, label=label, run_id=run_id)
        projected = comparison_summary.get(data_label)
        if isinstance(projected, Mapping) and metric in projected:
            return projected.get(metric)
    if metric == "objective":
        solver_comparison = data.get("solver_comparison") or {}
        by_run = solver_comparison.get("by_run") or {} if isinstance(solver_comparison, Mapping) else {}
        projected = by_run.get(run_id) if isinstance(by_run, Mapping) else None
        if isinstance(projected, Mapping):
            return projected.get("objective")
    return None


def _entity_metric(payload: Any, metric: str, *, label: str, run_id: str) -> Any:
    if not isinstance(payload, Mapping):
        return None
    run_payload = payload.get(run_id)
    if isinstance(run_payload, Mapping) and metric in run_payload:
        return run_payload.get(metric)
    label_payload = payload.get(label)
    if isinstance(label_payload, Mapping) and metric in label_payload:
        return label_payload.get(metric)
    return _series_value(payload.get(metric), label=label, run_id=run_id)


def _series_list(value: Any) -> Optional[list[Any]]:
    if isinstance(value, list):
        return value
    if isinstance(value, Mapping) and isinstance(value.get("values"), list):
        return list(value["values"])
    return None


def _entity_value(payload: Any, metric: str, *, label: str, run_id: str) -> Any:
    value = _entity_metric(payload, metric, label=label, run_id=run_id)
    if value is not None:
        return value
    if not isinstance(payload, Mapping):
        return None
    nested = payload.get(run_id)
    if not isinstance(nested, Mapping):
        nested = payload.get(label)
    if isinstance(nested, Mapping) and metric == "scheduled_total":
        return nested.get("value")
    return None


def _resource_ratio(payload: Any, *, label: str, run_id: str) -> Any:
    if not isinstance(payload, Mapping):
        return None
    value = payload.get(run_id, payload.get(label))
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    if not isinstance(value, Mapping):
        return None
    if isinstance(value.get("ratio"), (int, float)):
        return value.get("ratio")
    detail = value.get("detail")
    if isinstance(detail, Mapping):
        return detail.get("ratio")
    return None


def _contains_cjk(value: Any) -> bool:
    return any("\u3400" <= char <= "\u9fff" for char in str(value or ""))


def _airport_display_name(airport_id: Any, airport: Mapping[str, Any]) -> str:
    identifier = str(airport_id)
    display_name = airport.get("display_name")
    if isinstance(display_name, str) and display_name.strip():
        return display_name.strip()
    airport_name = airport.get("airport_name")
    if isinstance(airport_name, str) and _contains_cjk(airport_name):
        return airport_name.strip()
    return str(airport.get("iata_code") or airport.get("icao_code") or identifier)


def _label_map(data: Mapping[str, Any], single_data: Optional[Mapping[str, Any]] = None) -> Dict[str, Dict[str, str]]:
    labels = data.get("labels") or {}
    full_airport_names = dict(labels.get("airports") or {}) if isinstance(labels, Mapping) else {}
    short_airport_names = dict(labels.get("airport_short_names") or {}) if isinstance(labels, Mapping) else {}
    airports = {
        str(airport_id): (
            str(full_name)
            if _contains_cjk(full_name)
            else str(short_airport_names.get(airport_id) or airport_id)
        )
        for airport_id, full_name in full_airport_names.items()
    }
    for airport_id, short_name in short_airport_names.items():
        airports.setdefault(str(airport_id), str(short_name or airport_id))
    output = {
        "airports": airports,
        "missions": dict(labels.get("missions") or {}) if isinstance(labels, Mapping) else {},
        "aircraft": dict(labels.get("aircraft") or {}) if isinstance(labels, Mapping) else {},
    }
    if single_data is None:
        return output
    snapshot = single_data.get("snapshot") or {}
    situation = snapshot.get("situation") or single_data.get("situation") or {}
    for item in situation.get("airports") or []:
        if not isinstance(item, Mapping):
            continue
        airport = item.get("airport") if isinstance(item.get("airport"), Mapping) else item
        airport_id = airport.get("airport_id")
        if airport_id:
            output["airports"][str(airport_id)] = _airport_display_name(airport_id, airport)
    for item in situation.get("missions") or []:
        if not isinstance(item, Mapping):
            continue
        mission = item.get("mission") if isinstance(item.get("mission"), Mapping) else item
        mission_id = mission.get("mission_id")
        if mission_id:
            output["missions"][str(mission_id)] = str(mission.get("name") or mission_id)
    catalogs = snapshot.get("catalogs") or {}
    for item in catalogs.get("aircraft_types") or []:
        if isinstance(item, Mapping) and item.get("aircraft_type_id"):
            aircraft_id = str(item["aircraft_type_id"])
            output["aircraft"][aircraft_id] = str(item.get("name") or aircraft_id)
    return output


def _focus_report_rows(focus: Any) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    focus_rows: list[tuple[Any, ...]] = []
    objective_rows: list[tuple[Any, ...]] = []
    if not isinstance(focus, Mapping):
        return focus_rows, objective_rows
    values = focus.get("values") or {}
    if isinstance(values, Mapping):
        for metric, facts in values.items():
            if not isinstance(facts, Mapping):
                continue
            metric_label = _METRIC_LABELS.get(str(metric), str(metric))
            formatter = _percent if str(metric) == "completion_ratio" else (lambda value: value)
            focus_rows.append((
                metric_label,
                formatter(facts.get("baseline")),
                formatter(facts.get("current")),
                formatter(facts.get("delta")),
            ))
    components = focus.get("objective_components") or {}
    if isinstance(components, Mapping):
        for component, facts in components.items():
            if not isinstance(facts, Mapping):
                continue
            objective_rows.append((
                component,
                facts.get("baseline"),
                facts.get("current"),
                facts.get("delta"),
                "是" if facts.get("definition_comparable") is not False else "否",
            ))
    return focus_rows, objective_rows


def _airport_structure_fact(
    focus: Mapping[str, Any], *, stage: str, airport_labels: Mapping[str, str]
) -> str:
    structural_changes = focus.get("structural_changes") or {}
    rows = structural_changes.get("airports") or [] if isinstance(structural_changes, Mapping) else []
    recorded = [row for row in rows if isinstance(row, Mapping) and row.get("delta") is not None]
    changed = [row for row in recorded if float(row.get("delta") or 0) != 0]
    if not changed:
        if recorded:
            return f"{stage}机场承接结构未记录到出动架次变化"
        return f"{stage}机场承接结构缺少可用的正式变化记录"
    details = []
    for row in changed[:3]:
        airport_id = str(row.get("object_id") or "-")
        delta = float(row["delta"])
        direction = "增加" if delta > 0 else "减少"
        details.append(f"{airport_labels.get(airport_id, airport_id)}{direction}{abs(delta):g}架次")
    return f"{stage}机场承接结构发生调整：{'，'.join(details)}"


def _airport_names_in_text(value: Any, airport_labels: Mapping[str, str]) -> str:
    text = str(value)
    for airport_id in sorted(airport_labels, key=len, reverse=True):
        text = text.replace(airport_id, airport_labels[airport_id])
    return text


def _chart(
    chart_id: str,
    title: str,
    chart_type: str,
    *,
    series: Sequence[tuple[str, Sequence[Any]]],
    categories: Sequence[str] = (),
    windows: Sequence[Any] = (),
    value_type: str = "integer",
    note: str = "",
) -> Optional[Dict[str, Any]]:
    axis = list(windows) if chart_type == "line" else list(categories)
    if not axis or not series:
        return None
    normalized: list[Dict[str, Any]] = []
    for name, values in series:
        if not isinstance(values, (list, tuple)):
            return None
        row = list(values)
        if len(row) != len(axis) or any(value is None for value in row):
            return None
        normalized.append({"name": str(name), "values": row})
    return {
        "id": chart_id,
        "title": title,
        "type": chart_type,
        "categories": list(categories),
        "windows": list(windows),
        "series": normalized,
        "value_type": value_type,
        "note": note,
    }


def _trim_inactive_tail(
    windows: Sequence[Any], series: Sequence[tuple[str, Sequence[Any]]]
) -> tuple[list[Any], list[tuple[str, list[Any]]]]:
    """Drop only a trailing all-zero tail while retaining one closing zero window."""

    axis = list(windows)
    rows = [(name, list(values)) for name, values in series]
    if not axis or not rows or any(len(values) != len(axis) for _name, values in rows):
        return axis, rows
    active_indexes = [
        index
        for index in range(len(axis))
        if any(
            values[index] is not None and abs(float(values[index])) > 1e-12
            for _name, values in rows
        )
    ]
    if not active_indexes:
        return axis, rows
    stop = min(len(axis), active_indexes[-1] + 2)
    return axis[:stop], [(name, values[:stop]) for name, values in rows]


def _comparison_charts(
    data: Mapping[str, Any],
    runs: Sequence[tuple[str, str]],
    *,
    labels: Mapping[str, Mapping[str, str]],
    windows: Sequence[Any],
) -> list[Dict[str, Any]]:
    charts: list[Dict[str, Any]] = []
    timeline = data.get("timeline") or {}
    departures = timeline.get("departures") or {} if isinstance(timeline, Mapping) else {}
    departure_series = []
    for label, run_id in runs:
        data_label = _comparison_data_label(data, label=label, run_id=run_id)
        values = _series_list(_series_value(departures, label=data_label, run_id=run_id))
        if values is not None:
            departure_series.append((label, values))
    chart_windows, departure_series = _trim_inactive_tail(windows, departure_series)
    candidate = _chart(
        "departure_timeline", "出动量时间趋势", "line",
        windows=chart_windows, series=departure_series,
        note="横轴为模型时间窗；每个时间窗为 15 分钟。",
    )
    if candidate:
        charts.append(candidate)

    tasks = data.get("tasks") or {}
    if isinstance(tasks, Mapping) and tasks:
        task_ids = list(tasks)
        categories = [labels.get("missions", {}).get(str(task_id), str(task_id)) for task_id in task_ids]
        demand_by_run = [
            [
                _entity_value(
                    tasks[task_id],
                    "required_total",
                    label=_comparison_data_label(data, label=label, run_id=run_id),
                    run_id=run_id,
                )
                for task_id in task_ids
            ]
            for label, run_id in runs
        ]
        actual_by_run = [
            [
                _entity_value(
                    tasks[task_id],
                    "fulfilled_total",
                    label=_comparison_data_label(data, label=label, run_id=run_id),
                    run_id=run_id,
                )
                for task_id in task_ids
            ]
            for label, run_id in runs
        ]
        if not any(any(value is not None for value in values) for values in actual_by_run):
            actual_by_run = [
                [
                    _entity_value(
                        tasks[task_id],
                        "scheduled_total",
                        label=_comparison_data_label(data, label=label, run_id=run_id),
                        run_id=run_id,
                    )
                    for task_id in task_ids
                ]
                for label, run_id in runs
            ]
            actual_name = "出动"
        else:
            actual_name = "履行"
        task_series: list[tuple[str, Sequence[Any]]] = []
        if demand_by_run and all(values == demand_by_run[0] for values in demand_by_run[1:]):
            task_series.append(("需求", demand_by_run[0]))
        else:
            task_series.extend((f"{label}需求", values) for (label, _), values in zip(runs, demand_by_run))
        task_series.extend((f"{label}{actual_name}", values) for (label, _), values in zip(runs, actual_by_run))
        candidate = _chart(
            "task_fulfillment", "任务需求与实际完成", "grouped_bar",
            categories=categories, series=task_series,
            note="仅呈现结果页已保存的任务需求、履行或实际出动事实。",
        )
        if candidate:
            charts.append(candidate)

    airports = data.get("airports") or {}
    if isinstance(airports, Mapping) and airports:
        airport_ids = list(airports)
        categories = [labels.get("airports", {}).get(str(airport_id), str(airport_id)) for airport_id in airport_ids]
        candidate = _chart(
            "airport_departures", "机场出动承接对比", "grouped_bar",
            categories=categories,
            series=[
                (
                    label,
                    [
                        _entity_value(
                            airports[airport_id],
                            "departures_total",
                            label=_comparison_data_label(data, label=label, run_id=run_id),
                            run_id=run_id,
                        )
                        for airport_id in airport_ids
                    ],
                )
                for label, run_id in runs
            ],
            note="按机场展示各运行的实际出动架次。",
        )
        if candidate:
            charts.append(candidate)

    aircraft = data.get("aircraft") or {}
    if isinstance(aircraft, Mapping) and aircraft:
        aircraft_ids = list(aircraft)
        candidate = _chart(
            "aircraft_composition", "机型投入构成", "stacked_bar",
            categories=[label for label, _ in runs],
            series=[
                (
                    labels.get("aircraft", {}).get(str(aircraft_id), str(aircraft_id)),
                    [
                        _entity_value(
                            aircraft[aircraft_id],
                            "scheduled_total",
                            label=_comparison_data_label(data, label=label, run_id=run_id),
                            run_id=run_id,
                        )
                        for label, run_id in runs
                    ],
                )
                for aircraft_id in aircraft_ids
            ],
            note="堆叠高度为各运行的实际出动总量，颜色表示机型。",
        )
        if candidate:
            charts.append(candidate)

    resources = data.get("resources") or {}
    category_rows = resources.get("category_min_remaining_ratio") or {} if isinstance(resources, Mapping) else {}
    category_labels = {"fuel": "油料", "material": "保障物资", "munition": "弹药"}
    if isinstance(category_rows, Mapping) and category_rows:
        category_ids = [category for category in ("fuel", "material", "munition") if category in category_rows]
        candidate = _chart(
            "resource_minimums", "关键资源最低剩余比例", "grouped_bar",
            categories=[category_labels[category] for category in category_ids],
            series=[
                (
                    label,
                    [
                        _resource_ratio(
                            category_rows[category],
                            label=_comparison_data_label(data, label=label, run_id=run_id),
                            run_id=run_id,
                        )
                        for category in category_ids
                    ],
                )
                for label, run_id in runs
            ],
            value_type="percent",
            note="比例以冻结的损毁前初始库存为分母，取参与机场范围内的最低记录值。",
        )
        if candidate:
            charts.append(candidate)
    return charts


def _assessment_report_facts(
    report_data: Mapping[str, Any], runs: Sequence[tuple[str, str]]
) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]], list[Dict[str, Any]]]:
    """Project persisted assessment facts into PDF rows and charts without recomputing them."""

    raw = report_data.get("assessments") or {}
    if not isinstance(raw, Mapping):
        return [], [], []
    rating_labels = {
        "very_low": "很低",
        "low": "较低",
        "medium": "中等",
        "high": "较高",
        "very_high": "很高",
    }
    risk_labels = {1: "1级 低", 2: "2级 较低", 3: "3级 中", 4: "4级 较高", 5: "5级 高"}
    records: list[tuple[str, str, Mapping[str, Any]]] = []
    summary_rows: list[tuple[Any, ...]] = []
    indicator_rows: list[tuple[Any, ...]] = []
    for label, run_id in runs:
        evaluation = raw.get(run_id)
        if not isinstance(evaluation, Mapping) or evaluation.get("status") != "available":
            continue
        support_index = evaluation.get("support_index")
        if support_index is None:
            continue
        membership = evaluation.get("overall_membership") or {}
        dominant = None
        if isinstance(membership, Mapping) and membership:
            numeric_membership = [
                (str(key), value)
                for key, value in membership.items()
                if isinstance(value, (int, float)) and not isinstance(value, bool)
            ]
            if numeric_membership:
                dominant = max(numeric_membership, key=lambda item: float(item[1]))[0]
        risk = evaluation.get("risk") or {}
        risk_available = isinstance(risk, Mapping) and risk.get("status") == "available"
        risk_level = risk.get("level") if risk_available else None
        summary_rows.append((
            label,
            run_id,
            support_index,
            rating_labels.get(str(dominant), dominant or "未记录"),
            risk_labels.get(risk_level, "未评价") if risk_available else "未评价",
            risk.get("score") if risk_available else None,
            "AHP" if evaluation.get("weight_source") == "ahp" else "专家评分",
        ))
        indicators = evaluation.get("indicators") or []
        if isinstance(indicators, list):
            for item in indicators:
                if isinstance(item, Mapping):
                    indicator_rows.append((
                        label,
                        item.get("name") or item.get("code"),
                        item.get("raw_value"),
                        item.get("normalized_value"),
                        item.get("weight"),
                    ))
        records.append((label, run_id, evaluation))

    charts: list[Dict[str, Any]] = []
    composite = _chart(
        "assessment_composite",
        "综合能力指数",
        "grouped_bar",
        categories=[label for label, _run_id, _evaluation in records],
        series=(("综合能力指数", [evaluation.get("support_index") for _label, _run_id, evaluation in records]),),
        value_type="percent",
        note="直接展示已落库且来源与当前运行结果一致的正式综合评价。",
    )
    if composite:
        charts.append(composite)

    if records:
        first_indicators = records[0][2].get("indicators") or []
        if isinstance(first_indicators, list) and first_indicators:
            codes = [
                str(item.get("code"))
                for item in first_indicators
                if isinstance(item, Mapping) and item.get("code")
            ]
            names = [
                str(item.get("name") or item.get("code"))
                for item in first_indicators
                if isinstance(item, Mapping) and item.get("code")
            ]
            series = []
            for label, _run_id, evaluation in records:
                values = {
                    str(item.get("code")): item.get("normalized_value")
                    for item in evaluation.get("indicators") or []
                    if isinstance(item, Mapping) and item.get("code")
                }
                series.append((label, [values.get(code) for code in codes]))
            indicators_chart = _chart(
                "assessment_indicators",
                "评价指标归一化结果",
                "grouped_bar",
                categories=names,
                series=series,
                value_type="percent",
                note="指标值、权重和评价口径均来自现有正式评价结果；报告不重新计算指标。",
            )
            if indicators_chart:
                charts.append(indicators_chart)
    return summary_rows, indicator_rows, charts


def build_pdf_report_model(report_data: Mapping[str, Any]) -> Dict[str, Any]:
    """Normalize every canonical result shape into one fixed PDF report contract."""

    _validate_report_data(report_data)
    kind = str(report_data.get("kind") or "")
    data = report_data.get("data") or {}
    source_run_ids = list(report_data.get("source_run_ids") or [])
    runs = _report_runs(kind, data, source_run_ids)
    charts: list[Dict[str, Any]] = []
    condition_rows: list[tuple[Any, ...]] = []
    comparison_sections: list[Dict[str, Any]] = []
    objective_value_rows: list[tuple[Any, ...]] = []

    if kind == "single_run":
        run = data.get("run") or {}
        metrics = data.get("metrics") or {}
        summary = metrics.get("summary") or {} if isinstance(metrics, Mapping) else {}
        technical = metrics.get("technical") or {} if isinstance(metrics, Mapping) else {}
        run_id = str(run.get("run_id") or (source_run_ids[0] if source_run_ids else ""))
        runs = [("本次运行", run_id)]
        run_summary_rows = [tuple(
            ["本次运行", run_id]
            + [
                technical.get("objective") if metric == "objective" else summary.get(metric)
                for metric in _SUMMARY_METRICS
            ]
        )]
        objective_value_rows = [
            ("F1", technical.get("f1")),
            ("F2", technical.get("f2")),
            ("F3", technical.get("f3")),
            ("J", technical.get("objective")),
        ]
        labels = _label_map(data, data)
        run_config = data.get("run_config") or {}
        if isinstance(run_config, Mapping):
            config_labels = {
                "cluster_enabled": "启用组群",
                "cluster_size": "组群规模",
                "preference_mode": "偏好模式",
                "alpha": "偏好系数",
            }
            condition_rows = [
                (config_labels[key], _configuration_value(key, run_config.get(key), labels))
                for key in config_labels if key in run_config
            ]
        airports = metrics.get("airports") or {} if isinstance(metrics, Mapping) else {}
        airport_headers = ["机场", "出动", "返航", "出动占比"]
        airport_rows = [
            (labels["airports"].get(str(airport_id), airport_id), row.get("departures_total"), row.get("returns_total"), row.get("departure_share"))
            for airport_id, row in airports.items() if isinstance(row, Mapping)
        ] if isinstance(airports, Mapping) else []
        tasks = metrics.get("tasks") or {} if isinstance(metrics, Mapping) else {}
        task_headers = ["任务", "需求", "履行", "缺口", "实际出动"]
        task_rows = [
            (labels["missions"].get(str(mission_id), mission_id), row.get("required_total"), row.get("fulfilled_total"), row.get("unmet_total"), row.get("scheduled_total"))
            for mission_id, row in tasks.items() if isinstance(row, Mapping)
        ] if isinstance(tasks, Mapping) else []
        axis = metrics.get("time_axis") or {} if isinstance(metrics, Mapping) else {}
        raw_timeline = metrics.get("timeline") or {} if isinstance(metrics, Mapping) else {}
        windows = list(axis.get("windows") or []) if isinstance(axis, Mapping) else []
        timeline_series = []
        if isinstance(raw_timeline, Mapping):
            for label, key in (("出动", "departures_total"), ("返航", "returns_total")):
                values = raw_timeline.get(key)
                if isinstance(values, list) and len(values) == len(windows):
                    timeline_series.append((label, values))
        chart_windows, chart_timeline_series = _trim_inactive_tail(windows, timeline_series)
        candidate = _chart(
            "departure_timeline", "出动与返航时间趋势", "line",
            windows=chart_windows, series=chart_timeline_series,
            note="横轴为模型时间窗；每个时间窗为 15 分钟。",
        )
        if candidate:
            charts.append(candidate)

        if isinstance(tasks, Mapping) and tasks:
            task_ids = list(tasks)
            candidate = _chart(
                "task_fulfillment", "任务需求与实际履行", "grouped_bar",
                categories=[labels["missions"].get(str(task_id), str(task_id)) for task_id in task_ids],
                series=(
                    ("需求", [(tasks[task_id] or {}).get("required_total") for task_id in task_ids]),
                    ("履行", [(tasks[task_id] or {}).get("fulfilled_total") for task_id in task_ids]),
                ),
                note="履行量不含超过任务需求的额外调度。",
            )
            if candidate:
                charts.append(candidate)

        if isinstance(airports, Mapping) and airports:
            airport_ids = list(airports)
            candidate = _chart(
                "airport_departures", "机场出动承接结构", "grouped_bar",
                categories=[labels["airports"].get(str(airport_id), str(airport_id)) for airport_id in airport_ids],
                series=(("出动架次", [(airports[airport_id] or {}).get("departures_total") for airport_id in airport_ids]),),
                note="按始发机场统计实际出动架次。",
            )
            if candidate:
                charts.append(candidate)

        aircraft = metrics.get("aircraft") or {} if isinstance(metrics, Mapping) else {}
        if isinstance(aircraft, Mapping) and aircraft:
            aircraft_ids = list(aircraft)
            candidate = _chart(
                "aircraft_composition", "机型投入构成", "stacked_bar",
                categories=["本次运行"],
                series=[
                    (labels["aircraft"].get(str(aircraft_id), str(aircraft_id)), [(aircraft[aircraft_id] or {}).get("scheduled_total")])
                    for aircraft_id in aircraft_ids
                ],
                note="堆叠高度为实际出动总量，颜色表示机型。",
            )
            if candidate:
                charts.append(candidate)

        resources = metrics.get("resources") or {} if isinstance(metrics, Mapping) else {}
        category_timeline = resources.get("category_min_remaining_ratio_timeline") or {} if isinstance(resources, Mapping) else {}
        category_names = {"fuel": "油料", "material": "保障物资", "munition": "弹药"}
        if isinstance(category_timeline, Mapping):
            resource_series = []
            for category in ("fuel", "material", "munition"):
                values = []
                for item in category_timeline.get(category) or []:
                    values.append(item.get("ratio") if isinstance(item, Mapping) else None)
                if len(values) == len(windows) and all(value is not None for value in values):
                    resource_series.append((category_names[category], values))
            candidate = _chart(
                "resource_minimum_timeline", "关键资源最低剩余比例趋势", "line",
                windows=windows, series=resource_series, value_type="percent",
                note="逐窗展示参与机场范围内、以初始库存为分母的最低剩余比例。",
            )
            if candidate:
                charts.append(candidate)

        busiest_id = None
        max_airport = summary.get("max_airport_departure") if isinstance(summary, Mapping) else None
        if isinstance(max_airport, Mapping):
            busiest_id = max_airport.get("airport_id")
        if not busiest_id and isinstance(airports, Mapping) and airports:
            busiest_id = max(airports, key=lambda airport_id: (airports[airport_id] or {}).get("departures_total") or 0)
        inventory = metrics.get("aircraft_inventory") or {} if isinstance(metrics, Mapping) else {}
        inventory_rows = (inventory.get("by_airport") or {}).get(busiest_id) if isinstance(inventory, Mapping) else None
        if busiest_id and isinstance(inventory_rows, Mapping):
            candidate = _chart(
                "aircraft_availability", f"{labels['airports'].get(str(busiest_id), busiest_id)}航空器可用量", "line",
                windows=windows,
                series=[
                    (labels["aircraft"].get(str(aircraft_id), str(aircraft_id)), row.get("available_before_departure") or [])
                    for aircraft_id, row in inventory_rows.items() if isinstance(row, Mapping)
                ],
                note="展示出动前可用航空器数量，直接取自保有/周转状态序列。",
            )
            if candidate:
                charts.append(candidate)
        busiest_metrics = airports.get(busiest_id) if isinstance(airports, Mapping) and busiest_id else None
        capacity = busiest_metrics.get("capacity") if isinstance(busiest_metrics, Mapping) else None
        if isinstance(capacity, Mapping):
            candidate = _chart(
                "capacity_utilization", f"{labels['airports'].get(str(busiest_id), busiest_id)}容量占用率", "line",
                windows=windows, series=(("容量占用率", capacity.get("utilization") or []),), value_type="percent",
                note="容量占用率为结果页既有逐窗容量使用记录。",
            )
            if candidate:
                charts.append(candidate)
        conclusions = []
        focus_rows = []
        objective_rows = []
    else:
        labels = _label_map(data)
        run_summary_rows = [
            tuple([label, run_id] + [
                _comparison_summary_value(data, label=label, run_id=run_id, metric=metric)
                for metric in _SUMMARY_METRICS
            ])
            for label, run_id in runs
        ]
        focus = data.get("focus_comparison") or {}
        focus_rows, objective_rows = _focus_report_rows(focus)

        if kind == "damage_comparison":
            comparisons = data.get("comparisons") or {}
            damage_focus = comparisons.get("damage") if isinstance(comparisons, Mapping) else None
            cluster_focus = comparisons.get("cluster") if isinstance(comparisons, Mapping) else None
            if isinstance(damage_focus, Mapping) and isinstance(cluster_focus, Mapping):
                section_specs = (
                    (
                        "损毁影响对照",
                        "无损毁基准",
                        "损毁未组群",
                        "损毁影响目标函数分量",
                        damage_focus,
                    ),
                    (
                        "组群效果对照",
                        "损毁未组群",
                        "损毁后组群优化",
                        "组群效果目标函数分量",
                        cluster_focus,
                    ),
                )
                comparison_sections = []
                for title_text, baseline_label, current_label, objective_title, section_focus in section_specs:
                    section_focus_rows, section_objective_rows = _focus_report_rows(section_focus)
                    comparison_sections.append({
                        "title": title_text,
                        "headers": ["指标", baseline_label, current_label, "差值"],
                        "focus_rows": section_focus_rows,
                        "objective_title": objective_title,
                        "objective_headers": ["分量", baseline_label, current_label, "差值", "口径可比"],
                        "objective_rows": section_objective_rows,
                    })
                focus_rows = []
                objective_rows = []

        airports = data.get("airports") or {}
        airport_headers = ["机场"] + [f"{label}出动" for label, _ in runs] + [f"{label}占比" for label, _ in runs]
        airport_rows = []
        if isinstance(airports, Mapping):
            for airport_id, payload in airports.items():
                departures = [
                    _entity_metric(
                        payload,
                        "departures_total",
                        label=_comparison_data_label(data, label=label, run_id=run_id),
                        run_id=run_id,
                    )
                    for label, run_id in runs
                ]
                shares = [
                    _entity_metric(
                        payload,
                        "departure_share",
                        label=_comparison_data_label(data, label=label, run_id=run_id),
                        run_id=run_id,
                    )
                    for label, run_id in runs
                ]
                airport_rows.append(tuple([labels["airports"].get(str(airport_id), airport_id)] + departures + shares))

        tasks = data.get("tasks") or {}
        task_headers = ["任务"]
        for label, _ in runs:
            task_headers.extend((f"{label}需求", f"{label}出动"))
        task_rows = []
        if isinstance(tasks, Mapping):
            for mission_id, payload in tasks.items():
                row = [labels["missions"].get(str(mission_id), mission_id)]
                for label, run_id in runs:
                    data_label = _comparison_data_label(data, label=label, run_id=run_id)
                    row.extend((
                        _entity_metric(payload, "required_total", label=data_label, run_id=run_id),
                        _entity_metric(payload, "scheduled_total", label=data_label, run_id=run_id),
                    ))
                task_rows.append(tuple(row))

        raw_timeline = data.get("timeline") or {}
        windows = list(raw_timeline.get("windows") or []) if isinstance(raw_timeline, Mapping) else []
        departure_series = raw_timeline.get("departures") or {} if isinstance(raw_timeline, Mapping) else {}
        timeline_series = []
        for label, run_id in runs:
            data_label = _comparison_data_label(data, label=label, run_id=run_id)
            values = _series_list(_series_value(departure_series, label=data_label, run_id=run_id))
            if values is not None and len(values) == len(windows):
                timeline_series.append((label, values))
        conclusions = list(data.get("conclusions") or (focus.get("conclusions") if isinstance(focus, Mapping) else []) or [])
        if kind == "damage_comparison" and comparison_sections:
            damage_conclusions = damage_focus.get("conclusions") or []
            cluster_conclusions = cluster_focus.get("conclusions") or []
            conclusions = [
                str(damage_conclusions[0]) if damage_conclusions else "损毁影响缺少可用的正式汇总记录。",
                str(cluster_conclusions[0]) if cluster_conclusions else "组群效果缺少可用的正式汇总记录。",
                "机场承接结构："
                + _airport_structure_fact(
                    damage_focus,
                    stage="损毁影响阶段",
                    airport_labels=labels["airports"],
                )
                + "；"
                + _airport_structure_fact(
                    cluster_focus,
                    stage="组群效果阶段",
                    airport_labels=labels["airports"],
                )
                + "。",
            ]
        charts = _comparison_charts(data, runs, labels=labels, windows=windows)
        if kind == "configuration_comparison":
            differences = data.get("key_configuration_differences") or {}
            if isinstance(differences, Mapping):
                config_labels = {
                    "cluster_enabled": "启用组群",
                    "cluster_size": "组群规模",
                    "core_airports": "核心机场",
                    "preference_mode": "偏好模式",
                    "alpha": "偏好系数",
                }
                condition_rows = [
                    (
                        config_labels.get(str(key), str(key)),
                        _configuration_value(str(key), values.get("baseline"), labels),
                        _configuration_value(str(key), values.get("current"), labels),
                    )
                    for key, values in differences.items() if isinstance(values, Mapping)
                ]
        elif kind == "scenario_comparison":
            configurations = data.get("configurations") or {}
            if isinstance(configurations, Mapping):
                condition_rows = [
                    (label, (configurations.get(run_id) or {}).get("damage_scenario_id"))
                    for label, run_id in runs
                ]

    assessment_summary_rows, assessment_indicator_rows, assessment_charts = _assessment_report_facts(
        report_data, runs
    )
    charts.extend(assessment_charts)
    conclusions = [_airport_names_in_text(item, labels["airports"]) for item in conclusions]

    return {
        "kind": kind,
        "title": _REPORT_KIND_LABELS.get(kind, "仿真结果报告"),
        "source_run_ids": source_run_ids,
        "runs": runs,
        "summary_headers": ["对象", "运行编号"] + [_METRIC_LABELS[metric] for metric in _SUMMARY_METRICS],
        "run_summary_rows": run_summary_rows,
        "focus_rows": focus_rows,
        "objective_rows": objective_rows,
        "comparison_sections": comparison_sections,
        "objective_value_rows": objective_value_rows,
        "timeline": {"windows": windows, "series": timeline_series},
        "airport_headers": airport_headers,
        "airport_rows": airport_rows,
        "task_headers": task_headers,
        "task_rows": task_rows,
        "conclusions": conclusions,
        "condition_rows": condition_rows,
        "assessment_summary_rows": assessment_summary_rows,
        "assessment_indicator_rows": assessment_indicator_rows,
        "charts": charts,
    }


class ResultExportService:
    """Render one canonical report-data source to every supported file format."""

    def render_csv(self, report_data: Mapping[str, Any]) -> RenderedExport:
        rows = build_tidy_rows(report_data)
        out = io.StringIO(newline="")
        writer = csv.DictWriter(out, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
        # UTF-8 BOM keeps Chinese column/content readable when opened directly in Excel.
        content = ("\ufeff" + out.getvalue()).encode("utf-8")
        return RenderedExport(
            content,
            "text/csv; charset=utf-8",
            f"airport_run_results_{_filename_suffix(report_data)}.csv",
        )

    def render_json(self, report_data: Mapping[str, Any]) -> RenderedExport:
        _validate_report_data(report_data)
        try:
            content = (json.dumps(report_data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ResultExportError("report_data must contain JSON-serializable values") from exc
        return RenderedExport(
            content,
            "application/json; charset=utf-8",
            f"airport_run_results_{_filename_suffix(report_data)}.json",
        )

    def render_xml(self, report_data: Mapping[str, Any]) -> RenderedExport:
        _validate_report_data(report_data)
        root = ET.Element("report_data", {
            "schema_version": "report-data.v1",
            "type": "object",
        })
        for key, value in report_data.items():
            child = ET.SubElement(root, "field", {"name": str(key)})
            _append_xml_value(child, value)
        _indent_xml(root)
        content = ET.tostring(root, encoding="utf-8", xml_declaration=True)
        return RenderedExport(
            content,
            "application/xml; charset=utf-8",
            f"airport_run_results_{_filename_suffix(report_data)}.xml",
        )

    def render_pdf(self, report_data: Mapping[str, Any]) -> RenderedExport:
        try:
            from reportlab.lib import colors
            from reportlab.lib.enums import TA_LEFT
            from reportlab.lib.pagesizes import A4, landscape
            from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
            from reportlab.lib.units import mm
            from reportlab.graphics.charts.barcharts import VerticalBarChart
            from reportlab.graphics.charts.lineplots import LinePlot
            from reportlab.graphics.shapes import Drawing, Rect, String
            from reportlab.pdfbase import pdfmetrics
            from reportlab.pdfbase.ttfonts import TTFont
            from reportlab.platypus import (
                HRFlowable,
                KeepTogether,
                PageBreak,
                Paragraph,
                SimpleDocTemplate,
                Spacer,
                Table,
                TableStyle,
            )
        except ImportError as exc:  # pragma: no cover - deployment dependency
            raise ResultExportError("PDF rendering requires reportlab") from exc

        report = build_pdf_report_model(report_data)
        font_root = Path(__file__).resolve().parents[2] / "frontend" / "static" / "fonts"
        regular_font = "SourceHanSansCN-Regular"
        bold_font = "SourceHanSansCN-Bold"
        font_files = {
            regular_font: font_root / "SourceHanSansCN-Regular.ttf",
            bold_font: font_root / "SourceHanSansCN-Bold.ttf",
        }
        for font_name, font_path in font_files.items():
            if not font_path.is_file():
                raise ResultExportError(f"PDF font file is missing: {font_path.name}")
            if font_name not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont(font_name, str(font_path)))

        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=landscape(A4),
            rightMargin=13 * mm,
            leftMargin=13 * mm,
            topMargin=16 * mm,
            bottomMargin=14 * mm,
            title=f"{PDF_SYSTEM_NAME}结果报告",
            author=PDF_SYSTEM_NAME,
        )
        styles = getSampleStyleSheet()
        title = ParagraphStyle("zh-title", parent=styles["Title"], fontName=bold_font, fontSize=22, leading=30, spaceAfter=5 * mm, textColor=colors.HexColor("#143B56"))
        chart_section_title = ParagraphStyle(
            "zh-chart-section-title", parent=title, keepWithNext=True
        )
        subtitle = ParagraphStyle("zh-subtitle", parent=styles["Heading2"], fontName=regular_font, fontSize=12, leading=18, textColor=colors.HexColor("#4A687A"))
        h2 = ParagraphStyle("zh-h2", parent=styles["Heading2"], fontName=bold_font, fontSize=13, leading=18, spaceBefore=5 * mm, spaceAfter=2.5 * mm, keepWithNext=True, textColor=colors.HexColor("#1B5878"))
        body = ParagraphStyle("zh-body", parent=styles["BodyText"], fontName=regular_font, fontSize=9, leading=14, alignment=TA_LEFT, textColor=colors.HexColor("#203744"))
        small = ParagraphStyle("zh-small", parent=body, fontSize=7.5, leading=10, textColor=colors.HexColor("#415A6B"))
        conclusion = ParagraphStyle("zh-conclusion", parent=body, fontSize=10, leading=16, leftIndent=5 * mm, rightIndent=5 * mm, borderColor=colors.HexColor("#B9D5E3"), borderWidth=0.6, borderPadding=4 * mm, backColor=colors.HexColor("#F3F8FB"))
        story = [
            Spacer(1, 1 * mm),
            Paragraph(PDF_SYSTEM_NAME, subtitle),
            Paragraph(escape(report["title"]), title),
            HRFlowable(width="100%", thickness=1.2, color=colors.HexColor("#2A789E")),
            Spacer(1, 3 * mm),
        ]

        source_ids = ", ".join(str(x) for x in report["source_run_ids"])
        metadata = [
            ("报告类型", report["title"]),
            ("数据来源", "与结果页面同源的已保存运行结果"),
            ("来源运行编号", source_ids or "-"),
            ("时间粒度", "15 分钟 / 时间窗"),
        ]

        def table_flowable(headers: Sequence[str], records: Iterable[Sequence[Any]], widths=None):
            records = list(records)
            table_rows = [[Paragraph(escape(str(h)), small) for h in headers]]
            table_rows.extend([
                Paragraph(escape(_pdf_table_value(headers[index] if index < len(headers) else "", value)), small)
                for index, value in enumerate(record)
            ] for record in records)
            table = Table(table_rows, colWidths=widths, repeatRows=1, hAlign="LEFT")
            commands = [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#DCECF4")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#143B56")),
                ("FONTNAME", (0, 0), (-1, 0), bold_font),
                ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#B7C8D4")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
            for row_index in range(2, len(table_rows), 2):
                commands.append(("BACKGROUND", (0, row_index), (-1, row_index), colors.HexColor("#F7FAFC")))
            table.setStyle(TableStyle(commands))
            return table

        palette = ("#1F77B4", "#D85C41", "#2C9C69", "#8A62B8", "#B38320", "#4D7C8A", "#D179A3", "#6A7B36")

        def chart_drawing(spec: Mapping[str, Any], *, height_mm: float = 67) -> Any:
            drawing = Drawing(252 * mm, height_mm * mm)
            values = [
                [0.0 if value is None else float(value) for value in series["values"]]
                for series in spec["series"]
            ]
            if spec["type"] == "line":
                chart = LinePlot()
                chart.x = 16 * mm
                chart.y = 15 * mm
                chart.width = 224 * mm
                chart.height = (height_mm - 25) * mm
                chart.data = [
                    [(float(window), value) for window, value in zip(spec["windows"], series)]
                    for series in values
                ]
                chart.joinedLines = 1
                chart.xValueAxis.valueMin = min(float(value) for value in spec["windows"])
                chart.xValueAxis.valueMax = max(float(value) for value in spec["windows"])
                if chart.xValueAxis.valueMin == chart.xValueAxis.valueMax:
                    chart.xValueAxis.valueMax += 1
                chart.xValueAxis.labelTextFormat = "%d"
                chart.yValueAxis.valueMin = 0
                maximum = max((value for series in values for value in series), default=1.0)
                chart.yValueAxis.valueMax = max(1.0 if spec["value_type"] == "percent" else 1.0, maximum)
                chart.yValueAxis.labelTextFormat = (
                    (lambda value: f"{value * 100:.0f}%") if spec["value_type"] == "percent" else "%d"
                )
                for index, _series in enumerate(spec["series"]):
                    color = colors.HexColor(palette[index % len(palette)])
                    chart.lines[index].strokeColor = color
                    chart.lines[index].strokeWidth = 1.7
                drawing.add(chart)
            else:
                chart = VerticalBarChart()
                chart.x = 18 * mm
                chart.y = 18 * mm
                chart.width = 220 * mm
                chart.height = (height_mm - 29) * mm
                chart.data = values
                chart.categoryAxis.categoryNames = [str(value)[:18] for value in spec["categories"]]
                chart.categoryAxis.labels.fontName = regular_font
                chart.categoryAxis.labels.fontSize = 6.2
                chart.categoryAxis.labels.angle = 20 if len(spec["categories"]) > 4 else 0
                chart.categoryAxis.labels.boxAnchor = "ne" if chart.categoryAxis.labels.angle else "n"
                if spec["type"] == "stacked_bar":
                    chart.categoryAxis.style = "stacked"
                    chart.barLabelArray = _stacked_bar_label_array(
                        values, str(spec["value_type"])
                    )
                    chart.barLabelFormat = "values"
                else:
                    chart.groupSpacing = 8
                    chart.barSpacing = 1
                    chart.barLabelFormat = lambda value: _chart_value_label(
                        value, str(spec["value_type"])
                    )
                chart.barLabels.fontName = regular_font
                chart.barLabels.fontSize = 5.7
                chart.barLabels.fillColor = colors.HexColor("#203744")
                chart.barLabels.nudge = 2
                chart.valueAxis.valueMin = 0
                maximum = max((value for series in values for value in series), default=1.0)
                chart.valueAxis.valueMax = max(1.0, maximum)
                chart.valueAxis.labelTextFormat = (
                    (lambda value: f"{value * 100:.0f}%") if spec["value_type"] == "percent" else "%d"
                )
                for index, _series in enumerate(spec["series"]):
                    color = colors.HexColor(palette[index % len(palette)])
                    chart.bars[index].fillColor = color
                    chart.bars[index].strokeColor = color
                drawing.add(chart)
            for index, series in enumerate(spec["series"]):
                row = index // 4
                column = index % 4
                x = (15 + column * 58) * mm
                y = (7 - row * 4.2) * mm
                color = colors.HexColor(palette[index % len(palette)])
                drawing.add(Rect(x, y, 3 * mm, 1.8 * mm, fillColor=color, strokeColor=color))
                drawing.add(String(x + 4 * mm, y - 0.1 * mm, str(series["name"])[:18], fontName=regular_font, fontSize=6.3, fillColor=colors.HexColor("#415A6B")))
            return drawing

        def chart_block(spec: Mapping[str, Any], *, height_mm: float = 63) -> Any:
            items = [Paragraph(escape(str(spec["title"])), h2), chart_drawing(spec, height_mm=height_mm)]
            if spec.get("note"):
                items.append(Paragraph(escape(str(spec["note"])), small))
            return KeepTogether(items)

        story.append(table_flowable(["项目", "内容"], metadata, widths=[42 * mm, 205 * mm]))
        if report["conclusions"]:
            story.append(Paragraph("核心结论", h2))
            story.append(Paragraph("<br/>".join(escape(str(item)) for item in report["conclusions"]), conclusion))
        primary_chart = next((item for item in report["charts"] if item["id"] == "task_fulfillment"), None)
        if primary_chart:
            story.append(KeepTogether([
                Paragraph(escape(str(primary_chart["title"])), h2),
                chart_drawing(primary_chart, height_mm=47),
            ]))
        story.append(PageBreak())

        def add_table(title_text: str, headers: Sequence[str], records: Iterable[Sequence[Any]], widths=None, *, space_after=True):
            records = list(records)
            if not records:
                return
            story.append(Paragraph(title_text, h2))
            story.append(table_flowable(headers, records, widths=widths))
            if space_after:
                story.append(Spacer(1, 3 * mm))

        object_title = "运行对象" if report["kind"] == "single_run" else "比较对象"
        add_table(object_title, ["对象", "运行编号"], report["runs"], widths=[38 * mm, 205 * mm])
        if report["condition_rows"]:
            if report["kind"] == "configuration_comparison":
                add_table("关键配置差异", ["配置项", "基准配置", "对照配置"], report["condition_rows"])
            elif report["kind"] == "scenario_comparison":
                add_table("情景条件", ["运行对象", "损毁情景"], report["condition_rows"])
            else:
                add_table("运行条件", ["条件", "取值"], report["condition_rows"])
        add_table("核心指标摘要", report["summary_headers"], report["run_summary_rows"])
        if report["objective_value_rows"]:
            add_table(
                "目标函数分量",
                ["分量", "数值"],
                report["objective_value_rows"],
                widths=[55 * mm, 70 * mm],
            )
        if report["comparison_sections"]:
            for section in report["comparison_sections"]:
                add_table(
                    section["title"],
                    section["headers"],
                    section["focus_rows"],
                    widths=[70 * mm, 55 * mm, 55 * mm, 45 * mm],
                )
                add_table(
                    section["objective_title"],
                    section["objective_headers"],
                    section["objective_rows"],
                    widths=[42 * mm, 48 * mm, 48 * mm, 42 * mm, 42 * mm],
                )
        else:
            if report["focus_rows"]:
                add_table("重点对照指标", ["指标", "参照", "对照", "差值"], report["focus_rows"], widths=[70 * mm, 45 * mm, 45 * mm, 45 * mm])
            if report["objective_rows"]:
                add_table("目标函数分量", ["分量", "参照", "对照", "差值", "口径可比"], report["objective_rows"], widths=[45 * mm, 42 * mm, 42 * mm, 42 * mm, 42 * mm])
        if report["assessment_summary_rows"]:
            add_table(
                "顽存能力综合评价",
                ["对象", "运行编号", "综合能力指数", "评价等级", "风险等级", "风险值", "权重来源"],
                report["assessment_summary_rows"],
            )
        if report["assessment_indicator_rows"]:
            add_table(
                "评价指标明细",
                ["对象", "指标", "原始值", "归一化", "权重"],
                report["assessment_indicator_rows"],
            )

        remaining_charts = [item for item in report["charts"] if primary_chart is None or item["id"] != primary_chart["id"]]
        if remaining_charts:
            chart_heading = {
                "single_run": "运行结果图表",
                "damage_comparison": "损毁影响图表",
                "scenario_comparison": "多情景比较图表",
                "configuration_comparison": "配置方案比较图表",
            }.get(report["kind"], "结果图表")
            if not report["assessment_indicator_rows"]:
                story.append(PageBreak())
            story.append(Paragraph(chart_heading, chart_section_title))
            story.append(chart_block(remaining_charts[0], height_mm=50))
            story.append(Spacer(1, 2 * mm))
            for spec in remaining_charts[1:]:
                story.append(chart_block(spec))
                story.append(Spacer(1, 2 * mm))

        if report["airport_rows"] or report["task_rows"]:
            story.append(PageBreak())
        add_table("机场负载 / 承接情况", report["airport_headers"], report["airport_rows"])
        add_table("任务完成情况", report["task_headers"], report["task_rows"], space_after=False)

        def draw_page(canvas, _doc):
            canvas.saveState()
            canvas.setStrokeColor(colors.HexColor("#B7C8D4"))
            canvas.line(13 * mm, 11 * mm, landscape(A4)[0] - 13 * mm, 11 * mm)
            canvas.setFont(regular_font, 7)
            canvas.setFillColor(colors.HexColor("#607986"))
            canvas.drawString(13 * mm, 7 * mm, f"{PDF_SYSTEM_NAME} · 正式结果报告")
            canvas.drawRightString(landscape(A4)[0] - 13 * mm, 7 * mm, f"第 {canvas.getPageNumber()} 页")
            canvas.restoreState()

        doc.build(story, onFirstPage=draw_page, onLaterPages=draw_page)
        return RenderedExport(
            buffer.getvalue(),
            "application/pdf",
            f"airport_run_report_{_filename_suffix(report_data)}.pdf",
        )

    def render(self, report_data: Mapping[str, Any], fmt: str) -> RenderedExport:
        normalized = str(fmt or "").strip().lower()
        if normalized == "pdf":
            return self.render_pdf(report_data)
        if normalized == "csv":
            return self.render_csv(report_data)
        if normalized == "json":
            return self.render_json(report_data)
        if normalized == "xml":
            return self.render_xml(report_data)
        raise ResultExportError("format must be pdf, csv, json or xml")


__all__ = [
    "ResultExportService",
    "ResultExportError",
    "RenderedExport",
    "build_tidy_rows",
    "build_pdf_report_model",
    "CSV_COLUMNS",
    "SUPPORTED_FORMATS",
]
