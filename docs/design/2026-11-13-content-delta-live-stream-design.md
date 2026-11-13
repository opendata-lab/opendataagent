# 流式增量（content.delta）内存直推、不落库设计

日期：2026-11-13
状态：待评审
关联计划：[2026-11-13-content-delta-live-stream-plan.md](../plans/2026-11-13-content-delta-live-stream-plan.md)

## 1. 现状

```text
Pi Cell ──stdio NDJSON──> core/pi_runtime.py ──writer.ingest(event)──> core/pi_event_writer.py
                                                                         │ 每个事件一次 append_sdk_record
                                                                         ▼
                                     da_agent_sdk_record（id 自增，索引 (task_id, id)）
                                                                         ▲
api/routes.py _stream_sdk_events ── 每 1 秒 list_sdk_records(after_id) ──┘
```

- Pi 的 `event-normalizer.ts` 对模型的每个 `text_delta` / `thinking_delta` 各发一个 `content.delta`，`PiEventWriter` 每个事件同步写一行，一次回答几百行。
- 任务以 `finished` 结束时，`_delete_task_content_deltas` 删除全部 delta 行（`topic_task_store.py:2329`）。
- 中断（`failed` / `cancelled` / 超时）的任务保留 delta：未结束的内容块没有 `content.completed`，它的文字只在 delta 里，历史投影（`topic_task_store.py:196-209`）靠拼接 delta 还原。
- SSE 轮询间隔最少 1 秒（`routes.py:627`），打字一秒一顿。
- SSE 记录的 `seq_id` 即 `da_agent_sdk_record.id`，前端重连带 `after_id`（`nl2sql.js:241`）。
- 前端 reducer（`packages/agent-conversation/src/core/agentEvents/reducer.js`）：`content.started` 建块，`content.delta` 追加 `delta`，`content.completed` 用 `text` 覆盖并把块标为 `done`。

## 2. 问题

1. 每个 token 片段一次 INSERT，完成后再整批 DELETE：写了马上删，库里存的是过程不是结果。
2. 打字一秒一顿。

## 3. 参考：Claude Code 与 Codex

两者本地会话日志只记录完成的条目，不记录逐 token 增量（2026-11-13 核对本机日志：Codex `~/.codex/sessions/**/rollout-*.jsonl` 只有 `response_item`、`item_completed`、`token_count` 等；Claude Code `~/.claude/projects/**/*.jsonl` 只有完整的 text / thinking / tool 消息）。流式文字在进程内直接送到界面，落盘的只有结果。

本设计采用同一模型：**增量只在内存里流动，MySQL 只存结果。**

## 4. 方案

### 4.1 写入端：增量不落库

`PiEventWriter`：

- `content.delta` **不写库**。writer 按内容键 `(turn_id, content_id)` 在内存里累积该块已生成的文字，并把事件推给本任务的直播通道（4.2）。
- `content.completed` 写库（与现在相同），同时清掉该块的内存文字。
- 其余事件照常写库，写库后带上数据库 `seq_id` 推给直播通道。
- **中断时保留残余文字**：写入 `run.failed` / `run.cancelled` 之前，把每个仍未完成的块写成**一行** `content.delta`（`delta` 为该块全部已生成文字，`kind`、`turn_id`、`content_id` 不变）。现有历史投影与前端历史回放按"拼接 delta"处理，一行即完整文字，读取方不用改。writer 新增 `flush_open_blocks()` 执行这一步；`pi_runtime` 在运行结束的 `finally` 中也调用一次，覆盖外部取消等不经过终态事件的退出路径（已写过则为空操作）。
- 正常结束的任务不会有 delta 行，**删除逻辑整体移除**（`_delete_task_content_deltas` 及其调用、日志）。

### 4.2 直播通道（新模块 `core/live_events.py`）

进程内、按 `task_id` 注册：

- `open(task_id)` / `close(task_id)`：执行段开始时由 `task_executor` 打开，段结束（完成、失败、取消、挂起等卡片）时关闭。
- `publish(event)`：writer 同步调用（同一事件循环），对每个订阅者 `put_nowait`。订阅队列有上限（如 1000），满了即断开该订阅者，客户端重连后由快照补齐。
- `subscribe(task_id)`：原子地返回 `(队列, 快照)`。快照 = 当前所有未完成块的 `(content.started 对应的键, 已生成文字)`。因为 writer 与 SSE 在同一事件循环、`ingest` 是同步调用，订阅与取快照之间不会插入新事件。

### 4.3 SSE 端（`_stream_sdk_events`）

1. 若本进程有该任务的直播通道：
   1. 先 `subscribe`，拿到队列和快照；
   2. 查库补发 `id > after_id` 的持久记录，记录最大 `seq_id`；
   3. 对快照里每个未完成块，发一条 `content.delta`，`data` 带 `text`（该块全部已生成文字）而非 `delta`；
   4. 之后消费队列：持久记录 `seq_id` ≤ 已发最大值的跳过（已由第 2 步发过），其余原样发出；delta 记录不带 `seq_id`。
   5. 通道关闭（段结束）后回到第 2 种模式。
2. 否则（排队、等待卡片、已结束、或不在本进程）：沿用现有数据库轮询，逻辑不变。
3. 终止条件、ping、断开日志不变。

### 4.4 前端：快照覆盖

`reducer.js` 的 `content.delta` 分支：`data` 含 `text` 时用它**覆盖**块内容，否则追加 `delta`。不标 `done`，块仍在生成中。主站与 Widget 共用该 reducer，一处修改。

重连时前端已有的半截文字会被快照覆盖，不会重复；首次连接时块刚由 `content.started` 建好，快照填入全部已生成文字。

### 4.5 不变的部分

- 表结构、SSE 路由与 `after_id` 语义、持久事件类型。
- `pi_runtime` 的 `answer_parts`（读原始帧，不读库）。
- 历史投影与历史回放（读取方只会看到"中断任务的一行 delta"，格式与现在相同）。
- 超时链：run 预算、idle 超时、ping、反向代理超时都不变。

## 5. 约束：SSE 须由执行任务的进程提供

直播通道在进程内。当前部署是一个容器、一个 `uvicorn` 进程（`deploy/docker-compose.yml`、`docker-entrypoint.sh:22`），SSE 与执行在同一进程，满足要求。

`task_coordinator.py` 支持多实例（实例 id、Redis 租约）。若将来后端扩成多实例，打到非执行实例的 SSE 会落到数据库轮询模式：能看到工具、完成等持久事件，看不到逐字打字。届时需要按任务路由 SSE 或跨实例转发增量。已记入 `docs/TODO.md`。

## 6. 效果

| 项 | 现状 | 改后 |
| --- | --- | --- |
| `finished` 任务的 delta 行 | 几百行，写入后再删除 | 0，无删除 |
| 中断任务的残余文字 | 几百行 delta | 每个未完成块一行 |
| 打字延迟 | ≤ 1s | 事件到达即推送 |
| 页面刷新 / 重连 | 从库回放 delta | 库中持久记录 + 内存快照 |
| 表结构、SSE 路由 | — | 不变，无迁移、无开关 |

## 7. 未采用的方案

- **Redis Streams 承载 delta**（本 PR 初稿）：中断任务的残余文字 5 分钟后过期丢失；Redis id 与 `after_id` 不兼容；`MAXLEN` 截断导致刷新缺字；仅终态设 TTL，崩溃任务的 key 不过期；writer 为同步调用；需要 Redis + MySQL 兜底 + 开关两条路径。
- **合并 delta 后批量写库**（本 PR 第二稿）：写入减少一个数量级，但仍是"先写后删"，库里依旧存过程数据。
