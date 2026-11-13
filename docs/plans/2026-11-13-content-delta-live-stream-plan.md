# 流式增量（content.delta）内存直推、不落库实施计划

日期：2026-11-13
状态：待评审
关联设计：[2026-11-13-content-delta-live-stream-design.md](../design/2026-11-13-content-delta-live-stream-design.md)

涉及栈：`dataagent-backend`（writer、执行器、SSE 路由、存储）、`dataagent-frontend`（共享 reducer）。Pi runtime、表结构、部署配置不改。

## 1. 任务

### Task 1：直播通道 `core/live_events.py`（新增）
- 进程内注册表：`open(task_id)`、`close(task_id)`、`publish(task_id, record)`、`subscribe(task_id) -> (queue, snapshot) | None`、`unsubscribe`。
- 订阅队列有界（1000），满则断开该订阅者。
- 快照来源：writer 维护的未完成块文字（通道持有对它的只读引用或由 writer 推送更新）。

### Task 2：`PiEventWriter` 改造
- 文件：`dataagent-backend/core/pi_event_writer.py`
- `content.delta`：不调用 `append_sdk_record`；累积到 `(turn_id, content_id)` 的块文字；`publish`。
- `content.completed`：写库，清除该块文字，`publish`（带 `seq_id`）。
- 其他事件：写库后 `publish({"seq_id": id})` 标记。`append_sdk_record` 需返回新行 id。
- 任一终态事件写入前调用 `flush_open_blocks()`：每个未完成块写一行 `content.delta`，`data.text` 为全量文字。
- 新增公开方法 `flush_open_blocks()`，可重复调用，无未完成块时不写。

### Task 3：执行器与 runtime
- `dataagent-backend/core/task_executor.py`：段开始 `live_events.open(task_id)`，段结束（含异常）`close`。
- `dataagent-backend/core/pi_runtime.py`：运行结束的 `finally` 调用 `writer.flush_open_blocks()`。

### Task 4：存储
- `dataagent-backend/core/topic_task_store.py`：
  - `append_sdk_record` 返回 `lastrowid`；
  - 删除 `_delete_task_content_deltas` 及其调用与日志；
  - 历史投影：`content.delta` 带 `text` 时覆盖块文字；
  - 共享夹具 `contracts/sdk-block-projection/cases.json` 增加"中断块存为一行全量文字"用例。

### Task 5：SSE 路由
- `dataagent-backend/api/routes.py` `_stream_sdk_events`：按设计 4.3 实现"订阅 → 补库 → 快照 → 消费队列 → 通道关闭后回到轮询"；无通道时沿用现有轮询。

### Task 6：前端 reducer
- `dataagent-frontend/packages/agent-conversation/src/core/agentEvents/reducer.js`：`content.delta` 的 `data.text` 存在时覆盖块内容，否则追加。
- 契约说明：在 `contracts/agent-events/v1/README.md` 记录 SSE 快照形态（`content.delta` + `text`，仅直播、不落库）。

### Task 7：文档与待办
- `docs/TODO.md`：记录"多实例部署时，非执行实例的 SSE 只能轮询持久事件、无逐字打字"，链接本设计。
- 若 AGENTS.md 或部署文档提到 delta 删除，同步更新。

## 2. 测试

- `tests/test_pi_event_writer.py`：
  - delta 不写库，块文字累积正确，`publish` 被调用；
  - `content.completed` 写库并清空块文字；
  - `run.failed` / `run.cancelled` 前每个未完成块写一行全量 `content.delta`，顺序在终态事件之前；
  - `flush_open_blocks()` 可重复调用、无块不写；
  - 中断任务经历史投影得到的文字与生成文字一致。
- 新增 `tests/test_live_events.py`：订阅快照原子性、有界队列断开、`close` 后订阅返回 `None`。
- `tests/test_routes_contract.py`：
  - 直播模式：补库 + 快照 + 队列事件顺序正确，持久记录不重复；
  - 中途重连（`after_id` > 0）：快照覆盖，不重复不缺字；
  - 通道关闭后回到轮询并在终态结束；无通道时行为与现在一致。
- `tests/test_pi_runtime_contract.py`：`finally` 调用 `flush_open_blocks()`。
- 存储：完成任务不再调用删除；`append_sdk_record` 返回 id。
- 前端：reducer 单测覆盖 `text` 覆盖与 `delta` 追加；`deltaReplayEquivalence.spec.js`、`historyReplayEquivalence.spec.js` 原样通过。

## 3. 本地端到端冒烟

按 AGENTS.md 的 intelligent-query 冒烟方法（本地 MySQL `dataagent`、Redis `127.0.0.1:6379`、`.venv-py313`、Pi 构建、sandbox 镜像、真实模型）：

1. `POST /api/v1/nl2sql/tasks` 返回 `accepted=true` 与 `task_id`；状态 `waiting → running → success`。
2. 消费 `GET /api/v1/nl2sql/tasks/{task_id}/sdk-events/stream`：文字逐段即时到达。
3. 运行中断开，以 `after_id` 重连：快照补齐，后续文字无重复无缺失。
4. 完成后：`GET /api/v1/nl2sql/topics/{topic_id}/messages` 有最终助手消息；`SELECT count(*) FROM da_agent_sdk_record WHERE task_id=? AND event_type='content.delta'` 为 0。
5. 取消路径：运行中 `POST /api/v1/nl2sql/tasks/{task_id}/cancel`；该任务的 delta 行等于未完成块数，历史消息显示已生成的文字。
6. 前端：主站与 Widget 各跑一次，生成中刷新页面，文字从快照恢复后继续。
7. 记录环境（MySQL、Redis、解释器、是否真实模型）与每项结果。

## 4. 上线与回退

- 无迁移、无开关；前后端同一次发布（快照依赖前端 reducer 覆盖逻辑）。
- 已有数据：历史中断任务的多行 delta 照旧可读；发布时正在运行的任务随进程重启中断，按现有中断语义处理。
- 回退：revert 本次提交。回退后旧代码读到的"中断任务一行 delta"与多行 delta 格式相同。
