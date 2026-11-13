# 流式增量（content.delta）合并写入与 SSE 亚秒轮询设计

日期：2026-11-13
状态：待评审
关联计划：[2026-11-13-content-delta-batching-plan.md](../plans/2026-11-13-content-delta-batching-plan.md)

## 1. 现状

```text
Pi Cell ──stdio NDJSON──> core/pi_runtime.py ──writer.ingest(event)──> core/pi_event_writer.py
                                                                         │ 每个事件一次 append_sdk_record
                                                                         ▼
                                     da_agent_sdk_record（id 自增，索引 (task_id, id)）
                                                                         ▲
api/routes.py _stream_sdk_events ── 每 poll_interval 秒 list_sdk_records(after_id) ──┘
```

- Pi 的 `event-normalizer.ts` 对模型的每个 `text_delta` / `thinking_delta` 各发一个 `content.delta`；`PiEventWriter._ingest_safe` 对每个事件同步写一行。一次回答通常几百行。
- 任务以 `finished` 结束时，`topic_task_store._delete_task_content_deltas` 删除该任务的全部 delta 行（`topic_task_store.py:2329`）。
- 以 `failed` / `cancelled` / 超时等方式中断的任务**保留** delta：被打断的内容块没有 `content.completed`，它的文字只存在于 delta 中，历史投影（`topic_task_store.py:196-209`）靠拼接 delta 还原。
- SSE 端点的轮询间隔来自 `run_events_stream_poll_interval_seconds`（`config.py:49`，`int`，默认 1），并在 `routes.py:627` 被 `max(1, int(...))` 限制为至少 1 秒。
- SSE 的 `id:` 与前端重连用的 `after_id` 都是 `da_agent_sdk_record.id`（`nl2sql.js:241`）。

## 2. 问题

1. **写入过多**：每个 token 片段一次 INSERT（各自一次连接和提交），完成后再整批 DELETE。
2. **打字一秒一顿**：轮询下限 1 秒，前端每秒才收到一批文字。

## 3. 方案

在现有 MySQL 路径内解决，两处改动，不新增存储、不改 SSE 协议、不加开关。

### 3.1 Writer 合并连续 delta

`PiEventWriter` 内维护一个 delta 缓冲：

- **合并键**：`(turn_index, content_id, kind)`。只有键相同的连续 `content.delta` 才合并。
- **刷新时机**（任一满足即写一行）：
  1. 来了键不同的 `content.delta`；
  2. 来了任何非 delta 事件 —— 先刷缓冲再写该事件，保证行顺序与事件顺序一致；
  3. 缓冲最早一段的到达时间距今 ≥ `DELTA_FLUSH_MS`（150ms），在每次 `ingest` 时检查；
  4. 缓冲文字 ≥ `DELTA_FLUSH_CHARS`（1024 字符）；
  5. 显式 `writer.flush()`：`pi_runtime` 读帧循环每次 `read_frame` 超时（`CANCEL_POLL_INTERVAL_SECONDS` = 1s）时调用，使模型停顿时缓冲文字最多滞后约 1 秒；运行结束的 `finally` 中也调用一次，保证任何退出路径都不丢缓冲。
- **合并行内容**：`data` 取第一段的 payload，`delta` 为各段按序拼接；信封（`event_id`、`engine_sequence`、`occurred_at` 等）取最后一段，表示"截至该序号的文字"。
- 序号去重（`_highest_sequence`）仍按单个事件进行，不受合并影响。
- 两个阈值是 writer 模块常量，不做配置项。

拼接满足结合律，以下读取方无需改动：历史投影（`topic_task_store.py:196`）、前端流式渲染与 `deltaReplayEquivalence.spec.js`、`pi_runtime` 的 `answer_parts`（它读原始帧，不读库）。

`finished` 任务结束时的 delta 删除保留不变，只是要删的行少了一个数量级；中断任务的 delta 照旧保留。

### 3.2 SSE 亚秒轮询

- `run_events_stream_poll_interval_seconds` 改为 `float`，默认 `0.25`。
- `routes.py` 的下限改为 `max(0.1, float(...))`。
- `since_ping` 累加改为浮点，ping 间隔逻辑不变。

查询走 `idx_sdk_record_task_id (task_id, id)` 范围扫描，每个打开的 SSE 连接每秒 4 次轻量查询。

### 3.3 超时链

不影响：ping 间隔（默认 10s）、反向代理读写超时、后端 run 预算和 idle 超时都不变。writer 的 1 秒兜底刷新复用现有读帧超时，不新增计时器。

## 4. 效果

| 项 | 现状 | 改后 |
| --- | --- | --- |
| delta 写入 | 每个 token 片段一行 | 每 150ms 或 1024 字符一行，约少一个数量级 |
| 前端收到文字的间隔 | ≤ 1s | ≤ 0.25s + 150ms 合并窗口 |
| 中断任务的残余文字 | 保留在 MySQL | 不变 |
| SSE 协议、`after_id`、表结构 | — | 不变，无迁移 |

## 5. 未采用：Redis Streams 承载 delta

本 PR 初稿提出 delta 只写 Redis Stream（`MAXLEN ~2000`、终态 `EXPIRE 300`），评审后放弃，原因：

1. 中断任务的残余文字只存在于 delta，5 分钟过期后历史回放会丢失这段文字。
2. SSE `id` / `after_id` 是 MySQL 自增 id；Redis Stream id（`<ms>-<seq>`）与之不能互换，重连续传需要另建统一序号。
3. `MAXLEN` 截断加"Redis 优先、key 不在才查 MySQL"的读法，长回答中途刷新会缺开头的文字和早期工具事件。
4. 仅在终态设置 TTL，进程中途崩溃的任务 key 永不过期。
5. `PiEventWriter.ingest` 是同步调用，无法直接使用 `redis.asyncio`。
6. 需要"Redis + MySQL 兜底 + 开关"两条并行路径，与仓库"一条已验证主路径"的规则冲突。

合并写入已把写入量降一个数量级。若之后在真实负载下 MySQL 写入仍是瓶颈，再基于实测数据重新评估。
