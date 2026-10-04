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
        "status": "unconfigured",
        "url": "https://github.com/cmu-delphi/delphi-epidata",
        "note": "尚未验证。接入前需核查许可、覆盖范围、发布时间与运行条件。",
    },
    {
        "id": "flusight",
        "name": "FluSight Forecast Hub",
        "status": "unverified",
        "url": "https://github.com/cdcepi/FluSight-forecast-hub",
        "note": "尚未验证。不可默认与其他资源无条件混用。",
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
    items = []
    for item in CONNECTORS:
        row = dict(item)
        row["status_label"] = STATUS_LABELS.get(row["status"], row["status"])
        items.append(row)
    return items
