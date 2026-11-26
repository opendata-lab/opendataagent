# 用户自建智能体、Skill 与 MCP（设计）

- 日期：2026-11-26
- 状态：草案，待用户确认后实施
- 计划：[2026-11-26-user-owned-agents-plan.md](../plans/2026-11-26-user-owned-agents-plan.md)
- 涉及：`dataagent-backend`（schema、路由、运行时组装）、`dataagent-frontend`（智能体 / Skills / MCP 页面）、`dataagent-runtime-pi`（不改协议，仅沙箱挂载来源变化）

## 1. 现状（origin/main `97225d6` 核实）

- 账号体系只分管理员和普通用户两级。创建、编辑、发布、删除智能体，导入、编辑、导出、回滚、卸载 Skill，增删改 MCP 服务，全部挂 `require_admin`（`api/admin_routes.py`）。普通用户只能使用被授权的智能体、查看脱敏的 MCP 列表、管理自己的 MCP 访问令牌。
- 智能体：`da_agent_profile` 加草稿 / 发布表 `da_agent_draft`。可见性 `visibility_json` 取 `all` 或 `selected`；`selected` 的授权存在 `da_agent_grant(agent_id, principal_type in {user, role}, principal_id)` 中。没有所有者字段。
- Skill：一个全局命名空间 `SKILLS_ROOT_DIR/<folder>`。folder 名就是标识，智能体用 `skill_folders` 引用 folder 名。文件内容的版本存在 `da_skill_document` / `da_skill_document_version`，按 `relative_path` 区分。沙箱把选中的 Skill 只读挂载到 `/opt/skills/<folder>`。**导入同名 zip 会直接覆盖已有 Skill**（`import_skill_from_zip` 里的 `rmtree`，内置 Skill 除外）。
- MCP：存在 `da_mcp_server` 表，全局共享，支持 `stdio`、`http`、`sse` 三种传输。MCP 连接由后端里的 Pi 进程建立，不在沙箱内；所以 `stdio` 服务是**在后端主机上执行任意命令**。
- 智能体的 `env_vars` 最后合入 `runtime_env`（`core/task_executor.py:139`），可以覆盖 `SKILLS_ROOT_DIR`、`DATAAGENT_QUERY_LIMIT`、SQL 超时等运行时保留变量。

## 2. 目标

1. 普通用户可以创建自己的智能体、Skill、MCP 服务，并自行发布。
2. 三类资源使用同一套可见性：**仅自己 / 指定用户或角色 / 所有人**。
3. 可以把看得到的智能体**复制为自己的智能体**，修改后另存为新智能体。
4. Skill 重名时互不覆盖，也不泄露别人私有 Skill 的存在。
5. 管理员能查看和管理全部资源，内置资源仍归系统。

非目标：多租户隔离、资源配额与计费、Skill 市场或评分、单个 MCP 工具的勾选（另有 TODO）。

## 3. 统一的所有权与可见性

三类资源（智能体、Skill、MCP 服务）都增加两个字段：

| 字段 | 说明 |
|---|---|
| `owner_user_id BIGINT NULL` | 关联 `da_user.id`。`NULL` 表示系统资源（内置资源、迁移前已有的资源），只有管理员能改 |
| `visibility VARCHAR(16)` | `private`、`selected` 或 `public` |

把授权表通用化：把 `da_agent_grant` 迁移为 `da_resource_grant(resource_type, resource_id, principal_type, principal_id)`，`resource_type` 取 `agent`、`skill` 或 `mcp`。现有智能体授权原样搬过去。

**访问规则**（统一放在 `core/accounts` 的一个函数 `can_access(user, resource)` 里）：

- 管理员：全部可见、可改。
- 所有者：可见、可改、可删、可改可见性。
- 其他人：`public` 可见；`selected` 时本人或所属角色在授权表中则可见；`private` 不可见。
- “可见”在三类资源上的含义：
  - **智能体**：可对话、可复制，可在“智能体”列表里查看配置（系统提示词也能看到，见 §8）。
  - **Skill**：可在 Skills 页查看内容，可在**自己的**智能体里选用。
  - **MCP 服务**：可在自己的智能体里选用；连接配置对非所有者一律脱敏（沿用现有 `redact_mcp_servers`）。

旧数据迁移：现有智能体的 `all` 改为 `public`，`selected` 保持 `selected`；现有 Skill 与 MCP 的 `owner_user_id=NULL`，`visibility=public`，行为与现在一致。

## 4. 智能体

- 新建：任何登录用户都可以新建，默认 `private`，所有者为自己。草稿、发布流程复用 `da_agent_draft`，只把路由权限从 `require_admin` 换成“所有者或管理员”。
- **复制为我的智能体**：新增接口 `POST /agents/{agent_id}/copy`，要求调用者能看到源智能体。
  - 复制的内容：名称（加“副本”后缀）、描述、系统提示词、`max_turns`、`plan_timeout_action`，以及**调用者看得到的** `skill_ids` 和 `mcp_server_ids`。
  - 调用者看不到的 Skill / MCP 会被去掉，并在返回结果中列出，前端提示“以下 N 项因无权限未复制”。
  - 新副本为 `private`、未发布，所有者是调用者；可以自由修改，与源智能体再无关联（不跟随源更新）。
  - `env_vars` 不复制，因为其中常放连接串或密钥。
  - 不复制授权、远程 MCP 开关、经验库。
- 发布：所有者可以发布，可见性决定谁能在对话页选到它。`remote_mcp_enabled`（单助手 MCP）同样由所有者控制，个人访问令牌的校验沿用现有逻辑。
- 默认智能体、内置智能体：仍只有管理员可改；所有人都可以复制。
- 保存校验：智能体引用的每个 Skill、MCP 服务都必须**对所有者可见**，否则拒绝保存。
- 普通用户的智能体不能设置 `bypass` 权限模式（与 §7 的保留变量一样，属于提权）。

## 5. Skill 与重名

### 5.1 标识与存储

- 新建 Skill 登记表 `da_skill(id, name, owner_user_id, visibility, storage_dir, version, created_at, updated_at)`，用唯一键 `(owner_user_id, name)`。
  - `name` 取自 SKILL.md front matter，也是沙箱里的挂载目录名，即模型看到的名字。
  - `storage_dir`：
    - 系统 Skill 仍为 `SKILLS_ROOT_DIR/<name>`，路径不变；
    - 用户 Skill 为 `<DATAAGENT_HOST_ROOT 下的数据目录>/user-skills/<id>/`，不放进仓库的 `.claude/skills`。
- 智能体改为按 id 引用：`skill_folders` 改为 `skill_ids`。迁移时，现有 folder 名映射到对应系统 Skill 的 id。
- `da_skill_document.relative_path` 增加 `skill_id` 列，版本、对比、回滚都按 `(skill_id, relative_path)` 区分。

### 5.2 重名规则

| 情况 | 处理 |
|---|---|
| 同一所有者导入同名 Skill | 视为**更新**：生成新版本，可回滚（与现在一样，但只覆盖自己的） |
| 不同所有者重名 | 允许，互不影响。列表与选择器显示“名称 · 所有者” |
| 与系统 Skill 重名 | 允许（用户 Skill 不会覆盖系统 Skill）。管理员导入时走系统命名空间 |
| 一个智能体同时选了两个同名 Skill | **保存时拒绝**，因为沙箱挂载路径 `/opt/skills/<name>` 会冲突，Skill 之间按兄弟目录名互相引用也会出错 |
| 与运行时保留挂载名重名（如经验注入目录） | 导入时拒绝 |

这种做法不需要全局抢名，也不会因为“名称已被占用”而暴露别人私有 Skill 的存在。

### 5.3 运行时

`resolve_agent_skill_runtime` 按 `skill_ids` 查出 `(name, storage_dir)`，沙箱挂载为 `storage_dir → /opt/skills/<name>`。沙箱内的调用约定 `"$DATAAGENT_PYTHON_BIN" "${SKILLS_ROOT_DIR}/<skill-folder>/scripts/<name>.py"` 不变，也不改 Pi 协议，Pi 收到的仍是 `{source, folder}` 列表。

用户上传的 Skill 脚本只在无网络、只读挂载的沙箱里执行，与现状一致。`docs/TODO.md` 里“Skill 包卫生：导入/导出过滤与安全上限”一项变成**前置条件**：开放给普通用户上传前，必须先限制 zip 大小、文件数、路径和符号链接。

### 5.4 使用中的 Skill 被删除或改为私有

- 删除或收紧可见性之前，列出受影响的、别人的智能体，由所有者确认。
- 运行时再校验一次：如果 Skill 已对**智能体所有者**不可见或已被删除，本次运行明确失败，提示“智能体引用的 Skill X 已不可用”。不静默跳过，也不回退到别的版本。

## 6. MCP 服务

- 普通用户只能添加 `http` 或 `sse` 传输的服务；`stdio` 仍只限管理员，因为 stdio 等于在后端主机上执行命令。
- 所有者看得到完整配置（含 headers）；其他人只看名称、描述、地址主机名，看不到 headers 和 env。
- 别人选用 `public` MCP 时，调用会带着**所有者配置的凭据**发出。界面上说明这一点；所有者改为私有后，§5.4 的规则同样适用。
- SSRF：用户可以填任意内网地址，让后端去连接。当前部署在可信内网，本期只记日志，并在后端配置项增加可选的主机白名单 `DATAAGENT_USER_MCP_ALLOWED_HOSTS`（留空即不限制）。
- 每次调用继续附带 `X-Agent-User-Id` 和 `X-Agent-Id`。

## 7. 运行时保留变量

普通用户的智能体的 `env_vars` 不允许覆盖 `DATAAGENT_*`、`SKILLS_ROOT_DIR`、`PATH`、`PYTHON*`、`NODE_*`，保存时拒绝。合入顺序改为先放用户变量、再放运行时变量，作为第二道保险。管理员的系统智能体保持现状。

## 8. 已接受的取舍

- **智能体可见即配置可见**：看得到智能体的人能看到它的系统提示词并复制。模型在对话中本来就可能复述提示词和 Skill 内容，隐藏它们并不能提供真正的保护。如果以后有“只能用、不能看”的需求，再加 `allow_copy` 开关。
- 复制是一次性快照，不随源智能体更新。
- 不做资源配额。上传大小沿用 Skill 包卫生的上限。

## 9. 接口变化（概要）

- 新增普通路由前缀，例如 `/api/v1/dataagent/agents`、`/skills`、`/mcp/servers`，按所有者或管理员鉴权；原 `/nl2sql-admin/...` 的资源管理路由合并过来，管理员仍可管理全部资源。
- `POST /agents/{id}/copy`
- `PUT /{resource}/{id}/visibility`，请求体为 `{visibility, grants:[{principal_type, principal_id}]}`，三类资源共用一个组件。
- 列表接口增加 `scope=mine|shared|all`，并在每行返回 `owner`、`visibility`、`can_edit`。

## 10. 工作量评估

| 块 | 内容 | 估算 |
|---|---|---|
| A. 所有权 + 通用授权 | 迁移（owner/visibility 字段、`da_resource_grant`）、`can_access`、旧数据映射、单测 | 1–1.5 天 |
| B. 智能体自建 + 复制 + 发布 | 路由鉴权改造、copy 接口、保存校验、保留变量、前端可见性组件 / 复制按钮 / 我的·共享筛选 | 2 天 |
| C. Skill 所有权与重名 | `da_skill` 登记表、存储目录、`skill_folders`→`skill_ids` 迁移、文档表加 `skill_id`、导入规则、运行时挂载、前端选择器 | 3–4 天（最大的一块） |
| D. MCP 所有权 | 传输限制、脱敏、可见性、主机白名单 | 1–1.5 天 |
| E. 前置与验收 | Skill 包卫生上限、端到端冒烟（普通用户自建 → 发布 → 他人使用 → 复制 → 删除 Skill 后运行明确失败）、文档 | 1.5 天 |

合计约 **9–11 个工作日**，拆成 5 个 PR，按 A → B → D → E(卫生) → C 的顺序交付。B 完成后就能用上“自建、复制、发布智能体”（只能选用系统 Skill 和 MCP）；C 完成后才能上传自己的 Skill。

## 11. 待确认

1. 普通用户能否把智能体直接设为 `public`，还是 `public` 需要管理员审核？本设计默认**不审核**。
2. 是否开放普通用户的 stdio MCP？本设计默认**不开放**。
3. 系统提示词“可见即可复制”是否可以接受（§8）？
