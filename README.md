# Adaptive Epidemic Research Platform（AERP）

流行病学研究原型，用于管理多病种监测数据、比较历史资料、估计报告不完整程度、预测未来 1—4 周住院或病例，并在识别数据/趋势变化后再更新候选模型。

**研究问题：** 在监测数据修订或疫情趋势突然变化时，先识别变化类型再更新模型，是否比直接在线更新更可靠？

> 这不是临床系统，也不是公共卫生决策指令。合成演示数据上的实验成功不代表现实世界有效。不要把预测范围称为大流行概率，不要把统计关联解释为因果证明。

## 技术栈

- 前端：React 18、TypeScript、Vite、Tailwind CSS、Apache ECharts
- 后端：Python 3.11、FastAPI、Pydantic、SQLAlchemy、SQLite
- 科学计算：pandas、NumPy（核心路径）；SciPy / scikit-learn / statsmodels 为可选对照依赖。计数回归使用纯 NumPy IRLS，以兼容部分 macOS 上无法加载的 SciPy 二进制轮子。

## 快速启动（本地优先）

依赖：Python 3.11、[uv](https://github.com/astral-sh/uv)、Node.js 20+。

```bash
# 1) 后端
cd backend
uv sync --extra dev
cp ../.env.example ../.env   # 可选
uv run uvicorn aerp.api.main:app --host 127.0.0.1 --port 8000

# 2) 前端（新终端）
cd frontend
npm install
npm run dev
```

打开 http://127.0.0.1:5173 。API 文档 http://127.0.0.1:8000/docs 。

也可使用：

```bash
chmod +x scripts/dev.sh
./scripts/dev.sh
```

可选 Docker（不是唯一方式）：

```bash
docker compose up --build
```

## 端到端演示

启动后默认自动写入固定种子合成数据（`AERP_AUTO_SEED=true`）。

1. 打开「多病种总览」：顶部选择疾病/目标/地区，查看真实或合成状态，不填假数字。
2. 「疾病研究」：传播机制、数据来源、候选模型理由、可训练与资料分区。
3. 「数据管理」：CSV 模板、麻疹合成示例、连接器真实状态。
4. 「研究实验」：创建回放；开发/验证/锁定测试分开报告。
5. 「预测比较」：观测、成熟目标与区间；未成熟标待评价。
6. 「跨疾病迁移」：本地 / 固定迁移 / 可退出迁移对照（默认合成压力测试）。
7. 「可靠性与异常」：修订、误差、分歧与人工核实；不显示未经验证的机制已变化。

命令行示例实验：

```bash
cd backend && uv run python ../scripts/run_demo_experiment.py
# 输出 examples/sample_experiment.json
```

## 数据格式

CSV 字段：`disease_id,region_id,period_start,period_end,issued_at,metric,value,unit,source_id,quality_flag`

- `issued_at` 是该版本发布时间；同一周期可有多个版本。
- `value` 为空且 `quality_flag=missing` 表示缺失，不会保存为 0。
- 模板：`GET /api/data/template` 或 `backend/aerp/data/observation_template.csv`。

外部连接器统一返回 `未配置` / `尚未验证` / `可用`，不会显示虚假连接成功。

## 模型假设（摘要）

详见 [docs/METHODS.md](docs/METHODS.md) 与 API `/api/models`。

- 持续值：未来各周等于最近可见值。
- 阻尼趋势：限制增长率并衰减外推。
- 计数回归：泊松/负二项预测分布；固定系数臂只拟合一次。
- 等权/动态组合：分位数平均，不是完整混合分布；动态权重只用已成熟结果。
- 适应系统：先分类异常再调修订/窗口/权重；形态匹配足够强时并入历史类比方向；不默认优于等权。
- 历史形态类比：对齐定性归一化曲线或对照地区，再按当前水平缩放；不编造历史病例数。
- 地区分层：统一 GLM、独立 GLM、部分共享分层、分层动态更新对照；年龄结构只作地区协变量。
- 动态更新只调整地区截距和短期斜率，不把住院趋势改名为传播率。

## 研究模式（论文回放）

```bash
cd backend
uv run python -m aerp.research fetch --kind vintage
uv run python -m aerp.research inventory --kind vintage
uv run python -m aerp.research splits --kind vintage
uv run python -m aerp.research replay --kind vintage --run-experiment --seed 42
uv run python -m aerp.research verify-delphi
uv run python -m aerp.research fetch-nhsn --disease covid19 --geo-value pa
uv run python -m aerp.research check-dengue
uv run python -m aerp.research transfer-demo
uv run python -m aerp.research archive-verify
```

- snapshot 没有真实 `as_of`，只能做最终数据时间切分。
- vintage 使用 FluSight `time-series.csv` 的 `as_of`；2025-07-05 前若为周六则按 reference_date 转为周三发布日，不能用下载时间冒充发布时间。
- 外部 FluSight 提交只作对照，不并入本系统模型臂。
- 本地前瞻归档与哈希链不能证明独立公开发布。
- 没有真实变化类型标签时不报告变化诊断准确率。
- 研究方案、数据字典、EPIFORGE 映射见 `docs/RESEARCH_PROTOCOL.md`、`docs/DATA_DICTIONARY.md`、`docs/EPIFORGE.md`、`docs/EXTERNAL_TIMESTAMP.md`。
- 产物写到 `research_runs/`，不覆盖原始数据。实验未完成时标记 incomplete，不写虚构成绩。

## 验证

```bash
# 后端测试
cd backend && uv run pytest

# 前端类型检查与构建
cd frontend && npm run typecheck && npm run build
```

重点覆盖：截止时间不读未来版本、缺失不变 0、未成熟不更新权重、合成真值不进训练特征、WIS/覆盖率、分位数有序、小样本降级、组件失败不拖垮组合。

## 文档

- [方法说明](docs/METHODS.md)
- [功能状态与局限](docs/STATUS.md)
- [多病种状态与复现](docs/MULTI_DISEASE.md)

## 许可与伦理边界

本仓库提供研究原型代码与合成演示数据。勿将未经验证输出用于临床或公共卫生决策。历史资料卡只引用公开概述来源；缺少可靠序列时使用“待补充”，不编造病例数与传播参数。
