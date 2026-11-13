# DataAgent 待办

来源：2026-10-27 从 opendataworks docs/TODO.md 迁出；原设计和报告位于来源仓库。

## DataAgent 运行时与插件

### 会话历史分页加载（大会话打开慢）

- **现状**（2026-11-13 实测）：点击会话时前端一次读取 `GET /api/v1/nl2sql/topics/{topic_id}/messages` 的全部历史（默认 `page_size=200`、`order=asc`）。本地某会话 `topic_47941a2a…` 响应 4.0 MB（接口 100–120 ms），其余 0.2–0.7 MB；慢主要在浏览器渲染整段历史。接口已支持 `page`、`page_size`（≤500）、`order=asc|desc`。
- **要做**：参考 Codex 的做法，先只加载最近一页（如 `order=desc&page_size=20`），滚动到顶部时再加载更早的一页并保持滚动位置；主站与 Widget 都改。先确认 4 MB 来自消息条数还是单条消息过大（工具输出、`records`），若是后者还需精简单条消息的返回内容。属于中等变更，实施前先写设计与计划。来源：用户反馈，2026-11-13。

### 多实例部署时的流式文字直推

- **现状**（2026-11-13）：`content.delta` 只在执行任务的进程内直推给 SSE，不落库（[设计](design/2026-11-13-content-delta-live-stream-design.md)）。当前部署是单容器单 `uvicorn` 进程，满足要求。
- **缺口**：若后端扩成多实例，打到非执行实例的 SSE 只能轮询 MySQL 持久事件，看不到逐字打字（工具、完成等结果不受影响）。
- **要做**：扩容前，按任务把 SSE 路由到持有租约的实例，或把 `core/live_events.py` 换成跨实例通道（如 Redis）；writer、存储与前端不需改动。来源：PR #37 评审，2026-11-13。

### 定时消息（会话定时任务）功能完善

- **现状**（2026-10-27 核实）：只有后端。`/message-schedule` 接口（增删改查、执行日志）和协调器里的定时循环都在，数据存于 `da_agent_message_schedule`；前端 `dataagent-frontend/src/api/nl2sql.js` 封装了 `scheduleApi`，但没有任何页面或菜单调用，用户无入口。来源：2026-03-23 提交 `8bb5d774`。
- **身份**：定时消息以所属会话的所有者（`da_agent_topic.auth_user_id`）身份执行，和手动发消息走同一条路径。2026-11-10 起 OpenDataWorks MCP 只按服务账号 Token 授权，用户标识只记审计，不再有"人 EXPLAIN"检查（[服务令牌模式设计](https://github.com/opendata-lab/opendataworks/blob/main/docs/design/2026-11-09-mcp-service-token-mode-design.md)）。
- **要做**：决定是否正式提供定时任务。若提供，需要单独的定时任务菜单（列表、创建/编辑 cron、启停、执行记录），并按中大型变更先写设计和计划；若不提供，删除后端接口、定时循环、两张表和前端封装。

### 插件管理与导入（暂缓，后续专门实施）

- **新需求：整包安装插件**（2026-11-05 用户提出）：DataAgent 能直接导入一个插件 ZIP，不再靠管理员手工拼装。第一个真实用例是 OntoFoundry v0.2.7 的 `ontofoundry-dataagent-plugin.zip`。
  - **现状**：它的 `plugin.json` 写明 `installation: manual-admin`、限制“DataAgent 暂不支持整包原生加载”。管理员现在要手工完成五步：运行 `install.sh` 把三个技能复制到 `SKILLS_ROOT_DIR`；按 `plugin.json` 的 `mcp_services` 登记 OntoFoundry MCP 并录入凭据；把 `agents/*.md` 的提示词同步到两个已有 Agent，按清单挂载技能、设 `max_turns`；停用旧技能目录；重启 DataAgent。
  - **要做**：上传插件 ZIP 后依次完成以下步骤：
    - 校验清单与 `minimum_platform_version`；
    - 安装或升级其中的技能；
    - 按 `mcp_services` 生成 MCP 登记（凭据由管理员填写，不随包分发）；
    - 按 `agents` 给出挂载建议，由管理员确认后应用到已有 Agent（不自动新建或改写 Agent）；
    - 升级、停用、回滚都不用重启；
    - 列表里显示插件版本、来源和所含技能与工具。
  - **验收**：用该 ZIP 在一台干净的 DataAgent 上走完安装，不手工复制文件、不重启；再装下一个版本，验证升级和回滚。
  - 实施前沿用下面的设计并先定“版本目录”方案。清单字段以 OntoFoundry 仓库 `integrations/dataagent/plugin.json` 为参考，两边需约定插件清单格式。

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

### 真实 provider 与业务问数最终验收

- **现状（2026-11-03）**：运行时/沙箱改造已实施。2026-11-04 真实 DeepSeek + OpenDataWorks MCP 对现有验收订单表查询 2 / 30.00 通过；Stop 的模型断流/进程/容器清理通过。当前服务账号仅有验收数据源权限，真实零售业务数据和图表报告仍未验收，见[最新报告](reports/2026-11-04-agent-stop-and-odw-verification.md)。
- **要做**：在用户明确批准进一步付费测试并配置真实业务数据源权限后运行“最近 30 天工作流发布次数趋势”，核对目标 MCP 身份、真实查询、图表和完整报告下载。来源：用户本轮要求将凭据相关验证留后；关联[设计](design/2026-11-03-agent-runtime-sandbox-architecture-design.md)、[计划](plans/2026-11-03-agent-runtime-sandbox-architecture-plan.md)、[验证报告](reports/2026-11-03-agent-runtime-sandbox-architecture-review.md)。

### MCP 工具名命名空间

- **现状**：Pi 直接使用 MCP 返回的工具名注册，没有按服务加命名空间，不同 MCP 的同名工具会冲突。插件设计里先用"冲突即拒绝"兜底。
- **要做**：设计按服务加命名空间的方案，兼顾现有 MCP 配置和技能文档；本轮已实现重复/保留工具名明确拒绝。

### 智能体可查看并选择 MCP 工具（新功能）

- **现状**（2026-11-04 核实）：智能体只能整体绑定 MCP 服务（`mcp_server_ids`），运行时注册该服务返回的全部工具，配置页看不到具体有哪些工具。`da_agent_profile.allowed_tools_json` 是旧 Claude SDK 时代的工具名列表（Skill/Bash/Read 等），Pi 运行时已不读取。Pi 自带的 MCP 探测入口（`dist/src/mcp/probe-cli.js`，后端 `resolve_mcp_probe_command`）可以列出服务的工具。
- **要做**：在智能体配置里按服务列出 MCP 工具（名称、说明、只读/写入/破坏性注解），允许只勾选部分工具；保存为智能体快照的一部分，运行时只注册被允许的工具，并对未允许的调用明确拒绝。同时决定旧 `allowed_tools_json` 是复用还是删除，并与上方“MCP 工具名命名空间”一起设计（同名工具需按服务区分）。涉及 schema、后端接口、前端配置页与 Pi 运行时，属中大型变更，先写设计与计划。来源：用户 2026-11-04 提出。

### Skill 包卫生：导入/导出过滤与安全上限

- **现状**（2026-11-04 核实）：
  - 导入只跳过顶层 `__MACOSX/` 与任意层的 `.DS_Store`；绝对路径、`..`、符号链接、空包、缺少 `SKILL.md`、多个 Skill 会整体拒绝（这些检查是对的）。
  - 导出 `export_skill_as_zip` 与重复导入判断 `_skill_tree_digest` 不做任何过滤。技能运行后目录里有 `scripts/__pycache__/*.pyc`，所以导出包会带上缓存（真实导出已复现，其余文件逐字节一致）；同一个 ZIP 再导入也会被判为“有变化”而替换已安装目录。
  - 没有大小上限：上传整体读入内存，解压不限制解压后总字节数和文件数，ZIP 炸弹可以写满磁盘（仅管理员可导入，但仍需防护）。
  - 路由 `POST /skills/imports` 是 `async`，却同步调用解压与安装，大包会卡住事件循环，期间其他请求（包括对话流）都会停顿。
- **要做**：
  1. 一条共享的排除规则，同时用于导入、导出和摘要比较：`__MACOSX/`、`._*`、`.DS_Store`、`Thumbs.db`、`desktop.ini`、`__pycache__/`、`*.pyc`、`.git/`。
  2. 导入设上限：压缩包大小、解压后总大小、文件数，按 ZIP 头声明值预检并在解压时按实际写入量强制。
  3. 导入在线程中执行（`asyncio.to_thread`）。
  4. 补往返测试（导出→导入不带缓存且判为相同）与超限测试。
- 来源：PR #8 合并前的导出验证与导入代码核对。

### 图表不支持超过三个系列

- **现状**（2026-11-05 核实）：上限在两处。图表模板 `.claude/skills/chart-visualization/assets/chart-template/line.json`、`area.json`、`scatter.json`、`radar.json` 写死 `"max_series": 3`，模型按模板只准备 3 个系列；即使传入更多，图表技能脚本 `.claude/skills/chart-visualization/scripts/build_chart_spec.py` 的 `choose_chart()` 对折线、柱状、面积、散点、组合、雷达图都用 `numeric_fields[: min(3, ...)]` 截取，最多 3 个系列；第 4 个及以后的数值列被静默丢弃，图上不提示，模型和用户都不知道数据少了，与系统提示“不得只截取部分数据”的要求冲突。另外两处相关限制：
  - 命令行只有 `--value-field` 用来把一个字段排到最前，没有参数指定要画哪些系列；
  - 只支持宽表（每个系列一列），不支持长表按某个维度列拆分系列（例如“各区域每月销售额”以 区域、月份、销售额 三列返回时，无法按区域画多条线）。
- **要做**：同时去掉模板的 `max_series: 3` 和脚本里固定的 3 个上限（或改为可配置的较大上限），超出时明确报错或提示而不是静默丢弃；增加指定系列字段的参数和按维度拆分系列（长表透视）的能力；同步 `SKILL.md`、`reference/10-chart-spec-contract.md`、前端渲染（系列多时的配色与图例）和回归测试。来源：用户 2026-11-05 提出。

### 系统提示落库的后续

- 管理端查看页面（接口 `GET /api/v1/dataagent/tasks/{task_id}/system-prompt` 已有）。
- `da_system_prompt` 中不再被任务引用的记录的定时清理（按"无引用且超过 N 天"）。

### 生产运行拓扑回归

- **现状（2026-11-03 更新）**：原 child/Runner 拓扑已由本轮 PR 移除。新版 backend + 工具专用 sandbox 在本机真实 Docker 下完成新卷/复用卷、socket、挂载、回退和再升级；模型/MCP 为本地 fixture。
- **要做**：真实 provider 与目标 MCP 配置后，在生产环境核对身份头、撤权和业务任务持久化，不能把 fixture 冒烟视为真实集成验收。来源：2026-10-27 原待办及[主体映射报告](https://github.com/opendata-lab/opendataworks/blob/828eb2f8/docs/reports/2026-10-26-dataagent-datasource-identity-verification.md)；本轮证据见[运行时报告](reports/2026-11-03-agent-runtime-sandbox-architecture-review.md)，与上方真实 provider 验收合并执行。

- **前端测试与组件对齐**（来源：2026-10-27 本轮验证）：ToolOutputRenderer 的 10 项断言与当前折叠/结果展示不一致，已在未修改 HEAD 副本复现相同 10 项失败；先确认是组件回归（运行中工具不再自动展开、展开后缺命令行）还是测试过时，再对齐。疑由 opendataworks `d8d434a1`（消息 UI 抽入 SDK 包）引入。CI（`.github/workflows/ci.yml`）暂时排除该 spec，对齐后删掉排除项。其余 743 项通过；见[主体映射报告](https://github.com/opendata-lab/opendataworks/blob/828eb2f8/docs/reports/2026-10-26-dataagent-datasource-identity-verification.md)。

## 部署

- **旧数据迁移未演练**（2026-10-27 拆分复核）：`deploy/README.md` 的“从 OpenDataWorks 内置部署迁移”是手工命令，尚未在真实旧部署上执行；首次正式迁移时按步骤演练并修正文档。
- **离线包未在无外网机器上跑过**（2026-10-27，2026-11-10 更新）：`oda-*` 镜像已发布，`v0.1.0` 与 `latest` 离线包已由 CI 生成；2026-11-10 下载 `latest` 核对过包内镜像（`oda-backend`、`oda-sandbox`、`oda-frontend`、`redis`）及其代码版本，但仍未在一台无外网机器上完整跑 `load-package-and-start.sh` 并完成一次问数。

## 内嵌身份

- **签名用户上下文与Portal委托接入延期（2026-11-01用户决定，2026-11-10 更新）**：DataAgent 仍为每次 MCP 调用附加`X-Agent-User-Id`和`X-Agent-Id`；OpenDataWorks 自 1.6.0 起只按服务账号 Bearer Token 授权，这两个头只记审计（opendataworks#511），按人隔离留待其 OAuth 访问令牌模式。不实现新的OAuth／JWT委托或每智能体Portal Token，也不增加缺用户时的默认账号。本机定向测试允许显式指定已登记用户，不能把固定身份部署为全部用户的身份。后续参考[Aily自定义MCP](https://aily.feishu.cn/hc/1u7kleqg_73ahi33a)的签名用户上下文：先验证宿主用户来源，再由后端逐请求注入可信身份，Portal验签并校验调用方绑定；独立登录需统一登录或明确账号绑定。凭证只发目标MCP、不进入模型环境，并覆盖会话归属／撤销／并发不串权。关联 OpenDataWorks [主体映射设计](https://github.com/opendata-lab/opendataworks/blob/main/docs/design/2026-10-26-dataagent-datasource-identity-design.md)第9.15节（当前委托限制）与第11节（服务账号接入）；当前未实现，多用户可信身份缺口保持开放。

- **宿主上报的用户可被冒充**（2026-10-27，用户决定先简单化；2026-11-10 更新）：widget 会话以 `X-DataAgent-User-Id` 区分会话与历史记录，并作为 `X-Agent-User-Id` 上报给 MCP。OpenDataWorks 已不再据此授权，因此改写它不能扩大查数范围，但仍可能看到他人的问数会话。该值来自宿主页面，可被篡改或直连伪造。需要一种可验证的宿主身份（例如只信任带内部令牌、由宿主反向代理写入的用户头）。

## 评测

- **参考 SQL 无处执行**（2026-10-27 拆分复核，codex 审查发现）：三个评测器（builtin / deepeval / opik）的 `expected_result.reference_query` 仍 POST `/api/v1/nl2sql/query/execute`，该接口随平台直连代理一起删除，带参考 SQL 的用例会以 `reference_sql_failed` 失败。需要决定参考结果的来源：数据集里预先存放期望行，或由评测侧显式配置一个外部只读查询入口；然后同步改三个评测器、`dataset/schema-v2.json`、README、示例与测试。

## Plugin 打包与导入

2026-10-27：本次只使用已有 MCP 新增和 Skill 导入。Plugin 的打包、导入、接口与页面另行设计，见 docs/migration.md。待办统一记在上方“插件管理与导入”。

## 测试与工程卫生（迁自来源仓库，2026-10-27）

- **Pi 现有依赖审计告警**（来源：2026-11-03 构建审查）：未改动的 Pi lockfile 在 npm audit --omit=dev 下报告 9 项（5 moderate、3 high、1 critical），涉及直接依赖 @modelcontextprotocol/sdk 及传递依赖 body-parser、proxy-addr、zod。后续单独评估兼容升级并回归 MCP 协议、身份和工具执行；不在本轮构建优化中自动 npm audit fix。见[验证报告](reports/2026-11-03-ci-build-optimization-verification.md)。
- **构建优化的最终双架构验收**（来源：2026-11-03 本地验证边界）：本机 pi-cell 的 Linux arm64 构建通过，但完整 amd64/arm64 后端与 sandbox 最终镜像、远程缓存和发布尚待本次远程工作流确认。通过后将结果移至已完成；见[设计](design/2026-11-03-ci-build-optimization-design.md)、[计划](plans/2026-11-03-ci-build-optimization-plan.md)与[验证报告](reports/2026-11-03-ci-build-optimization-verification.md)。

- `da_agent_draft` 表的排序规则是 `utf8mb4_0900_ai_ci`，与其他 `da_*` 表的 `utf8mb4_unicode_ci` 不一致，以后与其他表做字符串关联时会报错。
- README 还没有界面截图。来源：2026-10-28 README 整理。

## 本轮验证补充（2026-10-27）

- 2026-11-03 改造前再验仍为相同两项：全量 182/184，通过定向重跑 channel_flush，Bash 2 秒仍失败，详见[构建验证报告](reports/2026-11-03-ci-build-optimization-verification.md)。Pi 的 Bash 2 秒时序用例在本机全量与单独运行均超时；来源仓库未修改的 HEAD 副本同样失败，保留后续稳定性治理。另一次并发全量中 channel_flush 的负向控制用例未发生预期截断，重跑通过；需使该测试不依赖机器调度时序。来源：opendataworks `docs/reports/2026-10-27-dataagent-opendataworks-decoupling-verification.md`。
- 历史缺口（2026-10-27）：当时未测 child-container/技能卷。2026-11-03 新版拓扑已完成本地真实 backend 镜像、技能卷及工具 sandbox 演练；真实生产 provider/MCP 验收仍由上方生产拓扑待办跟踪。

### 对话界面改造（参考 ZCode）

- **现状**（2026-11-04）：对话界面由 `dataagent-frontend/packages/agent-conversation` 渲染，主聊天页 `NL2SqlChatV2.vue` 与 widget `WidgetChat.vue` 各自组织消息、工具块与图表。用户提出参考 Z.ai 的 ZCode（以智能体对话为中心的工作区：对话输入支持附件与 @ 文件、模式切换、过程与结果分区）改造我们的对话界面。参考：[zcode.z.ai](https://zcode.z.ai)、[zai-org/ZCode](https://github.com/zai-org/ZCode)、[第三方介绍](https://www.eesel.ai/blog/zcode)。
- **要做**：先对照 ZCode 梳理我们的信息结构（过程区/工具调用、结论区、图表与附件、卡片、输入区），确定改造范围，再按中大型变更写设计与计划；主聊天页与 widget 共用同一套组件和规则，不各自分叉。来源：用户 2026-11-04 提出。
- **widget 与门户对话统一（并入本项）**：用户希望 widget 对话和门户对话的 UX 一致并一起改进。现状（2026-11-04 核实）：门户 `NL2SqlChatV2.vue` 通过 `packages/agent-conversation` 的 `<dataagent-conversation>` 整体渲染；widget `WidgetChat.vue`（1247 行）自行拼装消息列表，只单独引用包里的 `ToolOutput`、`ChartSpecView`、`QuestionCard`、`PermissionCard`，并自带文本/图表拆分逻辑，导致两边的展示和修复不同步。要做：widget 改为复用同一个对话组件，只保留宿主差异（尺寸、身份、入口），本项的图表、HTML 预览等改进一次覆盖两端；对两端补同一组回归用例。

- **真实浏览器预览 HTML（并入本项）**：用户希望像 ZCode 的内置浏览器一样完整看到生成的 HTML（如报告技能导出的 HTML 报告）。现状（2026-11-04 核实）：`AttachmentPreview.vue` 用不带任何权限的 `<iframe sandbox="" srcdoc>` 预览，这是有意的安全选择，结果是脚本不执行（依赖 JS 绘图的报告显示为空或残缺），且 `srcdoc` 没有基础地址，相对路径的样式、图片、脚本加载失败；预览也只在对话内的小面板里。要做：提供可全屏或新标签打开的真实页面预览，允许脚本执行和相对资源加载，同时保持与主应用隔离——文件从独立的预览来源（单独域名/端口或不透明来源）提供，绝不同时给 `allow-scripts` 和 `allow-same-origin`，配合 CSP 禁止访问主应用接口和凭据。隔离方案需在设计里写清并做安全验证。

- **折叠过程、突出结论（并入本项）**：用户要求把回答中部的执行过程（思考、工具调用、中间输出）折叠起来，默认只突出最终结论；需要时可展开查看过程。与图表放在结论处的方案一起设计：结论区包含结论文字、图表和附件，过程区默认折叠并显示步骤数与耗时摘要，运行中仍能看到当前步骤；失败时自动展开出错的步骤。来源：用户 2026-11-04 提出。

- **启动阶段显示“准备环境”（并入本项）**：用户希望对话开始阶段显示进度，而不是一直显示固定的“正在处理…”。2026-11-04 真实 ODW 任务的本地时间线：提交→协调器接单 0.03s，→Pi 进程就绪 0.32s（准备工作区与配置、启动 Pi），→`run.started` 0.01s，→MCP 连接完成并 `turn.started` 0.04s，→模型首段文字 0.27s，合计约 0.7s；排队（并发占满）、远端 MCP 慢（最长 10s 连接超时）或模型慢时会明显变长。建议只用前端已收到的事件切换文案，不新增协议：提交后到 `run.started` 前显示“正在准备环境…”，到 `turn.started` 前显示“正在连接工具…”，之后到首段内容前显示“正在思考…”；widget 与门户一致。

- **图表重复显示（并入本项）**：同一张图在回答中部（工具块下方）和结尾各出现一次。原因已核实：图表脚本输出的 `chart_spec` 由 `ToolOutput.vue` 画在工具块下；同时系统提示 `prompts/data_agent_system_prompt.md` 第 53 行要求模型把完整 `chart_spec` JSON 放进回答，图表技能 `SKILL.md` 第 47 行也允许，`MarkdownText.vue` 于是把回答里的副本再画一次。本地最近 30 天的消息里没有带图回答，未能用落库数据复现，依据是代码路径与用户观察。建议方向：图只保留一份，数据来自工具结果，显示在结论处；模型不再把图表 JSON 写进回答（省输出 token，也避免模型改写数据），旧消息中已内嵌的 JSON 继续由 `stripChartSpecsFromText` 去掉；工具块只留“已生成图表”的提示。具体位置随本项设计一起定。涉及提示词、图表技能、前端两处渲染，属中等变更，需先写设计与计划。

### PR #5 二次评审发现（2026-11-04）

- **失败段不保存会话快照**：`execute_pi_run` 只在 `run.completed`／`run.parked` 时调用 `commit_session_segment`。失败、超时或截断的执行段不写快照，下一条消息从更早的快照续聊，并忽略 `history`。界面能看到失败轮的问题和工具结果，模型却看不到。要做：决定失败段是否追加一条只含用户问题和失败摘要的条目，或在下一段的提示里说明上一轮失败；补跨段回归测试。来源：本次 PR 评审，关联[设计](design/2026-11-03-agent-runtime-sandbox-architecture-design.md) 3.4。
- **协调器在事件循环里同步访问 MySQL**：`task_coordinator._run_task` 中的 `mark_task_running`、`ensure_assistant_message`、`get_session_checkpoint`、`update_assistant_message`、`finish_task` 以及 `_heartbeat_tick` 的 `heartbeat_task` 都直接在事件循环上执行。本次只把每帧都会触发的取消检查改成按间隔轮询并放进线程。要做：把其余调用整体放进 `asyncio.to_thread`，并在并发任务下验证 SSE 不卡顿。来源：本次 PR 评审。
- **Pi 入口路径有两个配置来源**：`resolve_cell_command` 先读 settings，再读 `os.environ`。pydantic settings 已经读取同名环境变量，第二层兜底多余，还会让开发机 `.env` 覆盖测试中设置的环境变量（本次已把对应测试改为显式传入配置）。要做：只保留 settings 一个来源。来源：本次 PR 评审。

- **沙箱输出把 stderr 合并进 stdout 结果**：`DockerSandboxManager.command` 把 stdout 和 stderr 交给同一个 `onData`。技能脚本输出 JSON 时，stderr 上的一条警告就会让 `isRenderableStructuredOutput` 判定失败，图表无法渲染。要做：分开收集 stderr，结构化判定只看 stdout，同时保持模型看到的错误信息不变。来源：本次 Gemini 评审（已核实）。
- **结果写入失败时的提示与清理**：`sandbox-tools.ts` 在 `writeError` 时仍计算 `saved=true`，提示“Output saved”后又报 `RESULT_STORAGE_FAILED`；未落盘分支的 `fs.unlink` 没有保护。执行段已经因此失败，影响只是提示混乱。要做：`saved` 以 `!writeError` 为前提，`unlink` 失败只记日志。来源：本次 Gemini 评审。
- **批准后的 MCP 写操作没有心跳**：`Cell.run` 在进入 SDK 前直接执行 `resume_operation`，不经过工具心跳计时器。默认写超时 60s 小于 idle 300s，目前不会触发；如果调大写超时，会以 `PI_RUN_IDLE_TIMEOUT` 中断并留下 `OPERATION_RESULT_UNKNOWN`。要做：执行期间发心跳。来源：本次 Gemini 评审。
- **grep 的 limit 按文件计数**：`FILE_TOOL` 把 `limit` 映射为 `rg --max-count`，那是每个文件的上限，不是总条数。要做：在 Python 侧截断总输出行数。来源：本次 Gemini 评审。
- **容器名在两处计算**：`oda-sandbox-<sha256(runId)[:24]>` 同时写在 `sandbox_config.remove_segment_sandbox` 和 `docker-sandbox-manager.ts` 里，改一边就会让后端兜底清理找不到容器。要做：由后端计算后放进 `SandboxConfig.containerName`，Pi 只校验格式。来源：本次 Gemini 评审。
- **治理设置里的失效字段**：`protect_tail_turns`、`prune_high_watermark_ratio`、`prune_target_ratio` 仍从 `context_governance` 下发，但旧 context-pruner 删除后 Pi 已不再读取（压缩由 SDK 按模型上下文窗口触发）。本次已让 `max_context_tokens` 在模型未配置窗口时生效。要做：删除这三个字段及其配置项，同步 `frames.ts` 和设置页面。来源：本次评审核实 Gemini 第 8 项时发现。

### 实施验证发现（2026-11-03）

- **计划模式的 Bash 文件写入边界（2026-11-04 核实）**：当前 beforeToolCall 拦截 write/edit 与 MCP 写操作，但普通 Bash 仍可写 workspace；DATAAGENT_PLAN_STATE=pending 不等于文件系统只读。需要单独设计并验证执行层只读策略，或明确计划模式保证范围，不能声称已经按 output 路径全面禁写。本次仅收敛运行时实现，不扩展权限策略。来源：核对用户简化建议；见[收敛报告](reports/2026-11-04-agent-runtime-simplification.md)。
- **空“深度思考”区块（2026-11-04）**：用户历史消息中存在空的已完成 reasoning 事件，前端仍展示空思考区块。需过滤完成后的空区块并保留流式生成占位，补前端回归和历史会话验证。本次运行时简化未修改前端；来源：用户在 3001 聊天页的反馈，见[收敛报告](reports/2026-11-04-agent-runtime-simplification.md)。

- **SDK 升级依赖审计**：安装 pi-coding-agent@1.4.0 后 npm 报告 12 项依赖漏洞（7 moderate、4 high、1 critical）；需核对传递依赖和运行时可达性，再选择兼容修复。来源：本次 npm install；关联[实施计划](plans/2026-11-03-agent-runtime-sandbox-architecture-plan.md) Step 0，尚未执行自动升级。

- **前端依赖审计**：2026-11-03 全新 npm ci 报告 29 项漏洞（10 moderate、17 high、2 critical）；需单独核对并升级传递依赖，本次未执行强制修复。来源：本次本地工具链安装；关联[实施计划](plans/2026-11-03-agent-runtime-sandbox-architecture-plan.md)，前端生产构建通过。

## 已完成

- **沙箱网络模式支持 (bridge 与 host) 与 Compose 环境变量对齐（2026-11-11）**：新增 `DATAAGENT_SANDBOX_NETWORK_MODE` 配置项（支持 `bridge` 默认与 `host` 宿主机模式），当沙箱开启网络（`DATAAGENT_SANDBOX_NETWORK_ENABLED=true`）且处于特定内网/VPN 环境时可使用 `host` 模式直通宿主机网络，解决 bridge 下 NAT/路由不通导致无法连接数据库与 pip install 失败的问题；当未开启网络时强制保持 `--network=none` 断网隔离；在 `deploy/docker-compose.yml` 中补齐 `DATAAGENT_SANDBOX_NETWORK_ENABLED` 与 `DATAAGENT_SANDBOX_NETWORK_MODE` 变量声明；启动与运行时诊断日志完整打印沙箱 network、networkEnabled 与 networkMode。关联设计 [design](design/2026-11-11-sandbox-network-mode-design.md) 与计划 [plan](plans/2026-11-11-sandbox-network-mode-plan.md)，跟踪 [Issue #26](https://github.com/opendata-lab/opendataagent/issues/26)。

- **规范运行时失败码与异常文本展示（2026-11-10）**：`pi_runtime.execute_pi_run` 只保留本模块抛出的已知错误码（`CELL_LOSS`、`PI_RUN_TIMEOUT`、`PI_RUN_IDLE_TIMEOUT`、协议类错误码），其余归一为 `PI_EXECUTION_ERROR`，原文放进 `message`。`CELL_LOSS` 区分三种情况：stderr 出现 Node 的 `heap out of memory`（随后约 45 行栈并以 -6 退出，因此逐行识别而非看末尾）为内存不足；退出码 -9/137 为被系统强制终止（可能是内存超限）；其余为异常中断。前端 `reducer.js`、`message.js` 为历史记录中只有 `CELL_LOSS` 的旧消息补友好文案。来源：PR #25 及其评审（OOM 实测：Node 22 堆耗尽退出码 -6、标记行后 46 行栈）。

- **统一 DataAgent 品牌图标资源与引用（2026-11-10）**：在 `dataagent-frontend/public` 中采用标准 `dataagent-icon*` 图标资产（包括 SVG/PNG/ICO），同步更新 `index.html` 的 favicon 链接、`NL2SqlChatV2.vue` 与 `IntelligentQueryView.vue` 中的品牌 logo 路径为 `${import.meta.env.BASE_URL}dataagent-icon.svg`，清理历史遗留的 `opendataworks-icon*` 与 `example-icon*` 文件，修复离线包与容器部署后 dataagent 图标 404 找不到的问题。来源：本轮 UI smoke，关联[验证报告](reports/2026-11-03-agent-runtime-sandbox-architecture-review.md)。

- **删除未被调用的 `cleanup_orphan_topic_workspaces`（2026-11-06）**：该函数会对 `DATAAGENT_HOST_ROOT` 下所有非活跃子目录执行 `rmtree`（包括放在运行时目录里的 Skill 目录），生产路径从未调用，连同测试一并删除；删除单个 topic 仍用 `delete_topic_workspace`。来源：编写 OpenDataWorks 迁移脚本时发现。

- **运行时实现收敛 / Podman 验证范围（2026-11-04）**：移除未验证的 Podman sandbox 分支，正式只提供 Docker，原“Podman 实机覆盖”不再作为当前实现的缺失验收。简化生命周期、读取、轮次和用量；用任务起始快照保留终态附件，用接单前预热消除每段线程调用，卡片数据库操作整体在线程执行。来源：用户后续简化评审；结果见[收敛报告](reports/2026-11-04-agent-runtime-simplification.md)。

- **PR #5 代码评审修正（2026-11-04）**：已修复批准 MCP 的明确失败持久化、跨段附件/用量、回答立即入队及快照冲突、长 JSON 读取与重复文件、挂载发现与清理生命周期，删除旧等待循环和事件 sink，修复真实 folding 回归和测试目录清理，同步 Pi-only 本地指引。来源：用户提供的 PR review；设计/计划先行更新，验证与具体结果见[评审修正报告](reports/2026-11-04-agent-runtime-pr-review-fixes.md)。Podman 实机等既有环境边界仍由原待办跟踪。

- **HTTP fixture 的 MCP major 约束（2026-11-03）**：CI 解析到 Python mcp 2.x，FastMCP 已改名导致 fixture 无法启动；requirements 限定已验证的 v1 API 范围 `>=1.23.0,<2`，不保留兼容分支。来源：[首次实现 CI](https://github.com/opendata-lab/opendataagent/actions/runs/41769122504)，见[报告](reports/2026-11-03-agent-runtime-sandbox-architecture-review.md)。

- **OAuth 测试 helper 重复 Cookie（2026-11-03）**：远程 Python CI 发现传输配置用例曾先 authorize 再人工发 state，helper 未删除域 Cookie，同时发送两份不同 nonce。已仅修正测试构造，先清理同名 Cookie 再设置，并断言仅一份；生产 CSRF 校验保持原样。来源：[首次实现 CI](https://github.com/opendata-lab/opendataagent/actions/runs/41769122504)，结果见[运行时报告](reports/2026-11-03-agent-runtime-sandbox-architecture-review.md)。

- **Pi SDK / Docker 沙箱整合与卡片释放（2026-11-03）**：原运行时迁移及“等待用户时释放资源”两项已实现。七工具仅在沙箱执行，Runner/Claude/正则边界删除；同任务卡片会话持久化、重启后续聊、累计预算、写批准与未知结果停止重放、大结果只读消费、权限入口、迁移/回退及 CI/离线资产已接通。来源：本轮 PR #5；结果见[设计](design/2026-11-03-agent-runtime-sandbox-architecture-design.md)、[计划](plans/2026-11-03-agent-runtime-sandbox-architecture-plan.md)、[报告](reports/2026-11-03-agent-runtime-sandbox-architecture-review.md)。真实模型最终验收单列上方。
- **旧 Pi 验证及 POC 清理（2026-11-03）**：旧 Bash 超时/后代进程测试由真实 Docker 生命周期测试替代；无凭据协议测试显式隔离 provider；不再维护的 `pi-kernel-spike` 删除。92 项新运行时测试通过，结果见同一[报告](reports/2026-11-03-agent-runtime-sandbox-architecture-review.md)。

- **评审后简化方案同步（2026-11-03）**：原“简化待同步”已完成文档修订，采用卡片结束当前段后续聊、大输出自动落盘返回 `/mnt/results` 路径、7 个基础工具固定启用并保留编辑能力；删除延迟批次补跑及专用导出路径，统一迁移、超时清理、协议和验收要求。结果见[设计](design/2026-11-03-agent-runtime-sandbox-architecture-design.md) 3.2–3.7、[计划](plans/2026-11-03-agent-runtime-sandbox-architecture-plan.md)和[评审修订](reports/2026-11-03-agent-runtime-sandbox-architecture-review.md)。后续代码实现及凭据无关验证也已完成，见本节运行时整合记录。

- **运行时与沙箱方案评审补齐（文档修订，2026-11-03）**：对照[静态评审报告](reports/2026-11-03-agent-runtime-sandbox-architecture-review.md)的 R1–R8 共 8 项问题，已在[设计文档](design/2026-11-03-agent-runtime-sandbox-architecture-design.md)（3.2–3.6、4.1–4.2 节）与[实施计划](plans/2026-11-03-agent-runtime-sandbox-architecture-plan.md)（Step 0–Step 5）中逐项补齐机制、涉及文件与验收标准；代码实现与凭据无关冒烟已完成，真实 provider 验收单列上方。
