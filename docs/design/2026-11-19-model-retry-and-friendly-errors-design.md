# 模型调用自动重试与失败提示包装设计

日期：2026-11-19
状态：待评审
关联计划：[2026-11-19-model-retry-and-friendly-errors-plan.md](../plans/2026-11-19-model-retry-and-friendly-errors-plan.md)

## 1. 现状

- **模型调用不重试。** `dataagent-runtime-pi/src/kernel/sdk-session.ts` 创建会话时传入 `retry: { enabled: false }`（#5 引入，未记录原因）。模型服务的一次 5xx、429、过载或连接中断，都会让整个任务立即以 `PI_MODEL_ERROR` 失败。
- **Pi 自带重试，只是被关掉了。** `AgentSession` 对可重试错误（`pi-ai/dist/utils/retry.js` 的匹配表：429/5xx、overloaded、rate limit、连接重置、超时、流提前结束等）按 `baseDelayMs × 2^(n-1)`、上限 `maxAgentDelayMs` 退避重试，并发出 `auto_retry_start` / `auto_retry_end` 事件。上下文超长不走重试，由压缩处理；配额、计费类错误不重试。重试只重放模型调用，不重放工具调用；失败的那次回复从模型上下文中剔除。
- **失败提示直接暴露内部代码。** 子进程丢失时，界面显示"运行时子进程异常中断（CELL_LOSS），请联系管理员"；协议类错误（`PI_PROTOCOL_ERROR`、`PI_SETTLED_WITHOUT_TERMINAL_EVENT` 等）和未知异常直接把代码或 Python 异常原文当作提示。模型错误把服务商原文（如 `529 overloaded`）直接展示给业务用户。

## 2. 问题

1. 模型服务短暂抖动就让长任务（建模、问数）前功尽弃，用户只能手动重发。
2. 用户看到 `CELL_LOSS`、`PI_PROTOCOL_ERROR` 这类内部代码，既看不懂，也不知道该做什么。
3. 等待重试期间界面没有任何提示，看起来像卡住。

## 3. 方案

### 3.1 打开 Pi 自带的重试

`sdk-session.ts` 改为：

| 设置 | 值 | 说明 |
| --- | --- | --- |
| `enabled` | `true` | |
| `maxRetries` | `10` | |
| `baseDelayMs` | `1000` | |
| `maxAgentDelayMs` | `8000` | 单次等待上限 |

等待序列为 1、2、4、8、8、8、8、8、8、8 秒，10 次全部失败前累计等待约 63 秒。单次等待不超过 8 秒，远低于单轮空闲超时（默认 300 秒），不需要额外心跳。

取消：`Cell.cancel()` 同时调用 `session.abortRetry()`，用户取消能立即打断等待，而不是等这次退避结束。

### 3.2 新增 `run.retrying` 事件

运行时订阅 `auto_retry_start`，发出中立事件：

```json
{ "type": "run.retrying",
  "payload": { "attempt": 3, "max_attempts": 10, "delay_ms": 4000,
               "error_message": "503 Service Unavailable", "discard_turn_id": "turn-4" } }
```

`discard_turn_id` 是失败那次模型调用所在的内部轮次。Pi 每次模型调用都开一个内部轮次（`pi-agent-core` `agent-loop.js`），失败的调用在任何工具执行之前就结束了该轮，重试从新轮次开始。所以失败尝试恰好是一个只含模型输出的轮次；运行时只在该轮没有工具事件时才给出这个字段。内部轮次不是对话轮次：自动重试仍在同一个任务、同一条回复里，用户看不到新的一轮对话。

被丢弃的轮次不计入 `max_turns`，否则仅重试就可能耗尽轮数上限。

### 3.3 丢弃被打断的半段文字

模型输出一部分后断开、重试成功时，失败尝试的半段文字会出现在新回复上方。各处读者按 `discard_turn_id` 丢掉该轮内容：

| 位置 | 处理 |
| --- | --- |
| 前端 reducer（实时与回放） | 删除该轮的块和轮次 |
| 后端历史投影 `_project_sdk_records` | 删除该轮已投影的块 |
| `PiEventWriter` | 丢弃该轮未完成块，不在结束时补存 |
| `execute_pi_run` 的最终答案 | 去掉该轮的文字 |

共享夹具 `contracts/sdk-block-projection/cases.json` 新增一例，前后端投影同时受约束。

- 非终止事件，按普通事件落库，历史回放不受影响。
- 前端 reducer 收到后设置 `state.retryText = "模型服务连接异常，正在重试（3/10）…"`，并把仍在流式的块标记完成，让底部活动提示显示出来；收到下一条轮次、内容、工具或终止事件时清除。
- 界面复用现有的底部活动提示（`MessageItem.vue` 的 `dac-activity`），有 `retryText` 时显示它，否则显示原来的"正在处理…"。

不转发 `auto_retry_end`：成功时后续事件会清掉提示，失败时由终止事件处理。

### 3.4 失败提示只给可读文字，原文进 `detail`

`run.failed` 的 `message` 面向业务用户，`detail` 保留原始错误供管理员排查；`error_code` 不变，仍落库。

| 情况 | `message` |
| --- | --- |
| 重试用尽的模型错误 | 模型服务暂时不可用，已自动重试 10 次仍未成功，请稍后重试；持续出现请联系管理员 |
| 不可重试的模型错误（4xx、配额等） | 模型服务返回错误，请联系管理员 |
| `CELL_LOSS`：堆内存溢出 | 后台服务内存不足：本次处理的数据量过大，请缩小范围后重试；持续出现请联系管理员 |
| `CELL_LOSS`：被系统终止（-9/137） | 后台服务被系统终止，可能是内存超出上限，请稍后重试；持续出现请联系管理员 |
| `CELL_LOSS`：其他 | 后台服务异常中断，请稍后重试；持续出现请联系管理员 |
| 协议类错误、未知异常（`PI_EXECUTION_ERROR`） | 后台服务异常，请联系管理员 |
| 超时 | 不变（"单轮执行超时"/"单轮等待超时"） |

`CELL_LOSS` 的 `detail` 带上子进程退出码和最后几行 stderr，管理员不必翻容器日志就能看到崩溃原因。运行时 Cell 内部抛出的、本来就是中文说明的错误（轮数上限、输出截断等）保持原样。

前端 `extractErrorText` 和 reducer 里对 `CELL_LOSS` 的兜底文案同步改为"后台服务异常中断，请联系管理员"，覆盖旧记录。

## 4. 接口变化

- `contracts/agent-events/v1`：事件类型枚举新增 `run.retrying`。
- 运行时 `frames.ts` 的 `AgentEventType`、前端 `AgentEventType` 同步新增。
- `run.retrying` 的 `discard_turn_id` 可选；旧读者忽略它，只是不会丢弃半段文字。
- `run.failed` 的 `payload.detail` 由可选变为常用，字段本身已存在（`PiEventWriter` 已读取）。

## 5. 取舍

- **只重试模型调用**：子进程崩溃（`CELL_LOSS`）不自动重跑，因为工具可能已经产生副作用。
- **嵌入方**：OntoFoundry 通过打包的 `agent-conversation` SDK 渲染，需在发布新版 SDK 后更新其依赖才能显示重试提示；失败提示文字由后端生成，无需等待。
