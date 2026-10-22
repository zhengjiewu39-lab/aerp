# 前瞻预测的外部时间戳

本地 `research_runs/prospective` 归档会保存预测哈希、数据截止时间和一条本机哈希链。这些只能证明本机文件没有被后写覆盖，不能证明独立公开发布。

## 不被接受为公开发布证明

- 本机 `generated_at`
- 本机 `chain.jsonl`
- 未推送到公共托管的 git commit
- 操作系统文件修改时间
- 下载 FluSight / Delphi 数据的时间

## 可以使用的外部证明（需作者自行提交，本仓库不擅自上传）

1. RFC 3161 时间戳令牌，对象为 `*.stamp_request.json` 中的 `sha256`。
2. OpenTimestamps `.ots` 证明。
3. 预先登记的公共预测 Hub 提交回执（例如 FluSight），含公开对象哈希。
4. 独立第三方对哈希清单的公证。

## 命令

```bash
cd backend
uv run python -m aerp.research archive --file path/to/forecast.json
uv run python -m aerp.research archive-verify
# 获得外部证明后：
uv run python -m aerp.research archive-attach --stamp-request ...stamp_request.json --proof token.tsr
```

附加证明后状态变为 `external_proof_attached`。这仍不是法律认定，只是把证明文件与哈希绑在一起。
