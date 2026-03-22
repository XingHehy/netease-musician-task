# 网易云音乐 Playwright 登录调试说明

本文说明 `playwright_handle/login.py` 中 **`browser_login`** 在异常或可疑状态下的调试输出（截图、日志关键字），便于排查风控、滑块、二次验证与 Cookie 问题。

## 截图保存位置

- **根目录**：截图始终落在**项目根目录**下的 `debug/` 中，**不会**写到 `playwright_handle/` 包内（与当前终端工作目录无关，由 `login.py` 内 `_PROJECT_ROOT` 解析）。
- **子目录**：按账号生成子文件夹 `debug/{手机号净化}/`。
  - 手机号仅保留数字与 `+`，其余字符替换为 `_`；若结果为空则使用 `unknown`。

## 文件命名

- 格式：`YYYYMMDD_HHMMSS_{tag}.png`
- `tag` 由场景决定，其中的非法文件名字符会替换为 `_`，长度截断至约 80 字符。
- 截图为 **整页**（`full_page=True`）。

成功写入后，应用日志中会出现：`[登录调试] 已保存截图：<绝对或相对路径>`。

## 场景与 `tag` 对照

| `tag`（或前缀） | 触发条件 |
| --- | --- |
| `network_risk` / `network_risk_{where}` | 页面出现「您当前的网络环境存在安全风险」；随后会抛出 `NeteaseLoginNetworkRiskError` |
| `no_captcha` | 点击登录后等待易盾滑块弹窗超时，判定为未触发验证码并跳过滑块流程时 |
| `slider_failed` | 滑块在最大重试次数内仍未验证成功 |
| `slider_exception` | `solve_slider_captcha` 外层捕获到非风控类异常 |
| `login_flow_error` | `do_login_with_phone`（点击协议、输入账号、点登录等）抛错 |
| `secondary_verify_timeout` | 检测到二次验证弹窗后，等待用户操作超时（仍会继续尝试读 Cookie） |
| `secondary_verify_error` | 二次验证检测/等待逻辑内部异常 |
| `no_login_cookie` | 轮询结束仍未获得 `MUSIC_U` 或 `__csrf` 等登录态 Cookie |

## 日志关键字（配合截图排查）

- `[登录风控]`：网络环境安全风险，需换 IP / 网络或关闭异常代理。
- `[滑块]`：易盾滑块识别与拖动过程。
- `[二次验证]`：登录安全验证弹窗、扫码链接等。
- `[登录调试]`：截图保存成功或失败。

## 异常类型

- **`NeteaseLoginNetworkRiskError`**：明确为风控文案触发，不会被滑块流程的泛型 `except` 吞掉；`browser_login` 会关闭浏览器上下文后向上抛出。

## Docker / 持久化

容器内默认同样写入 `/app/debug/...`（根目录下的 `debug`）。若需在宿主机查看，请挂载卷，例如：

```yaml
volumes:
  - ./debug:/app/debug
```

## 清理建议

`debug/` 仅用于排错，可定期手动删除或加入 `.gitignore`（若尚未忽略），避免将带敏感页面信息的截图提交到版本库。
