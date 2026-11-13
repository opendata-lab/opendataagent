# 流式增量（content.delta）合并写入与 SSE 亚秒轮询实施计划

日期：2026-11-13
状态：待评审
关联设计：[2026-11-13-content-delta-batching-design.md](../design/2026-11-13-content-delta-batching-design.md)

涉及栈：`dataagent-backend`（writer、runtime 循环、SSE 路由、配置）。前端、Pi runtime、表结构、部署配置不改。

## 1. 任务

### Task 1：`PiEventWriter` 合并 delta
- 文件：`dataagent-backend/core/pi_event_writer.py`
- 新增缓冲与常量 `DELTA_FLUSH_MS = 150`、`DELTA_FLUSH_CHARS = 1024`。
- `_ingest_safe`：`content.delta` 进缓冲（键 `(turn_index, content_id, kind)`）；键变化、非 delta 事件、超时或超长时先 `flush()`。
- 新增公开方法 `flush()`：把缓冲写成一行，payload 取第一段、`delta` 为拼接、信封取最后一段；缓冲为空时什么都不做；与 `ingest` 一样不抛异常。

### Task 2：runtime 循环调用 `flush()`
- 文件：`dataagent-backend/core/pi_runtime.py`
- 读帧 `asyncio.TimeoutError` 分支（约 `pi_runtime.py:396`）调用 `writer.flush()`。
- 运行结束的 `finally` 调用 `writer.flush()`。终态事件经 `ingest` 写入，本身会先刷缓冲，`finally` 覆盖外部取消等其余路径。

### Task 3：SSE 亚秒轮询
- `dataagent-backend/config.py`：`run_events_stream_poll_interval_seconds: float = 0.25`。
- `dataagent-backend/api/routes.py` `_stream_sdk_events`：`poll_interval = max(0.1, float(...))`，`since_ping` 用浮点累加。

### Task 4：测试
- `dataagent-backend/tests/test_pi_event_writer.py`（扩充）：
  - 连续同键 delta 合并为一行，文字为按序拼接，信封为最后一段；
  - 键变化（`content_id` / `kind` / turn）各自成行；
  - 非 delta 事件前先落缓冲，行顺序与事件顺序一致；
  - 超过 `DELTA_FLUSH_CHARS` 或 `DELTA_FLUSH_MS`（注入时钟）即落行；
  - `flush()` 空缓冲无写入；
  - 合并后经历史投影得到的文字与逐条写入时一致（中断任务场景：无 `content.completed`）。
- `test_pi_runtime_contract.py`：补一条断言，读帧超时与运行结束都会调用 `flush()`。
- `test_routes_contract.py`：小数轮询间隔下 SSE 正常推送并结束。

## 2. 验证

1. 定向测试：`pytest dataagent-backend/tests/test_pi_event_writer.py` 及 runtime、路由相关测试；前端 `deltaReplayEquivalence.spec.js`、`historyReplayEquivalence.spec.js`（契约不变，应原样通过）。
2. 本地端到端冒烟（按 AGENTS.md 的 intelligent-query 冒烟方法：本地 MySQL `dataagent`、Redis `127.0.0.1:6379`、`.venv-py313`、真实模型）：
   - `POST /api/v1/nl2sql/tasks` 返回 `accepted=true` 与 `task_id`，状态经 `waiting → running → success`；
   - 消费 `GET /api/v1/nl2sql/tasks/{task_id}/sdk-events/stream`，确认文字按亚秒间隔到达，记录每条 delta 行的字数；
   - 运行中断开 SSE 并以 `after_id` 重连，文字无缺无重；
   - 完成后 `GET /api/v1/nl2sql/topics/{topic_id}/messages` 有最终助手消息，`content.delta` 行已删除；
   - 取消路径：运行中 `POST /api/v1/nl2sql/tasks/{task_id}/cancel`，确认残余 delta 行保留，历史消息显示已生成的文字；
   - 对比改动前后同一问题的 `content.delta` 行数（改动前在 `finished` 前查询）。
3. 报告注明环境（MySQL、Redis、解释器、是否真实模型）和每个冒烟项的结果。

## 3. 上线与回退

- 无迁移、无配置开关、无前端发布依赖；随后端一次发布。
- 回退：revert 本次提交。合并写入的行与逐条写入的行格式相同，回退前后的数据可互相读取。
