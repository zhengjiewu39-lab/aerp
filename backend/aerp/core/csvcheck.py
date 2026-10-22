from __future__ import annotations

import csv
import io
from datetime import datetime

from aerp.core.hashing import sha256_bytes

REQUIRED_COLUMNS = [
    "disease_id",
    "region_id",
    "period_start",
    "period_end",
    "issued_at",
    "metric",
    "value",
    "unit",
    "source_id",
    "quality_flag",
]

METRICS = {
    "hospital_admissions": "count",
    "reported_cases": "count",
    "tests": "count",
    "positivity": "proportion",
    "wastewater_index": "index",
    "mobility_index": "index",
    "vaccination_coverage": "proportion",
    "prior_infection_estimate": "proportion",
    "school_in_session": "proportion",
    "public_health_measure_index": "index",
    "variant_share": "proportion",
}

QUALITY_ALIASES = {
    "complete": "complete",
    "provisional": "provisional",
    "missing": "missing",
    "anomalous": "anomalous",
    "source_unknown": "source_unknown",
    "完整": "complete",
    "暂定": "provisional",
    "缺失": "missing",
    "异常": "anomalous",
    "来源未知": "source_unknown",
}

UNIT_ALIASES = {
    "count": "count",
    "人数": "count",
    "例": "count",
    "proportion": "proportion",
    "比例": "proportion",
    "index": "index",
    "指数": "index",
}

QUALITY_LABELS = {
    "complete": "完整",
    "provisional": "暂定",
    "missing": "缺失",
    "anomalous": "异常",
    "source_unknown": "来源未知",
}


def _parse_date(text: str, field: str):
    value = (text or "").strip()
    if not value:
        return None, f"{field} 为空"
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            parsed = datetime.strptime(value[:19] if "T" in fmt or "%H" in fmt else value[:10], fmt)
            return parsed, None
        except ValueError:
            continue
    return None, f"{field} 不是可识别的日期"


def validate_csv_bytes(payload: bytes) -> dict:
    file_hash = sha256_bytes(payload)
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError:
        return {
            "file_hash": file_hash,
            "columns_ok": False,
            "errors": [{"row": 1, "column": "", "message": "文件不是 UTF-8 编码"}],
            "warnings": [],
            "preview": [],
            "valid_rows": [],
            "row_count": 0,
        }
    reader = csv.DictReader(io.StringIO(text))
    fieldnames = reader.fieldnames or []
    missing = [column for column in REQUIRED_COLUMNS if column not in fieldnames]
    errors = []
    warnings = []
    preview = []
    valid_rows = []
    seen = set()
    if missing:
        errors.append({"row": 1, "column": ",".join(missing), "message": "缺少必要字段：" + "、".join(missing)})
        return {
            "file_hash": file_hash,
            "columns_ok": False,
            "errors": errors,
            "warnings": warnings,
            "preview": [],
            "valid_rows": [],
            "row_count": 0,
        }
    for offset, raw in enumerate(reader, start=2):
        row_errors = []
        row_warnings = []
        parsed = {column: (raw.get(column) or "").strip() for column in REQUIRED_COLUMNS}
        for column in ("disease_id", "region_id", "metric", "source_id"):
            if not parsed[column]:
                row_errors.append({"row": offset, "column": column, "message": f"{column} 为空"})
        metric = parsed["metric"]
        if metric and metric not in METRICS:
            row_errors.append({"row": offset, "column": "metric", "message": f"不支持的指标 {metric}"})
        quality = QUALITY_ALIASES.get(parsed["quality_flag"])
        if parsed["quality_flag"] and quality is None:
            row_errors.append({"row": offset, "column": "quality_flag", "message": "质量标记必须是完整、暂定、缺失、异常或来源未知"})
        if not parsed["quality_flag"]:
            row_errors.append({"row": offset, "column": "quality_flag", "message": "质量标记为空"})
        unit = UNIT_ALIASES.get(parsed["unit"])
        if parsed["unit"] and unit is None:
            row_errors.append({"row": offset, "column": "unit", "message": "单位必须是 count、proportion 或 index"})
        if metric in METRICS and unit and unit != METRICS[metric]:
            row_errors.append({"row": offset, "column": "unit", "message": f"{metric} 不能与单位 {unit} 混合"})
        period_start, start_error = _parse_date(parsed["period_start"], "period_start")
        period_end, end_error = _parse_date(parsed["period_end"], "period_end")
        issued_at, issued_error = _parse_date(parsed["issued_at"], "issued_at")
        for message, column in ((start_error, "period_start"), (end_error, "period_end"), (issued_error, "issued_at")):
            if message:
                row_errors.append({"row": offset, "column": column, "message": message})
        if period_start and period_end and period_end < period_start:
            row_errors.append({"row": offset, "column": "period_end", "message": "周期结束早于周期开始"})
        if period_start and period_end and (period_end - period_start).days != 6:
            row_warnings.append({"row": offset, "column": "period_end", "message": "周期不是 7 天。第一版预测不混合不同时间粒度，也不会自动汇总。"})
        if issued_at and period_start and issued_at < period_start:
            row_errors.append({"row": offset, "column": "issued_at", "message": "发布时间早于周期开始"})
        value_text = parsed["value"]
        value = None
        if value_text == "":
            value = None
        else:
            try:
                value = float(value_text)
            except ValueError:
                row_errors.append({"row": offset, "column": "value", "message": "数值不是数字。空值应留空，不能写成文字。"})
        if value is None and quality and quality != "missing":
            row_errors.append({"row": offset, "column": "value", "message": "数值为空时，质量标记必须为缺失。空值不会被保存为 0。"})
        if value is not None and quality == "missing":
            row_errors.append({"row": offset, "column": "quality_flag", "message": "已有数值时，质量标记不能是缺失"})
        if value is not None and metric in METRICS and METRICS[metric] == "count" and value < 0:
            row_errors.append({"row": offset, "column": "value", "message": "计数值不能为负"})
        if value is not None and metric in {"positivity", "vaccination_coverage", "prior_infection_estimate", "variant_share", "school_in_session"} and not 0 <= value <= 1:
            row_errors.append({"row": offset, "column": "value", "message": f"{metric} 必须在 0 到 1 之间"})
        if metric == "vaccination_coverage":
            row_warnings.append({"row": offset, "column": "metric", "message": "疫苗覆盖率不能直接等同于免疫人口比例。"})
        if value is not None and value > 1_000_000:
            row_warnings.append({"row": offset, "column": "value", "message": "数值大于 100 万，请核对是否串列或单位错误。"})
        if metric == "wastewater_index":
            row_warnings.append({"row": offset, "column": "metric", "message": "不同污水采样点的原始数值不能直接合并比较。导入后会按 source_id 分开保存。"})
        key = (parsed["disease_id"], parsed["region_id"], metric, parsed["period_start"], parsed["issued_at"], parsed["source_id"])
        if all(key) and key in seen:
            row_errors.append({"row": offset, "column": "issued_at", "message": "文件内存在相同地区、指标、周期、来源和发布时间的重复记录"})
        elif all(key):
            seen.add(key)
        if row_errors:
            errors.extend(row_errors)
        else:
            warnings.extend(row_warnings)
            item = {
                "disease_id": parsed["disease_id"],
                "region_id": parsed["region_id"],
                "period_start": period_start.date().isoformat(),
                "period_end": period_end.date().isoformat(),
                "issued_at": issued_at.isoformat(sep=" ", timespec="seconds"),
                "metric": metric,
                "value": value,
                "unit": unit,
                "source_id": parsed["source_id"],
                "quality_flag": quality,
            }
            valid_rows.append(item)
        if offset <= 8:
            preview.append(
                {
                    "row": offset,
                    "data": {**parsed, "value": value_text},
                    "errors": row_errors,
                    "warnings": row_warnings,
                }
            )
    return {
        "file_hash": file_hash,
        "columns_ok": True,
        "errors": errors,
        "warnings": warnings,
        "preview": preview,
        "valid_rows": valid_rows if not errors else [],
        "accepted_row_count": 0 if errors else len(valid_rows),
        "error_row_count": len({item["row"] for item in errors}),
        "row_count": len(valid_rows) + len({item["row"] for item in errors}),
    }
