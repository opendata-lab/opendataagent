# Agent Stop 与真实 OpenDataWorks 验证

日期：2026-11-04（America/Los_Angeles）。关联[运行时设计](../design/2026-11-03-agent-runtime-sandbox-architecture-design.md)、[计划](../plans/2026-11-03-agent-runtime-sandbox-architecture-plan.md)。

## 真实模型与真实 MCP

用户要求使用真实 Agent/OpenDataWorks，并限制测试花费。读取当前默认 DeepSeek `deepseek-flash`，复用现有本机 OpenDataWorks 服务账号，先无模型验证 MCP tools/list 和 orders 元数据。当前 DataAgent 没有 MCP 绑定；创建临时 provider/agent/MCP 供本次验证，未变更默认供应商或权限。

目标为 `http://127.0.0.1:8082/api/mcp/`。现有服务账号权限限于验收数据源，能读 `odwsvcac66ad6.orders`，列 `id int`、`amount decimal(12,2)`；**不是零售生产业务数据**。没有提升账号权限、创建 Token 或写入 ODW 数据。

- 一个真实 Agent 任务，最多 2 个模型回复，每次最多 512 output tokens，禁重试。
- 真实 Pi 调用 `odw_query_readonly`，执行 `SELECT COUNT(*) AS order_count, COALESCE(SUM(amount),0) AS total_amount FROM orders`，limit 1、SQL timeout 10s。
- 返回 `order_count=2`、`total_amount=30.00`；无 has_more 或截断，真实 SQL duration 9ms。
- 任务 `task_69cf7e4c58964f1fa538c209` finished，5.5s；真实工具开始/结果、中立事件及最终助手消息均落库验证。
- 两个模型回复 output tokens 分别为 301 和 79，总计 **380**；input 为 9,814 + 236，另第二轮缓存输入 9,984。不估算未核对的金额。
- 同日前一步极简 provider 连接检查另有一次最多 32 output tokens 的调用；本报告的 Agent 测试只发起这一个任务，没有失败重试。

临时话题、Agent、MCP、复制的限额 provider 及私有临时连接文件均已删除，原 provider 和默认模型保留。原始本地结果 `/tmp/dataagent-real-odw-result.json`，摘要 `/tmp/dataagent-real-odw-smoke.log`；不包含凭据。

## Stop 的实际资源检查

使用本地免费 SSE provider 与实际 backend/Pi/MySQL/Redis/Docker，脚本 `dataagent-backend/tests/runtime_cancel_smoke.py`。此部分不消耗真实模型额度，也不修改生产取消逻辑。

| 场景 | 结果 |
| --- | --- |
| 模型正在等待输出时取消 | 1.22s 到 suspended，provider 观测到客户端 disconnected，Pi PID 38558 消失，Redis lease 释放。 |
| Bash 后代进程仍运行时取消 | 1.09s 到 suspended，Pi PID 38586 消失，对应 sandbox 被删除，容器内后代同时销毁。 |
| 持久化及后续状态 | 两场景助手均 suspended；实际 SDK SSE 包含 run.cancelled 并结束；之后记录不增长、无新模型请求，无残留进程/容器。 |

复用当前本地 Python 3.13.18 `.venv-py313`、Node 22.22.0；MySQL `127.0.0.1:3326/dataagent`、Redis `127.0.0.1:6379`，Docker 已运行。免费 fixture 监听 8919，仅使用自己的临时 provider/agent/topics，清理后用户配置未改变。脚本已接入 runtime-http CI job。

API 取消 running task 的响应表示已请求停止；终态是在 Cell 退出及 sandbox 清理完成后写入。前端 SDK `cancel()` 保持订阅直到终态，不把停止本地 SSE 当成后端取消。这里不承诺撤销已完成的外部数据库操作，也不能由客户端保证供应商对已处理 token 的计费回滚。

日志 `/tmp/dataagent-cancel-smoke.log`，fixture 结果 `/tmp/cancel-smoke-6639a75c1ed8a852be/result.json`。前端 Stop/transport 45 项、Python cancel 协议 3 项定向测试通过。
