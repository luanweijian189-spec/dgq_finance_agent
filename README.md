# dgq_finance_agent

## 产品需求
- 痛点：收集荐股消息后容易遗忘，无法长期追踪并评估信息源质量。
- 目标：自动抓取微信推荐、维护股票池、每日评估股票质量与荐股人可靠性，并提供人工干预接口与 Agent 交互。

## 已实现（部署版）
本项目已升级为可部署版本，覆盖你提出的 5 个缺口：

1. 微信自动抓取预留接口
   - 提供 `POST /api/connectors/wechat/webhook`，可直接接 Wechaty/OpenClaw 转发消息。
   - 提供 `WeChatConnector` 抽象与 `WechatyConnectorPlaceholder`，可无侵入替换真实连接器。
2. PostgreSQL 持久化与迁移
   - SQLAlchemy ORM：股票、荐股人、推荐记录、每日表现、告警订阅。
   - Alembic 初始迁移：`alembic/versions/20260301_0001_init.py`。
3. 每日定时评估与外部数据源抽象
   - APScheduler 定时任务按 cron 执行每日评估 + RRS 刷新 + 日报推送。
   - `MarketDataProvider` / `NewsDataProvider` 抽象，内置 mock 实现，可切换 Tushare/Baostock。
4. Web 管理后台与人工操作
   - FastAPI + Jinja 页面：`GET /` 展示指标、荐股人、人工录入。
   - 股票池跟踪视图增加：股票代码、股票名称、荐股人、首次接收时间、状态、推荐逻辑、最新评估结果。
   - 每日迭代表：按天记录评分、收益、当日分析与逻辑，支持持续追踪。
5. 告警/订阅推送链路
   - `/alert on 代码` 指令与 `POST /api/alerts/subscribe` API。
   - 评分与收益触发风险告警，支持 stdout / webhook 推送。
   - 新增 QQ 通知 MVP，可通过 NapCat / go-cqhttp 风格 HTTP API 接入群或私聊推送。

6. 真实数据源可切换
   - 行情源支持 `baostock`（默认推荐）
   - 新闻逻辑验证支持 `mock / tushare / webhook / sites`（站点白名单聚合校验）
   - 提供系统可用性检查接口：`GET /api/system/check`

7. 官方 QQ 通知通道
   - 支持通过 OpenClaw 已配置的 QQ channel 发送主动通知。
   - 适用于“定频刷新 → 发现增量 → 主动推送到 QQ”的官方接入方式。

## 架构

```
app/
├── main.py          # FastAPI 应用与路由
├── services.py      # 核心业务编排（入库、评估、RRS、告警、日报）
├── models.py        # PostgreSQL ORM 模型
├── database.py      # DB 连接与会话
├── scheduler.py     # APScheduler 每日任务
├── providers.py     # 行情/新闻/微信连接器抽象与 mock
├── notifier.py      # 告警通知（stdout/webhook）
├── agent.py         # OpenClaw 指令处理
└── templates/
    └── dashboard.html
```

## 快速开始（本地）

### 1) 准备环境变量

```bash
cp .env.example .env
```

本地直接运行时，默认使用：

- `DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/dgq_finance_agent`

默认已配置 `MARKET_DATA_PROVIDER=baostock`，如需 mock 可手动改回。

### 2) 安装依赖

```bash
python -m pip install -r requirements.txt
```

### 3) 启动 PostgreSQL（推荐 Docker）

```bash
docker compose up -d db
```

### 4) 执行数据库迁移

```bash
alembic upgrade head
```

### 5) 启动服务

```bash
python run.py
```

- API 文档：`http://localhost:8000/docs`
- Web 管理后台：`http://localhost:8000/`

## 一键部署（Docker Compose）

```bash
cp .env.example .env
docker compose up --build
```

应用容器内部会自动把 `DATABASE_URL` 覆盖为 `db:5432`，并执行 `alembic upgrade head` 后启动 API。

## 核心 API

- `POST /api/messages/ingest`：写入微信/文本消息并解析推荐。
- `POST /api/messages/import_text`：批量导入复制文本（支持自由文本/JSON/CSV）。
- `POST /api/research/ingest`：录入资讯类文本（RAG资料，不直接当荐股）。
- `POST /api/recommendations/manual`：人工录入推荐。
- `POST /api/evaluations/daily`：手工写入当日评估结果。
- `POST /api/evaluations/run`：触发全量每日评估任务。
- `POST /api/reports/daily`：生成当日股票池追踪文件（`YYYY-MM-DD.md`）。
- `POST /api/commands`：执行 Agent 指令（`/status /who /top /worst /add /alert on`）。
- `POST /api/alerts/subscribe`：订阅股票告警。
- `POST /api/connectors/wechat/webhook`：Wechaty/OpenClaw 转发入口。
- `POST /api/connectors/openclaw/webhook`：OpenClaw / QQ 双向通信入口，支持入库与命令回执。
- `POST /api/connectors/qq/webhook`：与 OpenClaw webhook 等价的 QQ 别名入口。
- `POST /api/connectors/dingtalk/webhook`：钉钉 Stream relay / HTTP 回调兼容入口。
- `GET /api/dev/agent-matrix/roles`：查看代码开发 agent 矩阵角色定义。
- `POST /api/dev/agent-matrix/tasks`：创建代码任务，可选择立即分发到本地 loop 或外部 API。
- `POST /api/dev/agent-matrix/tasks/{task_id}/dispatch`：分发指定代码任务。
- `POST /api/dev/agent-matrix/tasks/{task_id}/summary`：汇总指定代码任务状态。
- `GET /api/dev/repo-ops/policy`：查看当前代码库自动化策略与 provider。
- `POST /api/dev/repo-ops/tasks`：创建 repo-ops 任务，可自动生成计划并转交外部 API。
- `POST /api/dev/repo-ops/tasks/{task_id}/plan`：生成代码库执行计划。
- `POST /api/dev/repo-ops/tasks/{task_id}/execute`：触发 repo-ops 执行。
- `POST /api/dev/repo-ops/tasks/{task_id}/approve`：人工审批 repo-ops 任务。
- `POST /api/dev/repo-ops/tasks/{task_id}/summary`：汇总 repo-ops 任务状态。
- `GET /api/system/check`：数据库 + 行情源 +新闻源可用性检查。
- `POST /api/news/scan`：执行新闻定时扫描（可手动触发）。
- `GET /api/news/candidates`：查看候选新股队列。
- `POST /api/news/candidates/promote`：将候选新股晋升到跟踪池。

## Agent 矩阵：让钉钉 / OpenClaw 驱动代码库

仓库现已内置一层“消息 -> 代码任务”的编排层，目标是把钉钉、OpenClaw 和后续外部 agent API 串起来。

默认矩阵包含 6 个角色：

1. 任务协调 agent
2. 方案规划 agent
3. 编码执行 agent
4. 代码评审 agent
5. 测试验证 agent
6. 交付运营 agent

### 你现在可以这样用

#### 1. 在钉钉 / OpenClaw 里发命令

- `/dev new 接入审批流回调并补测试`
- `/dev run am-xxxxxx`
- `/dev status`
- `/dev summary am-xxxxxx`
- `/dev matrix`

#### 2. 直接走 HTTP API

创建任务：

- `POST /api/dev/agent-matrix/tasks`

请求体示例：

```json
{
   "objective": "给回测模块增加策略版本比较接口",
   "context": "要求复用现有 FastAPI 与 SQLAlchemy 结构",
   "operator": "openclaw",
   "source": "dingtalk",
   "auto_dispatch": true,
   "auto_check": false
}
```

### provider 切换方式

通过环境变量控制：

- `AGENT_MATRIX_PROVIDER=local`：默认，本地生成 agent brief，并调用 [scripts/copilot_hybrid_loop.sh](scripts/copilot_hybrid_loop.sh)
- `AGENT_MATRIX_PROVIDER=http`：把任务转发到外部 agent API

当你后续把 OpenClaw + 自建 agent 矩阵服务接上后，只需要替换：

- `AGENT_MATRIX_HTTP_BASE_URL`
- `AGENT_MATRIX_HTTP_API_KEY`

现有钉钉 / OpenClaw 指令和后端 API 不需要再重写。

## Repo Ops：面向最终目标的下一层

为了让“钉钉 / OpenClaw -> agent矩阵 -> 自动管理代码库”真正落地，仓库中新增了一层 `repo-ops` 编排接口。

它和 `agent-matrix` 的分工是：

- `agent-matrix`：负责拆解任务、角色矩阵、调度状态
- `repo-ops`：负责代码库策略、仓库约束、审批与执行接口

### 现在可直接用的消息命令

- `/repo new <任务>`：创建代码库任务
- `/repo auto <任务>`：一键创建 agent + repo 双任务
- `/repo plan <task_id>`：生成仓库执行计划
- `/repo run <task_id>`：触发 repo-ops 执行
- `/repo approve <task_id> [备注]`：人工审批
- `/repo summary <task_id>`：汇总状态
- `/repo status [task_id]`：查看任务列表/详情
- `/repo policy`：查看当前安全策略

### provider 切换方式

- `REPO_OPS_PROVIDER=local`：默认，本地可在安全开关放行后执行“LLM 生成补丁 -> git apply -> 自动校验”
- `REPO_OPS_PROVIDER=http`：转发给外部高性能 agent API，真正执行代码修改和仓库治理

### 本地 autopilot（MVP）如何开启

先打开以下开关（建议在测试环境）：

- `REPO_OPS_REQUIRE_HUMAN_APPROVAL=true`（保留人工闸门）
- `REPO_OPS_ALLOW_GIT_WRITE=true`
- `REPO_OPS_ALLOW_SHELL=true`
- `REPO_OPS_LOCAL_AUTOPILOT_SCRIPT=scripts/repo_ops_autopilot.py`

然后走命令链路：

1. `/repo auto <任务描述>` 或 `/repo new <任务描述>`
2. `/repo plan <task_id>` 查看计划
3. `/repo approve <task_id> 允许执行`（如果启用人工审批）
4. `/repo run <task_id>` 触发本地自动改码与校验
5. `/repo summary <task_id>` 查看执行总结

说明：

- 本地 autopilot 会严格受 `REPO_OPS_ALLOWED_GLOBS`、`REPO_OPS_BLOCKED_GLOBS`、`REPO_OPS_MAX_FILES` 限制。
- 默认会对变更过的 Python 文件做 `py_compile`，并执行模型返回的 1 条关键验证命令。
- 若校验失败会自动回滚本轮补丁，不会把失败改动留在仓库里。

### 明天接入更强模型 API 时你要做的事

只需要补这两个配置：

- `REPO_OPS_HTTP_BASE_URL`
- `REPO_OPS_HTTP_API_KEY`

推荐同时把：

- `AGENT_MATRIX_PROVIDER=http`
- `REPO_OPS_PROVIDER=http`

这样钉钉 / OpenClaw 的消息入口不用变，系统就会把开发任务转发给外部 agent 编排服务。

详细设计见 [design_docs/agent_repo_autopilot.md](design_docs/agent_repo_autopilot.md)。

## 钉钉机器人（推荐主方案）

当前更推荐直接切到钉钉。原因很简单：钉钉 `Stream` 模式不需要公网 HTTPS 回调，适合个人/小团队快速把“群里 @机器人 + 主动推送”跑通。

当前仓库已补齐一版钉钉双向链路：

1. 钉钉开放平台创建企业内部应用机器人
2. 消息接收模式选择 `Stream` 模式
3. 运行仓库内置的 Stream relay，自动接收群里 `@机器人` 或单聊消息
4. relay 把消息转发到后端 `POST /api/connectors/dingtalk/webhook`
5. 后端复用现有荐股入库 / `/status` / `/who` / `/top` 等命令处理链路
6. relay 读取后端返回的 `reply_message`，直接回到当前钉钉会话
7. 定时告警/日报通过 [app/dingtalk_bot.py](app/dingtalk_bot.py) 主动发群消息

新增配置项：

- `DINGTALK_BOT_ENABLED=true`
- `DINGTALK_CLIENT_ID=你的钉钉应用Client ID`
- `DINGTALK_CLIENT_SECRET=你的钉钉应用Client Secret`
- `DINGTALK_ROBOT_CODE=你的机器人编码`
- `DINGTALK_OPEN_CONVERSATION_ID=`（主动发群消息时需要）
- `DINGTALK_STREAM_BACKEND_WEBHOOK_URL=http://127.0.0.1:8000/api/connectors/dingtalk/webhook`
- `DINGTALK_STREAM_SHARED_TOKEN=与你的 CONNECTOR_SHARED_TOKEN 一致`

新增文件：

- [app/dingtalk_bot.py](app/dingtalk_bot.py)
- [connectors/dingtalk_stream_relay/main.py](connectors/dingtalk_stream_relay/main.py)
- [scripts/start_dingtalk_stream_relay.sh](scripts/start_dingtalk_stream_relay.sh)
- [scripts/print_dingtalk_setup_info.sh](scripts/print_dingtalk_setup_info.sh)
- [scripts/smoke_dingtalk_webhook.sh](scripts/smoke_dingtalk_webhook.sh)
- [deploy/tencent/dingtalk-stream-relay.service](deploy/tencent/dingtalk-stream-relay.service)

最小启动方式：

1. 后端启动：`bash scripts/start_prod_server.sh`
2. relay 启动：`bash scripts/start_dingtalk_stream_relay.sh`
3. 群里 `@机器人 /status 002436`

主动通知目标 `openConversationId` 可通过先启动 relay、在群里 `@机器人` 发一条消息，然后从日志里的 `conversationId` 拿到。

## QQ 通知与双向通信

当前实现的是一版可插拔 QQ 通道：
1. 系统按 `SCHEDULER_INTRADAY_REFRESH_CRON` 定频刷新活跃股票池的盘中数据。
2. 每轮会比较刷新前后的维护快照。
3. 当涨跌幅变化超过 `SCHEDULER_INTRADAY_REFRESH_MIN_CHANGE_PERCENT` 时，自动生成摘要消息。
4. 消息会同时发往 stdout、通用 webhook，以及可选的 QQ 通道。

### 方案 A：官方 OpenClaw QQ 通道（仅出站，推荐保留）

如果你已经在腾讯云 / OpenClaw 环境里把 QQ channel 配好了，系统可以直接调用 OpenClaw CLI 发消息。

配置项：

- `OPENCLAW_NOTIFIER_ENABLED=true`
- `OPENCLAW_COMMAND=openclaw`
- `OPENCLAW_PROFILE=dev`
- `OPENCLAW_CHANNEL=qq`
- `OPENCLAW_RECIPIENT=`
- `OPENCLAW_TIMEOUT_SECONDS=30`

发送逻辑等价于：

```bash
openclaw --dev agent --channel qq --deliver -m '盘中刷新 10:35
本轮刷新 8 只，成功 8 只，显著变化 2 只。'
```

如果 OpenClaw 侧需要明确会话，可配置 `OPENCLAW_RECIPIENT`，系统会自动附加 `--to` 参数。

### 环境变量

- `SCHEDULER_INTRADAY_REFRESH_ENABLED=true`
- `SCHEDULER_INTRADAY_REFRESH_CRON=*/5 9-15 * * 1-5`
- `SCHEDULER_INTRADAY_REFRESH_LIMIT=12`
- `SCHEDULER_INTRADAY_REFRESH_MIN_CHANGE_PERCENT=0.8`

### 方案 B：QQ 开放平台官方 Bot（推荐做双向通信）

当前代码已补齐官方 QQ Bot 的最小闭环：

- 官方回调入口：`POST /api/connectors/qq/official/webhook`
- 回调校验：支持 `op=13` 的 `plain_token` 验证回包
- 回调验签：校验 `X-Signature-Ed25519` / `X-Signature-Timestamp`
- 事件支持：`GROUP_AT_MESSAGE_CREATE`、`C2C_MESSAGE_CREATE`
- 事件处理：复用现有荐股入库 / `/status`、`/who` 等命令处理链路
- 自动回信：后端拿到 `reply_message` 后，直接调用 QQ 官方 OpenAPI 回发

环境变量：

- `QQ_OFFICIAL_BOT_ENABLED=true`
- `QQ_OFFICIAL_BOT_APP_ID=你的AppID`
- `QQ_OFFICIAL_BOT_APP_SECRET=你的AppSecret`
- `QQ_OFFICIAL_BOT_API_BASE_URL=https://api.sgroup.qq.com`
- `QQ_OFFICIAL_BOT_TOKEN_URL=https://bots.qq.com/app/getAppAccessToken`
- `QQ_OFFICIAL_BOT_TARGET_TYPE=group`
- `QQ_OFFICIAL_BOT_TARGET_ID=`（仅主动通知时需要）

回调地址示例：

- `https://你的域名/api/connectors/qq/official/webhook`

临时验证 HTTPS（不买域名先验证链路）：

- 可先用基于公网 IP 的临时域名，例如 `https://58-87-91-152.sslip.io`
- 仓库已提供脚本：[scripts/setup_temp_https_sslip.sh](scripts/setup_temp_https_sslip.sh)
- 示例：`sudo LETSENCRYPT_EMAIL=you@example.com bash scripts/setup_temp_https_sslip.sh`
- 配置完成后，可先把 QQ 平台回调地址临时填为：`https://58-87-91-152.sslip.io/api/connectors/qq/official/webhook`
- 验证通过后，再替换成正式自有域名即可

联调脚本：

- 参数打印：[scripts/print_qq_official_bot_setup_info.sh](scripts/print_qq_official_bot_setup_info.sh)
- 冒烟脚本：[scripts/smoke_qq_official_webhook.sh](scripts/smoke_qq_official_webhook.sh)

### 方案 C：HTTP 中继 QQ Bot（保留兼容）

如果你暂时不走 OpenClaw，也仍可使用已有 HTTP 中继模式：

- `QQ_BOT_ENABLED=true`
- `QQ_BOT_BASE_URL=http://127.0.0.1:3000`
- `QQ_BOT_TARGET_TYPE=group`
- `QQ_BOT_TARGET_ID=123456789`
- `QQ_BOT_ACCESS_TOKEN=`

### 接入说明

- 出站通知优先建议用 OpenClaw 已配置好的 QQ channel。
- 双向通信优先建议用 QQ 开放平台官方 Bot 回调到 `POST /api/connectors/qq/official/webhook`。
- OpenClaw webhook 和 OneBot / NapCat / go-cqhttp 中继模式继续保留，作为兼容兜底。
- 若暂时不走官方回调，也仍可把 QQ / OpenClaw 收到的新消息回调到后端的 `POST /api/connectors/openclaw/webhook`。
- 该 webhook 会自动区分两类消息：
   - 普通文本：尝试识别荐股并入库；识别失败时按研究笔记归档。
   - 斜杠命令：如 `/who 张三`、`/status 002436`，会直接执行并返回 `reply_message`，供 OpenClaw 回发到 QQ。
- 生产环境建议配置 `CONNECTOR_SHARED_TOKEN`，并让 OpenClaw / OneBot 等兼容连接器通过 `X-Connector-Token` 请求头或 Bearer Token 传入。
- QQ 官方回调不依赖 `CONNECTOR_SHARED_TOKEN`，而是走平台签名校验。
- HTTP 请求体示例：

```json
{
   "group_id": 123456789,
   "message": "盘中刷新 10:35\n本轮刷新 8 只，成功 8 只，显著变化 2 只。"
}
```

如果你本地已经有 NapCat 或 go-cqhttp 风格 HTTP 服务，这版也可以继续接上。

## 腾讯云 + OpenClaw + QQ 推荐链路

当前代码库的现状可以概括为：

1. **出站通知已具备**：系统可通过 [app/notifier.py](app/notifier.py#L100-L138) 里的 `OpenClawNotifier` 或 `QQOfficialBotNotifier` 主动把盘中刷新、日报、告警发到 QQ。
2. **官方入站已补齐**：系统现已支持 [app/main.py](app/main.py#L1159-L1218) 的 QQ 官方回调，把群 / C2C 消息写入股票池，或把 `/status`、`/who` 这类命令直接回执给 QQ。
3. **兼容入站仍保留**：OpenClaw / QQ 兼容 webhook 仍可继续使用，见 [app/main.py](app/main.py#L1149-L1157)。
4. **生产配置默认未开启**：当前 [.env.example](.env.example#L66-L74) 中 `QQ_OFFICIAL_BOT_*` 默认都关闭，需要在腾讯云上显式填写。

推荐部署拓扑：

- 腾讯云主机运行本项目 FastAPI
- QQ 官方平台回调 -> `POST /api/connectors/qq/official/webhook`
- 后端返回 `reply_message` -> 调用 `https://api.sgroup.qq.com/v2/groups/{group_id}/messages` 或 `/v2/users/{user_id}/messages`
- 定时任务 / 风险告警 -> `QQOfficialBotNotifier` 或 `OpenClawNotifier` -> QQ
- 如需兼容历史链路，可继续保留 OpenClaw webhook / OneBot relay

建议至少开启这些环境变量：

```bash
QQ_OFFICIAL_BOT_ENABLED=true
QQ_OFFICIAL_BOT_APP_ID=请填写官方 AppID
QQ_OFFICIAL_BOT_APP_SECRET=请填写官方 AppSecret
QQ_OFFICIAL_BOT_API_BASE_URL=https://api.sgroup.qq.com
OPENCLAW_NOTIFIER_ENABLED=true
SCHEDULER_INTRADAY_REFRESH_ENABLED=true
```

仓库内已补齐可直接复用的生产模板：

- 腾讯云生产环境变量模板：[.env.tencent.example](.env.tencent.example)
- systemd 服务模板：[deploy/tencent/dgq-finance-agent.service](deploy/tencent/dgq-finance-agent.service)
- Nginx 反向代理模板：[deploy/tencent/nginx.dgq-finance-agent.conf](deploy/tencent/nginx.dgq-finance-agent.conf)
- 腾讯云初始化脚本：[scripts/setup_tencent_prod.sh](scripts/setup_tencent_prod.sh)
- 腾讯云系统依赖安装脚本：[scripts/install_tencent_system_packages.sh](scripts/install_tencent_system_packages.sh)
- 腾讯云 PostgreSQL 安装脚本：[scripts/install_tencent_postgres.sh](scripts/install_tencent_postgres.sh)
- 生产启动入口脚本：[scripts/start_prod_server.sh](scripts/start_prod_server.sh)
- QQ 官方 Bot 参数打印脚本：[scripts/print_qq_official_bot_setup_info.sh](scripts/print_qq_official_bot_setup_info.sh)
- QQ 官方 Bot 回调冒烟脚本：[scripts/smoke_qq_official_webhook.sh](scripts/smoke_qq_official_webhook.sh)
- OpenClaw QQ 参数打印脚本：[scripts/print_openclaw_qq_setup_info.sh](scripts/print_openclaw_qq_setup_info.sh)
- go-cqhttp 接入信息脚本：[scripts/print_go_cqhttp_setup_info.sh](scripts/print_go_cqhttp_setup_info.sh)
- 手机访问基础认证脚本：[scripts/enable_mobile_basic_auth.sh](scripts/enable_mobile_basic_auth.sh)
- 腾讯云公网访问检查脚本：[scripts/print_tencent_public_access_info.sh](scripts/print_tencent_public_access_info.sh)
- OpenClaw / QQ webhook 冒烟脚本：[scripts/smoke_openclaw_qq_webhook.sh](scripts/smoke_openclaw_qq_webhook.sh)
- OneBot / NapCat QQ webhook 冒烟脚本：[scripts/smoke_onebot_group_webhook.sh](scripts/smoke_onebot_group_webhook.sh)
- QQ OneBot relay 启动脚本：[scripts/start_qq_onebot_relay.sh](scripts/start_qq_onebot_relay.sh)
- QQ OneBot relay systemd 模板：[deploy/tencent/qq-onebot-relay.service](deploy/tencent/qq-onebot-relay.service)

### 腾讯云一键初始化

在腾讯云机器上进入项目目录后，可先执行：

```bash
sudo bash scripts/install_tencent_system_packages.sh
bash scripts/setup_tencent_prod.sh
```

该脚本会自动完成：

1. 生成 `.venv`
2. 安装 Python 依赖
3. 若 `.env` 不存在，则按 [.env.tencent.example](.env.tencent.example) 生成
4. 自动生成 `CONNECTOR_SHARED_TOKEN`
5. 打开 `OPENCLAW_NOTIFIER_ENABLED=true`
6. 打开 `SCHEDULER_INTRADAY_REFRESH_ENABLED=true`
7. 执行 `alembic upgrade head`

如果你已经拿到 QQ 官方 `AppID` / `AppSecret`，建议初始化后再执行：

```bash
bash scripts/print_qq_official_bot_setup_info.sh
bash scripts/smoke_qq_official_webhook.sh
```

若遇到 `connection to server at "127.0.0.1", port 5432 failed: Connection refused`，说明 PostgreSQL 还没启动。此时可任选一种方式：

#### 方案 A：安装本机 PostgreSQL

```bash
sudo bash scripts/install_tencent_postgres.sh
SKIP_PIP=1 bash scripts/setup_tencent_prod.sh
```

#### 方案 B：若已安装 Docker，则启动数据库容器

```bash
docker compose up -d db
SKIP_PIP=1 bash scripts/setup_tencent_prod.sh
```

如果机器上暂时没有 `python3-venv`，可退化为：

```bash
USE_SYSTEM_PYTHON=1 bash scripts/setup_tencent_prod.sh
```

### systemd 部署

可将 [deploy/tencent/dgq-finance-agent.service](deploy/tencent/dgq-finance-agent.service) 安装到系统服务目录：

```bash
sudo cp deploy/tencent/dgq-finance-agent.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now dgq-finance-agent
sudo systemctl status dgq-finance-agent
```

说明：当前 service 已改为调用 [scripts/start_prod_server.sh](scripts/start_prod_server.sh)，会优先使用 `.venv/bin/python`，若虚拟环境不存在则回退到系统 `python3`。

### Nginx 反向代理

可将 [deploy/tencent/nginx.dgq-finance-agent.conf](deploy/tencent/nginx.dgq-finance-agent.conf) 放到 Nginx 站点配置目录后重载：

```bash
sudo cp deploy/tencent/nginx.dgq-finance-agent.conf /etc/nginx/conf.d/dgq-finance-agent.conf
sudo nginx -t
sudo systemctl reload nginx
```

如果机器上还没安装 Nginx，先执行：

```bash
sudo bash scripts/install_tencent_system_packages.sh
```

若暂时不装 Nginx，也可先直接访问 `http://云服务器IP:8000/`。

给 OpenClaw / QQ 回调后端时，建议发送类似负载：

```json
{
   "channel": "qq",
   "message": "002384 看好，逻辑是消费电子复苏",
   "sender_name": "群友A",
   "sender_id": "123456",
   "group_name": "DGQ测试群",
   "group_id": "987654"
}
```

如果消息是命令：

```json
{
   "channel": "qq",
   "text": "/status 002384",
   "sender_name": "群友A",
   "sender_id": "123456",
   "group_name": "DGQ测试群"
}
```

后端会返回：

- `action=ingest` / `research` / `command`
- `reply_message`

其中 `reply_message` 可直接由 OpenClaw 回发到 QQ 群，形成闭环。

### 你当前这套 QQ 接入如何落地

你已确认：

- 接收群消息的 QQ 账号：`1612085779`
- 测试群：`finance-robot`

本项目后端已经就绪，但 **OpenClaw 那一侧仍需要你本人完成一次登录/绑定**。原因很简单：

- 登录 QQ 往往需要扫码或人工确认
- 这一步不能由后端代码替你完成

后端这边你已经可以直接打印出接线参数：

```bash
bash scripts/print_openclaw_qq_setup_info.sh
```

这个脚本会输出：

- webhook 地址
- `X-Connector-Token`
- 默认 QQ 账号/测试群信息
- 一条可直接联调的 `curl` 示例

你需要在 OpenClaw / QQ 连接器里完成的动作只有这些：

1. 用 `1612085779` 登录 QQ 连接器
2. 让它监听群 `finance-robot`
3. 收到群消息后，转发到：
   - `POST /api/connectors/openclaw/webhook`
4. 请求头带上：
   - `X-Connector-Token: <你的 CONNECTOR_SHARED_TOKEN>`
5. 把后端返回的 `reply_message` 再回发到 `finance-robot`

如果 OpenClaw 那边已经能把 QQ 消息转成 HTTP 回调，这一步就够了；本项目后端不用再保存 QQ 密码。

### 多手机访问云端前端

可以，且推荐这么用。

当前前端已经有移动端 viewport 和响应式样式：

- [app/templates/dashboard.html](app/templates/dashboard.html)
- [app/templates/stock_detail.html](app/templates/stock_detail.html)

所以只要服务放在腾讯云上，多部手机都能直接通过浏览器访问同一个云端页面。

为避免公网裸露，建议开启基础认证。仓库已补脚本：

```bash
bash scripts/enable_mobile_basic_auth.sh
sudo systemctl restart dgq-finance-agent
```

执行后会自动写入：

- `WEB_BASIC_AUTH_ENABLED=true`
- `WEB_BASIC_AUTH_USERNAME=...`
- `WEB_BASIC_AUTH_PASSWORD=...`

认证逻辑已内置在 [app/main.py](app/main.py) 中：

- 浏览器访问前端时会弹出 Basic Auth 登录框
- `/health` 与连接器 webhook 默认放行，不受影响

如果你后续再配上 Nginx + 域名 + HTTPS，那么多手机访问就会非常顺手。

如果本机可访问、但手机和电脑外网访问不了，通常是 **腾讯云安全组没有放行 80 端口**。可先打印检查信息：

```bash
bash scripts/print_tencent_public_access_info.sh
```

最少需要在腾讯云安全组中放行：

- TCP 80（前端页面）
- TCP 443（后续 HTTPS）
- 可选 TCP 8000（调试直连）

### QQ 真接入的现实建议

经实际排查，当前通过 `pip install openclaw` 安装到服务器上的包，本质上是 CMDOP/OpenClaw 的 Python 编排插件，并 **不是** 之前假设的那种 `openclaw --dev agent --channel qq --deliver` 消息 CLI。

所以现阶段要把 QQ 真接进来，最稳的方案是：

1. 使用支持 QQ 的桥接器（如 OneBot / NapCat / go-cqhttp 风格）登录 QQ `1612085779`
2. 监听群 `finance-robot`
3. 把群消息按 HTTP 回调转发到：
   - `POST /api/connectors/qq/webhook`
4. 带上请求头：
   - `X-Connector-Token: <CONNECTOR_SHARED_TOKEN>`
5. 读取后端返回的 `reply_message`，再发回 QQ 群

后端现已兼容 OneBot/NapCat 常见负载格式，群消息和命令都能识别。

可直接用下面脚本做联调：

```bash
CONNECTOR_TOKEN=$(grep '^CONNECTOR_SHARED_TOKEN=' .env | cut -d= -f2-) \
bash scripts/smoke_onebot_group_webhook.sh
```

### 当前推荐桥接器：go-cqhttp

结合你当前环境：

- 服务器类型：腾讯云轻量应用服务器（Linux）
- 当前没有 Docker
- 目标是接 QQ 账号 `1612085779` 和群 `finance-robot`

我建议优先使用 **go-cqhttp**，原因是：

1. 更适合 headless Linux 服务器
2. 原生支持 OneBot 风格 HTTP 上报/调用
3. 现有后端已经兼容 OneBot 负载
4. 现有 relay 已补齐“收到消息 -> 调后端 -> 把 reply_message 发回 QQ”闭环

仓库里已经补齐 relay：

- relay 程序：[connectors/qq_onebot_relay/main.py](connectors/qq_onebot_relay/main.py)
- 启动脚本：[scripts/start_qq_onebot_relay.sh](scripts/start_qq_onebot_relay.sh)
- systemd 模板：[deploy/tencent/qq-onebot-relay.service](deploy/tencent/qq-onebot-relay.service)

你现在可先打印接入参数：

```bash
bash scripts/print_go_cqhttp_setup_info.sh
```

推荐链路如下：

1. `go-cqhttp` 登录 QQ `1612085779`
2. `go-cqhttp` 监听并收到群 `finance-robot` 的消息
3. `go-cqhttp` 反向上报到 `http://127.0.0.1:5701/webhook`
4. relay 把消息转发到 `POST /api/connectors/qq/webhook`
5. relay 读取后端返回的 `reply_message`
6. relay 再通过 `go-cqhttp` HTTP API 发回 QQ 群

要启用 relay，后续可执行：

```bash
sudo cp deploy/tencent/qq-onebot-relay.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now qq-onebot-relay
sudo systemctl status qq-onebot-relay --no-pager
```

## Wechaty / OpenClaw 对接方式
外部连接器只需把消息转发到 webhook：

```json
{
  "message": "看好600519，逻辑是业绩持续改善",
  "recommender_name": "张三",
  "wechat_id": "wx_zhangsan"
}
```

目标地址：`POST /api/connectors/wechat/webhook`

## OpenClaw（主流 Telegram 方案，无需二次开发）

推荐采用 OpenClaw 官方 CLI + Telegram Bot 的标准链路：

1. 在 Telegram 使用 `@BotFather` 创建机器人并获取 token。
2. 把 token 保存到本机文件：

```bash
mkdir -p ~/.openclaw-dev
printf '123456:ABC-YourTelegramBotToken' > ~/.openclaw-dev/telegram_bot_token.txt
```

3. 一键配置并验证：

```bash
bash scripts/setup_openclaw_telegram.sh
```

4. 给机器人发送 `/start` 后，触发一次回消息：

```bash
openclaw --dev agent --channel telegram --deliver -m '你好，回我一句已联通'
```

如需查看连接状态：

```bash
openclaw --dev channels status --probe
```

## Copilot 付费模型优先的半自动迭代闭环（推荐）

如果你希望尽量使用 VS Code Copilot（GPT-5.3-Codex）而不是自建模型 API，可使用本仓库脚本：

```bash
bash scripts/copilot_hybrid_loop.sh init "你的任务目标"
```

脚本会生成：

- `.copilot-loop/COPILOT_PROMPT.md`：首轮提示词（粘贴到 Copilot Chat）
- `.copilot-loop/task.md`：任务目标

每轮 Copilot 修改代码后执行：

```bash
bash scripts/copilot_hybrid_loop.sh check
```

也可以通过命令接口直接触发：

- `/loop init 修复某个功能并自测`
- `/loop check`
- `/loop summary`
- `/discover scan`（扫描新闻并更新候选）
- `/discover list`（查看候选新股）
- `/discover promote 12`（按ID晋升候选到跟踪池）

脚本会自动：

- 运行测试（默认 `python -m unittest discover -s tests -v`）
- 读取失败日志
- 生成下一轮给 Copilot 的修复提示词

查看当前状态：

```bash
bash scripts/copilot_hybrid_loop.sh summary
```

> 可通过环境变量覆盖测试命令：
>
> `TEST_CMD="你的测试命令" bash scripts/copilot_hybrid_loop.sh check`

## 批量导入（推荐主通道）

当微信群自动监听不稳定时，可使用“复制粘贴批量导入”：

- Web 页面：`POST /manual/import`（首页“批量导入”表单）
- API：`POST /api/messages/import_text`

支持输入格式：

1. 自由文本（每行一条）

```text
张三：600519 看好，逻辑是高端白酒复苏
李四：000001 推荐，逻辑是估值修复
```

2. 微信常见复制样式（人名+时间，下一行消息）

```text
张三 2026-03-01 09:31
000001 推荐，逻辑是估值修复
```

3. JSON（list/dict）

```json
[
   {"sender": "王五", "content": "300750 看好，逻辑是出海", "time": "2026-03-01 10:20"}
]
```

4. CSV（带表头）

```csv
message,recommender_name,recommend_ts
600519 看好 逻辑是业绩改善,张三,2026-03-01 10:00
```

系统会自动做：消息解析、股票识别、重复去重（同荐股人+同股票+同日消息+同日消息日期），并返回导入统计。

说明：若消息中没有 6 位股票代码，但包含“建议关注 XXX、YYY”这类股票名称，系统会自动按名称入库并标记为 `pending_mapping`（待代码映射），保证信息先不丢失。

另外，未命中荐股的资讯文本不会丢弃，会自动沉淀到 RAG 资料库（`RAG_STORE_PATH`），供后续每日分析引用。

## 前端操作流（最终推荐）

首页只保留两个输入口：

1. 股票消息输入（复制粘贴）
   - 对应 `POST /manual/import`
   - 自动识别荐股并入池
2. 资讯输入（RAG资料）
   - 对应 `POST /manual/research`
   - 把观点/研报沉淀为可检索上下文

系统每日自动执行评估并生成 `reports/daily/YYYY-MM-DD.md`，完成“输入 -> 追踪 -> 输出结论”的闭环。

## 追踪与评价机制（重点）

### 数据存储位置
- 结构化主数据（股票池、推荐、日评估、告警订阅）：数据库（SQLite/PostgreSQL）
- 资讯RAG总库：`data/research_notes.jsonl`
- 每支股票单独知识文件：`data/stocks/<stock_code>_<stock_name>.jsonl`
- 每日结论文件：`reports/daily/YYYY-MM-DD.md`

说明：前端输入的荐股与资讯会同时沉淀到数据库和对应股票知识文件，后续每日分析会优先读取该股票历史文件作为RAG上下文。

### 1) 股票池追踪
- 每条荐股保存：`原始消息`、`提取逻辑`、`荐股人`、`首次接收时间`、`来源`。
- 对于只有股票名没有代码的内容，先入池并标记 `pending_mapping`，后续可映射真实代码再进入自动行情评估。

### 2) 每日评分（SQS）
- 每日为 tracking 股票计算综合评分，核心输入：
   - 收益率（`pnl_percent`）
   - 最大回撤（`max_drawdown`）
   - 夏普（`sharpe_ratio`）
   - 逻辑验证（`logic_validated`）
   - 市值/弹性/流动性子分（`market_cap_score`/`elasticity_score`/`liquidity_score`）
- 评分通过 `compute_stock_quality_score()` 输出当日分数与解释文本。

### 3) 信息源评分（RRS）
- 对每位荐股人，按历史荐股的收益、回撤、时间衰减做聚合，计算 `reliability_score`。
- 使用 `compute_recommender_reliability()`，每日评估任务后自动刷新。

### 4) RAG + Agent 智能分析
- 非荐股资讯会写入 `research_notes.jsonl`，按股票名/代码检索上下文。
- 每日文件为每只股票生成“智能分析 + 纠偏建议”：
   - 默认采用 LLM API（OpenAI 兼容接口），通过环境变量无痛切换模型与供应商。
   - 关键变量：`ANALYSIS_MODEL`、`LLM_API_BASE`、`LLM_API_KEY`、`LLM_API_MODE`、`LLM_API_CHAT_PATH`、`LLM_API_COMPLETIONS_PATH`。
   - `LLM_API_MODE=auto` 时会先走 `chat/completions`，失败后自动回退到 `completions`（适合 openai-completions 网关）。
   - 可用 `python scripts/smoke_llm_api.py` 快速验证配置是否打通。
   - 当 LLM 配置不可用时，会输出事实型摘要并提示检查 LLM 配置。

### 5) 记忆系统（RAG）设计预留
- 当前记忆后端：`jsonl`（研究总库 + 单股知识文件），已通过统一检索层聚合。
- 检索配置：`MEMORY_BACKEND`、`MEMORY_RETRIEVAL_LIMIT`。
- 扩展方向：向量库 / Embedding 检索可直接接入检索层，不影响业务接口。

### 6) 新闻扫描与新股发现
- 调度参数：`SCHEDULER_NEWS_SCAN_ENABLED`、`SCHEDULER_NEWS_SCAN_CRON`。
- 候选阈值：`NEWS_DISCOVERY_MIN_SCORE`、`NEWS_AUTO_PROMOTE_MIN_SCORE`。
- 扫描会同时完成两件事：
   - 发现候选新股并进入队列；
   - 对已跟踪股票写入增量新闻证据，支持实时更新结论。

### 6) 真实数据源建议（交付）
- 行情：`MARKET_DATA_PROVIDER=baostock`（日线量价可用）
- 逻辑验证：`NEWS_DATA_PROVIDER=tushare`（需配置 `TUSHARE_TOKEN`）/ `webhook` / `sites`
- 当 `NEWS_DATA_PROVIDER=sites` 时，可配置：
   - `NEWS_SITE_WHITELIST=https://www.eastmoney.com,https://finance.sina.com.cn,https://www.stcn.com,https://www.cnstock.com`
   - `NEWS_SITE_TIMEOUT=5`
- 交付环境不建议使用 mock。

### 5) 每日追踪文件
- 每日生成 `reports/daily/YYYY-MM-DD.md`：
   - 每只股票当日评分、收益、回撤
   - 原始逻辑与智能复盘
   - 与前一日对比后的“逻辑纠偏建议”

仓库已提供可直接运行的转发样例：

- [connectors/wechaty-relay/README.md](connectors/wechaty-relay/README.md)
- [connectors/wechaty-relay/index.js](connectors/wechaty-relay/index.js)

## 定时任务说明
- 默认 cron：`30 15 * * 1-5`（工作日 15:30）。
- 可通过 `.env` 修改：`SCHEDULER_CRON`。
- 执行内容：
  1. 对 tracking 推荐拉取行情与逻辑验证（当前为 mock provider）
  2. 更新每日 SQS
  3. 刷新荐股人 RRS
  4. 推送每日战报

## 真实可用验收（建议）

1. 启动服务后先检查：

```bash
curl http://127.0.0.1:8000/api/system/check
```

2. 执行端到端烟雾测试：

```bash
python scripts/smoke_e2e.py
```

3. 联调微信转发：

```bash
cd connectors/wechaty-relay
npm install
BACKEND_WEBHOOK_URL=http://127.0.0.1:8000/api/connectors/wechat/webhook WECHAT_ROOM_WHITELIST=一起赚钱 npm start
```

## 测试

```bash
python -m unittest discover -s tests -v
```

## 生产化下一步
- 将 `MockMarketDataProvider` 替换为 Tushare/Baostock 实现。
- 将 `MockNewsDataProvider` 替换为新闻抓取 + NLP 逻辑验证。
- 配置企业微信/钉钉机器人 webhook 作为告警输出。
- 在 Wechaty 服务中启用历史回补任务并调用 webhook 入库。


cd /Users/weijianluan/luan/finance/dgq_finance_agent
cp .env.example .env
python -m pip install -r requirements.txt
docker compose up -d db
alembic upgrade head
python run.py