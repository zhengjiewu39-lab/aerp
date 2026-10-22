# EPIFORGE 2020 条目映射

参考：Pollett 等，PLOS Medicine，https://doi.org/10.1371/journal.pmed.1003793

未完成的条目保持未完成，不填写虚构成绩。

| 条目 | 位置 | 状态 |
| --- | --- | --- |
| 研究问题与预测用途 | docs/RESEARCH_PROTOCOL.md | 已写明第一阶段问题与非临床用途 |
| 目标疾病、人群、地点、时间 | docs/DATA_DICTIONARY.md、RESEARCH_PROTOCOL.md | 流感、美国辖区、周住院；具体季节依数据完整性 |
| 预测目标定义 | calendar.py、METHODS.md | 1—4 周周住院计数分位数 |
| 数据来源与修订 | DATA_DICTIONARY.md、flusight.py | snapshot 与 vintage 分开；不伪造发布时间 |
| 模型与对照 | catalog.py、RESEARCH_PROTOCOL.md | A—F 臂；分层是残差收缩近似 |
| 验证设计 | splits.py | 开发/验证/锁定测试按可见跨度 |
| 评分 | scoring.py | WIS、MAE、覆盖率、宽度、完成率 |
| 不确定性 | revision.py、alerts_eval.py | 修订抽样与区间来源分开记录 |
| 软件与复现 | README.md、python -m aerp.research | 命令已列出 |
| 利益与局限 | LICENSE_NOTES.md、STATUS.md | 已知局限与许可待作者决定 |
| 结果表图 | research_runs/ | 无完整真实实验时标记 incomplete |

没有可信真实变化标签时，不报告变化诊断准确率。合成标签单独报告。
