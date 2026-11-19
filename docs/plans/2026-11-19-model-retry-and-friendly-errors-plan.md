# 模型调用自动重试与失败提示包装计划

日期：2026-11-19
设计：[2026-11-19-model-retry-and-friendly-errors-design.md](../design/2026-11-19-model-retry-and-friendly-errors-design.md)
涉及：运行时（`dataagent-runtime-pi`）、后端（`dataagent-backend`）、前端 SDK（`dataagent-frontend/packages/agent-conversation`）、事件契约。

## 任务

1. **契约**：`contracts/agent-events/v1/neutral-event.schema.json` 枚举加 `run.retrying`，README 说明载荷。
2. **运行时**
   - `src/kernel/sdk-session.ts`：开启重试（10 次，1 秒起，单次上限 8 秒）。
   - `src/protocol/frames.ts`：`AgentEventType` 加 `run.retrying`。
   - `src/kernel/event-normalizer.ts`：记录当前轮是否有工具事件，提供可丢弃的轮次 id。
   - `src/kernel/cell.ts`：订阅会话的 `auto_retry_start` 发 `run.retrying`（带 `discard_turn_id`），被丢弃的轮次不计入 `max_turns`；`cancel()` 调 `session.abortRetry()`；模型错误的 `message` 改为可读文字，原文放 `detail`。
3. **后端**
   - `core/pi_event_writer.py`、`core/topic_task_store.py`（历史投影）、`core/pi_runtime.py`（最终答案）：按 `discard_turn_id` 丢弃被打断的文字；共享夹具加一例。
   - `core/pi_runtime.py`：`CELL_LOSS` 文案去掉代码，`detail` 带退出码与 stderr 末尾；协议类错误和未知异常统一为"后台服务异常，请联系管理员"，原文放 `detail`。
4. **前端 SDK**：reducer 处理 `run.retrying`（设置/清除 `retryText`），`MessageItem.vue` 活动提示优先显示 `retryText`；`CELL_LOSS` 兜底文案更新。
5. **Backlog**：`docs/TODO.md` 记录 OntoFoundry 更新 SDK 依赖、前端既有失败用例。

## 验证

- 运行时（`npm test`）：可重试错误两次后成功，出现两条 `run.retrying` 且最终 `run.completed`；输出一半后断开，`discard_turn_id` 指向半段文字所在轮次、新回复在之后的轮次；失败调用不计入 `max_turns`（去掉修复时该用例失败）；重试等待中取消立即结束；不可重试错误不重试，原文进 `detail`。重试用尽（约 63 秒）不写自动化用例，与不可重试错误共用同一段文案逻辑。
- 后端（全量 pytest）：`CELL_LOSS` 与协议错误文案；writer 丢弃被打断轮次的未完成块；最终答案不含被打断的文字；共享投影夹具新增一例。
- 前端（全量 vitest）：reducer 覆盖重试提示的设置与清除、被打断轮次的删除；共享投影夹具。

## 上线与回退

- 运行时与后端随镜像发布，无数据迁移。
- 回退：恢复 `retry: { enabled: false }` 即关闭重试；旧前端忽略未知事件类型，`run.retrying` 不会破坏旧版本渲染。
