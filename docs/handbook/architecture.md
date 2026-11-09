# 架构

## 系统组成

```
浏览器（dataagent-frontend 管理台 / 问答页，或宿主页面里的 widget）
    │  HTTP + SSE
    ▼
dataagent-backend（FastAPI :8900）
    ├── API 层            会话、任务、消息、管理接口
    ├── Task Coordinator  异步任务调度，内嵌在 main.py，不需要单独的 worker
    ├── 运行时            Pi SDK（pi-coding-agent 1.4.0），每个执行段一个 Node 子进程
    ├── Skill / MCP       通用内置 Skill + 管理员导入的 Skill；智能体显式绑定的 MCP
    └── 存储              MySQL（dataagent schema）、Redis（任务协调）
    └── Pi Cell 按需通过 Docker CLI 启动 oda-sandbox
        ├── bash/read/write/edit/ls/find/grep（固定启用）
        ├── 当前 topic workspace 读写、results 与启用 skills 只读
        └── 无网络、无凭据、无 Docker socket；本段结束即销毁
```

DataAgent 不内置任何数据平台。访问外部系统只通过智能体显式绑定的 MCP；对每个 HTTP MCP，后端都附加
标识头 `X-Agent-User-Id`（带来源前缀的登录用户，`local:<用户名>` 或 `<provider>:<sub>`）和
`X-Agent-Id`（当前智能体）。这两个头由后端从会话填写，不来自模型参数（独立前端的会话用 DataAgent 登录用户，宿主内嵌的 widget 会话用宿主上报的用户）；MCP 自身的访问凭证是 MCP 记录里管理员配置的普通 header。是否按标识头授权由各 MCP 决定：OpenDataWorks 只按凭证里的服务账号 Token 授权，标识头仅记审计，所以不同数据范围要用不同的服务账号和 MCP 记录。

## 核心流程

1. 前端创建会话：`POST /api/v1/nl2sql/topics`。
2. 用户发送消息：`POST /api/v1/nl2sql/tasks/deliver-message`，返回 `task_id`，任务进入 `waiting`。
3. Task Coordinator 拾取任务（`running`），按智能体配置启动 Pi Cell；编排和 MCP 留在控制面，基础工具全部在沙箱中执行。
4. 运行时事件写入 `da_agent_sdk_record`，前端通过 `GET /api/v1/nl2sql/tasks/{task_id}/sdk-events/stream` 实时读取。
5. 结束后任务进入 `finished` / `error` / `suspended`，助手消息写入会话。卡片等待使用 `waiting_input` / `waiting_permission`，会话快照与卡片在同一事务提交后释放进程、容器及槽位；回答后同一 task_id 转回 waiting，以新执行段续聊。

## 运行预算

所有任务使用同一档预算：总超时默认 1800 秒，另有 300 秒的无活动超时；等待用户回答卡片的时间不计入。
会话可以长期存在，但单次运行有上限，不能靠继续拉长单次运行来容纳超长分析。
预算的配置位置见 [部署指南](../../deploy/README.md#运行配置)。

## 数据存储

主要表（`dataagent` schema，由 Alembic 管理）：

| 表 | 说明 |
| --- | --- |
| `da_agent_topic` | 会话（来源 `web` / widget、所属用户、绑定智能体） |
| `da_agent_message` | 会话消息 |
| `da_agent_session_checkpoint` | SDK 会话条目、叶节点、交互批准及累计预算 |
| `da_agent_task` | 任务状态与执行快照 |
| `da_agent_sdk_record` | 运行时事件流（`engine_kind` 标明实际运行的引擎） |
| `da_agent_profile` / `da_agent_draft` | 智能体及其草稿 |
| `da_model_provider` | 模型供应商与凭据 |
| `da_mcp_server` | MCP 连接配置 |
| `da_skill_document` / `da_skill_document_version` | 导入的 Skill |
| `da_agent_message_queue` / `da_agent_message_schedule` | 排队消息与定时消息 |
| `da_agent_settings` | 全局设置（含 widget 站点白名单） |

topic 文件位于宿主机 `DATAAGENT_HOST_ROOT/<topic_id>/`；workspace 供上传和产物使用，results 保存未经预览截断的原始 JSON/文本，沙箱只读路径为 `/mnt/results`。具体 MCP 写操作批准绑定服务、工具和原参数，只领取一次；结果未知时中止并等待核查，不自动重放。

## 相关文档

- [会话 API](api.md)
- [Widget 嵌入](widget.md)
- [部署指南](../../deploy/README.md)
