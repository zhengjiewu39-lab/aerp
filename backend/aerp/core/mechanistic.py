from __future__ import annotations

import numpy as np


class MechanisticSimulator:
    """SIRS 骨架只负责生成合成感染曲线。

    第一版不根据住院序列反推传播参数。
    """

    def fit_to_observations(self, observations) -> dict:
        del observations
        return {
            "status": "not_implemented",
            "message": "不能只凭住院曲线识别全部传播参数。第一版不拟合机制模型。",
            "outputs": None,
        }

    def simulate(self, beta: np.ndarray, rng: np.random.Generator, population: int = 200_000) -> np.ndarray:
        sigma = 1.0
        gamma = 0.85
        omega = 1.0 / 32.0
        susceptible = float(population - 100)
        exposed = 40.0
        infectious = 60.0
        recovered = 0.0
        infections = np.zeros(len(beta))
        for week, beta_t in enumerate(beta):
            exposed_now = max(beta_t, 0.0) * susceptible * infectious / population + 10.0
            to_infectious = sigma * exposed
            to_recovered = gamma * infectious
            to_susceptible = omega * recovered
            susceptible = max(susceptible - exposed_now + to_susceptible, 0.0)
            exposed = max(exposed + exposed_now - to_infectious, 0.0)
            infectious = max(infectious + to_infectious - to_recovered, 0.0)
            recovered = max(recovered + to_recovered - to_susceptible, 0.0)
            infections[week] = exposed_now
        return infections
