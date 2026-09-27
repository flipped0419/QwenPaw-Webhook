# QwenPaw Webhook 0.1.0

面向 QwenPaw 2.2.2b4 的三合一 Webhook 插件：

1. **入站 Webhook**：外部服务 `POST` 到 QwenPaw，触发 Agent。
2. **`send_webhook` Tool**：Agent 按需主动调用预配置 Webhook。
3. **Webhook Channel**：Cron / 主动消息可以直接选择 `webhook` 频道投递；默认把最终文本 `NO_REPLY` 静默丢弃。

## 安装

```bash
qwenpaw plugin install /path/to/qwenpaw-webhook-v0.1.0.zip
qwenpaw plugin list
```

插件按当前 QwenPaw `2.2.2b4` 插件 API 编写，声明兼容范围 `>=2.2.2b4,<2.3.0`。

## 1. 配置目标

不要把真实 Token、签名密钥或带密钥的 Webhook URL 写进插件源码。推荐把目标映射放在运行时文件里，并用环境变量引用敏感值。

复制 `targets.example.json` 到你自己的运行时位置，例如：

```bash
/data/qwenpaw/webhook_targets.json
```

并设置：

```bash
QWENPAW_WEBHOOK_TARGETS_FILE=/data/qwenpaw/webhook_targets.json
NOTIFY_WEBHOOK_URL=https://example.invalid/webhook
NOTIFY_WEBHOOK_TOKEN=replace-me
```

目标文件支持：

- `method`: `POST` / `PUT` / `PATCH`
- `format`: `json` / `form` / `text`
- `headers`: 自定义请求头
- `payload`: 模板
- `timeout`: 1~120 秒
- `verify_tls`: 是否校验证书，默认 `true`

模板变量：

- `{{title}}`
- `{{content}}`
- `{{data}}`：JSON 模式中可直接嵌入对象
- `{{data_json}}`：把 data 序列化为 JSON 字符串

环境变量写法 `${ENV_NAME}` 会在发送时展开。

## 2. Agent Tool：send_webhook

安装后在 Agent 工具设置里启用 `send_webhook`。

模型可调用：

```text
send_webhook(
  target="notify",
  title="北部湾大学监测",
  content="发现新的研究生招生通知",
  data={"url":"https://example.invalid/notice/123"}
)
```

默认不允许模型传任意 URL，只允许管理员在 targets 文件中定义的别名。需要时可以在工具设置中显式开启 `allow_raw_url`，但不建议。

### 配合静默 Cron

这是最适合条件通知的方式：

```text
Cron silent = true

没有更新：正常结束，不调用 send_webhook
有更新：调用 send_webhook(target="notify", ...)，然后正常结束
```

Cron 的最终回复会被静默，但工具调用产生的 Webhook 不受影响。

## 3. Webhook Channel

在 QwenPaw 频道设置中启用 `Webhook`。

常用字段：

- `Default target alias`: 默认目标，例如 `notify`
- `Targets file`: 可留空，使用 `QWENPAW_WEBHOOK_TARGETS_FILE`
- `Allow raw URLs`: 默认关闭
- `Suppress NO_REPLY`: 默认开启
- `Request timeout`: 默认 15 秒

Cron 若直接选择 Webhook Channel，可以把目标用户/目标句柄填写为目标别名：

```text
notify
```

也接受：

```text
webhook:notify
```

当 Agent 最终文本恰好是：

```text
NO_REPLY
```

Webhook Channel 会直接丢弃，不发 HTTP 请求；这正好可以用于“没更新就不通知”。

## 4. 入站 Webhook

先在 Webhook Channel 中开媯 `Enable inbound webhook`，并设置一个足够长的 `Inbound secret`；也可以使用环境变量：

```bash
QWENPAW_WEBHOOK_INBOUND_SECRET=replace-with-a-long-random-secret
```

接口：

```text
POST /api/webhook/inbound/{source}
```

例如：

```bash
curl -X POST 'https://qwenpaw.example/api/webhook/inbound/uptime-kuma' \
  -H 'Content-Type: application/json' \
  -H 'X-Webhook-Secret: replace-with-a-long-random-secret' \
  -d '{
    "text":"网站 example.com 已离线",
    "sender_id":"uptime-kuma",
    "session_id":"monitor:example.com"
  }'
```

返回 `202` 只表示已经加入 Agent 队列，不等待模型处理完成。

支持字段：

- `text` / `content` / `message`: 输入文本，优先按这个顺序读取
- `sender_id` / `user_id`: 可选
- `session_id`: 可选；不传则自动生成 `webhook:{source}:{sender}`
- `reply_target`: 可选；若提供，Agent 的最终回复会发往这个目标别名

若不提供 `reply_target`，入站事件默认**不自动把 Agent 最终回复发送出去**；Agent 仍可自行调用 `send_webhook`。

可以在频道设置 `Allowed sources` 中用逗号限制来源名，例如：

```text
uptime-kuma,github,beszel
```

## QwenPaw 自身启用了登录认证时

插件 HTTP Router 位于 `/api` 下，因此会同时经过 QwenPaw 自己的认证中间件。

这种情况下：

- `Authorization: Bearer <QwenPaw API token>` 用于通过 QwenPaw 认证；
- `X-Webhook-Secret: <Webhook secret>` 用于通过本插件认证。

如果第三方 Webhook 平台无法同时发送这些 Header，建议在反向代理上为固定入口安全地补充 QwenPaw 认证头，而不是关闭整个 QwenPaw 的认证。

## 健康检查

```text
GET /api/webhook/health
```

只返回正在运行的 Webhook Channel 实例数量和 Agent ID，不返回 Webhook URL、Header 或 Token。

## 安全设计

- 默认仅允许管理员预配置的 target alias；模型不能随意访问 URL。
- 不在日志中记录目标 URL 或 Header。
- 入站 Webhook 默认关闭。
- 入站启用吞必须通过共享密钥认证。
- 请求体有限制，默认 256 KiB。
- 不跟随 HTTP 30x 重定向。
- URL 中禁止 `user:password@host` 形式的凭据。
- 敏感信息应通过环境变量或 QwenPaw 运行时 Secret 配置提供，不要提交到源码库。

## 0.1.0 当前边界

第一版已经实现三个核心能力，但暂未加入：HMAC 时间戳签名验证、失败重试队列、Webhook 投递历史 UI、附件二进制上传。这些可以在实际跑通后按需要继续加。
