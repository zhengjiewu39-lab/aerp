# 数据字典与来源清单

## 系统观测字段

| 字段 | 含义 |
| --- | --- |
| disease_id | 疾病标识。研究模式流感为 `influenza`。 |
| region_id | 地区标识。FluSight 使用两位 FIPS。 |
| period_start / period_end | 数据对应周，默认周日到周六。 |
| issued_at | 该版本真实发布时间。禁止用下载时间或文件修改时间填充。 |
| metric | 指标。住院目标为 `hospital_admissions`。 |
| value | 观测值。缺失保持空，不等于 0。 |
| unit | 单位。住院为 `count`。 |
| source_id | 来源。 |
| quality_flag | 质量标记，如 `missing`。 |

## FluSight / NHSN

- 仓库：https://github.com/cdcepi/FluSight-forecast-hub
- snapshot：`target-data/target-hospital-admissions.csv`（date, location, location_name, value, weekly_rate）。无 as_of，只能 `final_time_split`。
- vintage：`target-data/time-series.csv` 的 `as_of`。2025-07-05 之前若为周六，按 Hub 说明视为 reference_date，本系统把 `issued_at` 记为该周六减 3 天（周三发布日）；不是周六的日期保持原值。2025-07-05 及之后 `as_of` 已是发布日，直接作为 `issued_at`。原始 `as_of` 保留在 `as_of_raw`。
- 原始评价数据：NHSN Weekly Hospital Respiratory Dataset，`totalconfflunewadm`。
- 许可/归属：按 Hub README；研究发表前联系 flusight@cdc.gov。本仓库不改第三方许可证。

## 回放类型

1. vintage：按历史发布时间重建。
2. final_time_split：用最终数据按周期结束时间切分，不能称为真实实时回测。
3. synthetic：合成压力测试。

## 预测任务字段

见 `aerp.core.calendar.CALENDAR_DEFINITIONS`：cutoff、数据周、参考周、目标周、预测时长。
