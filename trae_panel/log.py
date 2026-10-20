# -*- coding: utf-8 -*-
"""面板运行日志：内存环形缓冲 + 落盘，供面板「运行日志」视图读取。"""

from __future__ import annotations

import collections
import threading
from datetime import datetime

from .config import LOG_FILE, ensure_data_dir

_MAX = 500
_buf = collections.deque(maxlen=_MAX)
_lock = threading.Lock()


def log(msg: str, channel: str = "system") -> None:
    line = "[{}] [{}] {}".format(datetime.now().strftime("%Y-%m-%d %H:%M:%S"), channel, msg)
    with _lock:
        _buf.append({"ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                     "channel": channel, "text": msg})
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass
    try:
        print(line)
    except Exception:
        pass


def recent(limit: int = 200, channel: str = "") -> list:
    with _lock:
        items = list(_buf)
    if channel:
        items = [x for x in items if x["channel"] == channel]
    return items[-limit:]
