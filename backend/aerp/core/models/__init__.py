"""预测模型注册表。"""

from aerp.core.models.analog import AnalogShapeModel
from aerp.core.models.hierarchical import IDENTIFIABILITY_NOTE, PanelCountModel
from aerp.core.models.base import FitResult, HorizonForecast, PredictResult
from aerp.core.models.catalog import MODEL_CARDS
from aerp.core.models.damped import DampedTrendModel
from aerp.core.models.ensemble import combine_predictions, dynamic_weights
from aerp.core.models.negbin import NegBinGLMModel
from aerp.core.models.persistence import PersistenceModel
from aerp.core.models.transfer import TransferModel

__all__ = [
    "MODEL_CARDS",
    "AnalogShapeModel",
    "IDENTIFIABILITY_NOTE",
    "PanelCountModel",
    "DampedTrendModel",
    "FitResult",
    "HorizonForecast",
    "NegBinGLMModel",
    "PersistenceModel",
    "PredictResult",
    "TransferModel",
    "combine_predictions",
    "dynamic_weights",
]
