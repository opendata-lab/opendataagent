# 架构

## 系统组成

```
浏览器（dataagent-frontend 管理台 / 问答页，或宿主页面里的 widget）
    │  HTTP + SSE
    ▼
dataagent-backend（FastAPI :8900）
    ├── API 层            会话、任务、消息、管理接口
    ├── Task Coordinator  异步任务调度，内嵌在 main.py，不需要单独的 worker
    ├── 运行时            Pi（默认 pi_agent_core）或 Claude Agent SDK
    ├── Skill / MCP       通用内置 Skill + 管理员导入的 Skill；智能体显式绑定的 MCP
    └── 存储              MySQL（dataagent schema）、Redis（任务协调）
    │
    ▼  DATAAGENT_SANDBOX_MODE=container 时
dataagent-sandbox-runner（:8910，内部）
    └── 每个 task 一个一次性 child 容器，只挂当前 topic 的工作区
```

DataAgent 不内置任何数据平台。访问外部系统只通过智能体显式绑定的 MCP；对每个 HTTP MCP，后端都附加
执行身份头 `X-Agent-User-Id`（带来源前缀的登录用户，`local:<用户名>` 或 `<provider>:<sub>`）和
`X-Agent-Id`（当前智能体）。这两个头由后端从会话填写，不来自模型参数（独立前端的会话用 DataAgent 登录用户，宿主内嵌的 widget 会话用宿主上报的用户）；MCP 自身的访问凭证是 MCP 记录里管理员配置的普通 header。

## 核心流程

1. 前端创建会话：`POST /api/v1/nl2sql/topics`。
2. 用户发送消息：`POST /api/v1/nl2sql/tasks/deliver-message`，返回 `task_id`，任务进入 `waiting`。
3. Task Coordinator 拾取任务（`running`），按智能体配置启动运行时；容器模式下交给 runner 在 child 容器中执行。
4. 运行时事件写入 `da_agent_sdk_record`，前端通过 `GET /api/v1/nl2sql/tasks/{task_id}/sdk-events/stream` 实时读取。
5. 结束后任务进入 `success` / `failed` / `suspended`（等待用户审批或回答问题），助手消息写入会话。

## 运行预算

一次运行分交互档和后台档（页面发送 `execution_mode=auto`，走后台档）：总超时分别为 360 秒和 1800 秒，另有
300 秒的无活动超时。会话可以长期存在，但单次运行有上限；超出交互上限的分析应走后台档，而不是继续拉长同步请求。
预算的配置位置见 [部署指南](../../deploy/README.md#运行配置)。

## 数据存储

主要表（`dataagent` schema，由 Alembic 管理）：

| 表 | 说明 |
| --- | --- |
| `da_agent_topic` | 会话（来源 `web` / widget、所属用户、绑定智能体） |
| `da_agent_message` | 会话消息 |
| `da_agent_task` | 任务状态与执行快照 |
| `da_agent_sdk_record` | 运行时事件流（`engine_kind` 标明实际运行的引擎） |
| `da_agent_profile` / `da_agent_draft` | 智能体及其草稿 |
| `da_model_provider` | 模型供应商与凭据 |
| `da_mcp_server` | MCP 连接配置 |
| `da_skill_document` / `da_skill_document_version` | 导入的 Skill |
| `da_agent_message_queue` / `da_agent_message_schedule` | 排队消息与定时消息 |
| `da_agent_settings` | 全局设置（含 widget 站点白名单） |

topic 工作区不在数据库里，位于宿主机 `DATAAGENT_HOST_ROOT/<topic_id>/`。

## 相关文档

- [会话 API](api.md)
- [Widget 嵌入](widget.md)
- [部署指南](../../deploy/README.md)
