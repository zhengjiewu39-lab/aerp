# 全球疾病地图工作台

首页为全球交互式地图。研究总览改到 `/overview`。原有多病种、vintage、预测与实验功能保留。

## 边界来源

| 数据集 | 版本/获取日 | 许可 | 校验 |
| --- | --- | --- | --- |
| Natural Earth 110m Admin 0 | 2026-10-23 自 nvkelso/natural-earth-vector | 公有领域 | 见 `backend/aerp/data/geo/provenance.json` |
| 美国州界（FIPS） | PublicaMundi us-states.json，Census TIGER 衍生 | 公有领域 | 52 个要素 |
| 110m 缺失的小型国家 | 行政区代表点 | 仅定位，不是病例点 | 约 31 个点 |

覆盖清单以该边界数据集为准，不用“固定国家数量”证明完整性。挪威/法国 ISO 在源数据为 `-99`，用 `ISO_A2_EH` 映射；科索沃例外为 XK。北塞浦路斯与索马里兰保留为无 ISO 的 `ne:*`，并注明争议处理。Web Mercator 在约 ±85° 以外失真，不用于极区结论。

## 疾病数据覆盖（实际，不是承诺）

- **COVID-19 周报告病例**：WHO 国家日报告（HDX 再分发的 `WHO-COVID-19-global-data.csv`，CC BY-IGO）按周日–周六聚合。旧 `covid19.who.int` 直链现返回 HTML，不能当成功接入。国家层着色。不是住院、不是感染人数。拉取：`cd backend && uv run python -m aerp.research fetch-who-covid`。失败时国家保持无数据。
- **流感/COVID/RSV 周住院**：沿用 FluSight / Delphi NHSN。只有美国州级时，只给有观测的州着色，不复制到其他国家，也不把国家值拆到省州。
- **登革热**：仍待接入。
- **麻疹 / 合成 ARI**：演示数据，地图持续水印；合成地区码不会匹配到真实国家多边形。
- **其他病种的地图播放**：流感、RSV、登革热、麻疹、合成 ARI、天花、鼠疫、SARS 另有固定种子的国家级周病例演示层（`map_demo`），用于时间轴着色。持续显示演示水印，不写入真实监测，也不覆盖 COVID 的 WHO 真实国家序列。流感/RSV 住院仍只有美国州级时，下钻才用真实住院，不把演示病例当成住院。

有值地区按黄→橙→红五档着色，图例名为轻微、一般、较重、严重、非常严重。相对档按本图分位数分组；固定档（`map_burden_v1`）用每 10 万人区间 0–1 / 1–5 / 5–15 / 15–40 / ≥40。名称用于读热力图，不是临床诊断，也不是卫生部门官方等级。无数据、报告为零、未报告、过期/部分周分开着色：无数据和未报告用中性灰，零值用冷白。左侧显示本周各档地区数；地区悬停与排序表给出相对上周的档位升降。

## AI 分析

后端 OpenAI 兼容 Chat Completions：`POST {AERP_AI_BASE_URL}/chat/completions`。

```
AERP_AI_PROVIDER=openai_compat
AERP_AI_BASE_URL=https://api.openai.com/v1
AERP_AI_API_KEY=...
AERP_AI_MODEL=   # 填写当前服务文档中的真实模型名
```

密钥只在后端。未配置时接口返回「AI未配置/调用失败」，不会用模板冒充。测试可用 `AERP_AI_ALLOW_MOCK=true` 与 `AERP_AI_PROVIDER=mock`。

本轮若未提供可用密钥：**代码与集成入口已完成，尚未完成真实连通验证。**

状态机（后端强制）：生成中 → 待审核 / 需修正 → 已确认或已驳回。确认必须带当前 `revision_id` 与 `snapshot_hash`。正式导出只把已确认版本当正式记录，并保留「AI辅助、人工审核」。

## 启动

```bash
cd backend && uv sync --extra dev
uv run python -m uvicorn aerp.api.main:app --host 127.0.0.1 --port 8000

cd frontend && npm install && npm run dev
```

打开 http://127.0.0.1:5173 。可选拉取 WHO：`uv run python -m aerp.research fetch-who-covid`。
