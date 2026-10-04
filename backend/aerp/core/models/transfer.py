from __future__ import annotations

from typing import Any


class TransferModel:
    """跨疾病迁移接口。第一版只保留配置，不产生预测数字。"""

    model_id = "transfer"

    def fit(self, source_training_data, config: dict | None = None) -> dict[str, Any]:
        del source_training_data
        config = config or {}
        return {
            "status": "not_implemented",
            "message": "研究扩展，尚未实现",
            "source_disease_id": config.get("source_disease_id"),
            "target_disease_id": config.get("target_disease_id"),
            "exclude_target_from_training": bool(config.get("exclude_target_from_training", True)),
            "outputs": None,
        }

    def predict(self, horizons, available_covariates=None):
        del horizons, available_covariates
        raise NotImplementedError("研究扩展，尚未实现")

    def diagnostics(self) -> dict[str, Any]:
        return {"status": "not_implemented", "message": "研究扩展，尚未实现"}

    def serialize_metadata(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "status": "not_implemented",
            "message": "研究扩展，尚未实现",
            "keeps_no_transfer_baseline": True,
            "outputs": None,
        }
