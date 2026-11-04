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

### 真实 provider 与业务问数最终验收

- **现状（2026-11-03）**：运行时/沙箱改造已实施。2026-11-04 真实 DeepSeek + OpenDataWorks MCP 对现有验收订单表查询 2 / 30.00 通过；Stop 的模型断流/进程/容器清理通过。当前服务账号仅有验收数据源权限，真实零售业务数据和图表报告仍未验收，见[最新报告](reports/2026-11-04-agent-stop-and-odw-verification.md)。
- **要做**：在用户明确批准进一步付费测试并配置真实业务数据源权限后运行“最近 30 天工作流发布次数趋势”，核对目标 MCP 身份、真实查询、图表和完整报告下载。来源：用户本轮要求将凭据相关验证留后；关联[设计](design/2026-11-03-agent-runtime-sandbox-architecture-design.md)、[计划](plans/2026-11-03-agent-runtime-sandbox-architecture-plan.md)、[验证报告](reports/2026-11-03-agent-runtime-sandbox-architecture-review.md)。

### MCP 工具名命名空间

- **现状**：Pi 直接使用 MCP 返回的工具名注册，没有按服务加命名空间，不同 MCP 的同名工具会冲突。插件设计里先用"冲突即拒绝"兜底。
- **要做**：设计按服务加命名空间的方案，兼顾现有 MCP 配置和技能文档；本轮已实现重复/保留工具名明确拒绝。

### 系统提示落库的后续

- 管理端查看页面（接口 `GET /api/v1/dataagent/tasks/{task_id}/system-prompt` 已有）。
- `da_system_prompt` 中不再被任务引用的记录的定时清理（按"无引用且超过 N 天"）。

### 生产运行拓扑回归

- **现状（2026-11-03 更新）**：原 child/Runner 拓扑已由本轮 PR 移除。新版 backend + 工具专用 sandbox 在本机真实 Docker 下完成新卷/复用卷、socket、挂载、回退和再升级；模型/MCP 为本地 fixture。
- **要做**：真实 provider 与目标 MCP 配置后，在生产环境核对身份头、撤权和业务任务持久化，不能把 fixture 冒烟视为真实集成验收。来源：2026-10-27 原待办及[主体映射报告](https://github.com/opendata-lab/opendataworks/blob/828eb2f8/docs/reports/2026-10-26-dataagent-datasource-identity-verification.md)；本轮证据见[运行时报告](reports/2026-11-03-agent-runtime-sandbox-architecture-review.md)，与上方真实 provider 验收合并执行。

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
- **构建优化的最终双架构验收**（来源：2026-11-03 本地验证边界）：本机 pi-cell 的 Linux arm64 构建通过，但完整 amd64/arm64 后端与 sandbox 最终镜像、远程缓存和发布尚待本次远程工作流确认。通过后将结果移至已完成；见[设计](design/2026-11-03-ci-build-optimization-design.md)、[计划](plans/2026-11-03-ci-build-optimization-plan.md)与[验证报告](reports/2026-11-03-ci-build-optimization-verification.md)。

- `da_agent_draft` 表的排序规则是 `utf8mb4_0900_ai_ci`，与其他 `da_*` 表的 `utf8mb4_unicode_ci` 不一致，以后与其他表做字符串关联时会报错。
- README 还没有界面截图；前端图标文件仍沿用 `opendataworks-icon-*` 命名。来源：2026-10-28 README 整理。

## 本轮验证补充（2026-10-27）

- 2026-11-03 改造前再验仍为相同两项：全量 182/184，通过定向重跑 channel_flush，Bash 2 秒仍失败，详见[构建验证报告](reports/2026-11-03-ci-build-optimization-verification.md)。Pi 的 Bash 2 秒时序用例在本机全量与单独运行均超时；来源仓库未修改的 HEAD 副本同样失败，保留后续稳定性治理。另一次并发全量中 channel_flush 的负向控制用例未发生预期截断，重跑通过；需使该测试不依赖机器调度时序。来源：opendataworks `docs/reports/2026-10-27-dataagent-opendataworks-decoupling-verification.md`。
- 历史缺口（2026-10-27）：当时未测 child-container/技能卷。2026-11-03 新版拓扑已完成本地真实 backend 镜像、技能卷及工具 sandbox 演练；真实生产 provider/MCP 验收仍由上方生产拓扑待办跟踪。

### 实施验证发现（2026-11-03）

- **现有品牌图标路径缺失**：本地浏览器发现 Chat/智能体侧栏使用不存在的 `example-icon.svg`，public 目录实际为 `opendataworks-icon.svg` 系列。需统一品牌资源与引用；本轮不修改品牌。来源：本轮 UI smoke，关联[验证报告](reports/2026-11-03-agent-runtime-sandbox-architecture-review.md)。
- **Podman 实机覆盖**：Docker 路径完整通过，Podman 缺 Docker CLI 时的选择保留，尚无本机 Podman 实例做同样 mount/生命周期验收。后续在 Podman 主机运行工具及 HTTP smoke。来源：本轮环境边界，关联同一[报告](reports/2026-11-03-agent-runtime-sandbox-architecture-review.md)。


- **SDK 升级依赖审计**：安装 pi-coding-agent@1.4.0 后 npm 报告 12 项依赖漏洞（7 moderate、4 high、1 critical）；需核对传递依赖和运行时可达性，再选择兼容修复。来源：本次 npm install；关联[实施计划](plans/2026-11-03-agent-runtime-sandbox-architecture-plan.md) Step 0，尚未执行自动升级。


- **前端依赖审计**：2026-11-03 全新 npm ci 报告 29 项漏洞（10 moderate、17 high、2 critical）；需单独核对并升级传递依赖，本次未执行强制修复。来源：本次本地工具链安装；关联[实施计划](plans/2026-11-03-agent-runtime-sandbox-architecture-plan.md)，前端生产构建通过。

## 已完成

- **HTTP fixture 的 MCP major 约束（2026-11-03）**：CI 解析到 Python mcp 2.x，FastMCP 已改名导致 fixture 无法启动；requirements 限定已验证的 v1 API 范围 `>=1.23.0,<2`，不保留兼容分支。来源：[首次实现 CI](https://github.com/opendata-lab/opendataagent/actions/runs/41769122504)，见[报告](reports/2026-11-03-agent-runtime-sandbox-architecture-review.md)。

- **OAuth 测试 helper 重复 Cookie（2026-11-03）**：远程 Python CI 发现传输配置用例曾先 authorize 再人工发 state，helper 未删除域 Cookie，同时发送两份不同 nonce。已仅修正测试构造，先清理同名 Cookie 再设置，并断言仅一份；生产 CSRF 校验保持原样。来源：[首次实现 CI](https://github.com/opendata-lab/opendataagent/actions/runs/41769122504)，结果见[运行时报告](reports/2026-11-03-agent-runtime-sandbox-architecture-review.md)。

- **Pi SDK / Docker 沙箱整合与卡片释放（2026-11-03）**：原运行时迁移及“等待用户时释放资源”两项已实现。七工具仅在沙箱执行，Runner/Claude/正则边界删除；同任务卡片会话持久化、重启后续聊、累计预算、写批准与未知结果停止重放、大结果只读消费、权限入口、迁移/回退及 CI/离线资产已接通。来源：本轮 PR #5；结果见[设计](design/2026-11-03-agent-runtime-sandbox-architecture-design.md)、[计划](plans/2026-11-03-agent-runtime-sandbox-architecture-plan.md)、[报告](reports/2026-11-03-agent-runtime-sandbox-architecture-review.md)。真实模型最终验收单列上方。
- **旧 Pi 验证及 POC 清理（2026-11-03）**：旧 Bash 超时/后代进程测试由真实 Docker 生命周期测试替代；无凭据协议测试显式隔离 provider；不再维护的 `pi-kernel-spike` 删除。92 项新运行时测试通过，结果见同一[报告](reports/2026-11-03-agent-runtime-sandbox-architecture-review.md)。

- **评审后简化方案同步（2026-11-03）**：原“简化待同步”已完成文档修订，采用卡片结束当前段后续聊、大输出自动落盘返回 `/mnt/results` 路径、7 个基础工具固定启用并保留编辑能力；删除延迟批次补跑及专用导出路径，统一迁移、超时清理、协议和验收要求。结果见[设计](design/2026-11-03-agent-runtime-sandbox-architecture-design.md) 3.2–3.7、[计划](plans/2026-11-03-agent-runtime-sandbox-architecture-plan.md)和[评审修订](reports/2026-11-03-agent-runtime-sandbox-architecture-review.md)。后续代码实现及凭据无关验证也已完成，见本节运行时整合记录。

- **运行时与沙箱方案评审补齐（文档修订，2026-11-03）**：对照[静态评审报告](reports/2026-11-03-agent-runtime-sandbox-architecture-review.md)的 R1–R8 共 8 项问题，已在[设计文档](design/2026-11-03-agent-runtime-sandbox-architecture-design.md)（3.2–3.6、4.1–4.2 节）与[实施计划](plans/2026-11-03-agent-runtime-sandbox-architecture-plan.md)（Step 0–Step 5）中逐项补齐机制、涉及文件与验收标准；代码实现与凭据无关冒烟已完成，真实 provider 验收单列上方。
