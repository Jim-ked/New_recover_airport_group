from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence


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


def _scalar(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float, str)):
        return str(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


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


def build_tidy_rows(report_data: Mapping[str, Any]) -> list[Dict[str, str]]:
    if report_data.get("schema_version") != "report-data.v1":
        raise ResultExportError("report_data schema_version must be report-data.v1")
    kind = str(report_data.get("kind") or "")
    data = report_data.get("data")
    if not isinstance(data, Mapping):
        raise ResultExportError("report_data.data must be an object")
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
                for entity_id, payload in entities.items():
                    if not isinstance(payload, Mapping):
                        continue
                    for metric, values in payload.items():
                        if isinstance(values, Mapping):
                            for series, scalar in values.items():
                                if not isinstance(scalar, (Mapping, list, tuple)):
                                    rows.append(_row(kind, section, str(metric), scalar, entity_type=entity_type, entity_id=str(entity_id), series=str(series)))
                        elif not isinstance(values, (list, tuple)):
                            rows.append(_row(kind, section, str(metric), values, entity_type=entity_type, entity_id=str(entity_id)))
    return rows


class ResultExportService:
    """Render canonical report-data facts to the two frozen delivery formats."""

    def render_csv(self, report_data: Mapping[str, Any]) -> RenderedExport:
        rows = build_tidy_rows(report_data)
        out = io.StringIO(newline="")
        writer = csv.DictWriter(out, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
        # UTF-8 BOM keeps Chinese column/content readable when opened directly in Excel.
        content = ("\ufeff" + out.getvalue()).encode("utf-8")
        suffix = "_".join(str(x) for x in (report_data.get("source_run_ids") or [])[:3]) or "results"
        return RenderedExport(content, "text/csv; charset=utf-8", f"airport_run_results_{suffix}.csv")

    def render_pdf(self, report_data: Mapping[str, Any]) -> RenderedExport:
        try:
            from reportlab.lib import colors
            from reportlab.lib.enums import TA_LEFT
            from reportlab.lib.pagesizes import A4, landscape
            from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
            from reportlab.lib.units import mm
            from reportlab.pdfbase import pdfmetrics
            from reportlab.pdfbase.cidfonts import UnicodeCIDFont
            from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
        except ImportError as exc:  # pragma: no cover - deployment dependency
            raise ResultExportError("PDF rendering requires reportlab") from exc

        if report_data.get("schema_version") != "report-data.v1":
            raise ResultExportError("report_data schema_version must be report-data.v1")
        kind = str(report_data.get("kind") or "")
        data = report_data.get("data") or {}
        if not isinstance(data, Mapping):
            raise ResultExportError("report_data.data must be an object")

        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=landscape(A4),
            rightMargin=14 * mm,
            leftMargin=14 * mm,
            topMargin=12 * mm,
            bottomMargin=12 * mm,
            title="机场群顽存能力仿真结果报告",
        )
        styles = getSampleStyleSheet()
        title = ParagraphStyle("zh-title", parent=styles["Title"], fontName="STSong-Light", fontSize=18, leading=24, textColor=colors.HexColor("#15324A"))
        h2 = ParagraphStyle("zh-h2", parent=styles["Heading2"], fontName="STSong-Light", fontSize=12, leading=17, spaceBefore=6, spaceAfter=5, textColor=colors.HexColor("#1E4E72"))
        body = ParagraphStyle("zh-body", parent=styles["BodyText"], fontName="STSong-Light", fontSize=9, leading=13, alignment=TA_LEFT)
        small = ParagraphStyle("zh-small", parent=body, fontSize=8, leading=11, textColor=colors.HexColor("#415A6B"))
        story = [Paragraph("机场群顽存能力仿真结果报告", title)]
        labels = {
            "single_run": "单次运行",
            "damage_comparison": "损毁影响与优化效果比较",
            "scenario_comparison": "多场景比较",
            "configuration_comparison": "方案配置比较",
        }
        story.append(Paragraph(f"报告类型：{labels.get(kind, kind)}", body))
        source_ids = ", ".join(str(x) for x in report_data.get("source_run_ids") or [])
        story.append(Paragraph(f"来源 Run：{source_ids or '-'}", small))
        story.append(Spacer(1, 5 * mm))

        def add_table(title_text: str, headers: Sequence[str], records: Iterable[Sequence[Any]], widths=None):
            story.append(Paragraph(title_text, h2))
            table_rows = [[Paragraph(str(h), small) for h in headers]]
            for record in records:
                table_rows.append([Paragraph(_scalar(v) or "-", small) for v in record])
            table = Table(table_rows, colWidths=widths, repeatRows=1, hAlign="LEFT")
            table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8F0F5")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#17364B")),
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#B7C8D4")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]))
            story.append(table)
            story.append(Spacer(1, 3 * mm))

        if kind == "single_run":
            run = data.get("run") or {}
            situation = data.get("situation") or {}
            metrics = data.get("metrics") or {}
            summary = metrics.get("summary") or {} if isinstance(metrics, Mapping) else {}
            add_table("运行概况", ["项目", "值"], [
                ("Run ID", run.get("run_id")),
                ("情境", situation.get("name") or situation.get("situation_id")),
                ("运行状态", run.get("status")),
                ("开始时间", run.get("started_at")),
                ("完成时间", run.get("finished_at")),
                ("计划出动架次", summary.get("scheduled_sorties_total")),
                ("参与机场数", summary.get("participating_airport_count")),
                ("出动高峰时间窗", summary.get("peak_window")),
                ("峰值出动架次", summary.get("peak_sorties")),
            ], widths=[48 * mm, 150 * mm])
            airports = metrics.get("airports") or {} if isinstance(metrics, Mapping) else {}
            add_table("全机场承接", ["机场", "出动", "返航", "累计承接占比"], [
                (aid, row.get("departures_total"), row.get("returns_total"), row.get("departure_share"))
                for aid, row in airports.items() if isinstance(row, Mapping)
            ], widths=[55 * mm, 35 * mm, 35 * mm, 45 * mm])
            tasks = metrics.get("tasks") or {} if isinstance(metrics, Mapping) else {}
            add_table("任务调度结构", ["任务", "需求架次", "调度架次"], [
                (mid, row.get("required_total"), row.get("scheduled_total"))
                for mid, row in tasks.items() if isinstance(row, Mapping)
            ], widths=[65 * mm, 45 * mm, 45 * mm])
            aircraft = metrics.get("aircraft") or {} if isinstance(metrics, Mapping) else {}
            add_table("机型投入结构", ["机型", "调度架次"], [
                (fid, row.get("scheduled_total")) for fid, row in aircraft.items() if isinstance(row, Mapping)
            ], widths=[70 * mm, 45 * mm])
        else:
            roles = data.get("roles") or data.get("runs") or {}
            if isinstance(roles, Mapping):
                add_table("比较对象", ["角色/标签", "Run ID"], roles.items(), widths=[70 * mm, 100 * mm])
            overview = data.get("difference_overview") or data.get("comparison_summary") or {}
            if isinstance(overview, Mapping):
                records = []
                for metric, value in overview.items():
                    if isinstance(value, Mapping):
                        for series, scalar in value.items():
                            if not isinstance(scalar, (Mapping, list, tuple)):
                                records.append((metric, series, scalar))
                    else:
                        records.append((metric, "", value))
                add_table("结果差异概览", ["指标", "角色/差值", "值"], records, widths=[80 * mm, 60 * mm, 55 * mm])
            airports = data.get("airports") or {}
            if isinstance(airports, Mapping):
                records = []
                for aid, payload in airports.items():
                    if not isinstance(payload, Mapping):
                        continue
                    dep = payload.get("departures_total") or {}
                    if isinstance(dep, Mapping):
                        for series in ("R0", "R1", "R2", "damage_delta", "cluster_delta"):
                            if series in dep:
                                records.append((aid, series, dep.get(series)))
                    else:
                        records.append((aid, "", dep))
                add_table("全机场承接比较", ["机场", "角色/差值", "出动架次"], records, widths=[60 * mm, 60 * mm, 50 * mm])

        doc.build(story)
        suffix = "_".join(str(x) for x in (report_data.get("source_run_ids") or [])[:3]) or "results"
        return RenderedExport(buffer.getvalue(), "application/pdf", f"airport_run_report_{suffix}.pdf")

    def render(self, report_data: Mapping[str, Any], fmt: str) -> RenderedExport:
        normalized = str(fmt or "").strip().lower()
        if normalized == "pdf":
            return self.render_pdf(report_data)
        if normalized == "csv":
            return self.render_csv(report_data)
        raise ResultExportError("format must be pdf or csv")


__all__ = [
    "ResultExportService",
    "ResultExportError",
    "RenderedExport",
    "build_tidy_rows",
    "CSV_COLUMNS",
]
