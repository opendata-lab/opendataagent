# DataAgent 待办

来源：2026-10-27 从 opendataworks docs/TODO.md 迁出；原设计和报告位于来源仓库。

## DataAgent 运行时与插件

### 定时消息（会话定时任务）功能完善

- **现状**（2026-10-27 核实）：只有后端。`/message-schedule` 接口（增删改查、执行日志）和协调器里的定时循环都在，数据存于 `da_agent_message_schedule`；前端 `dataagent-frontend/src/api/nl2sql.js` 封装了 `scheduleApi`，但没有任何页面或菜单调用，用户无入口。来源：2026-03-23 提交 `8bb5d774`。
- **身份**：定时消息以所属会话的所有者（`da_agent_topic.auth_user_id`）身份执行，和手动发消息走同一条"人 EXPLAIN → 智能体执行"路径；[身份设计](https://github.com/opendata-lab/opendataworks/blob/828eb2f8/docs/design/2026-10-26-dataagent-datasource-identity-design.md)。
- **要做**：决定是否正式提供定时任务。若提供，需要单独的定时任务菜单（列表、创建/编辑 cron、启停、执行记录），并按中大型变更先写设计和计划；若不提供，删除后端接口、定时循环、两张表和前端封装。

### 插件管理与导入（暂缓，后续专门实施）

- **现状**（2026-10-23）：只有设计和计划，没有代码改动。见 [插件管理设计](https://github.com/opendata-lab/opendataworks/blob/828eb2f8/docs/design/2026-10-22-dataagent-plugin-management-design.md) 和 [计划](https://github.com/opendata-lab/opendataworks/blob/828eb2f8/docs/plans/2026-10-22-dataagent-plugin-management-plan.md)。已拍板：skill folder 加 `<plugin_id>__` 前缀；升级立即生效；第一阶段拒绝引用 `${CLAUDE_PLUGIN_ROOT}` 的 MCP；认证关闭的部署照常允许导入。
- **评审结论**：codex 两轮评审都是"需要修改后定稿"。第二轮复核第一轮的 14 项：6 项已解决、8 项部分解决；新发现 6 项 Critical，都和"原地替换 skill 目录"这条路有关：
  1. 替换目录的各步之间存在无法可靠恢复的中断窗口（需要按 folder 记录交换意图并做幂等恢复）；
  2. `SKILLS_ROOT_DIR` 与插件仓库不在同一挂载点时 `rename` 会失败，现有部署是单独 bind mount 的；
  3. 沙箱暖容器挂载的是具体 skill 目录，复用签名不含内容版本，升级后同一 topic 可能继续用旧 skill；
  4. 普通 MCP 创建、settings 全量保存、agent 草稿保存和发布都可能与插件安装并发写入；
  5. 其他副本启动时的全量 reindex 仍可能删除正在交换的 skill 的版本历史；
  6. 工具名冲突检查没有覆盖"已发布 agent + 立即升级"，且 MCP 能力接口返回的工具清单不完整。
  另有 6 项 Warning：命名锁的连接生命周期、settings 持久化的事务边界、启用状态取交集时内置 skill 的自动补入、Skill 调用名改写缺少可执行语法、stdio 确认令牌未绑定 env 值、"只停用后回退"不安全。
- **下次实施前先决定**：是否放弃"原地替换目录"，改为"每次安装或升级写入新的版本目录，运行时把稳定的 skill 标识解析到当前版本"。后一种方式能一并避开上面 1、2、3、5 项，代价是运行时要增加一层通用的 skill 路径解析。确定后再修订设计、重新评审。
- **可以先单独做的通用修正**：快照里的 skill 与当前启用状态取交集（计划第 0 项），注意沙箱会自动补入平台工具 skill，以及空配置与全局 fallback 的区别。

### 运行时迁到 `pi-coding-agent` SDK 与 Docker 沙箱架构整合

- **工具配置修订（2026-11-03 用户讨论）**：保留 `write/edit` 并让全部基础工具在沙箱执行，固定启用 `bash/read/write/edit/ls/find/grep`，删除旧基础工具勾选及 `allowed_tools` 业务契约，保留 Skills/MCP 选择。设计 3.2.1、3.2.4 和计划 Step 0–3 已同步，代码尚未实施；来源见[评审报告 R2](reports/2026-11-03-agent-runtime-sandbox-architecture-review.md)。
- **现状**：`dataagent-runtime-pi` 只用了 `pi-agent-core`；容器模式下经 `backend -> sandbox-runner -> sandbox_task_main -> Pi Cell -> bash` 5 层进程跳转，静态正则 `boundary_policy` 频繁误报路径拦截。
- **已拍板并完成评审修订（2026-11-03）**：
  - 合并原服务化架构与 `pi-coding-agent` 迁移方案，并按[静态评审报告](reports/2026-11-03-agent-runtime-sandbox-architecture-review.md)及后续用户讨论简化机制，设计见 [运行时整合与 Docker 沙箱架构设计](design/2026-11-03-agent-runtime-sandbox-architecture-design.md) 和 [实施计划](plans/2026-11-03-agent-runtime-sandbox-architecture-plan.md)。
  - `SA-01`（隔离与生命周期）：采用单轮按需启动的 Docker 沙箱容器（`oda-sandbox`），同段多次工具调用通过 `docker exec` 复用（命令超时或取消即结束本段并销毁容器，清理全部后代进程），本轮结束或卡片挂起时在 `finally` 中立即 `docker rm -f` 销毁；取消无隔离的宿主 `local` 回退模式。
  - `SA-02`（引擎）：只保留 Pi 引擎（迁至 `pi-coding-agent` SDK），删除 `claude-agent-sdk` 分支与每会话 `home/` 目录。
  - `SA-03`（服务收敛）：砍掉 `dataagent-sandbox-runner` 服务与 `sandbox_task_main.py`，由 `dataagent-backend`（`Pi Cell`）直接通过本地 Docker/Podman CLI 管理沙箱容器。
  - `SA-04`（工具边界）：关闭 SDK 默认宿主实现（`tools: []`），固定启用 `bash/read/write/edit/ls/find/grep` 并全部路由进 `oda-sandbox` 容器内执行（不在宿主进程调用 `node:fs` 解析模型路径），彻底删除 `boundary_policy` 正则检查与工作区 `.claude/skills` 拷贝。
  - `SA-05`（卡片续聊与串行门控）：保持串行执行与业务门控；卡片结束当前执行段，保存会话并释放资源；用户回答后在原 Topic/任务下正常续聊，不保存延迟工具队列、不改写历史结果或补跑工具批次。具体写操作批准绑定原工具和参数，结果未知不自动重放。
  - `SA-06`（大输出落盘、权限与配置瘦身）：大输出自动保存原始 JSON/文本，当前 Topic 结果目录只读挂载 `/mnt/results`，工具结果返回路径供基础工具和脚本直接读取；移除专用分页/搜索工具，不增加导出工具。旧结果一次性迁移，后端入口初始化权限并降权，精简冗余配置。
  - **要做**：按[实施计划](plans/2026-11-03-agent-runtime-sandbox-architecture-plan.md) Step 0 -> Step 5 执行代码改动与全链路冒烟验收。

### 等待用户时释放运行资源（卡片设计 PQ-06）

- **现状**（2026-10-26）：问题卡片和计划卡片等待期间，Pi Cell 进程和执行槽位一直占着，只暂停总超时和空闲超时。`park` 的计划会一直等待；服务重启后，等待中的运行按现有设计结束为 `suspended`，用户要重新发送消息。
- **要做**：按新版设计 3.4 实施：等待时结束本执行段并保存会话，回答后在原任务下以新输入续聊，保留计划批准和预算；同批未执行工具明确记为未执行，交给模型重新决定，不实现断点补跑。写确认仅执行已批准的具体操作，未知结果不自动重放。
- **关联**：[卡片设计](https://github.com/opendata-lab/opendataworks/blob/828eb2f8/docs/design/2026-10-25-dataagent-plan-and-question-cards-design.md) PQ-06；并入上面的运行时迁移与架构升级（迁移设计 MG-07、MG-D4，服务化架构 4.5），作为其验收目标“DataAgent 重启不影响用户会话”的一部分。

### MCP 工具名命名空间

- **现状**：Pi 直接使用 MCP 返回的工具名注册，没有按服务加命名空间，不同 MCP 的同名工具会冲突。插件设计里先用"冲突即拒绝"兜底。
- **要做**：设计按服务加命名空间的方案，同时兼顾 Claude 引擎和已有的技能文档。

### 系统提示落库的后续

- 管理端查看页面（接口 `GET /api/v1/dataagent/tasks/{task_id}/system-prompt` 已有）。
- `da_system_prompt` 中不再被任务引用的记录的定时清理（按"无引用且超过 N 天"）。

### 生产运行拓扑回归

- **现状**（2026-10-27）：真实模型账号已恢复，本地主进程 Pi 问数、MCP 和系统提示落库通过；生产 child-container 沙箱跳转尚未跑。后续在部署环境补测同一身份头、CLI 通道、撤权及任务持久化，不能用本地进程冒烟代替容器跳转。见[主体映射报告](https://github.com/opendata-lab/opendataworks/blob/828eb2f8/docs/reports/2026-10-26-dataagent-datasource-identity-verification.md)。

- **前端测试与组件对齐**（来源：2026-10-27 本轮验证）：ToolOutputRenderer 的 10 项断言与当前折叠/结果展示不一致，已在未修改 HEAD 副本复现相同 10 项失败；先确认是组件回归（运行中工具不再自动展开、展开后缺命令行）还是测试过时，再对齐。疑由 opendataworks `d8d434a1`（消息 UI 抽入 SDK 包）引入。CI（`.github/workflows/ci.yml`）暂时排除该 spec，对齐后删掉排除项。其余 743 项通过；见[主体映射报告](https://github.com/opendata-lab/opendataworks/blob/828eb2f8/docs/reports/2026-10-26-dataagent-datasource-identity-verification.md)。

## 部署

- **旧数据迁移未演练**（2026-10-27 拆分复核）：`deploy/README.md` 的“从 OpenDataWorks 内置部署迁移”是手工命令，尚未在真实旧部署上执行；首次正式迁移时按步骤演练并修正文档。
- **离线包未在真实镜像上跑过**（2026-10-27）：打包与加载脚本只用伪造的 docker 验证；`oda-*` 镜像首次发布后，在一台无外网机器上完整跑一次 `create-offline-package.sh` → `load-package-and-start.sh`。

## 内嵌身份

- **签名用户上下文与Portal委托接入延期（2026-11-01用户决定）**：DataAgent 当前仍会注入`X-Agent-User-Id`和`X-Agent-Id`；OpenDataWorks 最新接入使用服务账号 Bearer Token，MCP 只转发可选用户头，不再使用调用方`X-Agent-Id`认证。不实现新的OAuth／JWT委托或每智能体Portal Token，也不增加缺用户时的默认账号。本机定向测试允许显式指定已登记用户，不能把固定身份部署为全部用户的身份。后续参考[Aily自定义MCP](https://aily.feishu.cn/hc/1u7kleqg_73ahi33a)的签名用户上下文：先验证宿主用户来源，再由后端逐请求注入可信身份，Portal验签并校验调用方绑定；独立登录需统一登录或明确账号绑定。凭证只发目标MCP、不进入模型环境，并覆盖会话归属／撤销／并发不串权。关联 OpenDataWorks [主体映射设计](https://github.com/opendata-lab/opendataworks/blob/main/docs/design/2026-10-26-dataagent-datasource-identity-design.md)第9.15节（当前委托限制）与第11节（服务账号接入）；当前未实现，多用户可信身份缺口保持开放。

- **宿主上报的用户可被冒充**（2026-10-27，用户决定先简单化）：widget 会话以 `X-DataAgent-User-Id` 作为执行身份，该值来自宿主页面，可被篡改或直连伪造。需要一种可验证的宿主身份（例如只信任带内部令牌、由宿主反向代理写入的用户头）。

## 评测

- **参考 SQL 无处执行**（2026-10-27 拆分复核，codex 审查发现）：三个评测器（builtin / deepeval / opik）的 `expected_result.reference_query` 仍 POST `/api/v1/nl2sql/query/execute`，该接口随平台直连代理一起删除，带参考 SQL 的用例会以 `reference_sql_failed` 失败。需要决定参考结果的来源：数据集里预先存放期望行，或由评测侧显式配置一个外部只读查询入口；然后同步改三个评测器、`dataset/schema-v2.json`、README、示例与测试。

## Plugin 打包与导入

2026-10-27：本次只使用已有 MCP 新增和 Skill 导入。Plugin 的打包、导入、接口与页面另行设计，见 docs/migration.md。

## 测试与工程卫生（迁自来源仓库，2026-10-27）

- **Pi 现有依赖审计告警**（来源：2026-11-03 构建审查）：未改动的 Pi lockfile 在 npm audit --omit=dev 下报告 9 项（5 moderate、3 high、1 critical），涉及直接依赖 @modelcontextprotocol/sdk 及传递依赖 body-parser、proxy-addr、zod。后续单独评估兼容升级并回归 MCP 协议、身份和工具执行；不在本轮构建优化中自动 npm audit fix。见[验证报告](reports/2026-11-03-ci-build-optimization-verification.md)。
- **构建优化的最终双架构验收**（来源：2026-11-03 本地验证边界）：本机 pi-cell 的 Linux arm64 构建通过，但完整 amd64/arm64 后端与 runner 最终镜像、远程缓存和发布尚待本次远程工作流确认。通过后将结果移至已完成；见[设计](design/2026-11-03-ci-build-optimization-design.md)、[计划](plans/2026-11-03-ci-build-optimization-plan.md)与[验证报告](reports/2026-11-03-ci-build-optimization-verification.md)。

- `da_agent_draft` 表的排序规则是 `utf8mb4_0900_ai_ci`，与其他 `da_*` 表的 `utf8mb4_unicode_ci` 不一致，以后与其他表做字符串关联时会报错。
- `test_pi_runtime_e2e::test_real_cell_completes_the_protocol_round_trip` 假设环境里没有模型凭据；本机 `dataagent-backend/.env` 有凭据时请求会真实发出，导致失败。测试应显式清空凭据相关的环境变量。
- Pi 测试 `cell.init Bash timeout 2s reaches the real command` 为既有时序问题；本轮新仓库与来源 HEAD 副本单独运行均复现超时，详见下方验证补充。
- Pi 测试 `timeout stops background descendants and closes their output pipes`（`tool_env.test.js`）偶发失败：2026-10-28 本机连续 3 次 `npm test` 失败 1 次。CI 若因此变红，需让它不依赖调度时序。
- README 还没有界面截图；前端图标文件仍沿用 `opendataworks-icon-*` 命名。来源：2026-10-28 README 整理。
- `tools/dataagent-evals/pi-kernel-spike` 仍使用 Pi `0.85.1`，作为早期验证脚本不再维护，可以考虑归档。

## 本轮验证补充（2026-10-27）

- 2026-11-03 再验仍为相同两项：全量 182/184，通过定向重跑 channel_flush，Bash 2 秒仍失败，详见[构建验证报告](reports/2026-11-03-ci-build-optimization-verification.md)。Pi 的 Bash 2 秒时序用例在本机全量与单独运行均超时；来源仓库未修改的 HEAD 副本同样失败，保留后续稳定性治理。另一次并发全量中 channel_flush 的负向控制用例未发生预期截断，重跑通过；需使该测试不依赖机器调度时序。来源：opendataworks `docs/reports/2026-10-27-dataagent-opendataworks-decoupling-verification.md`。
- 生产 child-container 及命名 Skill 卷初始化/升级尚未实跑，需在部署环境补齐；本轮覆盖本地主进程 Pi、MCP、Java 与真实 MySQL。沿用上方生产拓扑待办。

## 已完成

- **评审后简化方案同步（2026-11-03）**：原“简化待同步”已完成文档修订，采用卡片结束当前段后续聊、大输出自动落盘返回 `/mnt/results` 路径、7 个基础工具固定启用并保留编辑能力；删除延迟批次补跑及专用导出路径，统一迁移、超时清理、协议和验收要求。结果见[设计](design/2026-11-03-agent-runtime-sandbox-architecture-design.md) 3.2–3.7、[计划](plans/2026-11-03-agent-runtime-sandbox-architecture-plan.md)和[评审修订](reports/2026-11-03-agent-runtime-sandbox-architecture-review.md)。仅文档检查通过，代码和端到端验收仍由上方运行时改造待办跟踪。

- **运行时与沙箱方案评审补齐（文档修订，2026-11-03）**：对照[静态评审报告](reports/2026-11-03-agent-runtime-sandbox-architecture-review.md)的 R1–R8 共 8 项问题，已在[设计文档](design/2026-11-03-agent-runtime-sandbox-architecture-design.md)（3.2–3.6、4.1–4.2 节）与[实施计划](plans/2026-11-03-agent-runtime-sandbox-architecture-plan.md)（Step 0–Step 5）中逐项补齐机制、涉及文件与验收标准；代码实现与新架构冒烟归入上方“运行时迁到 `pi-coding-agent` SDK 与 Docker 沙箱架构整合”跟踪。
