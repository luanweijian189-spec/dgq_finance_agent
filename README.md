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

6. 真实数据源可切换
   - 行情源支持 `baostock`（默认推荐）
   - 新闻逻辑验证支持 `mock / tushare / webhook / sites`（站点白名单聚合校验）
   - 提供系统可用性检查接口：`GET /api/system/check`

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

应用容器会自动执行 `alembic upgrade head` 后启动 API。

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
- `GET /api/system/check`：数据库 + 行情源 +新闻源可用性检查。

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
   - 默认使用规则 Agent（`ANALYSIS_MODEL=rule`）
   - 配置 `LLM_API_BASE + LLM_API_KEY + ANALYSIS_MODEL` 后可切换外部大模型。

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
