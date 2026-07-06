"""
活跃浏览器进程注册表：记录当前正在运行的浏览器任务（PID + 账号），
新任务启动前可强制结束旧的，避免持久化 profile 冲突。

Playwright 同步对象线程绑定，无法跨线程 close()，因此抢占靠杀进程树实现。
"""

from __future__ import annotations

import os
import signal
import threading
from typing import Optional

from app.event_bus import bus
from app.logging_conf import logger


class _Active:
    __slots__ = ("pid", "account_id", "label")

    def __init__(self, pid: int, account_id: Optional[int], label: str):
        self.pid = pid
        self.account_id = account_id
        self.label = label


_lock = threading.Lock()
_active: Optional[_Active] = None


def _kill_tree(pid: int) -> None:
    """杀掉进程及其所有子进程（浏览器会 fork 多个渲染进程）。"""
    try:
        import psutil

        parent = psutil.Process(pid)
        procs = parent.children(recursive=True) + [parent]
        for p in procs:
            try:
                p.kill()
            except Exception:
                pass
        psutil.wait_procs(procs, timeout=5)
        return
    except Exception as e:  # noqa: BLE001
        logger.warning(f"psutil 结束进程树失败，改用系统命令兜底：{e}")

    # 兜底：平台原生命令
    try:
        if os.name == "nt":
            os.system(f"taskkill /F /T /PID {pid} >NUL 2>&1")
        else:
            os.kill(pid, signal.SIGKILL)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"兜底结束进程 {pid} 失败：{e}")


def preempt_existing() -> None:
    """
    若存在活跃浏览器，强制结束它，为新任务让路。
    在提交新浏览器任务前调用。
    """
    global _active
    with _lock:
        cur = _active
    if cur is None:
        return

    tip = f"账号 {cur.account_id}" if cur.account_id is not None else "上一个任务"
    msg = f"检测到已有浏览器正在运行（{tip}·{cur.label}），强制关闭以避免冲突"
    logger.warning(msg)
    bus.log(cur.account_id, msg, level="warn")
    _kill_tree(cur.pid)
    bus.log(cur.account_id, "已强制关闭上一个浏览器进程", level="warn")

    with _lock:
        if _active is cur:
            _active = None


def register(pid: int, account_id: Optional[int], label: str = "") -> None:
    global _active
    with _lock:
        _active = _Active(pid, account_id, label)
    logger.info(f"登记活跃浏览器 pid={pid} account={account_id} {label}")


def unregister(pid: int) -> None:
    global _active
    with _lock:
        if _active is not None and _active.pid == pid:
            _active = None


def active_account_id() -> Optional[int]:
    """返回当前正在运行浏览器的账号 id，无则 None。"""
    with _lock:
        return _active.account_id if _active is not None else None


def active_info() -> Optional[dict]:
    with _lock:
        if _active is None:
            return None
        return {"account_id": _active.account_id, "label": _active.label, "pid": _active.pid}


def force_stop(account_id: Optional[int] = None) -> bool:
    """
    强制结束当前活跃浏览器。若指定 account_id，仅当匹配时才结束。
    返回是否执行了结束操作。
    """
    global _active
    with _lock:
        cur = _active
    if cur is None:
        return False
    if account_id is not None and cur.account_id != account_id:
        return False

    tip = f"账号 {cur.account_id}" if cur.account_id is not None else "任务"
    msg = f"手动强制停止（{tip}·{cur.label}）"
    logger.warning(msg)
    bus.log(cur.account_id, msg, level="warn")
    _kill_tree(cur.pid)
    bus.log(cur.account_id, "已强制停止浏览器进程", level="warn")
    bus.status(cur.account_id, "stopped", "已强制停止")
    with _lock:
        if _active is cur:
            _active = None
    return True
