# DataAgent 待办

来源：2026-10-27 从 opendataworks docs/TODO.md 迁出；原设计和报告位于来源仓库。

## DataAgent 运行时与插件

### 定时消息（会话定时任务）功能完善

- **现状**（2026-10-27 核实）：只有后端。`/message-schedule` 接口（增删改查、执行日志）和协调器里的定时循环都在，数据存于 `da_agent_message_schedule`；前端 `dataagent-frontend/src/api/nl2sql.js` 封装了 `scheduleApi`，但没有任何页面或菜单调用，用户无入口。来源：2026-03-23 提交 `8bb5d774`。
- **身份**：定时消息以所属会话的所有者（`da_agent_topic.auth_user_id`）身份执行，和手动发消息走同一条"人 EXPLAIN → 智能体执行"路径；[身份设计](https://github.com/MingkeVan/opendataworks/blob/828eb2f8/docs/design/2026-10-26-dataagent-datasource-identity-design.md)。
- **要做**：决定是否正式提供定时任务。若提供，需要单独的定时任务菜单（列表、创建/编辑 cron、启停、执行记录），并按中大型变更先写设计和计划；若不提供，删除后端接口、定时循环、两张表和前端封装。

### 插件管理与导入（暂缓，后续专门实施）

- **现状**（2026-10-23）：只有设计和计划，没有代码改动。见 [插件管理设计](https://github.com/MingkeVan/opendataworks/blob/828eb2f8/docs/design/2026-10-22-dataagent-plugin-management-design.md) 和 [计划](https://github.com/MingkeVan/opendataworks/blob/828eb2f8/docs/plans/2026-10-22-dataagent-plugin-management-plan.md)。已拍板：skill folder 加 `<plugin_id>__` 前缀；升级立即生效；第一阶段拒绝引用 `${CLAUDE_PLUGIN_ROOT}` 的 MCP；认证关闭的部署照常允许导入。
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

### 运行时迁到 `pi-coding-agent` SDK

- **现状**：`dataagent-runtime-pi` 只用了 `pi-agent-core`。commands、子代理、workflow、hooks 都依赖 `pi-coding-agent` 的扩展系统。
- **要做**：单独写设计文档。需要重写 `kernel/cell.ts`，把工作区边界、结果折叠、上下文压缩、工具结果注册改写成扩展事件；关闭 SDK 默认的编码工具、会话文件、项目信任和 `~/.pi` 配置；子代理示例会启动独立的 `pi` 进程，需要重新接入边界、取消和事件记录；hooks 需要另做安全评审。
- **进展（2026-10-25）**：用户已确认迁移方向，设计见 [pi-coding-agent 迁移设计](https://github.com/MingkeVan/opendataworks/blob/828eb2f8/docs/design/2026-10-25-pi-coding-agent-migration-design.md)，计划见 [实施计划](https://github.com/MingkeVan/opendataworks/blob/828eb2f8/docs/plans/2026-10-25-pi-coding-agent-migration-plan.md)，第 0 步是试验。
- **暂缓（2026-10-26）**：先完成问题卡片与计划卡片。之后与 [服务化架构](https://github.com/MingkeVan/opendataworks/blob/828eb2f8/docs/design/2026-10-25-dataagent-service-architecture-design.md) 的重构一起做，两份设计改的是同一批模块：技能 ↔ `getSkills`，隔离 ↔ 工具 `operations`，凭据与 MCP ↔ 宿主侧 MCP 扩展，会话状态 ↔ 会话条目存 MySQL。
  - 开工前先定：SA-02（是否只保留 Pi 引擎；保留 Claude Code 引擎的话，凭据、隔离、会话都要做两套）；SA-01（隔离方式，需要生产宿主机的 `uname -r`，以及容器是否允许用户命名空间）。
  - codex 评审结论记在迁移设计第 8 节，重点：MCP 扩展会另外读全局 `mcp.json`；工具白名单要列全；会话条目恢复不了执行中的工具调用；新增的帧和对话卡片是跨层变更。
  - 建议顺序：试验 → 换内核 + `run.json` + 按本轮列技能 → MCP 和凭据移到编排侧 → 会话条目存库、删除 `home/` → 用 `operations` 接新隔离、删除文本规则；技能钉版本可以随时单独做。
  - **验收目标（2026-10-26 用户确认）：DataAgent 重启不影响用户会话。** 重启前等待中的问题卡片和计划卡片，重启后仍可回答，回答后接着原来的运行继续，不要求用户重发消息；重启前已完成的对话完整保留工具上下文。下面的 PQ-06 并入这次升级，不单独在现有运行时上做。

### 等待用户时释放运行资源（卡片设计 PQ-06）

- **现状**（2026-10-26）：问题卡片和计划卡片等待期间，Pi Cell 进程和执行槽位一直占着，只暂停总超时和空闲超时。`park` 的计划会一直等待；服务重启后，等待中的运行按现有设计结束为 `suspended`，用户要重新发送消息。
- **要做**：等待时释放进程；用户回答后从事件日志和会话条目恢复，接着执行原来的工具调用。接续不了的，把回答作为新一轮的第一条消息自动发出。需要设计：待处理交互的记录方式、同一批里还没执行的工具、扩展状态（例如计划是否已批准）、避免重复写入。
- **关联**：[卡片设计](https://github.com/MingkeVan/opendataworks/blob/828eb2f8/docs/design/2026-10-25-dataagent-plan-and-question-cards-design.md) PQ-06；并入上面的运行时迁移与架构升级（迁移设计 MG-07、MG-D4，服务化架构 4.5），作为其验收目标“DataAgent 重启不影响用户会话”的一部分。

### MCP 工具名命名空间

- **现状**：Pi 直接使用 MCP 返回的工具名注册，没有按服务加命名空间，不同 MCP 的同名工具会冲突。插件设计里先用"冲突即拒绝"兜底。
- **要做**：设计按服务加命名空间的方案，同时兼顾 Claude 引擎和已有的技能文档。

### 系统提示落库的后续

- 管理端查看页面（接口 `GET /api/v1/dataagent/tasks/{task_id}/system-prompt` 已有）。
- `da_system_prompt` 中不再被任务引用的记录的定时清理（按"无引用且超过 N 天"）。

### 生产运行拓扑回归

- **现状**（2026-10-27）：真实模型账号已恢复，本地主进程 Pi 问数、MCP 和系统提示落库通过；生产 child-container 沙箱跳转尚未跑。后续在部署环境补测同一身份头、CLI 通道、撤权及任务持久化，不能用本地进程冒烟代替容器跳转。见[主体映射报告](https://github.com/MingkeVan/opendataworks/blob/828eb2f8/docs/reports/2026-10-26-dataagent-datasource-identity-verification.md)。

- **DataAgent 前端已有失败**（来源：2026-10-27 本轮验证）：ToolOutputRenderer 的 10 项断言与当前折叠/结果展示不一致，已在未修改 HEAD 副本复现相同 10 项失败；后续单独对齐组件与测试。其余 743 项通过；见[主体映射报告](https://github.com/MingkeVan/opendataworks/blob/828eb2f8/docs/reports/2026-10-26-dataagent-datasource-identity-verification.md)。

## 部署

- **旧数据迁移未演练**（2026-10-27 拆分复核）：`deploy/README.md` 的“从 OpenDataWorks 内置部署迁移”是手工命令，尚未在真实旧部署上执行；首次正式迁移时按步骤演练并修正文档。
- **离线包未在真实镜像上跑过**（2026-10-27）：打包与加载脚本只用伪造的 docker 验证；`oda-*` 镜像首次发布后，在一台无外网机器上完整跑一次 `create-offline-package.sh` → `load-package-and-start.sh`。

## 内嵌身份

- **宿主上报的用户可被冒充**（2026-10-27，用户决定先简单化）：widget 会话以 `X-DataAgent-User-Id` 作为执行身份，该值来自宿主页面，可被篡改或直连伪造。需要一种可验证的宿主身份（例如只信任带内部令牌、由宿主反向代理写入的用户头）。

## 评测

- **参考 SQL 无处执行**（2026-10-27 拆分复核，codex 审查发现）：三个评测器（builtin / deepeval / opik）的 `expected_result.reference_query` 仍 POST `/api/v1/nl2sql/query/execute`，该接口随平台直连代理一起删除，带参考 SQL 的用例会以 `reference_sql_failed` 失败。需要决定参考结果的来源：数据集里预先存放期望行，或由评测侧显式配置一个外部只读查询入口；然后同步改三个评测器、`dataset/schema-v2.json`、README、示例与测试。

## Plugin 打包与导入

2026-10-27：本次只使用已有 MCP 新增和 Skill 导入。Plugin 的打包、导入、接口与页面另行设计，见 docs/migration.md。

## 测试与工程卫生（迁自来源仓库，2026-10-27）

- `da_agent_draft` 表的排序规则是 `utf8mb4_0900_ai_ci`，与其他 `da_*` 表的 `utf8mb4_unicode_ci` 不一致，以后与其他表做字符串关联时会报错。
- `test_pi_runtime_e2e::test_real_cell_completes_the_protocol_round_trip` 假设环境里没有模型凭据；本机 `dataagent-backend/.env` 有凭据时请求会真实发出，导致失败。测试应显式清空凭据相关的环境变量。
- Pi 测试 `cell.init Bash timeout 2s reaches the real command` 为既有时序问题；本轮新仓库与来源 HEAD 副本单独运行均复现超时，详见下方验证补充。
- `tools/dataagent-evals/pi-kernel-spike` 仍使用 Pi `0.85.1`，作为早期验证脚本不再维护，可以考虑归档。

## 本轮验证补充（2026-10-27）

- Pi 的 Bash 2 秒时序用例在本机全量与单独运行均超时；来源仓库未修改的 HEAD 副本同样失败，保留后续稳定性治理。另一次并发全量中 channel_flush 的负向控制用例未发生预期截断，重跑通过；需使该测试不依赖机器调度时序。来源：opendataworks `docs/reports/2026-10-27-dataagent-opendataworks-decoupling-verification.md`。
- 生产 child-container 及命名 Skill 卷初始化/升级尚未实跑，需在部署环境补齐；本轮覆盖本地主进程 Pi、MCP、Java 与真实 MySQL。沿用上方生产拓扑待办。
