# 网易音乐人任务工具 2.0

> **v2 · 完全浏览器自动化**
> 彻底放弃 API 加密请求模式，所有登录与任务（签到、发布动态、VIP 领取）全部通过 Playwright 浏览器同源操作完成，从根本上规避 `301 用户未登陆` 等风控问题。新增 Web 管理界面，可视化登录、账号增删改、按账号定时执行，数据存 SQLite。

## 相比 1.x 的变化

| | 1.x（旧版） | 2.0（本版） |
|---|---|---|
| 任务执行 | requests 调 weapi 接口（易触发 301 风控） | 全程浏览器同源操作 |
| 数据存储 | Redis | SQLite（`app/data/app.db`） |
| 账号管理 | 手动写 Redis key | Web 界面增删改 |
| 登录 | 脚本 / 接口 | Web 可视化，扫码验证二维码直接显示在页面 |
| 配置 | 环境变量 + `config.py` | Web「全局设置」，保存即生效 |
| 入口 | `python main.py` | `uvicorn app.main:app`（Web 服务） |

## 快速开始

### Docker（推荐）

```bash
docker compose up -d --build
```

然后浏览器打开 `http://localhost:8000`。容器内默认无头运行浏览器。

`docker-compose.yml` 已把 `./app/data` 挂载出来持久化（SQLite + 浏览器 profile + 日志）。

### 本地运行

```bash
pip install -r requirements.txt
python -m playwright install chromium

# 启动 Web 服务
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

打开 `http://localhost:8000`：
1. 点「新增账号」，填手机号 + 密码，保存后自动启动浏览器登录
2. 登录过程（含易盾滑块自动识别、二次验证扫码二维码）实时显示在弹窗里
3. 登录成功后即可「执行」任务或等待定时触发

> 本地调试想看真实浏览器窗口：在「全局设置」里关闭「无头模式」，或设环境变量 `PLAYWRIGHT_HEADLESS=0`。

## 功能特性

- ✅ **完全浏览器自动化**：签到 / 发布动态 / VIP 领取全部走浏览器，规避接口风控
- ✅ **Web 管理界面**：账号增删改、手动执行、实时日志、扫码验证，移动端自适应
- ✅ **可视化登录**：易盾滑块自动识别（`ddddocr` + OpenCV），二次验证扫码二维码直接显示
- ✅ **定时执行**：每账号可单独设运行时间，缺省用全局时间；到点自动在**同一浏览器会话**内完成签到 +（按判断）间隔任务
- ✅ **智能间隔任务**：VIP 可领取日自动领 VIP，否则按间隔天数发布动态（受每月上限约束）
- ✅ **抢占式单浏览器**：同一时刻只跑一个浏览器，启动新任务自动强制关闭旧的，避免 profile 冲突；支持手动强制停止
- ✅ **多账号隔离**：每账号独立持久化 profile 目录，登录态复用
- ✅ **通知提醒**：企业微信 / 自定义 Webhook，用于扫码提醒与任务结果
- ✅ **配置即时生效**：所有全局设置在 Web 保存后立刻生效，无需重启
- ✅ **调试截图**：登录异常/风控/滑块失败时自动截图存档到 `数据目录/debug/{手机号}/`

## 配置项

全部可在 Web「全局设置」中修改（保存即生效）。也可用环境变量设初始默认值：

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `APP_HOST` | `0.0.0.0` | Web 服务监听地址 |
| `APP_PORT` | `8000` | Web 服务端口 |
| `PLAYWRIGHT_HEADLESS` | `1` | 无头模式；本地想看窗口设 `0` |
| `PLAYWRIGHT_DEBUG_SCREENSHOT` | `1` | 登录异常/风控时截图存档到数据目录 `debug/{手机号}/` |
| `SEND_TIME` | `09:30` | 每日任务默认执行时间（HH:MM） |
| `EXECUTION_INTERVAL_DAYS` | `3` | 发布动态的间隔天数 |
| `MAX_MONTHLY_SENDS` | `4` | 每月最大发布次数 |
| `WECOM_WEBHOOK_KEY` | 空 | 企业微信机器人 Key |
| `CUSTOM_WEBHOOK_URL` | 空 | 自定义 Webhook 地址（优先于企业微信） |
| `APP_DATA_DIR` | `app/data` | 数据目录（SQLite / profile / 日志） |

## 目录结构

```
app/
├── main.py            # FastAPI 入口
├── config.py          # 启动配置 + settings 表播种默认值
├── db.py              # SQLite 建表
├── repository.py      # 账号 / 日志 / 设置 CRUD
├── runner.py          # 任务编排与频率判断
├── scheduler.py       # 按账号定时调度
├── notify.py          # 企业微信 / 自定义 Webhook
├── event_bus.py       # 实时日志 → WebSocket
├── browser/
│   ├── manager.py     # 浏览器生命周期 + 抢占
│   ├── registry.py    # 活跃浏览器登记 / 强制停止
│   ├── login.py       # 登录 + 滑块 + 二次验证
│   ├── tasks.py       # 签到 / 发布动态 / VIP
│   └── selectors.py   # URL / 选择器常量
├── api/               # accounts / login / tasks / settings / ws 路由
├── web/               # 前端（HTML + 原生 JS/CSS）
└── data/              # 运行期数据（SQLite / profile / 日志），已 gitignore
```

## 相关文档

- 功能预览：[`docs/PREVIEW.md`](./docs/PREVIEW.md)
- 通知配置：[`docs/NOTIFY.md`](./docs/NOTIFY.md)
- 登录调试：[`docs/DEBUG_DOCS.md`](./docs/DEBUG_DOCS.md)

## 更新日志

### v2.0.1
- 完全浏览器自动化重构：签到 / 发布动态 / VIP 领取全部走浏览器同源操作，彻底解决 `301 用户未登陆` 风控
- 新增 FastAPI Web 管理界面：账号增删改、可视化登录、实时日志、扫码验证、移动端自适应
- 存储由 Redis 换成 SQLite（`app/data/app.db`），不再依赖外部服务
- 定时执行：每账号可单独设运行时间；到点在同一浏览器会话内完成签到 +（按判断）间隔任务
- 抢占式单浏览器 + 手动强制停止，避免 profile 冲突
- 全局配置改为 Web 保存即生效
- 登录异常/风控自动截图存档

### [v1.x](https://github.com/XingHehy/netease-musician-task/tree/1.x)（旧版归档）
基于 requests + weapi 加密接口 + Redis 的实现：脚本式登录、任务通过接口调用、账号手动写 Redis key、配置靠环境变量。因接口模式易触发风控（签到 `301`），2.0 起改为完全浏览器自动化。旧版代码见 [`1.x` 分支](https://github.com/XingHehy/netease-musician-task/tree/1.x)。

## 友情链接

- [LINUX DO 社区](https://linux.do)
- [Docker Hub 镜像仓库](https://hub.docker.com/r/xinghehy/netease-musician-task)

## 免责声明

本项目仅供学习交流，请勿用于商业用途。使用者需自行承担因使用本工具产生的风险与责任。
