# 推送通知配置说明

本项目支持两种推送通知方式：**自定义 Webhook**（优先）和**企业微信机器人**（兜底）。

---

## 配置优先级

```
CUSTOM_WEBHOOK_URL 已填写 → 推送到自定义 Webhook
CUSTOM_WEBHOOK_URL 未填写，WECOM_WEBHOOK_KEY 已填写 → 推送到企业微信
两者均未填写 → 不推送
```

---

## 一、企业微信机器人

| 环境变量 | 说明 |
| --- | --- |
| `WECOM_WEBHOOK_KEY` | 企业微信自定义机器人的 `key`（Webhook URL 中 `?key=` 后面的部分） |

消息格式为纯文本，标题 + 正文拼接后发送。

---

## 二、自定义 Webhook

适用于 Bark、飞书、钉钉、ntfy、自建服务等任意 HTTP 接口。

| 环境变量 | 说明 | 默认值 |
| --- | --- | --- |
| `CUSTOM_WEBHOOK_URL` | Webhook 完整地址 | 空 |
| `CUSTOM_WEBHOOK_METHOD` | 请求方法（`GET` / `POST` / `PUT` 等） | `POST` |
| `CUSTOM_WEBHOOK_HEADERS` | 请求头，支持 JSON 对象或 `Key: value;Key2: value2` 格式 | 空 |
| `CUSTOM_WEBHOOK_BODY` | 请求体模板（JSON 字符串），`${title}` 和 `${content}` 会被自动替换 | 空 |

### 请求体说明

**未配置 `CUSTOM_WEBHOOK_BODY`** 时，默认发送以下 JSON：

```json
{
  "event": "notification",
  "title": "网易音乐人任务",
  "content": "推送内容",
  "timestamp": "2026-06-16T10:00:00"
}
```

**配置 `CUSTOM_WEBHOOK_BODY`** 后，使用模板渲染（`${title}` 替换为标题，`${content}` 替换为正文）：

```
CUSTOM_WEBHOOK_BODY={"title":"${title}","body":"${content}"}
```

渲染后若为合法 JSON，以 `application/json` 发送；否则以原始字符串发送。

---

## 三、适配示例

### Bark（iOS 推送）

Bark 官方推荐 POST JSON 方式：

```env
CUSTOM_WEBHOOK_URL=https://api.day.app/your_device_key
CUSTOM_WEBHOOK_METHOD=POST
CUSTOM_WEBHOOK_BODY={"title":"${title}","body":"${content}","group":"netease"}
```

也可以将 key 放进 body，URL 使用 `/push` 路径：

```env
CUSTOM_WEBHOOK_URL=https://api.day.app/push
CUSTOM_WEBHOOK_BODY={"title":"${title}","body":"${content}","device_key":"your_device_key"}
```

> Bark 字段说明：`body` 为消息正文（对应 `${content}`），可附加 `sound`、`badge`、`icon`、`url` 等字段，详见 [Bark 文档](https://bark.day.app)。

### 飞书机器人

```env
CUSTOM_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/your_token
CUSTOM_WEBHOOK_BODY={"msg_type":"text","content":{"text":"${title}\n${content}"}}
```

### 钉钉机器人

```env
CUSTOM_WEBHOOK_URL=https://oapi.dingtalk.com/robot/send?access_token=your_token
CUSTOM_WEBHOOK_BODY={"msgtype":"text","text":{"content":"${title}\n${content}"}}
```

### ntfy

```env
CUSTOM_WEBHOOK_URL=https://ntfy.sh/your_topic
CUSTOM_WEBHOOK_METHOD=POST
CUSTOM_WEBHOOK_HEADERS={"Title":"${title}"}
CUSTOM_WEBHOOK_BODY=${content}
```

> ntfy 的 Title 在 Header 中传递，body 直接是纯文本，`CUSTOM_WEBHOOK_BODY` 填非 JSON 的纯文本也可以正常发送。

### PushPlus（微信推送）

```env
CUSTOM_WEBHOOK_URL=https://www.pushplus.plus/send
CUSTOM_WEBHOOK_METHOD=POST
CUSTOM_WEBHOOK_BODY={"token":"your_token","title":"${title}","content":"${content}","template":"txt"}
```

> `template` 支持 `txt`（纯文本）、`html`、`markdown` 等，建议用 `txt` 避免 HTML 转义。其他可选参数（`channel`、`option` 等）直接追加到 JSON 模板里即可。

---

## 四、触发场景

| 场景 | 说明 |
| --- | --- |
| 登录二次验证（扫码） | 生成扫码链接后立即推送，需尽快扫码 |
| Cookie 即将过期 | 到期前 `COOKIE_NOTIFY_BEFORE_DAYS` 天推送提醒 |
| 任务执行结果 | 每次任务完成后汇总日志推送 |
