from __future__ import annotations

import argparse
import json
from pathlib import Path

from aerp.core.alerts_eval import EVENT_DEFINITION, development_thresholds
from aerp.core.change_labels import diagnosis_report
from aerp.core.hashing import hash_observations
from aerp.core.research_mode import RESEARCH_QUESTION
from aerp.research.archive import ARCHIVE_NOTE, archive_forecast, attach_external_proof, verify_chain
from aerp.research.replay import run_vintage_replay
from aerp.research.runs import incomplete_artifacts, start_run, write_json, write_text
from aerp.research.splits import infer_splits
from aerp.research.nhsn_replay import run_nhsn_replay
from aerp.research.transfer_run import generate_paired_respiratory, run_transfer_comparison
from aerp.services.delphi import fetch_nhsn_weekly, verify_delphi
from aerp.services.dengue import check_dengue_sources
from aerp.services.flusight import DEFAULT_RESEARCH_LOCATIONS, fetch_flusight_target, observations_from_flusight
from aerp.services.flusight_models import fetch_and_score_baselines, fetch_hub_forecast, quantile_forecasts


def _print(payload: dict) -> int:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    status = payload.get("status")
    return 0 if status in {None, "success", "saved", "exists", "incomplete", "degraded", "verified", "ok", "local_only", "skipped", "completed"} else 1


def cmd_fetch(args: argparse.Namespace) -> int:
    parsed = fetch_flusight_target(kind=args.kind, cache_dir=Path(args.cache_dir) if args.cache_dir else None)
    if parsed.get("status") == "success":
        rows = observations_from_flusight(parsed)
        parsed = dict(parsed)
        parsed["n_rows"] = int(len(rows))
        parsed["rows"] = "omitted_from_cli_stdout"
    return _print(parsed)


def cmd_inventory(args: argparse.Namespace) -> int:
    parsed = fetch_flusight_target(kind=args.kind, payload=Path(args.file).read_bytes() if args.file else None)
    if parsed.get("status") != "success":
        return _print(parsed)
    rows = observations_from_flusight(parsed)
    coverage = (
        rows.groupby("region_id")
        .agg(n=("value", "size"), start=("period_end", "min"), end=("period_end", "max"), missing=("value", lambda s: int(s.isna().sum())))
        .reset_index()
    )
    payload = {
        "status": "success",
        "n_rows": int(len(rows)),
        "n_regions": int(rows["region_id"].nunique()),
        "replay_mode": parsed.get("replay_mode"),
        "dictionary": parsed.get("dictionary"),
        "logs": parsed.get("logs"),
        "n_as_of_after_break": parsed.get("n_as_of_after_break"),
        "coverage": coverage.to_dict(orient="records"),
        "input_hash": hash_observations(rows),
    }
    if args.run_dir:
        write_json(Path(args.run_dir), "inventory.json", payload)
    return _print({**payload, "coverage": payload["coverage"][:12], "coverage_truncated": len(payload["coverage"]) > 12})


def cmd_splits(args: argparse.Namespace) -> int:
    parsed = fetch_flusight_target(kind=args.kind, payload=Path(args.file).read_bytes() if args.file else None)
    if parsed.get("status") != "success":
        return _print(parsed)
    result = infer_splits(observations_from_flusight(parsed))
    return _print(result)


def cmd_replay(args: argparse.Namespace) -> int:
    if getattr(args, "run_experiment", False):
        result = run_vintage_replay(
            kind=args.kind,
            file=Path(args.file) if args.file else None,
            locations=args.locations.split(",") if getattr(args, "locations", None) else None,
            seed=args.seed,
            step_weeks=getattr(args, "step_weeks", 2),
            min_train_weeks=getattr(args, "min_train_weeks", 26),
            max_origins=getattr(args, "max_origins", None),
            include_baselines=not getattr(args, "no_baselines", False),
        )
        return _print({k: v for k, v in result.items() if k != "result"} | {"result": result.get("result")})
    if args.file:
        parsed = fetch_flusight_target(kind=args.kind, payload=Path(args.file).read_bytes())
    else:
        parsed = fetch_flusight_target(kind=args.kind)
    if parsed.get("status") != "success":
        run = start_run({"command": "replay", "kind": args.kind, "mode": "research"}, input_hash="unavailable", seed=args.seed)
        write_json(Path(run["directory"]), "result.json", incomplete_artifacts(parsed.get("message") or "数据不可用"))
        return _print({**parsed, "run": run})
    rows = observations_from_flusight(parsed)
    splits = infer_splits(rows)
    config = {
        "command": "replay",
        "mode": "research",
        "kind": args.kind,
        "disease_id": "influenza",
        "research_question": RESEARCH_QUESTION,
        "replay_mode": parsed.get("replay_mode"),
        "splits": splits.get("splits"),
        "allow_demo_analogs": False,
    }
    run = start_run(config, input_hash=hash_observations(rows), seed=args.seed)
    write_json(Path(run["directory"]), "inventory.json", {"n_rows": int(len(rows)), "n_regions": int(rows["region_id"].nunique())})
    write_json(Path(run["directory"]), "splits.json", splits)
    write_json(Path(run["directory"]), "provenance.json", parsed.get("provenance") or {})
    write_text(
        Path(run["directory"]),
        "README.md",
        "\n".join(
            [
                f"# research run {run['run_id']}",
                "",
                RESEARCH_QUESTION,
                "",
                "本目录保存配置、数据哈希和划分。完整对照回放请加 --run-experiment。",
                "没有完成评分时不写论文结论。",
            ]
        ),
    )
    return _print({"status": "success", "run": run, "splits": splits, "message": "已创建研究运行目录。完整评分需 --run-experiment。"})


def cmd_figures(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir)
    scores = run_dir / "scores.json"
    if not scores.exists():
        payload = incomplete_artifacts("没有 scores.json，不生成虚构成绩图。")
        write_json(run_dir, "figures.json", payload)
        return _print(payload)
    data = json.loads(scores.read_text(encoding="utf-8"))
    write_json(
        run_dir,
        "figures.json",
        {
            "status": "success",
            "tables": list(data.keys()) if isinstance(data, dict) else ["scores"],
            "event_definition": EVENT_DEFINITION,
        },
    )
    return _print({"status": "success", "path": str(run_dir / "figures.json")})


def cmd_archive(args: argparse.Namespace) -> int:
    payload = json.loads(Path(args.file).read_text(encoding="utf-8")) if args.file else {"cutoff_time": args.cutoff, "forecasts": []}
    result = archive_forecast(payload)
    result["external_timestamp_note"] = ARCHIVE_NOTE
    return _print(result)


def cmd_archive_verify(args: argparse.Namespace) -> int:
    return _print(verify_chain(Path(args.archive_dir) if args.archive_dir else None))


def cmd_archive_attach(args: argparse.Namespace) -> int:
    return _print(attach_external_proof(Path(args.stamp_request), Path(args.proof)))


def cmd_thresholds(args: argparse.Namespace) -> int:
    parsed = fetch_flusight_target(kind=args.kind, payload=Path(args.file).read_bytes() if args.file else None)
    if parsed.get("status") != "success":
        return _print(parsed)
    rows = observations_from_flusight(parsed)
    splits = infer_splits(rows)
    dev = splits.get("splits", {}).get("development")
    if not dev:
        return _print({"status": "error", "message": "没有开发集，不能按测试结果选择预警阈值。"})
    subset = rows[(rows["period_end"] >= dev["start"]) & (rows["period_end"] <= dev["end"])]
    thresholds = development_thresholds(subset, freeze_time=dev["end"])
    return _print(
        {
            "status": "success",
            "thresholds": thresholds,
            "source": "development_quantile_0.90",
            "event_definition": EVENT_DEFINITION,
            "note": "阈值只在开发集确定。没有床位数据，只称住院高负担预警。",
        }
    )


def cmd_verify_delphi(args: argparse.Namespace) -> int:
    return _print(verify_delphi(cache_dir=Path(args.cache_dir) if args.cache_dir else None))


def cmd_baselines(args: argparse.Namespace) -> int:
    if args.file:
        parsed = fetch_hub_forecast(model_id=args.model, reference_date=args.reference_date, payload=Path(args.file).read_bytes())
        if parsed.get("status") != "success":
            return _print(parsed)
        rows = quantile_forecasts(parsed)
        return _print({"status": "success", "n_quantile_forecasts": int(len(rows)), "model_id": args.model, "rows": "omitted_from_cli_stdout"})
    result = fetch_and_score_baselines(
        reference_dates=[args.reference_date],
        observations=__import__("pandas").DataFrame(),
        as_of=args.reference_date,
        models=[args.model],
    )
    return _print(result)


def cmd_fetch_nhsn(args: argparse.Namespace) -> int:
    parsed = fetch_nhsn_weekly(
        args.disease,
        geo_value=args.geo_value,
        time_values=args.time_values,
        cache_dir=Path(args.cache_dir) if args.cache_dir else None,
    )
    if parsed.get("status") == "success":
        parsed = dict(parsed)
        rows = parsed.get("rows")
        parsed["n_rows"] = 0 if rows is None else int(len(rows))
        parsed["rows"] = "omitted_from_cli_stdout"
    return _print(parsed)


def cmd_check_dengue(args: argparse.Namespace) -> int:
    return _print(check_dengue_sources(live=bool(args.live)))


def cmd_nhsn_replay(args: argparse.Namespace) -> int:
    return _print(
        run_nhsn_replay(
            args.disease,
            geo_value=args.geo_value,
            time_values=args.time_values,
            seed=args.seed,
            max_origins=args.max_origins,
        )
    )


def cmd_fetch_who(args: argparse.Namespace) -> int:
    from aerp.db.session import SessionLocal, init_db
    from aerp.services.geo_catalog import ensure_geo_catalog
    from aerp.services.who_covid import fetch_and_import

    init_db()
    db = SessionLocal()
    try:
        ensure_geo_catalog(db)
        return _print(
            fetch_and_import(
                db,
                cache_dir=Path(args.cache_dir) if args.cache_dir else None,
                max_weeks=args.max_weeks,
            )
        )
    finally:
        db.close()


def cmd_transfer_demo(args: argparse.Namespace) -> int:
    del args
    frames = generate_paired_respiratory()
    return _print(
        run_transfer_comparison(
            frames["synth-covid-hosp"],
            {"synth-flu-hosp": frames["synth-flu-hosp"]},
            target_disease_id="synth-covid-hosp",
            region_id="PA",
        )
    )


def cmd_change_labels(args: argparse.Namespace) -> int:
    if not args.file:
        return _print(diagnosis_report([], None, source="none", mode="research"))
    import pandas as pd

    labels = pd.read_csv(args.file)
    return _print(diagnosis_report([], labels, source=args.source, mode="research"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AERP 可复现研究入口")
    sub = parser.add_subparsers(dest="command", required=True)
    fetch = sub.add_parser("fetch", help="获取并缓存 FluSight 目标数据")
    fetch.add_argument("--kind", choices=["snapshot", "vintage"], default="snapshot")
    fetch.add_argument("--cache-dir")
    fetch.set_defaults(func=cmd_fetch)

    inv = sub.add_parser("inventory", help="生成数据清单")
    inv.add_argument("--kind", choices=["snapshot", "vintage"], default="snapshot")
    inv.add_argument("--file")
    inv.add_argument("--run-dir")
    inv.set_defaults(func=cmd_inventory)

    splits = sub.add_parser("splits", help="按可见跨度划分开发/验证/锁定测试")
    splits.add_argument("--kind", choices=["snapshot", "vintage"], default="snapshot")
    splits.add_argument("--file")
    splits.set_defaults(func=cmd_splits)

    replay = sub.add_parser("replay", help="创建研究运行目录；加 --run-experiment 执行完整回放")
    replay.add_argument("--kind", choices=["snapshot", "vintage"], default="snapshot")
    replay.add_argument("--file")
    replay.add_argument("--seed", type=int, default=42)
    replay.add_argument("--run-experiment", action="store_true")
    replay.add_argument("--locations", default=",".join(DEFAULT_RESEARCH_LOCATIONS))
    replay.add_argument("--step-weeks", type=int, default=2)
    replay.add_argument("--min-train-weeks", type=int, default=26)
    replay.add_argument("--max-origins", type=int)
    replay.add_argument("--no-baselines", action="store_true")
    replay.set_defaults(func=cmd_replay)

    figures = sub.add_parser("figures", help="从已有 scores.json 导出图表清单")
    figures.add_argument("--run-dir", required=True)
    figures.set_defaults(func=cmd_figures)

    archive = sub.add_parser("archive", help="追加保存前瞻预测")
    archive.add_argument("--file")
    archive.add_argument("--cutoff")
    archive.set_defaults(func=cmd_archive)

    archive_v = sub.add_parser("archive-verify", help="检查本地前瞻哈希链（不能证明公开发布）")
    archive_v.add_argument("--archive-dir")
    archive_v.set_defaults(func=cmd_archive_verify)

    archive_a = sub.add_parser("archive-attach", help="附加外部时间戳证明文件")
    archive_a.add_argument("--stamp-request", required=True)
    archive_a.add_argument("--proof", required=True)
    archive_a.set_defaults(func=cmd_archive_attach)

    thr = sub.add_parser("thresholds", help="只用开发集估计高负担阈值")
    thr.add_argument("--kind", choices=["snapshot", "vintage"], default="snapshot")
    thr.add_argument("--file")
    thr.set_defaults(func=cmd_thresholds)

    delphi = sub.add_parser("verify-delphi", help="探测 Delphi Epidata 流感住院路径")
    delphi.add_argument("--cache-dir")
    delphi.set_defaults(func=cmd_verify_delphi)

    base = sub.add_parser("baselines", help="读取或下载 FluSight 外部提交")
    base.add_argument("--model", default="FluSight-baseline")
    base.add_argument("--reference-date", default="2024-10-19")
    base.add_argument("--file")
    base.set_defaults(func=cmd_baselines)

    labels = sub.add_parser("change-labels", help="在有人工标签时才计算变化诊断；默认跳过")
    labels.add_argument("--file")
    labels.add_argument("--source", choices=["none", "synthetic", "manual"], default="none")
    labels.set_defaults(func=cmd_change_labels)

    nhsn = sub.add_parser("fetch-nhsn", help="按病种拉取 Delphi NHSN 周住院（流感/COVID/RSV 独立信号）")
    nhsn.add_argument("--disease", choices=["influenza", "covid19", "rsv"], required=True)
    nhsn.add_argument("--geo-value", default="pa")
    nhsn.add_argument("--time-values", default="202240-202520")
    nhsn.add_argument("--cache-dir")
    nhsn.set_defaults(func=cmd_fetch_nhsn)

    dengue = sub.add_parser("check-dengue", help="核查登革热候选来源，不伪造病例")
    dengue.add_argument("--live", action="store_true")
    dengue.set_defaults(func=cmd_check_dengue)

    transfer = sub.add_parser("transfer-demo", help="合成呼吸道住院上比较本地/固定迁移/可退出迁移")
    transfer.set_defaults(func=cmd_transfer_demo)

    nhsn_replay = sub.add_parser("nhsn-replay", help="导入 Delphi NHSN 并跑指定病种的小范围回放")
    nhsn_replay.add_argument("--disease", choices=["influenza", "covid19", "rsv"], required=True)
    nhsn_replay.add_argument("--geo-value", default="pa")
    nhsn_replay.add_argument("--time-values", default="202240-202520")
    nhsn_replay.add_argument("--seed", type=int, default=42)
    nhsn_replay.add_argument("--max-origins", type=int, default=6)
    nhsn_replay.set_defaults(func=cmd_nhsn_replay)

    who = sub.add_parser("fetch-who-covid", help="拉取 WHO COVID-19 国家日报告并聚合为周，失败不填补")
    who.add_argument("--cache-dir")
    who.add_argument("--max-weeks", type=int, default=52)
    who.set_defaults(func=cmd_fetch_who)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))
