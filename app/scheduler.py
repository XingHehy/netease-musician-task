"""
调度器：为每个启用的账号注册每日任务 job（run_time 或全局默认时间）。
账号增删改后调用 reschedule_all() 重排。
"""

from __future__ import annotations

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app import repository as repo
from app.logging_conf import logger
from app.runner import run_daily_for_account

scheduler = BackgroundScheduler(timezone="Asia/Shanghai")


def _parse_hhmm(s: str, default: str = "09:30") -> tuple[int, int]:
    try:
        h, m = (s or default).split(":")
        return int(h), int(m)
    except Exception:
        h, m = default.split(":")
        return int(h), int(m)


def _job_id(account_id: int) -> str:
    return f"daily_account_{account_id}"


def reschedule_all() -> None:
    """清空并按当前账号配置重建所有 job。"""
    for job in scheduler.get_jobs():
        job.remove()

    default_time = repo.get_setting("default_send_time", "09:30") or "09:30"
    for acc in repo.list_accounts():
        if not acc["enabled"]:
            continue
        run_time = acc["run_time"] or default_time
        h, m = _parse_hhmm(run_time, default_time)
        scheduler.add_job(
            run_daily_for_account,
            trigger=CronTrigger(hour=h, minute=m),
            args=[acc["id"]],
            id=_job_id(acc["id"]),
            replace_existing=True,
            misfire_grace_time=3600,
        )
        logger.info(f"已排程账号 {acc['phone']} 每日任务：{run_time}")


def start() -> None:
    if not scheduler.running:
        scheduler.start()
        logger.info("调度器已启动")
    reschedule_all()
