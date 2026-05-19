# WechatClaw

微信 Bot 租用平台。用户在微信里付费 → 拿到一个属于自己的 Bot（数据持久化），通过企业微信跟 Bot 对话。

## 三个核心部分

**服务号**
- 付费、管理 Bot（暂停、续费、获取 code）
- 用 OpenID 对应到账号信息

**企业微信（外部联系人）**
- 用户发 code 连接 Bot
- 连接一次后一直启用

**后台**
- 接收企业微信消息
- 根据 external_userid 找到绑定的 Bot
- 转发消息

## 用户流程

### 租用
1. 服务号点"租用 Bot"，付费
2. 后台创建 Bot 实例，生成 code（5 分钟有效）
3. 服务号推送 code + 企业微信二维码
4. 用户扫码加企业微信好友
5. 用户发"绑定 ABC123"，后台建立 external_userid → Bot 映射，code 作废

### 日常使用
1. 用户在企业微信发消息
2. 后台收到回调，立即返回 200 OK（避免 5 秒超时）
3. 任务进队列，worker 用 external_userid 查到对应 Bot
4. 调 Bot 处理，结果用主动发消息 API 推回用户

## 身份体系

| 身份 | 来源 | 作用 |
|------|------|------|
| OpenID | 服务号 | 账户管理（付费、订阅、Bot 控制） |
| external_userid | 企业微信 | 实际跟 Bot 对话 |
| Code | 后台生成 | 一次性桥梁，把两个身份绑定到同一个 user_id |

## Bot 实现

Bot 不是独立程序，是后台代码里的一个模块。"连接 Bot"本质上是数据库里一条 `external_userid → bot_instance_id` 的记录，收到消息时调用对应处理函数。

### Phase 1：PDF 总结
- 用户上传 PDF → 调企业微信 API 用 media_id 下载文件 → 提取文本 → 调 LLM → 返回总结
- 每个 PDF 独立处理，不保留上下文

### Phase 2：图片处理（裁剪、合并、打包等）

## LLM 调用层（DeepSeek）

- **统一 Key**：平台持有一个或几个 Key，所有用户共用。后台按 external_userid 记录用量
- **上下文自管**：DeepSeek API 无状态，上下文由后台维护并随请求一起发送
- **PDF 总结场景**：每次独立调用，无需存上下文
- **速率限制应对**：异步队列 + 指数退避重试（429 时 1s、2s、4s...）+ 并发数限制
- **多 Key 池**（可选）：扩容用，注册多个账号轮询

## 部署

- **Phase 1**：本地跑 + ngrok 暴露端口给微信回调
- **Phase 2**：迁移到云服务器，正式域名 + HTTPS
- 同一套代码，环境变量切换配置

## MVP 阶段

| 阶段 | 目标 | 支付 | 部署 |
|------|------|------|------|
| Phase 1 | 技术验证：跑通 PDF 总结全链路 | 手动发 code | 本地 + ngrok |
| Phase 2 | 用户 MVP：增加图片处理 + 引导优化 | 手动审核 | 云端 |
| Phase 3 | 商业化：接 WeChat Pay + 企业认证 | 自动 | 云端 |

## 数据表（草稿）

- users（user_id, openid, external_userid）
- bot_instances（bot_id, user_id, type, status, data_path）
- bindings（external_userid, bot_id, status）
- codes（code, user_id, expires_at, used）
- subscriptions（user_id, plan, expires_at）

## 风险

- 企业微信外部联系人 API 在未认证企业下的权限边界（需查官方文档确认）
- DeepSeek 速率限制是动态的，没有公开数字，靠队列 + 重试 + 多 Key 池兜底
- Bot 实例资源占用：未来用户量大需要"按需启动"或共享 runtime
- 商业化阶段的合规问题（用户数据、隐私协议、企业认证）
