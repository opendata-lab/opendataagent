# Pi 运行时实现收敛验证（2026-11-04）

来源：用户对 PR #5 提交 `7600273` 的简化建议。先更新同主题[设计](../design/2026-11-03-agent-runtime-sandbox-architecture-design.md)和[计划](../plans/2026-11-03-agent-runtime-sandbox-architecture-plan.md)，再修改实现。本报告描述收敛后的行为；上一份[评审修正报告](2026-11-04-agent-runtime-pr-review-fixes.md)记录当时版本。

## 最终实现

| 建议 | 实施结果 |
| --- | --- |
| 撤回 parked 时合并附件和更新消息 | 已撤回。改用既有 checkpoint state 保存任务起始工作区快照，任务终态统一 diff；无挂起消息状态覆盖，也不写入半截 content。保留“先写文件再追问”的合法流程，旧任务已有文件不会混入本次附件。 |
| 去掉每段 build_sandbox_config 的线程调用 | 已移除。接单前同步预热挂载缓存，再启动 coordinator；本地宿主不 inspect，容器仅首次 inspect，后续段同步读取缓存。这样消除每段调度开销，也不让首次 inspect 阻塞正在处理的请求。 |
| 去掉 sandbox_state / onLifecycle | 已删除。利用 main.ts 既有顺序：Cell.run 的 finally 完成清理后才发送终态或 run.parked。收到完成帧后跳过 Python 清理；未收到则先结束 Pi 子进程，再执行 Docker 兜底清理。 |
| 删除未验证 Podman 兼容分支 | sandbox 只支持 Docker；移除 Podman 选择、回退和容器环境标记。配置类型、TypeScript、README、AGENTS 和活动设计/计划同步。Redis 本地引导规则不属于 sandbox 支持范围。 |
| 删除 read 的字节模式和常驻尾注 | 已删除 byteOffset/byteLimit，恢复 offset/limit 按行读取；仅超出预览上限才引用原文件并提示用更小行范围或 Bash/Python 处理单行大文件。普通小文件输出干净，read 和小错误不生成重复结果文件。 |
| 删除卡片接口逐条 to_thread 和专用 AST 检查 | 每次请求改为一次整体同步持久化调用，之后异步立即入队；删除逐条调用 AST 检查，增加行为测试证明数据库操作不会阻塞事件循环。直接将同步数据库调用放回 async handler 会重新引入阻塞，因此没有采用该部分建议。 |
| 删除 reserveNextTurn 状态 | 已删除。批准工具使用发起批准的既有 turnOffset；下一次真实模型调用自然开始下一轮。运输中断不再留下人工合成的 turn.started。 |
| 删除重复 usage 累加 | 仅由 execute_pi_run 逐事件累计，writer 只写事件。内部 segment commit 显式接收用量，checkpoint 与最终消息保持跨段总量一致。 |
| 其它清理 | 删除未使用的 HANDSHAKE_TIMEOUT_SECONDS 常量；实际总运行/无进展超时保持原有控制路径。 |

## 核实的前提

- AskUserQuestion 没有“只允许执行前调用”的阶段限制，MCP 审批也可以发生在分析和文件生成之后，因此不能靠该假设删掉跨段附件保证。用任务起始快照替换每段合并既保留终态消息契约，也保留文件正确性。
- lru_cache 缓存命中不消除第一次同步 inspect 的阻塞；接单前预热使该成本留在启动阶段，并避免并发首次未命中。
- 计划模式的 write/edit 和 MCP 写调用受 beforeToolCall 约束；DATAAGENT_PLAN_STATE=pending 本身不将 workspace 变成只读，也不能保证任意 Bash 命令不生成文件。该已有边界另列 [TODO](../TODO.md)，本次未扩展计划模式行为。

## 验证

- Python 全量 **603 passed**，无跳过；Python 3.13.18，`dataagent-backend/.venv-py313`。日志 `/tmp/dataagent-simplify-python-full.log`；最终启动调整后定向检查也通过。
- Pi 全量 **96 passed**，无跳过；nvm Node 22.22.0，Docker `oda-sandbox:test`。日志 `/tmp/dataagent-simplify-runtime-full.log`。
- HTTP：真实 FastAPI/coordinator/Pi/MCP/MySQL/Redis/SSE 路径通过七工具、1000 行 Excel/图表、问题/计划/写确认、重复提交、重启续聊、明确服务端失败和未知写操作停止重放、超时与取消。日志 `/tmp/dataagent-simplify-http-final.log`。
- 附件用例验证等待时助手消息仍为 running，content/attachments 未提前更新；重启续聊后同时包含前后两段的新文件，并排除任务之前已存在的 output/pre-existing.txt。用量与所有已持久化 usage.updated 事件之和一致。
- 独立 Stop smoke：模型流取消 **1.00s**、沙箱任务取消 **1.13s**；Pi/Sandbox 消失、Redis lease 释放、任务/消息/SSE 终态且记录稳定。额外对已创建沙箱的 Pi 发送 SIGKILL，**0.16s** 内兜底清理沙箱并保存失败终态。日志 `/tmp/dataagent-simplify-stop.log`。
- 真实容器：启动预热、UID/GID 1000、socket、daemon 挂载源映射、新/复用卷、会话继续、旧结果迁移、旧镜像回退及再升级通过。日志 `/tmp/dataagent-simplify-container-final.log`。测试镜像 `oda-backend:simplify-test` 复用已验证依赖层，清除旧源码后放入当前 backend 和编译后的 Pi；本次没有依赖或生产 Dockerfile 改动。
- HTTP/Stop 环境：隔离 backend `127.0.0.1:8901`，MySQL `127.0.0.1:3326/dataagent_simplify_8f5122aaf5`，Redis `127.0.0.1:6379` DB 14（现有 Docker Redis），fixture provider 8919 / MCP 8920。容器 smoke 使用自建隔离 schema/network/volumes 和 8902 端口。
- **未使用真实 provider 凭据，未发起付费模型调用。** 用户 3001 聊天记录和模型配置保留；测试只创建并清理自己的会话、配置、进程、卷及 schema。前端未修改；空“深度思考”展示待办单独跟踪。
