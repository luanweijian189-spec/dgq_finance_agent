# Agent 矩阵接入说明

## 目标

把“钉钉 / OpenClaw 消息”转换成“代码库任务”，并通过一层可替换的 API provider 驱动后续自动开发。

当前仓库提供两种 provider：

1. `local`
   - 本地生成任务 brief
   - 调用 `scripts/copilot_hybrid_loop.sh`
   - 适合现在在本仓库内先跑通流程
2. `http`
   - 把任务转发到外部 agent matrix API
   - 适合后续接入 OpenClaw + 自建 agent 服务

## Agent 矩阵角色

1. `coordinator`
   - 拆解任务、控制边界、整理依赖
2. `planner`
   - 阅读代码并设计方案 / API / 数据流
3. `coder`
   - 实施代码改动
4. `reviewer`
   - 检查安全、回归风险、可维护性
5. `tester`
   - 执行测试与冒烟验证
6. `delivery`
   - 汇总交付说明，给人或外部系统消费

## 环境配置

见：

- [.env.example](../.env.example)
- [.env.tencent.example](../.env.tencent.example)

关键变量：

- `AGENT_MATRIX_ENABLED`
- `AGENT_MATRIX_PROVIDER`
- `AGENT_MATRIX_STORE_PATH`
- `AGENT_MATRIX_WORKSPACE`
- `AGENT_MATRIX_LOOP_SCRIPT`
- `AGENT_MATRIX_BRIEF_DIR`
- `AGENT_MATRIX_DEFAULT_BRANCH`
- `AGENT_MATRIX_HTTP_BASE_URL`
- `AGENT_MATRIX_HTTP_API_KEY`
- `AGENT_MATRIX_TIMEOUT_SECONDS`
- `AGENT_MATRIX_AUTO_CHECK`

## HTTP API

### 1. 查看角色

`GET /api/dev/agent-matrix/roles`

### 2. 创建任务

`POST /api/dev/agent-matrix/tasks`

请求体示例：

```json
{
  "objective": "新增一个日报审核接口并补测试",
  "context": "要求复用 FastAPI 和现有 service 层",
  "operator": "openclaw",
  "source": "dingtalk",
  "conversation_id": "cidxxx",
  "branch": "main",
  "auto_dispatch": true,
  "auto_check": false
}
```

### 3. 查询任务

- `GET /api/dev/agent-matrix/tasks`
- `GET /api/dev/agent-matrix/tasks/{task_id}`

### 4. 手动分发任务

`POST /api/dev/agent-matrix/tasks/{task_id}/dispatch`

请求体示例：

```json
{
  "auto_check": false
}
```

### 5. 汇总任务

`POST /api/dev/agent-matrix/tasks/{task_id}/summary`

## 消息命令

钉钉 / OpenClaw 命令：

- `/dev new <任务描述>`
- `/dev run <task_id>`
- `/dev status`
- `/dev status <task_id>`
- `/dev summary <task_id>`
- `/dev matrix`

## 本地产物

默认会产生两类本地文件：

1. 任务快照：`data/runtime/agent_matrix_tasks.jsonl`
2. 任务说明：`data/runtime/agent_matrix/<task_id>.md`

如果 provider=`local`，还会继续复用：

- [.copilot-loop/COPILOT_PROMPT.md](../.copilot-loop/COPILOT_PROMPT.md)
- [scripts/copilot_hybrid_loop.sh](../scripts/copilot_hybrid_loop.sh)

## 外部 API 替换建议

当你后续拥有自己的 agent matrix 服务时，建议至少支持这两个接口：

### 创建/分发任务

`POST {AGENT_MATRIX_HTTP_BASE_URL}/tasks`

输入字段：

- `task_id`
- `objective`
- `context`
- `operator`
- `source`
- `conversation_id`
- `workspace`
- `branch`
- `matrix`
- `auto_check`

建议返回：

```json
{
  "status": "dispatched",
  "message": "任务已进入外部 agent 队列",
  "run_id": "run-123"
}
```

### 查询任务状态

`GET {AGENT_MATRIX_HTTP_BASE_URL}/tasks/{task_id}`

建议返回：

```json
{
  "status": "running",
  "message": "coder agent 正在改代码",
  "run_id": "run-123",
  "steps": [
    {"role": "planner", "status": "done"},
    {"role": "coder", "status": "running"}
  ]
}
```
