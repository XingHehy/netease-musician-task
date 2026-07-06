"""
事件总线：浏览器 worker 线程产生的事件（日志行、二维码、状态变更）
通过它广播给所有 WebSocket 连接。

线程模型：
- FastAPI 事件循环在主线程；浏览器 worker 在独立线程。
- worker 线程调用 publish()（同步），内部用 run_coroutine_threadsafe 投递到事件循环。
- 每个 WebSocket 连接持有一个 asyncio.Queue，广播即向所有队列 put。
"""

from __future__ import annotations

import asyncio
import json
import threading
from collections import defaultdict, deque
from datetime import datetime
from typing import Any, Optional

# 每个账号保留的最大日志条数（累积，不随查看清空）
_MAX_BUFFER = 500


class EventBus:
    def __init__(self) -> None:
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._subscribers: set[asyncio.Queue] = set()
        # 每账号累积日志缓冲（供「查看」时回看历史）
        self._buffers: dict[int, deque] = defaultdict(lambda: deque(maxlen=_MAX_BUFFER))
        # 每账号当前最新二维码（登录扫码用），完成后清除
        self._qr: dict[int, dict] = {}
        self._buf_lock = threading.Lock()

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """在 FastAPI 启动时调用，记录主事件循环。"""
        self._loop = loop

    # ---- 订阅端（WebSocket 侧，在事件循环内调用）----
    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    # ---- 日志缓冲读取（供 HTTP「查看」拉取历史）----
    def get_buffer(self, account_id: int) -> list[dict]:
        with self._buf_lock:
            return list(self._buffers.get(account_id, []))

    def get_qr(self, account_id: int) -> Optional[dict]:
        with self._buf_lock:
            return self._qr.get(account_id)

    def clear_buffer(self, account_id: int) -> None:
        with self._buf_lock:
            self._buffers.pop(account_id, None)
            self._qr.pop(account_id, None)

    # ---- 发布端（可在任意线程调用）----
    def publish(self, event_type: str, payload: dict[str, Any]) -> None:
        msg = {
            "type": event_type,
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            **payload,
        }
        # 记录到对应账号的缓冲区（log / qrcode / status 都留痕）
        acc = payload.get("account_id")
        if acc is not None:
            with self._buf_lock:
                self._buffers[acc].append(msg)
                if event_type == "qrcode":
                    self._qr[acc] = msg
                elif event_type == "status" and payload.get("status") == "login_ok":
                    self._qr.pop(acc, None)

        data = json.dumps(msg, ensure_ascii=False)
        loop = self._loop
        if loop is None:
            return
        # 从任意线程安全地投递到事件循环
        try:
            loop.call_soon_threadsafe(self._broadcast, data)
        except RuntimeError:
            pass

    def _broadcast(self, data: str) -> None:
        for q in list(self._subscribers):
            try:
                q.put_nowait(data)
            except asyncio.QueueFull:
                pass

    # ---- 便捷方法 ----
    def log(self, account_id: Optional[int], line: str, level: str = "info") -> None:
        self.publish("log", {"account_id": account_id, "level": level, "line": line})

    def qrcode(self, account_id: Optional[int], qr_url: str, tip: str = "") -> None:
        self.publish("qrcode", {"account_id": account_id, "qr_url": qr_url, "tip": tip})

    def status(self, account_id: Optional[int], status: str, detail: str = "") -> None:
        self.publish("status", {"account_id": account_id, "status": status, "detail": detail})


# 全局单例
bus = EventBus()
