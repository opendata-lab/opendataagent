# DataAgent 运行时整合与 Docker 沙箱架构设计

日期：2026-11-03

状态：**已实施；凭据无关验证完成，真实模型验收待用户配置 provider**

实施计划：[2026-11-03-agent-runtime-sandbox-architecture-plan.md](../plans/2026-11-03-agent-runtime-sandbox-architecture-plan.md)

关联评审：[2026-11-03-agent-runtime-sandbox-architecture-review.md](../reports/2026-11-03-agent-runtime-sandbox-architecture-review.md)

## 1. 背景与现状问题

### 1.1 改造前：5 层跨进程调用链
改造前在容器沙箱模式（`DATAAGENT_SANDBOX_MODE=container`）下，一次用户问答经过以下 5 层进程跳转：

```text
[1] dataagent-backend (Python FastAPI + 内嵌 TaskCoordinator)
      │  组装 TaskExecutionInput，经 HTTP POST /internal/sandbox/runs 转发 NDJSON 流
      ▼
[2] dataagent-sandbox-runner (第 2 个 Python FastAPI 服务，挂载 docker.sock)
      │  拼装 docker run 参数，拉起/复用子容器，经 stdin 传入整包 JSON
      ▼
[3] 子容器内: sandbox_task_main.py (第 3 个 Python 进程，运行 _execute_task_stream_local)
      │  在子容器内重连 MySQL 查询模型凭据、MCP 配置与取消状态，调用 core/pi_runtime.py
      ▼
[4] 子容器内: Pi Cell (Node.js 子进程，dataagent-runtime-pi)
      │  运行 Agent 循环：调用大模型 API、连接外部 MCP、执行上下文折叠与压缩
      ▼
[5] 子容器内: bash / python 技能脚本 (真正需要隔离的工具执行)
```

### 1.2 核心痛点
1. **调用链冗长，流式转发损耗高**：每个运行时事件需经 `Pi Cell -> sandbox_task_main -> sandbox_runner -> backend -> MySQL -> 前端 SSE` 多道序列化转发；早期为兼容 `claude-agent-sdk` 将整套后端执行器塞进子容器的历史包袱已不再必要。
2. **安全隔离被架空（凭据与网络进沙箱）**：由于第 3、4 层（Python 执行器与 `Pi Cell`）跑在子容器内，子容器被迫共享 `runner` 的网络（`container:dataagent-sandbox-runner`）以访问 MySQL、LLM 与 MCP，且 `MYSQL_PASSWORD`、大模型 API Key、MCP 凭据全部进入子容器。
3. **静态正则边界检查（`boundary_policy`）频繁误杀**：为弥补容器内含敏感信息与本地无隔离的问题，在 Python（`core/boundary_policy.py`）和 TypeScript（`workspace-boundary-enforcer.ts`）维护了两套静态文本分词/正则拦截器（`validateBash`），导致 `cd ..`、引号内含 `/` 或 `..` 的参数、`/usr/bin/python3` 等合法命令频繁误报 `PATH_OUT_OF_SCOPE` / `PARENT_SEGMENT`。
4. **镜像职责错位且重复臃肿**：`Dockerfile` 与 `Dockerfile.runner` 均打包了完整的 Python 后端 + Node 22 + `dataagent-runtime-pi`；而在真正执行命令的容器里缺少 `jq`、`ripgrep (rg)`、`fd-find`、`duckdb` 等高频工具。
5. **双引擎与自研运行时维护负担重**：`dataagent-runtime-pi` 仅基于底层 `pi-agent-core` 自研了 ~4300 行代码，且 `task_executor.py` 仍保留了 ~1000 行 `claude-agent-sdk` 兼容代码与 `<topic>/home/` 目录。

---

## 2. 已确认的架构决策（2026-11-03）

| 编号 | 决策项 | 拍板结论 |
| :--- | :--- | :--- |
| **SA-01** | 沙箱隔离与生命周期 | 采用 **单轮按需启动的 Docker 沙箱容器（`oda-sandbox`）**；本轮首次调沙箱工具时 `docker run -d`（`sleep <run_timeout>`），同轮多次工具调用通过 `docker exec` 复用，**本轮结束或卡片挂起时在 `finally` 中立即 `docker rm -f` 销毁**，不设跨轮次 `WARM_POOL`；**取消无隔离的宿主 `local` 回退模式**，本地开发与生产统一走 Docker 容器执行 |
| **SA-02** | 运行时引擎 | **只保留 Pi 引擎**，升级至 `@earendil-works/pi-coding-agent` SDK；下线并删除 `claude-agent-sdk`（`claude_code`）执行分支及每会话 `home/` 目录 |
| **SA-03** | Runner 服务去留 | **砍掉独立的 `dataagent-sandbox-runner` 常驻服务与 `sandbox_task_main.py`**；由 `dataagent-backend`（`Pi Cell`）直接通过本地容器 CLI（`docker`）管理 `oda-sandbox` 容器 |
| **SA-04** | 基础工具固定启用与沙箱执行 | **关闭 SDK 默认宿主执行实现，保留 `read/bash/edit/write` 等能力**；`bash / read / write / edit / ls / find / grep` 100% 路由进 `oda-sandbox` 容器内执行（不在宿主进程调用 `node:fs` 解析模型传入路径）；彻底删除 `boundary_policy` 正则检查 |
| **SA-05** | 会话持久化与卡片续聊 | 工具串行执行；卡片产生后结束当前执行段、保存会话并释放资源；用户回答后在同一 Topic、同一任务下启动下一执行段，基于历史继续推理。保留计划/权限门控和任务预算，不实现工具批次断点补跑（见 3.4 节） |
| **SA-06** | 大输出落盘、权限与配置瘦身 | 大输出自动保存到当前 Topic 的 `results/`，只读挂载为 `/mnt/results`，工具结果直接返回该路径。Agent 通过基础工具读取和处理文件；后端通过 `gosu` 初始化权限并降权；清理冗余配置 |

---

## 3. 目标架构与详细机制

### 3.1 总体拓扑（从 5 层收敛为 2 层）

```text
1. dataagent-backend（控制面 + Agent 编排）
   root entrypoint 初始化权限 -> gosu 降权运行 FastAPI + TaskCoordinator
       └─ Pi Cell（Node.js，pi-coding-agent，串行工具）
          ├─ LLM / MCP：凭据仅留在控制面
          ├─ AskUserQuestion / ExitPlanMode：卡片结束本次执行，回答后续聊
          ├─ SessionManager.inMemory：条目经 stdio 交给后端存 MySQL
          ├─ 大输出：后端可见 results/ 落盘，返回 /mnt/results/<文件名>
          └─ bash/read/write/edit/ls/find/grep
                    │ docker exec
                    ▼
2. oda-sandbox（每执行段按需启动，本段结束即销毁）
   /mnt/workspace           读写，当前 Topic 的 workspace/
   /mnt/results             只读，当前 Topic 的 results/
   /opt/skills/<folder>     只读，本段启用的技能
   根分区只读、/tmp 可写、无网络、UID:GID 与 backend 一致
   无 Docker socket、模型/MCP/数据库凭据或后端代码
```

### 3.2 工具白名单与执行边界（解决 R2、R3、R7）

#### 3.2.1 保留基础工具能力，替换宿主执行实现（解决 R2）
`pi-coding-agent@1.4.0` 默认开启宿主侧内置工具 `["read", "bash", "edit", "write"]`。需要替换的是直接访问 backend 文件系统的执行实现，不能因为隔离要求禁止 `edit/write` 能力。
- **固定启用一套沙箱基础工具**：调用 `createAgentSession` 时传入本段全部允许工具名称的显式 `tools` 清单，并通过 `customTools` 用同名沙箱实现覆盖全部 7 个基础工具；SDK 1.4.0 的 `tools: []` 会同时禁用自定义工具，不能用于启用沙箱工具。启动时逐个验证实际执行入口，不允许缺失实现回落到内置工具。注册以下工具：
  1. **沙箱环境工具（7 个，容器内执行）**：`bash`、`read`、`write`、`edit`、`ls`、`find`、`grep`。沿用 Pi 的小写工具名；`find` 承担按文件名模式查找能力，不再另注册同义 `Glob` 工具。`write/edit` 可以修改当前工作区文件，不能修改只读技能目录。
  2. **交互工具**：`AskUserQuestion`；Plan 模式另启用 `ExitPlanMode`。
  3. **MCP 工具**：当前智能体绑定的 MCP 服务工具。
- 大结果读取统一使用 `read/grep/bash`，移除原分页/搜索专用工具，不新增导出工具。结果落盘由通用输出处理完成，无需模型额外调用。
- **启动自检断言**：检查 7 个基础工具均已注册且执行入口绑定沙箱执行器，禁止遗漏工具落回 SDK 默认宿主实现；检查未注册清单外的文件/命令工具。`edit/write/read/bash` 名称本身是允许的，不能用工具名或大小写判断是否安全。用工作区编辑、只读挂载拒写和宿主文件未改变的行为测试验证边界。

#### 3.2.2 统一容器执行与移除无隔离 `local` 回退（解决 R3）
- 提示词与运行环境变量仅向模型暴露容器内路径：工作目录 `/mnt/workspace`，结果目录 `/mnt/results`，技能根目录 `SKILLS_ROOT_DIR=/opt/skills`；`DATAAGENT_PYTHON_BIN` 指向沙箱镜像内实际 Python 可执行文件。技能脚本统一使用 `"$DATAAGENT_PYTHON_BIN" "${SKILLS_ROOT_DIR}/<skill-folder>/scripts/<name>.py" ...`，不得注入宿主 Python 路径。
- 保留现有 `DATAAGENT_ENABLED_SKILL_ROOTS` 调用契约，其值仅包含本段启用技能的沙箱路径；同级技能仍从自身目录定位，不使用主技能专属锚点定位其它技能。
- `bash / read / write / edit / ls / find / grep` 的参数直接通过 `docker exec`送入当前任务的 `oda-sandbox` 容器内执行，**绝不在 `Pi Cell` 宿主进程内调用 `node:fs` 解析模型传入路径**。
- **不设宿主 `local` Bash 回退模式**：本地开发（macOS/Linux 启动 `uvicorn`）与容器化部署使用完全相同的 `oda-sandbox` 容器隔离路径；若容器运行时（Docker）不可用或镜像缺失，直接返回 `SANDBOX_UNAVAILABLE` 错误，绝不静默降级为在宿主机直接执行 `Bash`。

#### 3.2.3 串行工具执行与业务门控保留（解决 R7）
- **保持串行执行**：通过目标 SDK 版本验证支持的配置显式设为 `toolExecution = "sequential"`，确保同一轮内多个工具对 `/mnt/workspace` 文件修改、SQL 执行及卡片挂起的先后顺序完全确定。
- **完整保留 4 项业务与治理门控**（删除 `boundary_policy` 仅删除静态路径正则，不删业务门控）：
  1. **工具调用次数上限（`max_tool_calls`）**：在 `pi.on("tool_call")` 中计数，超限时发出 `tool.denied` 事件并终止本轮。
  2. **最大轮次上限（`max_turns`）**：在运行时适配层统计，达到上限时通过 SDK 验证支持的停止接口结束运行；不假定事件钩子返回值能控制循环。
  3. **计划模式门控（`permission_mode === "plan"`）**：
     - 从持久化的计划记录读取当前计划版本的批准状态；未批准时追加 `prompts/plan_mode.md`；
     - 在 `pi.on("tool_call")` 中，批准前拦截 `write/edit` 和写类型 MCP 工具（`readOnlyHint !== true || destructiveHint === true`），继续传递现有技能脚本的计划状态约束，发出 `tool.denied` 事件并返回拒绝原因。
  4. **MCP 写操作与高危操作确认门控**：
     - 计划批准后有效权限模式转为 `"acceptEdits"`；
     - 当调用 MCP 写工具且 `mode !== "bypassPermissions"`，并在 `mode === "default"` 或 `hints?.destructiveHint === true` 时，触发 `permission` 确认卡片（走 3.4 节挂起与恢复流程）。

#### 3.2.4 智能体配置与运行时工具统一（2026-11-03 用户修订）

- **基础工具固定启用**：所有智能体使用 3.2.1 的 7 个沙箱基础工具，删除配置页中 `Skill / Bash / Read / LS / Glob / Grep` 的勾选区，以及列表和详情中根据 `allowed_tools` 显示的可选工具数量。该列表不能再作为权限隔离手段。
- **Skills 保持独立配置**：保留 `skill_folders` 选择，通过 SDK 的技能元数据和沙箱 `read` 加载正文；不再维护独立的旧 `Skill` 工具开关。
- **MCP 保持独立配置**：保留 `mcp_server_ids`，仅注册智能体绑定的 MCP 工具；计划模式和写操作审批按业务门控执行。
- **清理完整配置链**：同步删除前端表单提交、后端创建/更新及能力接口、草稿发布和运行快照中的 `allowed_tools` 业务契约。已有数据库历史列不作为新版执行配置；本次不为它增加名称映射或运行时兼容分支，保留旧表列供既定旧版镜像回退使用。
- **名称保持一致**：运行时、提示词、技能文档、同步模板、演示数据及回归测试统一使用 `bash/read/write/edit/ls/find/grep`，不同时维护新旧两套工具别名。

---

### 3.3 大输出自动落盘与文件路径返回（解决 R4）

#### 3.3.1 一条读取路径

- 小输出直接进入模型上下文；文本或 JSON 输出达到 `dataagent_context_max_inline_result_bytes`（默认 16KB）后，自动保存到 `/dataagent_runtime/<topic_id>/results/`，工具返回文件路径、格式、大小和有界预览。
- 当前 Topic 的 `results/` 以只读方式挂载到沙箱 `/mnt/results`。目录在容器启动前创建；新增结果在同一目录挂载中立即可读，跨执行段及服务重启后仍可使用。
- 文件名由运行时生成且不覆盖已有结果；模型只看到 `/mnt/results/<文件名>`，不暴露 backend 或 Docker daemon 的宿主路径。`result_ref` 仅作为文件身份及事件溯源信息，不再作为另一套读取工具的入口。
- Agent 按需用 `read/grep/bash` 检查文件。生成图表或报告时，脚本直接读取该文件；需要提取 `rows` 或转换为 CSV 时，Agent 用普通 Python 在 `/mnt/workspace` 生成输入文件。运行时不注册格式导出工具，也不包含技能专属处理。

#### 3.3.2 保存内容与工具返回

- JSON 保存为标准 JSON，文本保存为 UTF-8 文本；保留本次实际接收的原始内容，包括总数、警告和上游截断信息，不自动改写为只含行的私有 JSONL 格式。
- `bash` 在接收 stdout/stderr 时保存输出，再限制内联预览，避免先截断后落盘。工具自身已有输出文件时直接使用已保存的文件；失败、超时或存储限额导致不完整时，明确返回已保存范围和原因。
- “完整保存本次工具返回”不等于“查询获得全部数据”。源结果中的 `has_more`、`truncated` 等标志必须保留，预览不能宣称为完整数据集。图片等非文本块沿用现有事件契约。
- `TabularDigest` 可保留为有界预览，文件路径始终可见。保留前端图表/结构化结果与事件溯源通道；折叠仅影响模型内联上下文，不能把前端必需的结构化结果替换为普通预览。

示例（路径均为沙箱可见路径）：

```text
输出较大，本次工具返回的内容已保存。
文件：/mnt/results/res_xxx.json
格式：JSON
大小：<实际字节数>
以下仅为预览；进一步分析或生成报告时，请直接读取该文件。
```

此设计参考 [Claude Code 的 MCP 大输出落盘行为](https://code.claude.com/docs/en/mcp#mcp-output-limits-and-warnings)；16KB 是本项目阈值，不是 Claude 的阈值。

---

### 3.4 卡片结束当前执行，回答后基于会话继续（解决 R1）

#### 3.4.1 恢复目标与最小状态

- 一个 Topic 是持久会话；一个用户任务可包含多个执行段。每次启动一个 Pi Cell 是一个有界执行段，等待卡片时结束该段。用户回答后在原 Topic、原 task_id 下启动下一段，前端仍展示为原任务继续。
- 持久化 SessionManager 的 `header/entries/leaf_id`、现有交互记录，以及任务预算和当前计划的批准记录；不保存 Promise、SDK 调用栈或同批工具调度队列。
- 已完成工具结果保留在历史中，下一段向模型追加用户回答或批准/拒绝信息，由模型基于历史重新决定后续动作；不重跑原用户问题，不改写过去的工具结果，不承诺任意中途崩溃后的精确断点续跑。

#### 3.4.2 卡片产生与执行段结束

1. `AskUserQuestion`、`ExitPlanMode` 或写操作确认产生卡片，设置本段停止标记。交互工具返回真实的“交互请求已创建，等待用户”结果，不把它伪装为用户已经回答或操作已经执行。
2. 串行执行中，已完成的工具照常保留；本批剩余工具由适配层阻止执行，并记录明确的“因等待用户而未执行”结果。不建立待补跑列表，回答后也不逐条重放这些调用。
3. 使用 SDK 验证支持的停止方式，等待本批工具结果与会话条目收尾后导出会话；不在工具执行回调中等待会导致自等待的停止操作，不依赖未经验证的事件返回值。Step 0 必须验证这一边界。
4. Pi Cell 发送会话条目与卡片信息，backend 在同一 MySQL 事务中保存会话、交互记录和 `waiting_input/waiting_permission` 状态。事务成功后才向前端发布可回答的卡片；保存失败则报错，不能将丢失上下文的卡片当成可恢复等待。
5. 本段正常退出，`finally` 销毁沙箱，协调器释放执行槽位与 lease。执行段的结束不能把逻辑任务记为成功或失败；原任务保持等待状态。

#### 3.4.3 用户回答后的普通续聊

1. 问题回答沿用 `POST /api/v1/nl2sql/tasks/{task_id}/question-answer`；计划和权限决策沿用 `POST /api/v1/nl2sql/tasks/{task_id}/permission-decision`。后端校验任务归属、当前 request_id 和待回答状态，条件更新一次性写入回答并置为 `waiting`。
2. 仅首次提交触发重新入队；回答已入库但进程在入队前退出，由协调器扫描 `waiting` 任务补调度。重复点击不重复入队，历史 request_id 不能解决后一张卡片。
3. 新 Pi Cell 从 MySQL 导入同一会话并恢复 leaf，以该 request_id 对应的回答作为新的输入，通过正常会话 prompt 入口继续。一个回答只追加一次；不调用假定存在的 `session.continue()`，不回放已完成工具。
4. 计划批准绑定计划记录/版本，后续执行从数据库读取；拒绝时追加修改意见并继续计划模式。问题回答、计划批准和未执行工具的历史共同提供上下文，不需要修改旧 entries。
5. 总用时、轮次和调用数按同一任务累计，等待用户不计执行用时；跨段启动不重置任务预算。会话持久化与任务预算属于业务数据，不扩展为通用工作流执行器。

#### 3.4.4 写操作确认的必要约束

- 写操作确认记录只保存该次操作的服务、工具、规范化参数、request_id 和执行状态；批准只适用于这条操作。批准后由下一执行段通过原 MCP 适配器及权限门控执行一次这条记录，再将实际结果作为新的会话上下文交给模型；拒绝时不执行。其它未执行工具由模型重新决定，不补跑整批工具。
- 执行前以条件更新领取该操作；已保存结果时复用结果。参数或工具目标变化需重新确认，不能把批准扩散为任意写调用的放行。
- 外部写成功但本地结果未落库的崩溃窗口不能靠执行标记消除。目标工具支持幂等键时复用稳定键；缺少幂等保障且结果未知时，标记未知并停止自动重放，先核对实际状态。设计不承诺跨系统 exactly-once。
- `_recover_parked_tasks` 对正常结束并持久化的执行段按以上规则继续，不再因原 Pi Cell 已退出就返回 `run_lost`。真正缺少会话快照或操作结果未知时如实报错，不能盲目自动重试。

---

### 3.5 单命令超时与运行预算（解决 R8）

- 采用一个明确的终止路径：任何沙箱命令超时或本段取消时，终止本执行段并执行 `docker rm -f`，销毁容器内全部进程，原任务落入既有失败或取消终态；不自动新开执行段重试，不继续复用超时后的容器，不用环境变量匹配 `pkill` 清理进程。不能仅终止宿主 Docker CLI 就声称命令已停止。单命令超时从容器就绪后开始计时：容器启动另有 Docker CLI 超时，取消信号仍覆盖启动阶段，避免守护进程较慢时把命令预算耗在启动上（2026-11-04 CI 发现）。
- backend 的超时/取消清理也应能按本段容器标识删除容器，覆盖 Pi Cell 被强杀、未执行 finally 的情况；`--rm` 和 `sleep <run_timeout>` 作为进程异常退出后的有界寿命保障，不设置暖池或后台回收器。
- 容器资源上限（2026-11-04 评审补充）：每个执行段容器带 `--memory`/`--memory-swap`（同值，不用 swap）、`--cpus`、`--pids-limit`，默认 `2g`/`2`/`512`，由 `DATAAGENT_SANDBOX_MEMORY`、`DATAAGENT_SANDBOX_CPUS`、`DATAAGENT_SANDBOX_PIDS_LIMIT` 配置；Pi 侧在调用 Docker 前校验格式。超限由内核 OOM/fork 失败体现为命令非零退出，不另设恢复路径。
- 轮数上限（2026-11-05 补充）：达到 `agent_max_turns` 时若模型仍要调用工具，本次运行以 `PI_LIMIT_EXCEEDED` 失败结束（与工具调用上限一致），不再显示为正常完成；最后一轮正好给出最终回答时仍为完成。起因：OntoFoundry 端到端测试中建模在批准计划后用满 40 轮，界面显示完成但没有产出。
- 沙箱网络（2026-11-05 用户决定）：默认仍为 `--network=none`；`DATAAGENT_SANDBOX_NETWORK` 可改为 `bridge` 或自建网络名，供可信部署中需要访问内部服务的技能使用（例：OntoFoundry 建模技能在运行中把结果提交到平台）。Pi 侧只接受普通网络名，拒绝 `container:<id>` 和带选项的值。开启后沙箱内代码可访问该网络能到达的所有服务，由部署方负责。沙箱镜像加入 `jsonschema`（技能本地校验依赖，断网时无法临时安装）。运行时安装（同日用户决定：需要能联网并写入，会话结束后丢弃可以接受）：镜像设 `PYTHONUSERBASE=/mnt/workspace/.dataagent/python`，`pip install` 自动落到本话题工作区，跨执行段保留；根分区仍只读，`/tmp` 仍是单容器内存盘。
- 统一任务预算：`agent_timeout_seconds=1800`、`agent_idle_timeout_seconds=300`、`agent_bash_timeout_seconds=600`、`agent_max_turns=40`、`agent_max_tool_calls=200`。MCP SQL 读/写预算分别保留 300s/60s。
- 单一预算档（2026-11-04 补充）：删除请求字段 `execution_mode` 与 interactive/background 两档配置。现状是所有真实调用方（前端、管理端试运行、定时与队列重投）都已走长档；只有漏传字段的 API 调用会落到 interactive 档，而该档只影响提交时写入的总超时和 SQL 读超时，执行期轮次、工具次数和 Bash 超时仍按 background 档，形成未经设计的组合。改为上面一组 `agent_*` 配置加 `agent_sql_read_timeout_seconds=300`。Pydantic 请求模型忽略未知字段，旧客户端继续发送 `execution_mode` 不受影响；`DATAAGENT_SETTINGS` 里的旧配置名会在启动时按未知字段报错，需要改名，环境变量形式的旧名会被忽略。不保留兼容分支。
- `bash_timeout < total_timeout`；MCP 已在控制面执行，SQL/MCP 超时独立受剩余任务预算约束，不再要求它包含在 Bash 超时中。等待卡片不消耗执行预算，跨段累计遵循 3.4.3。
- 保留工具活跃心跳与 idle 判定，检查反向代理读/写超时大于 1800s（当前 1860s），前端 SSE 在卡片等待和重连时能正确展示原任务。不能以 axios 配置推断 SSE 超时。

---

### 3.6 部署权限契约与宿主机挂载源发现（解决 R5）

1. **移除 `dataagent-home-init` 后的权限初始化与降权流程**：
   - `dataagent-backend` 镜像内置轻量 `gosu` 与 `/opt/dataagent-backend/docker-entrypoint.sh`：
     1. 容器以 `0:0`（root）启动进入 `docker-entrypoint.sh`；
     2. 读取目标运行身份 `RUNTIME_UID=${DATAAGENT_RUNTIME_UID:-1000}`、`RUNTIME_GID=${DATAAGENT_RUNTIME_GID:-1000}`；
     3. 初始化目录与命名卷权限：`mkdir -p /dataagent_runtime /app/.claude/skills`，将镜像内置技能同步至 `/app/.claude/skills`（若为空或版本更新），并执行 `chown -R ${RUNTIME_UID}:${RUNTIME_GID} /dataagent_runtime /app/.claude/skills`（覆盖新卷与复用旧卷）；
     4. 配置 Docker Socket 访问权限：检测 `/var/run/docker.sock` 的属主与属组 GID，为降权后的实际运行用户保留该 GID 附加组，并验证能够执行 `docker inspect/run/exec/rm`；不自动修改宿主 socket 权限。若 socket 为 root-only 且运行身份无权访问，启动自检明确失败并报告所需权限；
     5. 执行 `exec gosu ${RUNTIME_UID}:${RUNTIME_GID} sh -c "alembic upgrade head && uvicorn main:app --host 0.0.0.0 --port 8900"`。
   - **UID/GID 一致性**：`backend`（含 `Pi Cell`）与 `oda-sandbox`（`--user ${RUNTIME_UID}:${RUNTIME_GID}`）使用完全相同的 UID:GID 运行，`backend` 创建的工作区文件在沙箱容器内可读写，`results/` 文件通过独立只读挂载供基础工具读取，无需在业务代码中调用 `os.chown`。
2. **宿主机挂载源路径发现（区分容器内部署与宿主机本地开发）**：
   - **容器内部署模式**（检测到 `/.dockerenv` 或显式容器环境）：`backend` 启动时通过 `docker inspect <hostname>` 读取自身容器 `/dataagent_runtime` 与 `/app/.claude/skills` 的宿主机 `Source` 路径，作为拉起 `oda-sandbox` 时的 bind-mount 源路径。
   - **宿主机本地开发模式**（直接在宿主机运行 `uvicorn main:app`）：`backend` 与本地 Docker 守护进程处于同一宿主机文件系统，直接使用 `Path(dataagent_runtime_root).resolve()` 与 `Path(skills_root_dir).resolve()` 作为 bind-mount 源路径，不调用 `docker inspect <self>`。

---

### 3.7 文件与会话协议

- `cell.init` 升级到 `protocol_version: 2`，携带任务/本段标识、会话条目及 leaf、本次输入（原问题或 request_id 对应的回答）、剩余预算、MCP/技能配置、沙箱挂载配置及 backend 可见的 `results_dir`。Docker daemon 使用的宿主源路径与 Pi Cell 读写路径分别传递，均不进入模型提示。
- `session.entries` 回传完整会话快照（header、entries、leaf_id）及本次交互请求标识；`run.parked` 仅表示执行段已结束且逻辑任务等待用户。保留既有中立事件和前端卡片载荷契约。
- 会话导入后显式恢复 leaf；不将旧快照覆盖到已有更新版本上。快照沿用传输大小限制，超限明确报错，不能静默截掉会话条目或卡片。卡片提交与快照持久化按 3.4 的事务边界执行。
- 所有模型可见的结果路径通过同一目录映射生成。大输出持久化、SDK 帧序列化和前端结构化输出的大小限制分别验证，不能认为落盘之后其它通道自然不再超限。

---

## 4. 数据迁移、发布与回退边界（解决 R6）

### 4.1 存量数据平滑兼容
1. **存量会话历史初始化**：升级后首次在旧 Topic 中发消息时，若 `da_agent_session_checkpoint` 无记录，自动从 `da_agent_message` 读取历史 User/Assistant 文本消息构建初始 `SessionEntry` 列表；本轮结束后写入 `da_agent_session_checkpoint`。
2. **旧结果一次性迁移**：停止接单后执行幂等迁移，将 `workspace/.dataagent/results/` 的旧 JSONL 行数据转换为标准 JSON，按原文件名写入 `results/`；旧文本/JSON 保持内容。旧格式已丢失的非行元数据不能重建，迁移说明必须标明。原文件保留用于回退，新运行时仅使用 `/mnt/results` 一条读取路径；旧会话首次续聊时补充旧缓存路径到新路径的说明，不要求模型猜测。
3. **旧目录清理时点**：在会话运行准备阶段自动清理 `<topic_id>/workspace/.claude/skills` 下的旧拷贝副本；`<topic_id>/home` 不再创建或用于运行；原目录保留为回退数据，显式删除 Topic 时与 Topic 根目录一并清理，不设置稳定期或过渡运行分支。
4. **配置向后兼容**：`config.py` 中实际会话存储字段 `session_mysql_database` 通过 `validation_alias=AliasChoices("MYSQL_DATABASE", "SESSION_MYSQL_DATABASE", "DATAAGENT_SESSION_MYSQL_DATABASE")` 兼容旧环境变量名，避免旧 `.env` 文件在升级或回退时报错。

### 4.2 切换步骤与回退数据边界
- **升级切换前**：停止入口流量，等待 `running` 任务完成；对处于 `waiting_input` / `waiting_permission` 的旧版内存态挂起任务，迁移脚本将其状态置为 `suspended`（提示“系统已升级，请重新发送或确认”）。
- **回退步骤**：若需回退至旧版镜像（`oda-backend` + `oda-runner`），先用新版镜像运行 `scripts/prepare_runtime_rollback.py` 关闭等待卡片并恢复 Alembic 版本戳，随后还原旧版 `docker-compose.yml` 并执行 `docker compose up -d`。
- **回退数据边界说明**：
  - 新版运行期间写入的 `da_agent_message`（问答消息）与 `da_agent_sdk_record`（中立事件流）与旧版表结构完全一致，回退后历史消息、图表、SQL 块均可正常查看，后续追问可按旧版文本历史继续；
  - 新版特有的 `da_agent_session_checkpoint` 表在回退后会被旧版忽略（旧版退化为纯文本历史拼接，不再保留跨轮工具调用内部状态）；回退时刻处于等待中的卡片任务需由用户重新发起。

- **结果文件回退边界**：旧版仅能使用其原有缓存目录中的文件；新版 `/mnt/results` 对应的新结果不会自动被旧版分页工具识别。回退保留文件和用户产物，但不承诺旧版工具能继续读取新版文件路径。

### 回退版本戳（2026-11-03 容器演练修订）

旧镜像的启动迁移不能识别新增 revision。停止入口与运行中任务后，使用新版镜像的 `scripts/prepare_runtime_rollback.py` 关闭等待卡片并将 Alembic 版本戳恢复为 `20261027_000031`，保留新增会话表及文件。新增迁移使用 `CREATE TABLE IF NOT EXISTS`，再次升级可重复应用，并清空回退前的 SDK 快照，从包含旧镜像期间消息的文本历史重建，避免使用过期上下文。此脚本只支持本次明确的 31→32 回退，不推测其它版本。验证须启动真实旧镜像并继续历史文本会话。

### 报告技能文件输入修正

完整结果 smoke 发现报告技能文档声明 `--input-file`，但 generate_report.py 未实现，且 JSON 路径按 CSV 读取。本轮在技能本地实现互斥的 `--input`/`--input-file`，JSON 文件直接读取 rows；保留已有 CSV/内联 JSON 调用，更新技能文档及回归测试。共享运行时不增加报告专用调用层。


### 2026-11-04 PR review corrections

The review identified gaps in segment completion and sandbox lifecycle accounting. The backend, Pi runtime and local infrastructure paths are affected; frontend contracts remain unchanged.

- An explicit MCP `isError` response is a known result: persist it with `isError: true` under the existing `operation_status=done` contract before continuing. Transport failures keep the claimed operation unknown and must never be replayed. Approved tools belong to the existing approval turn, followed by the next model turn; no synthetic turn is created.
- Persist the initial task workspace baseline in checkpoint state and collect attachments once at terminal completion. Accumulate numeric token usage across model turns and segments separately from elapsed/tool/turn budgets.
- Card submission validates the current checkpoint before inserting the answer, returns conflict for missing/mismatched checkpoints, and queues the first accepted response immediately. Recovery polling covers crashes and responses racing the preceding segment's lease release.
- Reading an existing result references that file rather than saving another copy. Line reads keep offset/limit; truncated single-line JSON is processed directly with bash/Python, preserving exact raw payloads. Small errors stay inline; large/partial Bash output remains durable.
- Docker mount discovery is warmed once at startup before accepting requests, then cached for per-segment use. Inspection errors report SANDBOX_UNAVAILABLE. Backend cleanup runs only when Cell completion was not received; normal terminal and parked frames follow Node cleanup.
- Delete superseded interaction wait loops and event sinks. Correct regression fixtures to exercise MCP folding directly and remove all temporary fixture directories. Local contributor guidance uses only Pi and documents the sandbox image build.

Verification uses deterministic local provider/MCP fixtures, real MySQL/Redis and Docker, with no additional paid provider calls. Production-data verification remains tracked in the backlog; sandbox execution now supports Docker only.


### 2026-11-04 审阅后的实现收敛

本次范围涉及 backend/Pi 协议、附件持久化与 Docker 部署；前端 API 与消息格式保持现有契约。

1. 附件仅在任务终态更新助手消息。首段开始时的工作区快照写入既有 checkpoint state，后续段复用它，终态只做一次 diff。不在 parked 时保存半截 content、改写助手状态或合并附件。不能假定追问只发生在产物生成之前：通用 AskUserQuestion 可在任何工具之后执行，已有 HTTP fixture 验证先写文件再询问。
2. 挂载发现保留成功缓存，在 backend 启动阶段、接单前同步预热，再启动 coordinator；每段同步构造配置，仅做缓存读取与路径映射。这样同时避免首次 inspect 阻塞运行中的事件循环和每段线程跳转。sandbox 仅支持已验证的 Docker，移除 Podman 选择、自动回退及容器标记；Redis 本地引导不受影响。
3. 删除 sandbox_state/onLifecycle 协议状态。main.ts 只在 Cell.run 的 finally（含 sandbox.close）完成后发布终态或 run.parked，因此 backend 收到这些帧即认为 Cell 已完成清理。仅未收到完成帧的崩溃、超时、强制取消路径，在先终止 Cell 后进行兜底清理。
4. read 仅保留 offset/limit 按行读取，不再提供 byteOffset/byteLimit。普通读取无常驻尾注；超出预览上限才引用源路径并提示按行缩小范围或用 bash/Python 处理单行大文件。read 不生成重复结果文件。
5. 卡片持久化及鉴权作为一次同步操作整体移入线程，异步 HTTP handler 只负责随后立即入队。不能因已有其它 handler 仍做同步数据库访问，就把这些数据库访问放回事件循环。
6. 批准后的工具归属发起批准的已有轮次，随后模型自然开启下一轮；删除预留轮次状态。Token 只在 Pi supervisor 中累计，通过内部 segment commit 参数一次传给 checkpoint 持久化；writer 只存事件。

回退为本次修改前的 PR 提交，无表迁移。用无付费凭据的真实 HTTP/Pi/Docker/MySQL/Redis 流程，验证终态附件、跨段用量、异常清理和卡片冲突；保留先产出后追问、首次慢 inspect、卡片并发访问和批准操作传输中断用例。
