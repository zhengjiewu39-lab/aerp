from __future__ import annotations

import os

# 测试中同步执行实验，避免后台任务使用默认引擎。
os.environ["AERP_SYNC_TASKS"] = "true"
os.environ["AERP_AUTO_SEED"] = "false"
