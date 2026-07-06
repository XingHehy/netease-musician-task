# 登录调试说明（2.0）

本文说明 `app/browser/login.py` 中 **`login_account`** 在异常或风控状态下的调试输出（截图、日志关键字），便于排查风控、滑块、二次验证与 Cookie 问题。

## 开关

由环境变量 `PLAYWRIGHT_DEBUG_SCREENSHOT` 控制（默认 `1` 开启，设 `0` 关闭）。开启后，登录流程遇到异常/风控会自动整页截图存档。

## 截图保存位置

- **数据目录下的 `debug/`**：默认 `app/data/debug/`（由 `APP_DATA_DIR` / `APP_DEBUG_DIR` 决定）。
- **按账号分子目录**：`debug/{手机号净化}/`。
  - 手机号仅保留数字与 `+`，其余字符替换为 `_`；结果为空则用 `unknown`。

## 文件命名

- 格式：`YYYYMMDD_HHMMSS_{tag}.png`，整页截图（`full_page=True`）。
- `tag` 由场景决定，非法字符替换为 `_`。

保存成功后，Web 实时日志与文件日志中会出现：`[调试] 已保存截图：<路径>`。

## 场景与 `tag` 对照

| `tag` | 触发条件 |
| --- | --- |
| `login_flow_error` | `do_login_with_phone`（点协议、输账号、点登录等）抛错 |
| `network_risk_slider` | 滑块阶段出现「您当前的网络环境存在安全风险」 |
| `slider_exception` | `solve_slider` 捕获到非风控类异常 |
| `network_risk` | 登录重试结束后检测到网络环境安全风险 |
| `no_login_cookie` | 轮询结束仍未获得 `MUSIC_U` / `__csrf` 等登录态 Cookie |

## 日志关键字（配合截图排查）

- `[登录风控]`：网络环境安全风险，需换 IP / 网络或关闭异常代理。
- `[滑块]`：易盾滑块识别与拖动过程。
- `[二次验证]`：登录安全验证弹窗、扫码链接等。
- `[调试]`：截图保存成功或失败。

所有这些日志都会通过 WebSocket 实时推送到 Web 界面的运行弹窗，也写入 `数据目录/log/app.log`。

## Docker / 持久化

容器内数据目录 `app/data`（含 `debug/`）已通过 `docker-compose.yml` 挂载到宿主机：

```yaml
volumes:
  - ./app/data:/app/app/data
```

## 清理建议

`debug/` 截图可能含敏感页面信息，且已随 `app/data/` 一起被 `.gitignore` 忽略，不会提交到版本库。可定期手动清理。
