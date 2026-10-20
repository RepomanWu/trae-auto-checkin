# -*- coding: utf-8 -*-
"""入口：python -m trae_panel（或 uvicorn trae_panel.web:app）。"""

from __future__ import annotations

import uvicorn

from .config import load_config


def main() -> None:
    cfg = load_config()
    listen = cfg.get("listen") or {}
    host = listen.get("host", "0.0.0.0")
    port = int(listen.get("port", 7860))
    uvicorn.run("trae_panel.web:app", host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
