# 网易音乐人分享任务工具

网易音乐人分享任务自动分享工具，支持多用户、定时执行、自动登录和日志管理功能。

## 功能特性

- ✅ **每日签到任务**：自动执行网易云音乐日常签到，获取经验值
- ✅ **音乐人签到任务**：自动获取并完成音乐人云豆签到任务
- ✅ **自动分享音乐**：定时自动分享随机（避免风控）歌曲到动态
- ✅ **自动删除动态**：分享后10s自动删除，避免打扰好友
- ✅ **多用户支持**：支持同时管理多个网易云音乐账号
- ✅ **智能登录**：优先使用缓存的 Cookie，失效后自动重新登录
- ✅ **任务分类执行**：每日任务每天执行，分享任务按间隔天数执行
- ✅ **执行记录管理**：Redis存储执行记录，精确控制任务执行频率
- ✅ **环境变量配置**：支持通过环境变量灵活配置执行参数
- ✅ **日志管理**：详细的日志记录，支持日志轮转和大小限制
- ✅ **Docker 部署**：提供 Docker 镜像和 Compose 配置，便于部署

## 技术栈

- Python 3.12
- Requests - HTTP 请求库
- PyCryptodome - 加密解密库
- Redis - 存储用户 Cookie 和任务信息
- APScheduler - 定时任务调度
- Docker - 容器化部署

## 依赖要求

- Python >= 3.8
- Redis 服务
- Node.js（推荐）：用于通过 `execjs` 执行 `checkToken.js` 生成 `checkToken`。如果缺少可用的 JS 运行时，音乐人相关接口可能返回 `301 用户未登陆`。
- Docker (可选，用于容器化部署)

## 安装步骤

### 1. 克隆项目

```bash
git clone <repository-url>
cd wyy-musician
```

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 2.1 （可选）使用 Playwright 网页登录一次，写入 Cookie 到 Redis

当接口登录容易触发 `301 用户未登陆/风控` 时，建议用 Playwright 先在网页端登录一次，让脚本复用网页 Cookie：

```bash
# 安装浏览器（只需一次）
python -m playwright install chromium

# 打开网易云音乐网页，手动扫码/登录，完成后自动写入 Cookie 到 Redis
python playwright_handle/login.py
```

### 3. 配置Redis

#### 环境变量配置

| 环境变量名 | 说明 | 默认值 | 示例 |
| --- | --- | --- | --- |
| REDIS_URL | Redis连接地址 | redis://localhost:6379/0 | redis://password@redis-server:6379/1 |
| SEND_TIME | 每天检测执行的时间 | 09:30 | 14:00 |
| EXECUTION_INTERVAL_DAYS | 任务执行间隔天数 | 7 | 14 |
| MAX_MONTHLY_SENDS | 每月最大发送次数限制 | 4 | 5 |

通过 `REDIS_URL` 环境变量配置Redis连接：

```bash
export REDIS_URL="redis://[password@]host:port/db"
```

例如：
```bash
export REDIS_URL="redis://localhost:6379/0"
export REDIS_URL="redis://mypassword@redis.example.com:6379/2"
```

### 4. 添加用户任务

在 Redis 中添加用户任务信息：

```bash
# 使用Redis客户端执行以下命令
HSET netease:music:task <task_key> '{"phone": "13800138000", "password": "password123"}'
```

- `<task_key>`：任务唯一标识
- `phone`：网易云音乐账号（手机号）
- `password`：网易云音乐密码

## 使用方法

### 直接运行

```bash
python main.py
```

### 任务执行配置

系统包含两类任务，执行逻辑不同：

### 每日任务（每天执行）
- 网易云音乐日常签到任务
- 音乐人云豆签到任务
- 每天在 `SEND_TIME` 指定的时间点自动执行

### 间隔任务（按设定间隔执行）
- 音乐人发布动态任务（分享音乐并删除）
- 每天在 `SEND_TIME + 5分钟` 时间点进行检测
- 只有当距离上次执行时间达到设定的间隔天数（默认7天）时，才会真正执行分享操作

可以通过以下环境变量进行配置：

```bash
# 设置每日任务执行时间（格式：HH:MM）
export SEND_TIME="09:30"

# 设置间隔任务的执行间隔天数
export EXECUTION_INTERVAL_DAYS="7"

# 设置每月最大发送次数限制
export MAX_MONTHLY_SENDS="4"

# 登录方式
export LOGIN_METHOD="playwright"
```

执行逻辑说明：
1. 每日任务：每天在 `SEND_TIME` 自动执行，无需检查间隔时间
2. 间隔任务：每天在 `SEND_TIME + 5分钟` 进行检测
   - 系统会检查每个用户距离上次执行分享任务的时间间隔
   - 如果间隔天数大于等于 `EXECUTION_INTERVAL_DAYS`，则执行分享任务
   - 任务执行成功后，更新用户的最后执行时间记录到Redis
3. 执行记录存储在Redis中，键名为 `netease:music:data`，确保数据持久化

## Docker 部署

### 构建镜像

```bash
docker build -t netease-musician-task:latest .
```

### 使用 Docker Compose

```bash
docker-compose up -d
```

### 直接使用 Docker 命令

如果不使用 Docker Compose，也可以直接使用 `docker run` 命令启动容器：

```bash
docker run -d --name netease-musician-task -e TZ=Asia/Shanghai -e REDIS_URL="redis://localhost:6379/0" -e SEND_TIME="09:30" -e EXECUTION_INTERVAL_DAYS="7" -e MAX_MONTHLY_SENDS="4" -e LOGIN_METHOD="playwright" --restart always netease-musician-task:latest
```


## 日志与数据存储

### 日志文件
日志文件位于项目根目录的 `log` 文件夹下：

- `log/netease_music_cron.log` - 定时任务调度日志
- `log/netease_music.log` - 核心功能执行日志

### 执行记录
任务执行记录存储在Redis中，键名为 `netease:music:data`，包含每个用户的最后执行时间信息。

## 项目结构

```
wyy-musician/
├── core.py              # 核心功能模块
├── main.py              # 定时任务入口
├── Dockerfile           # Docker镜像构建文件
├── docker-compose.yml   # Docker Compose配置
├── requirements.txt     # 项目依赖
├── checkToken.js        # CheckToken生成脚本
├── log/                 # 日志文件夹
│   ├── netease_music_cron.log
│   └── netease_music.log
└── README.md            # 项目说明文档
```

## 注意事项

1. **Cookie 有效期**：网易云音乐的 Cookie 通常有一定的有效期，工具会自动检测并重新登录
2. **网络环境**：确保服务器网络可以访问网易云音乐 API 和 Redis 服务
3. **账号安全**：请妥善保管 Redis 中的账号信息和密码
4. **数据持久化**：执行记录存储在Redis中，确保数据持久化；使用 Docker 部署时，建议挂载 `log` 目录保存日志
5. **执行频率**：默认每7天执行一次，每月最多执行4次（取决于月份天数）


## 许可证

MIT License

## 更新日志
- v1.3.3
  - 优化docker构建，提升构建效率和缓存利用率
  - 修改二次验证方式为原设备扫码验证，优化登录流程

- v1.3.2
  - Dockerfile把加上Playwright浏览器安装命令

- v1.3.1
  - 添加playwright获取音乐人任务方式，避免userMissionId获取失败
  
- v1.3.0
  - 添加playwright登录、分享方式，避免出现“安全验证分享异常”
  - 添加任务执行失败重试机制，提高任务成功率

- v1.2.3
  - 新增任务执行失败重试机制，最多重试3次，提高任务成功率
  - 创建统一的配置文件 config.py，集中管理所有配置项
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
