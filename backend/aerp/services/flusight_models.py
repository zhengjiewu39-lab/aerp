from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path

import numpy as np
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pandas as pd

from aerp.config import ROOT
from aerp.core.hashing import canonical_hash, sha256_bytes
from aerp.core.scoring import score_forecast
from aerp.core.timeutil import utcnow
from aerp.core.vintages import mature_value

FLUSIGHT_REPO = "https://github.com/cdcepi/FluSight-forecast-hub"
LICENSE_NOTE = (
    "外部 FluSight 提交仍属于各建模队与 Hub。用于对照前请遵守 Hub README 与归属要求，"
    "本项目不改许可证，也不把他人提交说成自己的模型。"
)
RAW_DIR = ROOT / "data" / "raw" / "flusight_models"
DEFAULT_MODELS = ("FluSight-baseline", "FluSight-ensemble")
HUBS = (
    "cdcepi/FluSight-forecast-hub",
    "cdcepi/Flusight-forecast-hub-2024-2025",
    "cdcepi/Flusight-forecast-hub",
)
QUANTILE_LEVELS = (0.025, 0.1, 0.25, 0.5, 0.75, 0.9, 0.975)


def _download(url: str, timeout: int = 60) -> tuple[int, bytes]:
    request = Request(url, headers={"User-Agent": "aerp-research/0.2"})
    try:
        with urlopen(request, timeout=timeout) as response:
            return int(response.status), response.read()
    except HTTPError as exc:
        return int(exc.code), exc.read() if exc.fp else b""


def _raw_url(hub: str, model_id: str, filename: str) -> str:
    return f"https://raw.githubusercontent.com/{hub}/main/model-output/{model_id}/{filename}"


def parse_hubverse_forecast(payload: bytes, *, model_id: str) -> dict:
    frame = pd.read_csv(BytesIO(payload))
    columns = {item.lower(): item for item in frame.columns}
    required = ("location", "target_end_date", "output_type", "output_type_id", "value")
    missing = [name for name in required if name not in columns]
    if missing:
        return {
            "status": "error",
            "message": f"不是可识别的 hubverse 预测表，缺列 {missing}：{list(frame.columns)}",
            "rows": pd.DataFrame(),
        }
    work = pd.DataFrame(
        {
            "model_id": model_id,
            "location": frame[columns["location"]].astype(str),
            "target_end_date": pd.to_datetime(frame[columns["target_end_date"]]),
            "output_type": frame[columns["output_type"]].astype(str).str.lower(),
            "output_type_id": frame[columns["output_type_id"]],
            "value": pd.to_numeric(frame[columns["value"]], errors="coerce"),
        }
    )
    if "horizon" in columns:
        work["horizon"] = pd.to_numeric(frame[columns["horizon"]], errors="coerce")
    elif "horizon" in frame.columns:
        work["horizon"] = pd.to_numeric(frame["horizon"], errors="coerce")
    else:
        work["horizon"] = pd.NA
    loc = work["location"]
    work["region_id"] = loc.where(~loc.str.fullmatch(r"\d+"), loc.str.zfill(2))
    if "target" in columns:
        keep = frame[columns["target"]].astype(str).str.contains("flu hosp", case=False, na=False)
        work = work.loc[keep.values]
    return {
        "status": "success",
        "n_rows": int(len(work)),
        "n_quantile": int((work["output_type"] == "quantile").sum()),
        "n_sample": int((work["output_type"] == "sample").sum()),
        "rows": work,
        "license_note": LICENSE_NOTE,
    }


def quantile_forecasts(parsed: dict) -> pd.DataFrame:
    if parsed.get("status") != "success":
        return pd.DataFrame()
    work = parsed["rows"]
    quant = work[work["output_type"] == "quantile"].copy()
    if quant.empty:
        return pd.DataFrame()
    quant["qlevel"] = pd.to_numeric(quant["output_type_id"], errors="coerce")
    rows = []
    grouped = quant.groupby(["model_id", "region_id", "target_end_date"], dropna=False)
    for (model_id, region_id, target_end), group in grouped:
        table = {float(level): float(value) for level, value in zip(group["qlevel"], group["value"]) if pd.notna(level)}
        if not table:
            continue
        horizon = group["horizon"].dropna()
        rows.append(
            {
                "model_id": model_id,
                "region_id": str(region_id),
                "target_period_end": pd.Timestamp(target_end),
                "horizon": None if horizon.empty else int(horizon.iloc[0]),
                "quantiles": table,
                "q025": table.get(0.025, table.get(min(table))),
                "q10": table.get(0.1),
                "q25": table.get(0.25),
                "q50": table.get(0.5, table.get(0.50)),
                "q75": table.get(0.75),
                "q90": table.get(0.9),
                "q975": table.get(0.975, table.get(max(table))),
            }
        )
    return pd.DataFrame(rows)


def sample_tables(parsed: dict) -> dict[tuple, dict]:
    if parsed.get("status") != "success":
        return {}
    work = parsed["rows"]
    samples = work[work["output_type"] == "sample"].copy()
    if samples.empty:
        return {}
    from aerp.core.trajectories import paths_from_sample_table

    samples["sample_id"] = samples["output_type_id"].astype(str)
    out = {}
    for (model_id, region_id), group in samples.groupby(["model_id", "region_id"]):
        if group["horizon"].notna().all():
            packed = paths_from_sample_table(group[["horizon", "sample_id", "value"]])
        else:
            packed = {"status": "error", "message": "样本缺少 horizon，无法按预测时长对齐轨迹。", "paths": np.zeros((0, 0)), "horizons": []}
        out[(str(model_id), str(region_id))] = packed
    return out


def list_hub_reference_dates(model_id: str, *, timeout: int = 45) -> dict:
    url = f"https://api.github.com/repos/cdcepi/FluSight-forecast-hub/contents/model-output/{model_id}"
    try:
        status, body = _download(url, timeout=timeout)
    except (URLError, TimeoutError, OSError, ValueError) as exc:
        return {"status": "error", "message": str(exc), "dates": []}
    if status != 200:
        return {"status": "error", "message": f"GitHub API HTTP {status}", "dates": []}
    try:
        listing = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        return {"status": "error", "message": str(exc), "dates": []}
    if not isinstance(listing, list):
        return {"status": "error", "message": "GitHub API 未返回目录列表。", "dates": []}
    dates = []
    for item in listing:
        name = str(item.get("name") or "")
        if not name.endswith(".csv"):
            continue
        if name.endswith(f"-{model_id}.csv"):
            dates.append(name[: len("YYYY-MM-DD")])
    dates = sorted(set(dates))
    return {"status": "success", "model_id": model_id, "n": len(dates), "dates": dates, "source_url": url}


def fetch_hub_forecast(
    *,
    model_id: str,
    reference_date: str,
    cache_dir: Path | None = None,
    payload: bytes | None = None,
    filename: str | None = None,
) -> dict:
    cache_dir = cache_dir or RAW_DIR
    cache_dir.mkdir(parents=True, exist_ok=True)
    name = filename or f"{reference_date}-{model_id}.csv"
    errors = []
    source_url = None
    if payload is None:
        for hub in HUBS:
            url = _raw_url(hub, model_id, name)
            try:
                status, body = _download(url)
                if status == 200 and len(body) > 80 and not body.lstrip().startswith(b"404"):
                    payload = body
                    source_url = url
                    break
                errors.append(f"{url} HTTP {status}")
            except (URLError, TimeoutError, OSError, ValueError) as exc:
                errors.append(f"{url}: {exc}")
        if payload is None:
            return {
                "status": "error",
                "message": f"未找到 {model_id} @ {reference_date}。{' | '.join(errors)}。不把缺失提交记为已对照。",
                "model_id": model_id,
                "license_note": LICENSE_NOTE,
            }
    checksum = sha256_bytes(payload)
    path = cache_dir / f"{model_id}-{reference_date}-{checksum[:10]}.csv"
    if not path.exists():
        path.write_bytes(payload)
    parsed = parse_hubverse_forecast(payload, model_id=model_id)
    parsed["provenance"] = {
        "source_url": source_url or "payload",
        "model_id": model_id,
        "reference_date": reference_date,
        "sha256": checksum,
        "downloaded_at": utcnow().isoformat(sep=" ", timespec="seconds"),
        "raw_path": str(path),
        "license_note": LICENSE_NOTE,
        "fetch_notes": errors,
    }
    parsed["input_hash"] = canonical_hash({"sha256": checksum, "model_id": model_id})
    return parsed


def score_external_against_observations(
    quantile_rows: pd.DataFrame,
    observations: pd.DataFrame,
    *,
    as_of,
    maturity_lag_days: int = 28,
    horizons: tuple[int, ...] = (1, 2, 3, 4),
) -> dict:
    if quantile_rows is None or quantile_rows.empty:
        return {"status": "incomplete", "message": "没有外部分位数预测可评分。", "scores": None}
    keep_horizons = {int(h) for h in horizons}
    rows = []
    skipped_other_horizons = 0
    for item in quantile_rows.to_dict(orient="records"):
        q50 = item.get("q50")
        q025, q10, q25, q75, q90, q975 = (
            item.get("q025"),
            item.get("q10"),
            item.get("q25"),
            item.get("q75"),
            item.get("q90"),
            item.get("q975"),
        )
        horizon = item.get("horizon")
        if horizon is not None and not (isinstance(horizon, float) and pd.isna(horizon)):
            if int(horizon) not in keep_horizons:
                skipped_other_horizons += 1
                continue
        if any(value is None or (isinstance(value, float) and pd.isna(value)) for value in (q50, q025, q10, q25, q75, q90, q975)):
            continue
        actual = mature_value(
            observations,
            region_id=item["region_id"],
            metric="hospital_admissions",
            period_end=item["target_period_end"],
            as_of=as_of,
            maturity_lag_days=maturity_lag_days,
            replay_mode="vintage",
        )
        if actual is None:
            continue
        actual_value = float(actual[0])
        scored = score_forecast(
            actual_value,
            float(q50),
            float(q025),
            float(q10),
            float(q25),
            float(q75),
            float(q90),
            float(q975),
        )
        rows.append(
            {
                "model_id": item["model_id"],
                "region_id": item["region_id"],
                "target_period_end": pd.Timestamp(item["target_period_end"]).date().isoformat(),
                "horizon": item.get("horizon"),
                "wis": scored.wis,
                "abs_error": scored.abs_error,
                "covered_80": scored.covered_80,
                "actual_value": actual_value,
            }
        )
    if not rows:
        return {
            "status": "incomplete",
            "message": "外部预测与成熟观测没有可对齐的目标周，不写对照成绩。",
            "scores": None,
            "license_note": LICENSE_NOTE,
        }
    frame = pd.DataFrame(rows)
    summary = (
        frame.groupby("model_id")
        .agg(n=("wis", "size"), wis=("wis", "mean"), mae=("abs_error", "mean"), coverage_80=("covered_80", "mean"))
        .reset_index()
        .to_dict(orient="records")
    )
    return {
        "status": "success",
        "n_scored": int(len(frame)),
        "by_model": summary,
        "rows": rows[:400],
        "license_note": LICENSE_NOTE,
        "skipped_other_horizons": skipped_other_horizons,
        "kept_horizons": sorted(keep_horizons),
        "note": (
            "按 location 与 target_end_date 对齐。默认只评 FluSight horizon 1–4，排除 nowcast（horizon -1/0）。"
            "对照样本通常小于本系统全原点评分，不能直接当作同一任务集上的胜负。"
        ),
    }


def fetch_and_score_baselines(
    *,
    reference_dates: list[str],
    observations: pd.DataFrame,
    as_of,
    models: tuple[str, ...] | list[str] = DEFAULT_MODELS,
    cache_dir: Path | None = None,
    payloads: dict[tuple[str, str], bytes] | None = None,
    maturity_lag_days: int = 28,
) -> dict:
    parsed_rows = []
    errors = []
    for model_id in models:
        for reference_date in reference_dates:
            payload = None if payloads is None else payloads.get((model_id, reference_date))
            fetched = fetch_hub_forecast(
                model_id=model_id,
                reference_date=reference_date,
                cache_dir=cache_dir,
                payload=payload,
            )
            if fetched.get("status") != "success":
                errors.append(fetched.get("message") or f"{model_id} {reference_date} 失败")
                continue
            parsed_rows.append(quantile_forecasts(fetched))
    if not parsed_rows:
        return {
            "status": "incomplete",
            "message": "未接入任何外部 FluSight 提交。" + (" " + " | ".join(errors[:8]) if errors else ""),
            "scores": None,
            "errors": errors[:20],
            "license_note": LICENSE_NOTE,
        }
    quantiles = pd.concat(parsed_rows, ignore_index=True)
    scored = score_external_against_observations(
        quantiles, observations, as_of=as_of, maturity_lag_days=maturity_lag_days
    )
    scored["errors"] = errors
    scored["n_files"] = len(parsed_rows)
    return scored


def paired_common_tasks(internal_rows: list[dict], external_rows: list[dict]) -> dict:
    """只在共同地区、目标周、时长上配对。日期对不上时不拿其他日期替代。"""
    def key(row: dict) -> tuple:
        return (
            str(row.get("region_id")),
            str(row.get("target_period_end"))[:10],
            int(row.get("horizon") or 0),
        )

    left = {key(row): row for row in internal_rows if row.get("horizon") not in (None, "")}
    right = {key(row): row for row in external_rows if row.get("horizon") not in (None, "")}
    common = sorted(set(left) & set(right))
    if not common:
        return {
            "status": "incomplete",
            "message": "没有共同任务可配对。不使用邻近日期替代成绩。",
            "n_internal": len(left),
            "n_external": len(right),
            "n_common": 0,
        }
    internal_wis = np.array([float(left[item]["wis"]) for item in common], dtype=float)
    external_wis = np.array([float(right[item]["wis"]) for item in common], dtype=float)
    return {
        "status": "success",
        "n_common": len(common),
        "n_internal": len(left),
        "n_external": len(right),
        "internal_wis": float(np.mean(internal_wis)),
        "external_wis": float(np.mean(external_wis)),
        "mean_diff_internal_minus_external": float(np.mean(internal_wis - external_wis)),
        "note": "仅共同任务。样本数不同时不得把外部全季平均拿来与本系统全原点比较。",
    }
