"""历史疫情资料卡。没有可靠来源的字段标为待补充，不编造病例数或传播参数。"""

HISTORY_CARDS = [
    {
        "id": "smallpox",
        "name": "天花",
        "era": "古代至 20 世纪",
        "summary": "由天花病毒引起的传染病。通过全球免疫规划于 1980 年正式宣布根除。",
        "trainable_series_available": False,
        "notes_vs_series": "本页仅保存资料卡与来源范围。第一版不提供可训练的地区级周住院序列。",
        "sources": [
            {
                "title": "WHO — Smallpox",
                "url": "https://www.who.int/health-topics/smallpox",
                "scope": "疾病背景与根除声明。不含本系统所需的地区级周住院监测版本。",
            }
        ],
        "pending_fields": ["地区级周新增住院时间序列", "可追溯的发布版本（issued_at）", "统一人口分母"],
        "parameters": {"R0": "待补充", "serial_interval": "待补充", "case_fatality": "待补充"},
        "analog_ids": ["smallpox_unimodal_control"],
        "qualitative_phases": ["上升", "单峰", "控制后下降"],
        "period_note": "古代至 20 世纪",
        "scope": "资料卡与归一化形态对照。第一版不提供可训练的地区级周住院序列。",
        "notes": "本页仅保存资料卡与来源范围。第一版不提供可训练的地区级周住院序列。",
    },
    {
        "id": "plague",
        "name": "鼠疫",
        "era": "多次历史大流行与地方性病例",
        "summary": "由鼠疫耶尔森菌引起。历史大流行资料与现代监测系统的字段定义不同，不可直接混训。",
        "trainable_series_available": False,
        "notes_vs_series": "历史叙述与可训练时序分开展示。不把中世纪估计直接当作模型输入。",
        "sources": [
            {
                "title": "WHO — Plague",
                "url": "https://www.who.int/health-topics/plague",
                "scope": "现代临床与公共卫生背景。不提供本原型所需的多版本修订轨迹。",
            }
        ],
        "pending_fields": ["可比的周住院/病例序列", "修订发布时间", "跨地区可比单位"],
        "parameters": {"R0": "待补充", "serial_interval": "待补充", "case_fatality": "待补充"},
        "analog_ids": ["plague_multi_wave"],
        "qualitative_phases": ["多波次", "地方性残留"],
        "period_note": "多次历史大流行与地方性病例",
        "scope": "历史叙述与可训练时序分开展示。不把中世纪估计直接当作模型输入。",
        "notes": "历史叙述与可训练时序分开展示。不把中世纪估计直接当作模型输入。",
    },
    {
        "id": "sars",
        "name": "SARS",
        "era": "2002–2003",
        "summary": "严重急性呼吸综合征冠状病毒引起的疫情。历史汇总与现代周住院监测在时间粒度与修订制度上不同。",
        "trainable_series_available": False,
        "notes_vs_series": "资料卡不等于可训练时序。缺少可靠 issued_at 时不做“真实实时回测”声明。",
        "sources": [
            {
                "title": "WHO — SARS",
                "url": "https://www.who.int/health-topics/severe-acute-respiratory-syndrome",
                "scope": "事件背景。未在此仓库内附带完整地区级周住院修订版本。",
            }
        ],
        "pending_fields": ["地区级周住院修订版本", "成熟滞后定义", "与当前指标定义的对齐说明"],
        "parameters": {"R0": "待补充", "serial_interval": "待补充", "case_fatality": "待补充"},
        "analog_ids": ["sars_short_unimodal"],
        "qualitative_phases": ["快速上升", "短峰值", "控制后下降"],
        "period_note": "2002–2003",
        "scope": "资料卡不等于可训练时序。缺少可靠 issued_at 时不做“真实实时回测”声明。",
        "notes": "资料卡不等于可训练时序。缺少可靠 issued_at 时不做“真实实时回测”声明。",
    },
    {
        "id": "covid19",
        "name": "新冠",
        "era": "2019 至今",
        "summary": "SARS-CoV-2 相关疫情。外部监测资源丰富，但接入前需核查许可、覆盖、发布时间与修订制度。",
        "trainable_series_available": False,
        "notes_vs_series": "第一版演示使用合成数据。未配置并验证的外部连接器不会显示为已连接。",
        "sources": [
            {
                "title": "WHO — Coronavirus disease (COVID-19)",
                "url": "https://www.who.int/health-topics/coronavirus",
                "scope": "背景资料。不是本仓库内已验证的住院修订数据源。",
            },
            {
                "title": "Delphi Epidata（候选接入）",
                "url": "https://github.com/cmu-delphi/delphi-epidata",
                "scope": "未来连接器候选。接入前需核查版本、许可、覆盖与发布时间。",
            },
        ],
        "pending_fields": ["已验证的外部连接器配置", "地区对齐表", "修订成熟滞后的实证估计"],
        "parameters": {"R0": "待补充", "serial_interval": "待补充", "case_fatality": "待补充"},
        "analog_ids": ["covid_two_wave_seasonal"],
        "qualitative_phases": ["首波", "间隔", "季节性再起"],
        "period_note": "2019 至今",
        "scope": "第一版演示使用合成数据。未配置并验证的外部连接器不会显示为已连接。",
        "notes": "第一版演示使用合成数据。未配置并验证的外部连接器不会显示为已连接。",
    },
]

EXTERNAL_CONNECTORS = [
    {
        "id": "cape",
        "name": "CAPE",
        "url": "https://github.com/Emory-Melody/CAPE",
        "status": "尚未验证",
        "note": "接入前核查版本、许可、数据覆盖、发布时间和运行条件。",
    },
    {
        "id": "delphi-rf",
        "name": "Delphi-RF",
        "url": "https://github.com/cmu-delphi/DelphiRF",
        "status": "尚未验证",
        "note": "研究扩展候选，第一版未接入。",
    },
    {
        "id": "epinowcast",
        "name": "epinowcast",
        "url": "https://github.com/epinowcast/epinowcast",
        "status": "尚未验证",
        "note": "面向 nowcasting；与本仓库 Python 栈的混用条件尚未验证。",
    },
    {
        "id": "flusight",
        "name": "FluSight",
        "url": "https://github.com/cdcepi/FluSight-forecast-hub",
        "status": "尚未验证",
        "note": "流感预测枢纽格式可作对照，未默认混用。",
    },
    {
        "id": "delphi-epidata",
        "name": "Delphi Epidata",
        "url": "https://github.com/cmu-delphi/delphi-epidata",
        "status": "未配置",
        "note": "需要显式配置与验证后才能使用，禁止展示虚假连接成功。",
    },
]
