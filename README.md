# WechatClaw

微信 AI 机器人项目。当前代码处于 **Phase 0.5：本地 AI backend MVP**。

当前阶段只做本地后端验证：

- FastAPI 后端
- OpenAI SDK 调用 LLM
- `user_id` 级别的本地记忆
- `memory.json` 持久化
- Swagger `/docs` 测试

暂时不做企业微信、webhook、PDF、数据库、Docker、上云、前端、登录、支付、服务号。

## 安装

进入项目目录：

```powershell
cd D:\WechatClaw
```

安装依赖：

```powershell
python -m pip install -r requirements.txt
```

如果 `python` 命令不存在，需要先安装 Python，并重新打开 PowerShell。

## 配置 .env

复制示例配置：

```powershell
Copy-Item .env.example .env
```

编辑 `.env`：

```powershell
notepad .env
```

OpenAI 配置示例：

```env
OPENAI_API_KEY=your_openai_api_key_here
MODEL_NAME=gpt-4o-mini
MAX_HISTORY_MESSAGES=20
SYSTEM_PROMPT=你是一个运行在微信里的 AI 助手，回答要简洁、有帮助。
```

DeepSeek 配置示例：

```env
OPENAI_API_KEY=your_deepseek_api_key_here
OPENAI_BASE_URL=https://api.deepseek.com
MODEL_NAME=deepseek-chat
MAX_HISTORY_MESSAGES=20
SYSTEM_PROMPT=你是一个运行在微信里的 AI 助手，回答要简洁、有帮助。
```

`.env` 是本地密钥文件，不要提交到 Git。

## 运行

```powershell
python -m uvicorn main:app --reload
```

打开 Swagger：

```text
http://127.0.0.1:8000/docs
```

## 测试 /health

浏览器打开：

```text
http://127.0.0.1:8000/health
```

预期返回：

```json
{
  "status": "ok"
}
```

PowerShell 测试：

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:8000/health
```

## 测试 /chat

在 Swagger 里打开 `POST /chat`，点击 `Try it out`，输入：

```json
{
  "user_id": "leo",
  "message": "你好，我叫 Leo，我在开发 WechatClaw。"
}
```

再发一次：

```json
{
  "user_id": "leo",
  "message": "我是谁？我在做什么项目？"
}
```

预期：AI 能根据 `leo` 的历史记忆回答。

PowerShell 测试：

```powershell
$body = @{
  user_id = "leo"
  message = "你好，我叫 Leo，我在开发 WechatClaw。"
} | ConvertTo-Json

Invoke-RestMethod `
  -Uri http://127.0.0.1:8000/chat `
  -Method Post `
  -ContentType "application/json" `
  -Body $body
```

## 测试多用户记忆隔离

先给 `leo` 写入记忆：

```json
{
  "user_id": "leo",
  "message": "我叫 Leo，我在开发 WechatClaw。"
}
```

再用另一个用户提问：

```json
{
  "user_id": "alice",
  "message": "我是谁？我在做什么项目？"
}
```

预期：`alice` 不应该知道 `leo` 的信息。

查看用户记忆长度：

```text
GET http://127.0.0.1:8000/memory/leo
GET http://127.0.0.1:8000/memory/alice
```

返回示例：

```json
{
  "user_id": "leo",
  "history_length": 4
}
```

## 清除用户记忆

Swagger 里调用：

```text
DELETE /memory/{user_id}
```

例如：

```text
DELETE http://127.0.0.1:8000/memory/leo
```

预期返回：

```json
{
  "deleted": true,
  "user_id": "leo"
}
```

PowerShell 测试：

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:8000/memory/leo -Method Delete
```

## 当前 API

### GET /health

健康检查。

### POST /chat

请求：

```json
{
  "user_id": "leo",
  "message": "你好"
}
```

返回：

```json
{
  "user_id": "leo",
  "answer": "你好！有什么我可以帮你的吗？",
  "history_length": 2
}
```

### GET /memory/{user_id}

查看某个用户的记忆长度。

### DELETE /memory/{user_id}

清除某个用户的记忆。

## 项目方向

长期目标是微信 Bot 租用平台。用户在微信里付费，拿到一个属于自己的 Bot，通过企业微信或后续服务号能力跟 Bot 对话。

后续阶段才考虑：

- 企业微信消息接入
- PDF 总结
- 多用户正式存储
- 服务号
- 支付
- 上云部署
