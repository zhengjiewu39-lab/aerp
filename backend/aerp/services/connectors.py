from __future__ import annotations

CONNECTORS = [
    {
        "id": "csv_import",
        "name": "CSV 导入",
        "status": "available",
        "note": "本地文件导入。每次导入保存文件哈希，不覆盖旧版本。",
    },
    {
        "id": "synthetic_demo",
        "name": "合成演示数据",
        "status": "available",
        "note": "固定随机种子生成。页面持续标注为合成演示数据。",
    },
    {
        "id": "delphi_epidata",
        "name": "Delphi Epidata",
        "status": "unverified",
        "url": "https://github.com/cmu-delphi/delphi-epidata",
        "note": "默认尚未验证。运行 python -m aerp.research verify-delphi 后，仅当探测路径真正返回数据才改为可用。",
    },
    {
        "id": "flusight",
        "name": "FluSight Forecast Hub",
        "status": "available",
        "url": "https://github.com/cdcepi/FluSight-forecast-hub",
        "note": (
            "snapshot 没有 as_of，只能做最终数据时间切分。"
            "vintage 使用 time-series.csv 的 as_of，并按 2025-07-05 前后语义转换发布日。"
            "外部 FluSight 提交可在许可允许时作为对照，不并入本系统模型臂。"
            "下载失败时返回明确错误，不伪造成功。"
        ),
    },
    {
        "id": "epinowcast",
        "name": "epinowcast",
        "status": "unconfigured",
        "url": "https://github.com/epinowcast/epinowcast",
        "note": "研究扩展接口预留。第一版未配置。",
    },
    {
        "id": "cape",
        "name": "CAPE",
        "status": "unconfigured",
        "url": "https://github.com/Emory-Melody/CAPE",
        "note": "研究扩展接口预留。第一版未配置。",
    },
    {
        "id": "delphi_rf",
        "name": "Delphi-RF",
        "status": "unconfigured",
        "url": "https://github.com/cmu-delphi/DelphiRF",
        "note": "研究扩展接口预留。第一版未配置。",
    },
]

STATUS_LABELS = {
    "available": "可用",
    "unconfigured": "未配置",
    "unverified": "尚未验证",
}


def list_connectors() -> list[dict]:
    from aerp.services.delphi import connector_status as delphi_status

    items = []
    for item in CONNECTORS:
        row = dict(item)
        if row["id"] == "delphi_epidata":
            row.update(delphi_status())
        row["status_label"] = STATUS_LABELS.get(row["status"], row["status"])
        items.append(row)
    return items
