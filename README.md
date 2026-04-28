# 网易音乐人分享任务工具

网易音乐人分享任务自动分享工具，支持多用户、定时执行、自动登录、日志管理和通知提醒等功能。

👉 **想快速了解能做什么？请查看功能预览：[`docs/PREVIEW.md`](./docs/PREVIEW.md)**

## 最近更新 / 新功能概览

- **企业微信 Webhook 通知**：任务执行结果可推送到企业微信，实现异常告警与结果提醒
- **VIP 自动领取**：支持自动领取音乐人永久 VIP（自动完成任务后）
- **登录与分享增强**：
  - Playwright 网页端登录与分享，减少风控与安全验证异常
  - **自动更新cookie**：每次运行，会自动更新 Cookie
  - 支持复用网页 Cookie，降低 `301 用户未登陆`、分享异常概率
  - 登录流程支持易盾滑块（`ddddocr`）、网络风控文案识别；失败时可在项目根目录 `debug/{手机号}/` 查看截图
- **任务可靠性提升**：
  - 任务失败自动重试（最多多次尝试）以提高成功率
  - 统一配置文件 `config.py` 集中管理配置项，执行逻辑更加清晰可控

## 功能特性

- ✅ **每日签到任务**：自动执行网易云音乐日常签到，获取经验值
- ✅ **音乐人签到任务**：自动获取并完成音乐人云豆签到任务
- ✅ **自动分享音乐**：定时自动分享随机（避免风控）歌曲到动态
- ✅ **自动删除动态**：分享后约 10s 自动删除，避免打扰好友
- ✅ **多用户支持**：支持同时管理多个网易云音乐账号
- ✅ **智能登录**：优先使用缓存的 Cookie，失效后自动重新登录
- ✅ **任务分类执行**：每日任务每天执行，分享任务按间隔天数执行
- ✅ **执行记录管理**：Redis 存储执行记录，精确控制任务执行频率
- ✅ **环境变量配置**：支持通过环境变量灵活配置执行参数
- ✅ **日志管理**：详细的日志记录，支持日志轮转和大小限制
- ✅ **Docker 部署**：提供 Docker 镜像和 Compose 配置，便于部署
- ✅ **VIP 自动领取**：自动完成 VIP 相关权益的领取操作
- ✅ **企业微信通知**：通过企业微信 Webhook 推送任务执行结果和异常告警
- ✅ **任务失败重试机制**：任务失败时自动按策略重试，提高成功率
- ✅ **Playwright 支持**：基于 Playwright 的网页登录、音乐人任务与分享，降低接口风控风险

## 技术栈

- Python 3.12（推荐与 Docker 一致；最低建议 3.10+）
- Requests、PyCryptodome
- Redis（Cookie、任务数据、执行记录）
- APScheduler（定时调度）
- Playwright + Chromium（网页登录与部分页面能力）
- ddddocr（易盾滑块辅助识别）
- pyexecjs + Node.js（`checkToken.js`）
- Docker（可选）

## 依赖要求

- **Python**：建议 3.12，需安装 `requirements.txt`
- **Redis**：必须，用于任务与登录态
- **Node.js**：推荐安装；用于通过 `execjs` 执行 `checkToken.js` 生成 `checkToken`。若缺少可用的 JS 运行时，音乐人相关接口可能返回 `301 用户未登陆`。
- **Playwright 浏览器**：使用 `LOGIN_METHOD=playwright` 或运行 `playwright_handle/login.py` 前需执行：`python -m playwright install chromium`
- **Docker**（可选）：容器化部署

## 安装步骤

### 1. 克隆项目

```bash
git clone <repository-url>
cd netease-musician-task
```

### 2. 安装 Python 依赖

```bash
pip install -r requirements.txt
```

### 3.（推荐）安装 Playwright 浏览器
`API版基本上已无法使用 `

仅在需要网页登录 / `LOGIN_METHOD=playwright` 时需要：

```bash
python -m playwright install chromium
```

### 4. 配置 Redis

见下文 [环境变量说明](#环境变量说明)。通过 `REDIS_URL` 连接你的 Redis 实例。

### 5. 添加用户任务

在 Redis 的哈希表 `netease:music:task` 中为每个任务写入账号信息，例如：

```bash
HSET netease:music:task <task_key> '{"phone": "13800138000", "password": "your_password"}'
```

- `<task_key>`：任务唯一标识（自定义字符串）
- `phone`：网易云登录账号（手机号）
- `password`：密码（Playwright 与 API 登录均可能用到）

---

## 环境变量说明

配置集中在 `config.py`，以下为常用环境变量（默认值以代码为准）。

| 环境变量 | 说明 | 默认值 |
| --- | --- | --- |
| `REDIS_URL` | Redis 连接地址 | `redis://localhost:6379/5` |
| `SEND_TIME` | 每日调度触发时间（`HH:MM`） | `09:30` |
| `EXECUTION_INTERVAL_DAYS` | 分享类间隔任务的最小间隔天数 | `3` |
| `MAX_MONTHLY_SENDS` | 每月分享次数上限 | `4` |
| `LOGIN_METHOD` | 登录方式：`api`（接口） / `playwright`（网页 Cookie） | `playwright` |
| `PLAYWRIGHT_PROFILE_BASEDIR` | Playwright 用户数据目录（持久化登录态） | `.playwright_profiles` |
| `PLAYWRIGHT_PROFILE_PER_USER` | 是否按账号分子目录（建议 `1`，避免多账号串 Cookie） | `1` |
| `WECOM_WEBHOOK_KEY` | 企业微信机器人 Webhook 的 `key`，留空则不推送 | 空 |

示例：

```bash
export REDIS_URL="redis://localhost:6379/5"
export SEND_TIME="09:30"
export EXECUTION_INTERVAL_DAYS="7"
export MAX_MONTHLY_SENDS="4"
export LOGIN_METHOD="playwright"  # 推荐：API版基本上已无法使用 
export WECOM_WEBHOOK_KEY="your-wecom-webhook-key"
```

---

## Playwright 网页登录说明

在接口登录易触发风控、或音乐人接口频繁 `301` 时，建议使用 **`LOGIN_METHOD=playwright`**，由浏览器完成登录并写入 Redis Cookie（约 7 天过期，失效后会自动再走登录流程）。

### 独立运行登录脚本（写入 Redis）

在项目**根目录**下执行（保证能正确找到 `core` 等模块；若从其他目录运行需配置 `PYTHONPATH`）：

```bash
python playwright_handle/login.py
```

按提示输入手机号与密码。脚本会：

1. 打开网易云登录页，自动完成「其他登录模式 → 协议 → 手机号登录 → 密码登录 → 输入账号密码 → 登录」等步骤  
2. 如出现 **易盾滑块**，会尝试自动拖动；失败时日志会有 `[滑块]` 相关说明  
3. 如出现 **「登录安全验证」**，会尝试生成「原设备扫码」类链接并打日志，部分步骤需人工在手机上完成  
4. 若页面提示 **「您当前的网络环境存在安全风险」**，脚本会识别并终止，需更换网络 / 代理环境后再试  
5. 登录失败、未触发验证码、滑块失败、二次验证超时等情况，会在项目根目录 **`debug/{手机号}/`** 下保存带时间戳的 PNG 截图，便于排查（各场景 `tag` 与日志关键字见 [docs/DEBUG_DOCS.md](./docs/DEBUG_DOCS.md)）。

登录成功后会尝试识别 `uid` 并将 Cookie 写入 Redis（键名形如 `netease:music:user:{uid}:cookie`）。

### 与主程序集成

`main.py` / `core.py` 在 `LOGIN_METHOD=playwright` 时会调用同一套 `browser_login` 逻辑；`PLAYWRIGHT_PROFILE_BASEDIR` 与 `PLAYWRIGHT_PROFILE_PER_USER` 控制浏览器用户数据目录，多账号务必保持 **每用户独立 profile**（默认已开启）。

---

## 使用方法

### 直接运行

在项目根目录：

```bash
python main.py
```

### 任务调度逻辑简述

1. **每日任务**（每天在 `SEND_TIME` 执行）：网易云日常签到、音乐人云豆签到等  
2. **间隔任务**（每天在 `SEND_TIME` 延后约 5 分钟检测）：音乐人分享动态等；仅当距上次成功执行已满 `EXECUTION_INTERVAL_DAYS` 天且未超过 `MAX_MONTHLY_SENDS` 等限制时才会真正分享  

执行记录与部分状态保存在 Redis 键 `netease:music:data` 等（详见下文）。

---

## 故障排查

| 现象 | 建议 |
| --- | --- |
| `301 用户未登陆` | 尝试 `LOGIN_METHOD=playwright`；确认 Node 可用、`checkToken.js` 正常；重新执行登录脚本或等待 Cookie 刷新 |
| 网页登录提示网络安全风险 | 更换 IP / 关闭可疑代理，稍后重试；查看 `debug/{手机号}/` 下截图 |
| 滑块反复失败 | 查看同目录截图与日志中的 `[滑块]`；确认本机网络可加载验证码图片 |
| Docker 内登录态丢失 | 检查是否挂载了 Playwright profile 目录，且 `PLAYWRIGHT_PROFILE_BASEDIR` 与挂载路径一致（见 [Docker 部署](#docker-部署)） |

---

## Docker 部署

### 构建镜像

```bash
docker build -t netease-musician-task:latest .
```

### Docker Compose

```bash
docker-compose up -d
```

默认 `docker-compose.yml` 将宿主机的 `./log`、`./playwright_profiles` 挂载到容器内。镜像工作目录为 `/app`，若使用默认 `PLAYWRIGHT_PROFILE_BASEDIR=.playwright_profiles`，数据在容器内**未**挂载到上述卷。为持久化浏览器登录态，建议在 Compose 中增加环境变量，使目录与卷一致，例如：

```yaml
# 推荐：API版基本上已无法使用 
environment:
  - PLAYWRIGHT_PROFILE_BASEDIR=playwright_profiles
```

（与 `volumes` 里的 `/app/playwright_profiles` 对应。）

可选：挂载调试截图目录，便于宿主机查看：

```yaml
volumes:
  - ./debug:/app/debug
```

### docker run 示例

```bash
docker run -d --name netease-musician-task \
  -e TZ=Asia/Shanghai \
  -e REDIS_URL="redis://host.docker.internal:6379/0" \
  -e SEND_TIME="09:30" \
  -e EXECUTION_INTERVAL_DAYS="7" \
  -e MAX_MONTHLY_SENDS="5" \
  -e LOGIN_METHOD="playwright" \    # 推荐：API版基本上已无法使用 
  -e PLAYWRIGHT_PROFILE_BASEDIR="playwright_profiles" \
  -e WECOM_WEBHOOK_KEY="your-wecom-webhook-key" \
  -v "$(pwd)/log:/app/log" \
  -v "$(pwd)/playwright_profiles:/app/playwright_profiles" \
  -v "$(pwd)/debug:/app/debug" \
  --restart always \
  netease-musician-task:latest
```

---

## 日志与本地目录

| 路径 | 说明 |
| --- | --- |
| `log/netease_music_cron.log` | 定时调度相关日志 |
| `log/netease_music.log` | 核心业务日志 |
| `debug/{手机号}/` | Playwright 登录失败等场景的页面截图（**项目根目录**，非 `playwright_handle` 下） |
| `.playwright_profiles/` | 默认 Playwright 用户数据目录（可通过 `PLAYWRIGHT_PROFILE_BASEDIR` 修改；建议加入 `.gitignore`） |

---

## Redis 键说明（摘要）

| 键 | 用途 |
| --- | --- |
| `netease:music:task` | 哈希表，`task_key` → 用户 JSON（含 `phone`、`password` 等） |
| `netease:music:data` | 任务执行间隔、上次执行时间等 |
| `netease:music:user:{uid}:cookie` | 用户登录 Cookie（带过期时间） |
| `netease:music:user:{uid}:userdata` | 用户资料缓存 |

---

## 项目结构

```
netease-musician-task/
├── main.py                 # 定时任务入口
├── core.py                 # 登录、任务、API 封装
├── config.py               # 环境变量与 Redis 初始化
├── checkToken.js           # checkToken 生成（需 Node/execjs）
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── playwright_handle/
│   ├── login.py            # Playwright 登录（滑块、二次验证、调试截图）
│   ├── musician.py         # 音乐人相关 Playwright 能力
│   └── friend.py           # 分享等 Playwright 能力
├── docs/
│   └── PREVIEW.md          # 功能预览
├── log/                    # 运行日志（自动创建）
├── debug/                  # 登录调试截图（按需生成）
└── README.md
```

---

## 注意事项

1. **Cookie 有效期**：网页登录写入的 Cookie 在 Redis 中约 7 天过期，失效后程序会尝试重新登录。  
2. **网络环境**：需能访问网易云音乐相关域名；异常风控时优先检查 IP / 代理。  
3. **账号安全**：密码存放在 Redis 任务数据中，请做好 Redis 访问控制与备份策略。  
4. **工作目录**：建议在项目根目录运行 `python main.py`，以便日志、`debug`、`profile` 路径与预期一致。  
5. **执行频率**：分享任务受 `EXECUTION_INTERVAL_DAYS` 与 `MAX_MONTHLY_SENDS` 共同约束，请合理设置避免风控。

---

## 许可证

MIT License

---

## 更新日志
- v1.4.3
  - 添加Cookie自动更新功能(每次执行任务后，更新最新的Cookie) -> 测试功能

- v1.4.2
  - 修复黑胶VIP自动领取功能(具体是否可用等下个月才能确认)

- v1.4.1  
  - 增强网易云音乐登录调试能力（失败截图、风控识别等），详见 [docs/DEBUG_DOCS.md](./docs/DEBUG_DOCS.md)。

- v1.4.0  
  - 添加企业微信 Webhook 通知功能  

- v1.3.5  
  - 优化 VIP 自动领取功能逻辑  

- v1.3.4  
  - 添加 VIP 自动领取功能支持  

- v1.3.3  
  - 优化 Docker 构建，提升构建效率和缓存利用率  
  - 修改二次验证方式为原设备扫码验证，优化登录流程  

- v1.3.2  
  - Dockerfile 增加 Playwright 浏览器安装步骤  

- v1.3.1  
  - 添加 Playwright 获取音乐人任务方式，避免 `userMissionId` 获取失败  

- v1.3.0  
  - 添加 Playwright 登录、分享方式，避免出现「安全验证分享异常」  
  - 添加任务执行失败重试机制，提高任务成功率  

- v1.2.3  
  - 新增任务执行失败重试机制，最多重试 3 次，提高任务成功率  
  - 创建统一的配置文件 `config.py`，集中管理所有配置项  
  - 修复预计下次执行时间计算逻辑，正确处理时间已过的情况  
  - 修复分钟数溢出问题，正确处理跨小时的时间计算  

- v1.2.0  
  - 新增每日签到任务功能，自动执行网易云音乐日常签到  
  - 新增音乐人签到任务功能，自动获取并完成音乐人云豆签到  
  - 任务系统重构，分离每日任务和间隔执行的分享任务  
  - 优化任务执行逻辑，提高任务稳定性和可靠性  

- v1.1.0  
  - 新增基于间隔天数的执行逻辑，每天定时检测  
  - 添加执行记录存储功能  
  - 优化环境变量配置，支持更多自定义参数  
  - 完善日志记录和数据持久化  

- v1.0.0  
  - 初始版本  
  - 支持多用户自动分享和删除动态  
  - 支持定时任务和 Docker 部署  
  - 实现日志管理和大小限制  

## 友情链接
 - [LINUX DO 社区](https://linux.do)

