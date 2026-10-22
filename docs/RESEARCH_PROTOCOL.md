# AERP 研究方案（第一阶段）

研究问题：在报告延迟、数据修订及疫情趋势变化条件下，结合监测变化识别与地区信息共享的自适应方法，是否比常规更新和组合方法提供更可靠的短期住院预测？

本方案是预注册式说明，不是已经得到的论文结论。住院趋势预测不得称为已识别传播速率，也不得保证提高预测表现或保证发表。

## 范围

- 疾病：流感（FluSight / NHSN 周确诊流感住院）。
- 空间：多个美国辖区。
- 时间粒度：流行病学周（周日到周六）。
- 任务：未来 1—4 周周住院人数的概率预测。
- 回放：优先使用带 `as_of` 的历史版本；snapshot 只能做最终数据时间切分。
- 前瞻：本地按周归档，不把本地时间戳当作独立公开发布证明。

## 运行模式

- `demo`：合成数据、定性历史形态和主观区间倍率可用于教学。
- `research`：上述教学模块默认不进入正式预测；缺少必要数据时失败或降级。

## 对照臂

| 臂 | 含义 |
| --- | --- |
| A persistence / 季节性持续值 | 基线 |
| B retrain_glm | 地区独立计数回归 |
| C hierarchical | 地区部分共享（残差收缩近似，不是完整贝叶斯分层） |
| D equal_weight | 等权组合 |
| E dynamic_weight | 常规动态组合（只用当时已成熟评分） |
| F adaptive | 监测变化感知适应 |

外部强基准（FluSight-baseline / FluSight-ensemble 等提交）在许可允许且文件可获取时按 `location` 与 `target_end_date` 对齐评分，不并入本系统模型臂，也不把他人提交说成自己的方法。

## 消融

只有模块真正接入完整方法时才叫对应消融：无修订处理、无变化检测、无动态权重、无地区共享、无修订不确定性传播。删除一个独立模型臂不等于删除完整方法中的模块。

## 划分

开发、验证、锁定测试按**当时可见数据跨度**划分，见 `python -m aerp.research splits`。锁定测试期间不按测试成绩改阈值、窗口或规则。

## 主要结局

WIS 及相对基线变化、MAE、50/80/95% 覆盖率与区间宽度、任务完成率。同时报告总体与地区等权汇总。差异区间用配对分块重采样，不把重叠预测当独立样本。

## 预警任务

未来两周任一周周住院数超过开发集 90% 分位阈值，称为住院高负担预警，不是容量超载预测。连续预警周视为同一事件。联合超阈概率优先由样本轨迹或高斯 copula 轨迹估计；没有轨迹时才使用独立边际近似并标记 degraded。

FluSight / NHSN 没有可信的真实“变化类型”标签。没有人工标注时不报告变化诊断准确率。合成标签如需报告，只能单独标注为 synthetic_only。

## 复现命令

```bash
cd backend
uv sync --extra dev
uv run python -m aerp.research fetch --kind vintage
uv run python -m aerp.research inventory --kind vintage
uv run python -m aerp.research splits --kind vintage
uv run python -m aerp.research replay --kind vintage --run-experiment --seed 42
uv run python -m aerp.research verify-delphi
uv run pytest
```

默认 vintage 回放使用 US、CA、NY、TX、PA，流感季每两周一个原点。网络不可用时，可用 `backend/tests/fixtures/` 做离线解析测试，不伪造成功接入。本地前瞻归档见 `docs/EXTERNAL_TIMESTAMP.md`。
