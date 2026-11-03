# DataAgent 运行时整合与 Docker 沙箱架构实施计划

日期：2026-11-03

状态：设计已按用户讨论简化，尚未实施或完成 SDK 验证。

设计文档：[2026-11-03-agent-runtime-sandbox-architecture-design.md](../design/2026-11-03-agent-runtime-sandbox-architecture-design.md)

关联评审：[2026-11-03-agent-runtime-sandbox-architecture-review.md](../reports/2026-11-03-agent-runtime-sandbox-architecture-review.md)

## 影响栈范围

- **DataAgent Pi 运行时（`dataagent-runtime-pi/`）**：升级 `pi-coding-agent@1.4.0`；固定注册 7 个沙箱基础工具；大输出自动落盘并返回文件路径；卡片结束本段，回答后从同一会话继续推理；保留事件、结构化结果与业务门控。
- **Python 后端（`dataagent-backend/`）**：删除 Runner/Claude SDK 路径；持久化会话和交互；回答后调度新的执行段；移除 `allowed_tools` 业务契约；整合权限初始化、配置和旧结果迁移。
- **前端（`dataagent-frontend/`）**：移除基础工具勾选及其数量展示，保留 Skills/MCP 选择；验证卡片续聊、SSE、图表和附件。现有问题/计划/权限卡片载荷及中立事件保持兼容。
- **基础设施（`deploy/`、Dockerfile、CI、离线包）**：仅保留 backend 编排服务与按需启动的 `oda-sandbox` 工具镜像；本地开发也通过 Docker/Podman 执行工具。

## Step 0：验证目标 SDK 的隔离与会话边界

先验证 SDK 接口，再替换主路径；本步骤不自研另一套执行器。

- **涉及文件**：`dataagent-runtime-pi/package.json`、`package-lock.json`、`test/sdk_spike.test.ts`。
- 固定 `@earendil-works/pi-coding-agent` 为 `1.4.0`；确认内存 SettingsManager、SessionManager、凭据及自定义 resourceLoader 的实际配置接口，禁止工作区或全局配置自动发现。
- 使用 `tools: []` 关闭默认宿主实现，注册小写 `bash/read/write/edit/ls/find/grep` 沙箱工具；检查执行入口，验证 `write/edit` 可修改工作区而不能修改只读挂载或宿主文件。不得按工具名禁止编辑能力。
- 验证模型配置和凭据只驻留控制面，MCP 扩展只使用数据库下发配置，不读取额外 `mcp.json`。
- 设置串行执行；用 `[已完成工具, 问题卡, 尚未执行工具]` 验证：后续工具被阻止并形成明确的未执行结果，SDK 能停止且导出合法会话。重新导入 header/entries、恢复 leaf 后，用新的用户回答正常 prompt，已有工具结果仍在上下文中。不得修改历史结果或补跑旧批次。
- 验证停止接口不在当前工具回调内自等待，不假定 `turn_end` 的返回值能终止循环；验证问答和计划卡不会在下一次模型请求前继续执行副作用工具。
- 验证保留现有中立事件、前端图表/结构化结果通道和折叠溯源。SDK 方法名和工具曝光方式以实际导出类型及测试结果为准。
- **通过标准**：以上行为均经独立测试验证；失败时先修订同主题设计，不加入默认宿主执行或另一套断点补跑机制。
- **验证命令**：在仓库根目录先 `. "$HOME/.nvm/nvm.sh" && nvm use`，进入 `dataagent-runtime-pi` 后运行 `npm run build && node --test dist/test/sdk_spike.test.js` 定向验证。

## Step 1：工具箱镜像与统一沙箱执行器

- **新增** `dataagent-backend/Dockerfile.sandbox`、`requirements-sandbox.txt`，预装 `jq/rg/fd/sqlite3`、Python 和设计需要的数据包；无需后端代码、Node、Docker CLI 或任何服务凭据。
- **新增** `dataagent-runtime-pi/src/sandbox/docker-sandbox-manager.ts` 及 `test/docker_sandbox.test.ts`：
  - 明确选择可用的 Docker/Podman CLI；不可用或镜像缺失时返回 `SANDBOX_UNAVAILABLE`，不降级到宿主执行。
  - 首次调用沙箱工具时，通过本执行段的启动 Promise 拉起一个容器，后续调用复用；容器名包含执行段标识，避免同一逻辑任务的前后执行段误删彼此容器。
  - 挂载 `/mnt/workspace` 读写、当前 Topic 的 `/mnt/results` 只读，以及启用技能 `/opt/skills/<folder>` 只读；启动前创建目录，使用只读根分区、可写 `/tmp`、无网络、相同 UID:GID 和 no-new-privileges。
  - 提供 `bash/read/write/edit/ls/find/grep` 的容器内实现；模型路径不在 backend/Node 宿主解析。容器内配置 `DATAAGENT_PYTHON_BIN` 与 `SKILLS_ROOT_DIR`，验证完整技能脚本调用契约。
  - 命令超时或取消即结束当前执行段并 `docker rm -f`，停止所有后代进程；不继续复用超时容器，不使用环境变量匹配进程的清理机制。Pi Cell finally 与 backend 强杀兜底都按同一本段标识清理。
  - 使用 `--rm` 和 `sleep <run_timeout>` 限定异常退出后的容器寿命；无暖池和后台回收器。
- **验证**：真实构建 `docker build -f dataagent-backend/Dockerfile.sandbox -t oda-sandbox:test .`；定向运行沙箱测试，验证 7 个工具、挂载读写权限、断网、跨 Topic 不可见、正常路径不误拦、并发首调只起一个容器，以及超时/取消后无残留进程。

## Step 2：Pi Cell 迁移与大输出直接落盘

- **重写** `dataagent-runtime-pi/src/kernel/cell.ts`：
  - 采用 Step 0 验证过的 SDK 接口，固定注册 7 个沙箱工具并保留串行调用；Skills 由 resourceLoader 注入元数据、沙箱 read 读取正文，不再注册旧 Skill/Glob 别名。
  - MCP 仍由控制面连接，按可信任务身份注入认证和用户头。
  - 保留调用数、轮次、计划模式写门控和 MCP 写操作确认；批准绑定具体计划版本或具体操作，不能扩大权限。
  - 卡片工具返回“等待用户”结果并结束本段，阻止同批后续工具；会话收尾后导出，不保存或补跑 SDK 调度队列。
  - 回答后导入同一会话，将回答或操作实际结果作为新上下文继续正常 prompt；重复恢复不能重复追加同一 request_id 的输入。
- **修改** `src/context/result-store.ts`、`tabular-digest.ts`、`src/tools/tool-registry.ts` 和事件归一化适配：
  - 新结果按原始 JSON/文本保存在 `<topic_root>/results/`，不再转换为私有 JSONL 行格式；工具返回 `/mnt/results/<文件名>`、实际格式、大小、有界预览和原有截断标记。
  - bash 输出接收时先保存、再限制预览，覆盖成功、失败及超时部分输出；明确保存失败或存储限额，不宣称已丢弃数据能够读回。
  - 文件名由运行时生成，写入不覆盖已有文件；基础工具可在只读挂载中读同一文件，跨段路径保持稳定。
  - 移除原分页/搜索专用工具及其提示；不增加导出工具或中间复制目录。需要格式转换时由 Agent 使用普通 Python 处理，不增加报表专用运行时逻辑。
  - 保留 UI 所需的完整结构化结果、图表契约和折叠溯源；不能因模型折叠而破坏前端渲染。
- **修改** `src/protocol/frames.ts` 与 `src/main.ts`：升级到协议 v2，增加 `session.entries`、`run.parked`；明确它们与原任务等待状态的关系，验证快照大小限制及输出排空。
- **移除替代实现**：旧边界正则、旧 Skill loader/MCP client、自研上下文压缩模块；现有事件和 UI 结果适配仅在对应行为已保留并测试后删除冗余部分。
- **同步文档与测试**：提示词、技能文档、同步模板、演示数据统一工具名和 `/mnt/results` 读取方式，技能可执行引用始终使用 `"$DATAAGENT_PYTHON_BIN" "${SKILLS_ROOT_DIR}/<skill-folder>/scripts/<name>.py" ...`。
- **验证**：先 nvm use，运行 Pi 定向的会话、工具、大结果、事件/图表契约测试，再执行 `npm run build && npm test`；新增用例验证完整文件直接交给沙箱脚本消费，不能只检查摘要字段。

## Step 3：会话持久化、任务续聊与智能体配置清理

- **新增 Alembic 迁移** `20261103_000032_session_checkpoint.py`：`da_agent_session_checkpoint` 按 topic_id 保存 task_id、header_json、entries_json、leaf_id、版本号。交互、操作状态及执行预算使用业务持久化记录，不增加任意 SDK 执行状态或延迟调用队列表。
- **修改** `core/topic_task_store.py`：
  - 同一事务保存会话快照、卡片记录及等待状态；防止旧版本快照覆盖新版本，事务失败不能发布可回答卡片。
  - 用户回答按任务、当前 request_id 和等待状态条件更新，一次性持久化回答并转 waiting；重复提交返回已处理。
  - 原任务累计执行用时、工具数和模型轮次，等待不计入用时；跨段启动不重置预算。持久化当前计划版本批准信息。
  - 写操作确认记录绑定服务/工具/规范化参数；条件领取执行，保存结果后可复用。结果未知且无目标幂等能力时不自动重试。
- **修改** `core/task_coordinator.py`、`api/routes.py`、`core/user_interaction.py`：
  - 本段结束且有已提交会话/卡片时保留原逻辑任务的等待状态，并释放执行槽位和 lease。
  - 回答或既有超时策略写入决策后，将原 task_id 重新入队；扫描 waiting 弥补提交后、入队前的进程退出窗口，避免重复调度。
  - 新执行段只追加本次回答或决策，不重发原用户问题。正常卡片等待不因旧进程已释放而 run_lost；缺失快照、未知写结果或无效批准如实结束并报告。
  - 普通执行中崩溃的副作用任务不得借本次“恢复”语义自动重放；恢复逻辑应区分有稳定会话边界的卡片续聊与结果未知的中断。
- **修改** `core/pi_runtime.py`：解析 backend 可见目录和 daemon 宿主挂载源；构建 `/mnt/results` 挂载与协议 v2 输入，持久化输出快照；Pi Cell 异常退出时删除本段沙箱。
- **精简** `config.py`、`core/task_executor.py`、`core/agent_runtime.py`、`core/topic_workspace.py`：删除 Runner HTTP 与 Claude SDK 路径、工作区技能拷贝和每会话 home/logs 新建；统一预算，MCP/SQL 超时独立约束于剩余任务预算；保留已确认的数据库配置名称迁移。
- **清理配置链**：修改 `models/schemas.py`、`core/agent_profile_service.py`、`core/agent_draft_service.py`、内置种子及任务快照；移除 allowed_tools 请求、响应、草稿及运行配置，历史表列仅供旧镜像回退，不参与新版工具选择。
- **修改前端** `AgentDetailView.vue`、`AgentStudio.vue`、`src/demo/mockServer.js` 及相关测试：删除基础工具勾选、数量和表单载荷，保留 Skills/MCP 选择。验证新建、编辑、预览、发布和存量智能体加载。
- **删除** `sandbox_runner_main.py`、`sandbox_task_main.py`、`core/boundary_policy.py`、`core/claude_cli.py`、`core/sdk_block_writer.py`、`core/slash_command_cache.py`、`tools/dataagent-evals/pi-kernel-spike/`。
- **验证**：Python 使用 `dataagent-backend/.venv-py313`，先定向测试 task store/coordinator/interaction/Pi 协议和 profile/draft；前端先 nvm use，运行 `npm test -- src/views/intelligence/__tests__/AgentDetailView.spec.js src/views/intelligence/__tests__/AgentStudio.spec.js` 及实际受影响的卡片测试。

## Step 4：部署、存量结果迁移与构建流水线

- **新增** `docker-entrypoint.sh`，修改 backend Dockerfile：安装 gosu、Docker CLI；root 初始化工作区、结果目录与 skills 卷，配置实际降权进程需要的 socket 附加组后再运行服务。不修改宿主 socket 权限；降权后无访问权时明确启动失败。
- **修改** Compose、`.env.example`、部署与架构文档：删除 Runner 和 home-init；backend 挂 socket；沙箱按需挂当前 Topic 的 workspace/results 和启用 skills。支持宿主进程开发时的本地路径解析。
- **新增一次性结果迁移脚本**：停止接单后将旧 `workspace/.dataagent/results/` 内容按原文件名迁入 results；将旧 JSONL 转为标准 JSON、保留缺失元数据说明；幂等执行且不覆盖已有新结果。旧文件保留作回退，新路径不增加读取回退链。
- 存量 Topic 无会话条目时，以已有用户/助手文本历史初始化会话并说明旧结果路径对应 `/mnt/results`；旧版等待任务在此次架构切换时标为 suspended，明确与新版正常卡片续聊的重启恢复区别。
- 旧 home 停止使用但保留回退数据，不设置稳定期运行分支；清理旧技能拷贝不能删除用户业务文件。
- **修改** CI、离线打包和加载脚本，构建并装载 oda-sandbox，删除 Dockerfile.runner；移除 backend 不再需要的 Claude SDK 与沙箱专属依赖，保持主镜像功能可启动。
- **验证**：全新卷和复用卷均走真实 Compose 启动；检查降权身份/socket/挂载、旧数据转换、重复迁移无覆盖及离线镜像加载。核对代理 SSE 超时大于 1800s。

## Step 5：本地端到端与部署拓扑验收

环境遵循 AGENTS.md：Python 3.13 `.venv-py313`、nvm 指定 Node、本地 MySQL `127.0.0.1:3326/dataagent`、Redis 优先 `127.0.0.1:6379`；启用真实 Pi 构建和模型配置，启动 frontend、backend 与按需沙箱，coordinator 内嵌于 backend，不另起 worker。

1. 通过真实 HTTP 创建 Topic、提交带 `message_type/message_content/execution_mode="auto"` 的任务，确认 accepted/task_id、拾取和终态；卡片任务覆盖 `waiting → running → waiting_input/waiting_permission → waiting → running`，之后按既有状态契约结束。不能只验证 health。
2. 消费 `/api/v1/nl2sql/tasks/{task_id}/sdk-events/stream`，检查事件和最终助手消息落库、engine_kind 为 pi_agent_core，以及前端实际渲染。
3. 纯 LLM/MCP 且不调用沙箱基础工具时不创建容器。真实 NL2SQL 覆盖“最近 30 天工作流发布次数趋势”。
4. 大结果超过 16KB 和 100 行后自动落盘，工具消息包含可读的 `/mnt/results` 路径；沙箱脚本直接读取该文件生成完整报表/图表，核对文件内容、行数和 UI；上游截断必须如实标明，不把预览当完整数据。跨轮和重启后文件仍可读，无法读其它 Topic 的结果。
5. 实际执行 7 个基础工具，尤其 write/edit 正常创建和修改工作区；结果目录和技能挂载拒写，宿主测试文件不变，容器不可用不退回宿主。UI 不再有基础工具勾选，Skills/MCP 选择仍影响实际能力。
6. 问题卡/计划卡产生后进程、沙箱和执行槽位释放；重启 backend 再回答，原任务自动续聊。确认完成工具结果仍在历史，未执行工具没有被手动补跑；重复提交不重复追加输入，批准仅适用于当前计划版本，拒绝后仍处于计划模式。
7. MCP 写确认覆盖允许、拒绝及参数变更；批准后的具体操作只领取一次。模拟外部成功但本地结果未存的未知窗口，验证无幂等保障时停止自动重放。不能以本地执行标记宣称跨系统 exactly-once。
8. 取消及命令超时均结束本段、销毁沙箱，无后台后代残留；正常跨卡片续聊不重置总预算。快照过大或保存失败必须显式失败，不能发布不可恢复卡片。
9. 使用新卷、旧会话/旧结果/旧技能卷分别演练 Compose 拓扑与升级/回退，核对设计 4.2 的数据边界。

记录实际 MySQL/Redis 地址与启动方式、Python、Node、容器运行时、模型/凭据是否真实以及每个场景结果；清理冒烟会话和进程。未执行的端到端层必须在验证报告和 docs/TODO.md 明确记录，不能称为完整验证。

## 发布与回退

- 交付一个完整架构变更，Step 0–5 是实现顺序，不分阶段上线，不保留双执行主路径。
- 发布前停止接单并排空 running，保留旧卷/配置用于回退；执行数据库升级、一次性旧结果迁移并处理旧版内存等待任务，再使用新版 backend/sandbox 镜像及 Compose 启动、完成冒烟后恢复入口。
- 回退先停止入口与新版执行，恢复旧镜像/Compose。新增会话表由旧版忽略，历史消息按旧版能力读取；等待任务需重新发起，旧版不恢复新版工具上下文。
- 保留的新结果文件和用户产物不会自动转成旧版分页工具格式，旧版仅能读取原缓存目录中的旧文件；不承诺旧工具能消费新版结果路径。新版移除的 allowed_tools 业务字段对应的历史表列保留，回退不需要重建该列。
