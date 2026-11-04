# 多病种状态、来源与复现

本页记录 0.3 版多病种扩展。不承诺准确预测下一次全球大流行，不把住院预测称为传播机制发现，不保证新方法优于基准或能够发表。

## 疾病状态（不得混为一谈）

| 疾病 | 状态 | 含义 | 默认目标 |
| --- | --- | --- | --- |
| 流感 | 真实接入，已有回放验证 | FluSight/NHSN 周确诊流感住院流程已存在 | 周住院 |
| COVID-19 | 真实数据可预测 | 国家层：WHO 周报告病例（拉取后）。州级住院：Delphi NHSN `confirmed_admissions_covid_ew`。两类指标不得混用。默认 covidcast 常为单一 issue，只能做最终数据时间切分 | 周病例 |
| RSV | 真实数据可预测 | 独立信号 `confirmed_admissions_rsv_ew` 已验证可解析。同样需核查是否有多版本 | 周住院 |
| 登革热 | 待接入 | 适配接口与来源核查已写；无可靠可再分发周病例修订版本 | 周病例 |
| 麻疹 | 仅演示 | 固定种子合成周病例，用于零值/稀疏压力测试 | 周病例 |
| 合成呼吸道（synth-ari） | 仅演示 | 原有端到端演示病种 | 周住院 |
| 天花 / 鼠疫 / SARS | 有页面 | 历史资料卡。没有可靠时序，不作为真实训练集 | 无 |

不为凑齐五种疾病伪造真实数据。界面用状态标签区分，不用红绿作为唯一编码。

## 数据来源与许可

- FluSight Forecast Hub：https://github.com/cdcepi/FluSight-forecast-hub  
  流感周住院。snapshot 无 `as_of` 时只能做最终数据时间切分。vintage 的 `as_of` 按 `docs/EXTERNAL_TIMESTAMP.md` 转换。许可证保持 Hub 原样。
- Delphi Epidata / CDC NHSN：https://cmu-delphi.github.io/delphi-epidata/  
  流感、COVID、RSV 使用**各自信号**，不能把流感连接器改名后宣称支持另一病种。`downloaded_at` 不是历史发布时间。无 `issue` 时不能称为真实 vintage。
- PAHO PLISA / WHO 登革热页面：仅作候选来源核查。未确认再分发条件前不缓存整库、不训练。
- 麻疹合成数据：本仓库生成，必须标注合成。

## 跨疾病迁移

第一阶段只在可比呼吸道住院目标上研究。对照臂：

1. `transfer_local`：只用当前疾病本地数据。
2. `transfer_fixed`：来源疾病训练期内尺度变换 + 历史窗口检索，固定权重。
3. `transfer_exit`：根据当时已成熟评分降低或退出迁移。

合成压力测试命令：

```bash
cd backend && uv run python -m aerp.research transfer-demo
```

该结果的 `data_mode` 为 `synthetic_stress_test`，不能写成真实 COVID/流感提升。真实双病种住院同时可用时，再用同一接口替换输入帧。

## 复现命令

```bash
# 后端测试
cd backend && uv sync --extra dev && uv run python -m pytest

# 流感 vintage 回放（需已缓存 FluSight 文件）
uv run python -m aerp.research replay --kind vintage --run-experiment --seed 42

# Delphi 探测（流感/COVID/RSV 独立路径）
uv run python -m aerp.research verify-delphi
uv run python -m aerp.research fetch-nhsn --disease covid19 --geo-value pa --time-values 202240-202520
uv run python -m aerp.research fetch-nhsn --disease rsv --geo-value pa --time-values 202240-202520
uv run python -m aerp.research nhsn-replay --disease covid19 --geo-value pa --max-origins 4

# 登革热来源核查（默认离线）
uv run python -m aerp.research check-dengue
uv run python -m aerp.research check-dengue --live

# 迁移合成对照
uv run python -m aerp.research transfer-demo

# 前端
cd frontend && npm run typecheck && npm run build
```

本地启动：后端 `uv run uvicorn aerp.api.main:app --host 127.0.0.1 --port 8000`，前端 `npm run dev -- --host 127.0.0.1 --port 5173`。不要部署或推送。

## 论文主张边界

- 已修复泊松 IRLS 的 offset 扣除；回归测试要求均值≈12、截距≈log(6)，不只看 `converged`。
- 修订抽样后混合预测 CDF；对应分位数平均仍保留为独立组合方法。
- 锁定测试写入 `Evaluation.split_set`，主结果单独报告 `models_locked_test`。
- 预警阈值每个地区每个周期只用冻结时点前最后一个版本。
- `no_hierarchy` 现在从适应臂中去掉地区部分共享。
- 外部基准只在共同任务配对；日期对不上不替代。
- 预警按模型计算 Brier，提前量窗口 6 周，copula rho 做敏感性。
- 分块重采样接入 `pairwise_block_resample`。
- 不编造提升比例、新颖性或显著性。
