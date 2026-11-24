---
name: dataagent-mcp
description: 使用 DataAgent MCP 查询业务数据并生成报表。用户指定 DataAgent 或其问数助手，或要续接已有 DataAgent 会话、查询任务进度、取回结果或下载附件时使用。
---

# 使用 DataAgent MCP

## 选择工具

优先使用用户指定或本轮上下文已明确的 DataAgent 助手。通过工具搜索找到对应的 MCP 连接，以客户端公开的工具名和参数为准。只有在多个匹配助手之间无法判断时才请用户选择。

| 用户意图 | 工具与参数 |
| --- | --- |
| 新查询或生成报表 | `ask(question, request_key)`。在 `question` 中保留用户指定的指标、时间范围、筛选条件和输出格式。为新请求生成一个 UUID 作为 `request_key`。 |
| 在原会话继续问 | `ask(question, request_key, thread_id)`。带上原 `thread_id`，为新问题生成新的 `request_key`。 |
| 查找会话或回看消息 | 用 `list_threads(query)` 按标题找会话，用 `get_thread(thread_id)` 读取上下文。 |
| 查询已有任务或取回结果 | `get_run(run_id)`。已有任务 ID 时直接读取。 |

## 等待与恢复

- 默认使用 `ask` 等待结果。用户要求后台运行且工具提供该参数时，传 `background: true`。优先读取 `structuredContent`，客户端只提供文本时，解析 `content[0].text` 里的 JSON。
- 收到 `in_progress` 时保存 `run_id` 和 `thread_id`，按返回的 `retry_after_ms` 或 `next_step` 调用 `get_run`。当前回合无法继续等时，告知用户任务仍在运行并给出 `run_id`。
- 提交断线后，已有 `run_id` 就用 `get_run`。还没有 `run_id` 时，向同一助手用相同的 `question`、`thread_id` 和 `request_key` 重试。收到 `thread_busy` 时，用返回的 ID 查看在途任务，等会话可用后再提交本次问题。
- `answered` 时交付 `answer` 和实际返回的 `files`。`failed` 或 `cancelled` 时说明状态，并用 `error.message` 和 `error.next_step` 给出面向用户的原因和可行操作。

## 交付附件

用户要求生成报表、获取文件或用附件继续处理时，按[终端下载](#终端下载)直接下载所需文件。保存到用户指定位置，未指定时保存到当前工作区。同名文件选用不同的文件名。核实保存成功后，提供可点击的本地文件路径。用户只要下载链接时，直接提供原始 `download_url`。

## 连接与权限

- **找不到 MCP 工具**。已知助手 ID 时给出 `<DataAgent 地址>/agents/<助手 ID>`，否则给出助手列表 `<DataAgent 地址>/agents`。本机默认部署的列表地址是 [http://localhost:8901/agents](http://localhost:8901/agents)。远程部署使用实际访问 DataAgent 的地址。打开对应助手，选择「MCP 配置 → 复制配置」，再到当前客户端添加并启用该 Streamable HTTP MCP 连接。
- **MCP 调用返回 401**。引导用户在当前客户端的 MCP 配置中检查或更新该助手的 DataAgent 令牌。

### 终端下载

1. 从当前客户端已启用的 MCP 连接配置中找到本次调用的 DataAgent 助手。确认该连接的 MCP URL 和原始 `download_url` 的协议、主机、端口及助手 ID 一致。
2. 在同一个本地 shell 调用中，从该连接的配置或凭据来源取得完整的 `Authorization` 请求头值并存入 `AUTHORIZATION`。配置已有 `headers.Authorization` 时使用完整值。配置引用 Bearer 令牌时，按客户端的凭据机制解析引用，再加上 `Bearer ` 前缀。根据 `files[].name` 确定 `OUTPUT_PATH`，将原始 `files[].download_url` 存入 `DOWNLOAD_URL`。用管道将请求头交给 curl，使令牌留在本地执行过程中。

```bash
printf 'Authorization: %s\n' "$AUTHORIZATION" | curl -q -fsS -H @- -o "$OUTPUT_PATH" -- "$DOWNLOAD_URL"
```

无法从当前配置取得有效凭据，或带凭据下载仍返回 401 时，使用已登录同一 DataAgent 地址的浏览器打开原链接。无法操作该浏览器时，交付原链接并说明登录方式。
