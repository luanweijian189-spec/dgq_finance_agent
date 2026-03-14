# Agent Matrix + Repo Ops Autopilot 设计说明

## 目标

把“钉钉 / OpenClaw 消息”升级成“可直接驱动代码库自动化”的任务入口。

目标链路：

1. 用户在钉钉 / OpenClaw 中发开发指令
2. 后端生成 `agent-matrix task`
3. 后端生成 `repo-ops task`
4. 本地或外部 HTTP API 接管代码规划 / 修改 / 测试 / 交付
5. 结果回传到钉钉 / OpenClaw

## 当前实现

### 1. 消息入口

- 钉钉：`/api/connectors/dingtalk/webhook`
- OpenClaw / 通用命令：`/api/commands`
- 聊天命令：`/dev ...`、`/repo ...`

### 2. Agent Matrix

负责“需求拆解 + 角色矩阵 + 调度状态”。

关键能力：

- 创建任务
- 分发到本地 loop 或外部 HTTP API
- 汇总状态

### 3. Repo Ops

负责“面向代码库的执行策略与仓库治理”。

关键能力：

- 记录仓库级任务
- 定义执行策略：`base_branch`、`target_branch`、`require_human_approval`
- 控制是否允许 `git write / git push / shell`
- 为外部高性能模型 API 预留标准 HTTP 合约

## 推荐运行模式

### 今天

- `AGENT_MATRIX_PROVIDER=local`
- `REPO_OPS_PROVIDER=local`

效果：

- 消息可以创建 agent/repo 双任务
- 系统会生成 brief / 计划 / 仓库状态摘要
- 不直接写代码，先把编排接口打通

### 明天接入更强模型 API

- `AGENT_MATRIX_PROVIDER=http`
- `REPO_OPS_PROVIDER=http`
- 配置：
  - `AGENT_MATRIX_HTTP_BASE_URL`
  - `AGENT_MATRIX_HTTP_API_KEY`
  - `REPO_OPS_HTTP_BASE_URL`
  - `REPO_OPS_HTTP_API_KEY`

效果：

- 消息入口不变
- 钉钉 / OpenClaw 不变
- 后端直接把任务转发给外部 agent 编排服务

## HTTP API 合约建议

### Agent Matrix

- `POST /tasks`
- `POST /tasks/{task_id}/summary`

### Repo Ops

- `POST /repo-ops/tasks`
- `POST /repo-ops/tasks/{task_id}/execute`
- `POST /repo-ops/tasks/{task_id}/summary`
- `POST /repo-ops/tasks/{task_id}/approve`

## 建议的外部服务职责

外部高性能模型服务建议拆成 4 个 worker：

1. `planner`：看代码、做方案
2. `coder`：改代码
3. `reviewer`：做安全和回归审查
4. `tester`：跑测试、生成结论

后端自身只做：

- 接消息
- 存任务
- 做状态编排
- 回传结果

## 安全边界

默认策略：

- `REPO_OPS_REQUIRE_HUMAN_APPROVAL=true`
- `REPO_OPS_ALLOW_GIT_WRITE=false`
- `REPO_OPS_ALLOW_GIT_PUSH=false`
- `REPO_OPS_ALLOW_SHELL=false`

只有在外部 agent 服务成熟后，再按需逐步放开。