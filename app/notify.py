"""
通知：企业微信 / 自定义 Webhook。配置从 settings 表读取（运行期可改）。
从原 wecom_notify.py 迁移。
"""

from __future__ import annotations

import json
from datetime import datetime

import requests

from app.logging_conf import logger
from app.repository import get_setting


def _truncate(content: str, limit: int = 3800) -> str:
    if content is None:
        return ""
    content = str(content)
    if len(content) <= limit:
        return content
    return f"{content[: max(0, limit - 900)]}\n\n...(内容过长已截断)...\n\n{content[-800:]}"


def send_wecom_webhook(webhook_key: str, content: str, *, title: str | None = None, timeout: int = 10) -> bool:
    if not webhook_key:
        return False
    url = f"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={webhook_key}"
    text = f"{title or '网易云运行日志'}\n\n{_truncate(content or '')}".strip()
    try:
        resp = requests.post(url, json={"msgtype": "text", "text": {"content": text}}, timeout=timeout)
        if resp.status_code != 200:
            return False
        data = resp.json() if resp.content else {}
        return isinstance(data, dict) and data.get("errcode", 0) == 0
    except Exception:
        return False


def _parse_headers(headers_text: str) -> dict[str, str]:
    if not headers_text:
        return {}
    try:
        h = json.loads(headers_text)
        if isinstance(h, dict):
            return {str(k): str(v) for k, v in h.items()}
    except Exception:
        pass
    out: dict[str, str] = {}
    for item in headers_text.split(";"):
        if ":" in item:
            k, v = item.split(":", 1)
            if k.strip():
                out[k.strip()] = v.strip()
    return out


def send_custom_webhook(
    webhook_url: str,
    content: str,
    *,
    title: str | None = None,
    timeout: int = 10,
    event: str = "notification",
    extra: dict | None = None,
) -> bool:
    if not webhook_url:
        return False
    title_str = title or "网易音乐人任务"
    content_str = content or ""
    payload = {
        "event": event,
        "title": title_str,
        "content": content_str,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    }
    if extra:
        payload["extra"] = extra

    method = (get_setting("custom_webhook_method", "POST") or "POST").upper()
    headers = _parse_headers(get_setting("custom_webhook_headers", "") or "")
    headers.setdefault("Content-Type", "application/json")

    body_tpl = get_setting("custom_webhook_body", "") or ""
    if body_tpl:
        rendered = body_tpl.replace("${title}", title_str).replace("${content}", content_str)
        try:
            body_data = json.loads(rendered)
        except Exception:
            body_data = rendered
    else:
        body_data = payload

    try:
        if method == "GET":
            resp = requests.get(
                webhook_url,
                params=body_data if isinstance(body_data, dict) else payload,
                headers=headers,
                timeout=timeout,
            )
        else:
            kwargs = {"json": body_data} if isinstance(body_data, dict) else {"data": body_data.encode()}
            resp = requests.request(method, webhook_url, headers=headers, timeout=timeout, **kwargs)
        return 200 <= resp.status_code < 300
    except Exception:
        return False


def send_configured_notification(
    content: str,
    *,
    title: str | None = None,
    timeout: int = 10,
    event: str = "notification",
    extra: dict | None = None,
) -> bool:
    """优先自定义 Webhook，其次企业微信。配置从 settings 表读。"""
    custom_url = get_setting("custom_webhook_url", "") or ""
    wecom_key = get_setting("wecom_webhook_key", "") or ""
    try:
        if custom_url:
            return send_custom_webhook(custom_url, content, title=title, timeout=timeout, event=event, extra=extra)
        if wecom_key:
            return send_wecom_webhook(wecom_key, content, title=title, timeout=timeout)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"发送通知失败：{e}")
    return False
