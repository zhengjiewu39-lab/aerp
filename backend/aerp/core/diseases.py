"""多病种注册表。状态分四档，不得混为一谈。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

STATUS_HAS_PAGE = "has_page"
STATUS_DEMO_ONLY = "demo_only"
STATUS_REAL_FORECASTABLE = "real_forecastable"
STATUS_VERIFIED = "verified"
STATUS_PENDING = "pending"

TRANSMISSION_RESPIRATORY = "respiratory_person_to_person"
TRANSMISSION_VECTOR = "vector_borne"
TRANSMISSION_OTHER = "other_extension"

TARGET_WEEKLY_CASES = "weekly_cases"
TARGET_WEEKLY_ADMISSIONS = "hospital_admissions"
TARGET_LAB_DETECTIONS = "lab_detections"
TARGET_POSITIVITY = "positivity"
TARGET_THRESHOLD_RISK = "threshold_risk"


@dataclass(frozen=True)
class DiseaseSpec:
    disease_id: str
    name_zh: str
    name_en: str
    pathogen_class: str
    transmission: str
    targets: tuple[str, ...]
    time_grain: str
    regions_note: str
    time_range_note: str
    sources: tuple[dict[str, str], ...]
    license_note: str
    surveillance_definition: str
    surveillance_changes: str
    auxiliaries: tuple[str, ...]
    models: tuple[str, ...]
    uncertainty_limits: str
    status: str
    status_label: str
    trainable: bool
    is_synthetic: bool
    default_target: str = TARGET_WEEKLY_ADMISSIONS
    notes: str = ""
    extras: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["targets"] = list(self.targets)
        payload["sources"] = [dict(item) for item in self.sources]
        payload["auxiliaries"] = list(self.auxiliaries)
        payload["models"] = list(self.models)
        return payload


DISEASE_SPECS: dict[str, DiseaseSpec] = {
    "influenza": DiseaseSpec(
        disease_id="influenza",
        name_zh="流感",
        name_en="Influenza",
        pathogen_class="orthomyxovirus",
        transmission=TRANSMISSION_RESPIRATORY,
        targets=(TARGET_LAB_DETECTIONS, TARGET_WEEKLY_ADMISSIONS, TARGET_THRESHOLD_RISK),
        time_grain="week",
        regions_note="全球国家层用 WHO FluNet 实验室周检出；美国州级住院仍用 FluSight/NHSN，两类指标不得混用。",
        time_range_note="FluNet 以实际导入周为准。美国住院覆盖以 FluSight/NHSN 为准。",
        sources=(
            {
                "id": "who_flunet",
                "name": "WHO FluNet / GISRS",
                "url": "https://www.who.int/tools/flunet/",
                "field": "INF_ALL weekly laboratory detections",
            },
            {
                "id": "flusight",
                "name": "CDC FluSight Forecast Hub",
                "url": "https://github.com/cdcepi/FluSight-forecast-hub",
                "field": "wk inc flu hosp / totalconfflunewadm",
            },
            {
                "id": "delphi_nhsn_flu",
                "name": "Delphi Epidata NHSN flu admissions",
                "url": "https://cmu-delphi.github.io/delphi-epidata/api/covidcast-signals/nhsn.html",
                "field": "confirmed_admissions_flu_ew",
            },
        ),
        license_note="WHO FluNet 需署名。FluSight 与 NHSN/Delphi 条款保持原样。",
        surveillance_definition="国家层为周实验室流感检出（INF_ALL）。美国州层为周确诊流感住院。二者不是同一指标。",
        surveillance_changes="NHSN 覆盖与 FluSight as_of 语义在 2025-07-05 前后不同。",
        auxiliaries=("population",),
        models=("persistence", "damped_trend", "retrain_glm", "hierarchical", "equal_weight", "adaptive"),
        uncertainty_limits="住院预测不是传播机制发现。修订与区间覆盖需回放检验。",
        status=STATUS_VERIFIED,
        status_label="真实接入，已有回放验证",
        trainable=True,
        is_synthetic=False,
        notes="世界地图默认 FluNet 检出。美国住院不下钻不会外推到其他国家。",
    ),
    "covid19": DiseaseSpec(
        disease_id="covid19",
        name_zh="COVID-19",
        name_en="COVID-19",
        pathogen_class="coronavirus",
        transmission=TRANSMISSION_RESPIRATORY,
        targets=(TARGET_WEEKLY_CASES, TARGET_WEEKLY_ADMISSIONS, TARGET_THRESHOLD_RISK),
        time_grain="week",
        regions_note="WHO 国家 ISO2 用于周报告病例；Delphi NHSN 州码用于住院。两类指标不得混用。",
        time_range_note="无历史版本时只支持最终数据时间切分，不能称为真实实时回测。",
        sources=(
            {
                "id": "who_covid19",
                "name": "WHO COVID-19 global daily data",
                "url": "https://data.who.int/dashboards/covid19/data",
                "field": "New_cases / New_deaths aggregated to Sun–Sat week",
            },
            {
                "id": "delphi_nhsn_covid",
                "name": "Delphi Epidata NHSN COVID admissions",
                "url": "https://cmu-delphi.github.io/delphi-epidata/api/covidcast-signals/nhsn.html",
                "field": "confirmed_admissions_covid_ew",
            },
        ),
        license_note="WHO COVID-19 dashboard 需署名；Delphi 与 CDC NHSN 条款保持原样。",
        surveillance_definition="WHO 周新增报告病例与死亡是报告数，不是感染人数。NHSN 周住院是另一指标，不得与病例混用。",
        surveillance_changes="报告定义与医院覆盖随时间变化；无 vintage 时不得声称实时回放。",
        auxiliaries=("population",),
        models=("persistence", "damped_trend", "retrain_glm", "equal_weight", "adaptive"),
        uncertainty_limits="COVID 住院与流感住院监测定义不同，尺度变换只在训练可见期内估计。",
        status=STATUS_REAL_FORECASTABLE,
        status_label="真实数据可预测（以连接器实际覆盖为准）",
        trainable=True,
        is_synthetic=False,
        default_target=TARGET_WEEKLY_CASES,
        notes="国家层优先 WHO 报告病例。住院仍走 Delphi NHSN，只覆盖实际州。默认 covidcast 常为单一 issue，只支持最终数据时间切分。",
    ),
    "rsv": DiseaseSpec(
        disease_id="rsv",
        name_zh="RSV 感染",
        name_en="RSV",
        pathogen_class="pneumovirus",
        transmission=TRANSMISSION_RESPIRATORY,
        targets=(TARGET_LAB_DETECTIONS, TARGET_WEEKLY_ADMISSIONS, TARGET_THRESHOLD_RISK),
        time_grain="week",
        regions_note="全球国家层用 WHO FluNet RSV 检出；美国州级住院仍用 NHSN。",
        time_range_note="FluNet 以实际导入周为准。NHSN 住院只覆盖美国。",
        sources=(
            {
                "id": "who_flunet",
                "name": "WHO FluNet / GISRS",
                "url": "https://www.who.int/tools/flunet/",
                "field": "RSV weekly laboratory detections",
            },
            {
                "id": "delphi_nhsn_rsv",
                "name": "Delphi Epidata NHSN RSV admissions",
                "url": "https://cmu-delphi.github.io/delphi-epidata/api/covidcast-signals/nhsn.html",
                "field": "confirmed_admissions_rsv_ew",
            },
        ),
        license_note="WHO FluNet 需署名。Delphi 与 CDC NHSN 条款保持原样。",
        surveillance_definition="国家层为周 RSV 实验室检出。美国州层为周确诊 RSV 住院。二者不是同一指标。",
        surveillance_changes="儿科与成人覆盖可能不同，本版不拆年龄组。",
        auxiliaries=("population",),
        models=("persistence", "damped_trend", "retrain_glm", "equal_weight"),
        uncertainty_limits="无分年龄住院时不识别年龄组传播。",
        status=STATUS_REAL_FORECASTABLE,
        status_label="真实数据可预测（以连接器实际覆盖为准）",
        trainable=True,
        is_synthetic=False,
        notes="独立 RSV 信号。世界地图默认 FluNet 检出，不用美国住院填别国。",
    ),
    "dengue": DiseaseSpec(
        disease_id="dengue",
        name_zh="登革热",
        name_en="Dengue",
        pathogen_class="flavivirus",
        transmission=TRANSMISSION_VECTOR,
        targets=(TARGET_WEEKLY_CASES, TARGET_THRESHOLD_RISK),
        time_grain="week",
        regions_note="候选：PAHO 国家/行政区。实际地区以核查后的文件为准。",
        time_range_note="尚未确认可靠、可再分发的周病例修订版本。",
        sources=(
            {
                "id": "paho_plisa",
                "name": "PAHO PLISA / dengue indicators",
                "url": "https://www3.paho.org/data/index.php/en/mnu-topics/indicadores-dengue-en.html",
                "field": "probable/confirmed dengue cases (to be verified)",
            },
            {
                "id": "who_dengue",
                "name": "WHO dengue dashboard",
                "url": "https://www.who.int/emergencies/disease-outbreak-news",
                "field": "event reports; not a complete weekly vintage series",
            },
        ),
        license_note="PAHO/WHO 条款需逐项核对后再导入。未获许可不缓存整库。",
        surveillance_definition="周报告病例（疑似/确诊需分开）。住院与病例不得混为目标。",
        surveillance_changes="病例定义、血清型与实验室确认比例随国家变化。",
        auxiliaries=("climate", "vector_index"),
        models=("persistence", "damped_trend", "retrain_glm"),
        uncertainty_limits="无气候/媒介数据时不得启用对应辅助变量。媒介传播模型接口预留。",
        status=STATUS_PENDING,
        status_label="待接入：已有适配接口与来源核查，尚无可靠时序",
        trainable=False,
        is_synthetic=False,
        notes="完成适配与来源核查。有可靠数据再真实接入，不为凑病种伪造。",
    ),
    "measles": DiseaseSpec(
        disease_id="measles",
        name_zh="麻疹",
        name_en="Measles",
        pathogen_class="morbillivirus",
        transmission=TRANSMISSION_RESPIRATORY,
        targets=(TARGET_WEEKLY_CASES, TARGET_THRESHOLD_RISK),
        time_grain="week",
        regions_note="合成教学区 M1–M3。",
        time_range_note="固定种子合成爆发，明确标注不可当作真实监测。",
        sources=(
            {
                "id": "synthetic_measles",
                "name": "AERP labeled synthetic measles demo",
                "url": "",
                "field": "weekly_cases (synthetic)",
            },
        ),
        license_note="本仓库生成的教学合成数据。",
        surveillance_definition="周病例数（合成）。允许大量零值。",
        surveillance_changes="无真实监测定义变化。",
        auxiliaries=(),
        models=("persistence", "damped_trend", "retrain_glm"),
        uncertainty_limits="稀疏爆发与零膨胀。数据不足时降级。不是真实疫情。",
        status=STATUS_DEMO_ONLY,
        status_label="仅演示：合成研究示例",
        trainable=True,
        is_synthetic=True,
        default_target=TARGET_WEEKLY_CASES,
        notes="用于方法压力测试。界面必须标注合成。",
    ),
    "synth-ari": DiseaseSpec(
        disease_id="synth-ari",
        name_zh="合成呼吸道感染（演示）",
        name_en="Synthetic ARI demo",
        pathogen_class="demo",
        transmission=TRANSMISSION_RESPIRATORY,
        targets=(TARGET_WEEKLY_ADMISSIONS, TARGET_THRESHOLD_RISK),
        time_grain="week",
        regions_note="合成示范区 R1–R5。",
        time_range_note="固定种子 104 周。",
        sources=(
            {
                "id": "synthetic_demo",
                "name": "AERP synthetic demo",
                "url": "",
                "field": "hospital_admissions (synthetic)",
            },
        ),
        license_note="本仓库生成的演示数据。",
        surveillance_definition="合成周住院。",
        surveillance_changes="含人为报告过程变化标签，仅用于演示。",
        auxiliaries=("mobility_index", "vaccination_coverage"),
        models=("persistence", "damped_trend", "retrain_glm", "hierarchical", "adaptive", "analog"),
        uncertainty_limits="合成成功不代表现实有效。",
        status=STATUS_DEMO_ONLY,
        status_label="仅演示",
        trainable=True,
        is_synthetic=True,
        notes="原有演示病种，保留用于界面与端到端测试。",
    ),
    "smallpox": DiseaseSpec(
        disease_id="smallpox",
        name_zh="天花",
        name_en="Smallpox",
        pathogen_class="orthopoxvirus",
        transmission=TRANSMISSION_RESPIRATORY,
        targets=(),
        time_grain="none",
        regions_note="无可靠地区级周时序。",
        time_range_note="历史资料卡，不可训练。",
        sources=({"id": "who_smallpox", "name": "WHO Smallpox", "url": "https://www.who.int/health-topics/smallpox", "field": "background only"},),
        license_note="WHO 公开资料。",
        surveillance_definition="无本系统可用的周住院监测定义。",
        surveillance_changes="历史记录与现代监测不可比。",
        auxiliaries=(),
        models=(),
        uncertainty_limits="没有可靠时序，不作为真实训练集。",
        status=STATUS_HAS_PAGE,
        status_label="有页面：历史资料，无可训练序列",
        trainable=False,
        is_synthetic=False,
    ),
    "plague": DiseaseSpec(
        disease_id="plague",
        name_zh="鼠疫",
        name_en="Plague",
        pathogen_class="bacterium",
        transmission=TRANSMISSION_OTHER,
        targets=(),
        time_grain="none",
        regions_note="无可靠可比周时序。",
        time_range_note="历史资料卡。",
        sources=({"id": "who_plague", "name": "WHO Plague", "url": "https://www.who.int/health-topics/plague", "field": "background only"},),
        license_note="WHO 公开资料。",
        surveillance_definition="历史叙述与现代监测字段不同。",
        surveillance_changes="不可混训。",
        auxiliaries=(),
        models=(),
        uncertainty_limits="不作为真实训练集。",
        status=STATUS_HAS_PAGE,
        status_label="有页面：历史资料，无可训练序列",
        trainable=False,
        is_synthetic=False,
    ),
    "sars": DiseaseSpec(
        disease_id="sars",
        name_zh="SARS",
        name_en="SARS",
        pathogen_class="coronavirus",
        transmission=TRANSMISSION_RESPIRATORY,
        targets=(),
        time_grain="none",
        regions_note="无本仓库内的地区级周住院修订版本。",
        time_range_note="2002–2003 资料卡。",
        sources=({"id": "who_sars", "name": "WHO SARS", "url": "https://www.who.int/health-topics/severe-acute-respiratory-syndrome", "field": "background only"},),
        license_note="WHO 公开资料。",
        surveillance_definition="历史汇总与现代周住院不可直接对齐。",
        surveillance_changes="缺少 issued_at 时不做真实实时回测。",
        auxiliaries=(),
        models=(),
        uncertainty_limits="不作为真实训练集。",
        status=STATUS_HAS_PAGE,
        status_label="有页面：历史资料，无可训练序列",
        trainable=False,
        is_synthetic=False,
    ),
}


def get_disease(disease_id: str) -> DiseaseSpec | None:
    return DISEASE_SPECS.get(str(disease_id))


def list_disease_specs() -> list[dict[str, Any]]:
    return [spec.as_dict() for spec in DISEASE_SPECS.values()]


def assert_target_allowed(disease_id: str, target: str) -> None:
    spec = get_disease(disease_id)
    if spec is None:
        raise ValueError(f"未知疾病 {disease_id}")
    if target not in spec.targets:
        raise ValueError(f"{disease_id} 不支持目标 {target}。可用：{list(spec.targets)}")


def comparable_respiratory_admissions() -> tuple[str, ...]:
    return tuple(
        spec.disease_id
        for spec in DISEASE_SPECS.values()
        if spec.transmission == TRANSMISSION_RESPIRATORY
        and TARGET_WEEKLY_ADMISSIONS in spec.targets
        and spec.trainable
        and spec.disease_id != "synth-ari"
    )
