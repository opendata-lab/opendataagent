# 会话 API

Base URL：`http://<DataAgent 主机>:8900/api/v1/nl2sql`。启用认证后，请求需带登录会话（见部署指南的“登录配置”）。

## 健康检查

```
GET /health
```

只说明进程存活；会话存储是否可用，以 `POST /topics` 能否成功为准。

## 会话

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/topics` | 创建会话，请求体 `{"title"?, "agent_id"?, "permission_mode"?}` |
| `GET` | `/topics` | 当前用户的会话列表 |
| `GET` | `/topics/{topic_id}` | 会话详情 |
| `PUT` | `/topics/{topic_id}` | 修改标题或权限模式 |
| `DELETE` | `/topics/{topic_id}` | 删除会话 |
| `GET` | `/topics/{topic_id}/messages` | 会话消息 |
| `POST` / `GET` | `/topics/{topic_id}/files` | 上传 / 列出会话附件 |
| `PUT` | `/topics/{topic_id}/messages/{message_id}/feedback` | 消息反馈 |
| `POST` | `/topics/{topic_id}/messages/{message_id}/followup-suggestions` | 生成追问建议 |

## 发送消息

```
POST /tasks/deliver-message
```

```json
{
  "topic_id": "<topic_id>",
  "content": "最近 30 天工作流发布次数趋势",
  "agent_id": "<可选，默认用会话绑定的智能体>",
  "execution_mode": "auto"
}
```

`execution_mode` 省略时按交互档（360 秒）执行；页面发送 `auto`，走后台档（1800 秒）。

另有通用入口 `POST /tasks`，请求体用 `message_type` 与 `message_content`（不是 `question`）。

响应：

```json
{
  "accepted": true,
  "topic_id": "<topic_id>",
  "task_id": "<task_id>",
  "task_status": "waiting",
  "user_message_id": "<id>",
  "assistant_message_id": "<id>"
}
```

## 任务

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/tasks/{task_id}` | 任务状态：`waiting` → `running` → `success` / `failed` / `suspended` |
| `GET` | `/tasks/{task_id}/message` | 任务对应的助手消息 |
| `GET` | `/tasks/{task_id}/sdk-events` | 已持久化的运行时事件 |
| `GET` | `/tasks/{task_id}/sdk-events/stream` | SSE 实时事件流（`/events/stream` 不存在） |
| `POST` | `/tasks/{task_id}/cancel` | 取消任务 |
| `POST` | `/tasks/{task_id}/permission-decision` | 审批工具调用：`{"request_id", "decision": "allow"\|"deny", "note"?}` |
| `POST` | `/tasks/{task_id}/question-answer` | 回答运行时向用户提出的问题 |

`suspended` 表示任务在等待审批或回答，处理后自动继续。
