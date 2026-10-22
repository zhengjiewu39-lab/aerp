# 实验有效性审查记录

| 问题 | 证据 | 修改 | 验证 |
| --- | --- | --- | --- |
| 已成熟预测未在后续原点进入反馈，最终补评还删除在线评分 | `run_experiment` 曾对本次 saved 预测按当前 origin 评分，结束时 `Evaluation.delete()` | 每原点先评此前预测（purpose=online，幂等），再生成本次预测；最终 purpose=final，不回写权重 | `test_matured_errors_change_later_weights_immature_do_not`、`test_evaluate_is_idempotent` |
| 短窗口重训污染等权/动态对照 | `_predict_arms` 替换 `components["damped_trend"]` 后再做等权 | 对照使用 clone；短窗口、修订、形态、区间倍率只作用于 adaptive | `test_equal_weight_unchanged_when_adaptive_enabled`、`test_adaptation_rules_do_not_mutate_controls` |
| 目标周跟最后可见周绑定 | `_store_forecasts` 用 cutoff-1 日；分层用各地区序列位置 | 统一 `ForecastTask`：参考周六 + horizon×7；模型按步数外推但不改贴日期 | `test_delayed_last_observation_does_not_relabel_target`、`test_hierarchical_uses_calendar_not_local_length` |
| 分层 IRLS 与收缩缺乏数值对照，且冒称完整分层 | 仅有分位数有序测试；`IDENTIFIABILITY_NOTE` 偏贝叶斯口吻 | 秩/收敛检查；残差收缩命名；截距仅泊松对照 | `test_intercept_only_poisson_matches_log_mean`、`test_residual_shrinkage_moves_region_toward_grand_mean` |
| 教学形态进入正式预测 | analogs.py + adapt.py 默认并入 adaptive | research 模式关闭 demo analogs 与主观区间倍率 | `test_research_mode_disables_demo_analogs_and_subjective_intervals` |
| vintage `as_of` 在 2025-07-05 前后语义不同 | Hub README | 周六 reference_date 转为周三 `issued_at`，原始值保留 | `test_as_of_saturday_before_break_shifts_to_wednesday` |
| 无轨迹时把独立边际写成联合概率 | `joint_exceed_probability` | 轨迹优先，独立边际标记 degraded | `test_copula_joint_differs_from_independent_marginal` |
| 本地归档被当成公开发布 | `archive.py` 只有本机时间 | 哈希链 + stamp_request，状态 local_only | `test_archive_chain_is_local_only` |
| 无真实变化类型标签仍写准确率 | FluSight 无此类标签 | `diagnosis_report` 无标签跳过 | `test_change_diagnosis_skipped_without_real_labels` |
