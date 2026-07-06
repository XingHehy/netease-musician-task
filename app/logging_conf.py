"""统一日志：文件轮转 + 控制台。供全 app 复用。"""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler

from app.config import LOG_DIR

logger = logging.getLogger("netease_app")

if not logger.handlers:
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    fh = RotatingFileHandler(
        os.path.join(LOG_DIR, "app.log"),
        maxBytes=10 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(sh)
