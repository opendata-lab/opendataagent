# 用户自建智能体、Skill 与 MCP（计划）

- 设计：[2026-11-26-user-owned-agents-design.md](../design/2026-11-26-user-owned-agents-design.md)
- 状态：待设计 §11 三个问题确认后开始
- 交付顺序：PR A → B → D → E1 → C → E2，每个 PR 单独过 CI 后合并

## PR A：所有权与通用授权（1–1.5 天）

- Alembic 迁移：
  - `da_agent_profile`、`da_mcp_server` 增加 `owner_user_id`、`visibility`。
  - 新建 `da_resource_grant`，把 `da_agent_grant` 的数据搬入后删除旧表。
  - 旧数据映射：智能体的 `visibility_json.mode=all` 映射为 `public`，`selected` 保持 `selected`；`visibility_json` 列删除。MCP 服务设为 `owner=NULL`、`public`。
- `core/accounts`：新增 `can_access(user, resource_type, row)` 和 `can_edit(...)`，`core/agent_visibility.py` 改为调用它们。
- 测试：在 `tests/test_accounts.py` 补访问矩阵（管理员 / 所有者 / 被授权用户 / 被授权角色 / 其他人 × private/selected/public），并补迁移映射用例。

## PR B：智能体自建、复制、发布（2 天）

- 后端：
  - 智能体的增删改、草稿、发布、远程 MCP 开关改为“所有者或管理员”鉴权。
  - 新增 `POST /agents/{id}/copy`，返回新的 `agent_id` 和 `dropped: {skills, mcp_servers}`。
  - 保存校验：引用的 Skill 和 MCP 必须对所有者可见；普通用户不能使用 `bypass`；`env_vars` 不能设置保留键。
  - `task_executor` 的合并顺序改为先放用户变量、再放运行时变量。
- 前端：
  - 智能体页：新建按钮对所有人可见；增加“我的 / 共享给我 / 全部（管理员）”筛选、所有者列、“复制为我的智能体”按钮。
  - 可见性组件，三类资源共用：仅自己 / 指定用户或角色 / 所有人。
- 测试：copy 接口（去掉无权引用的项、不复制 env）、越权编辑返回 403、保留变量被拒，以及前端组件单测。

## PR D：MCP 所有权（1–1.5 天）

- 普通用户创建时只允许 `http` 或 `sse`；配置了 `DATAAGENT_USER_MCP_ALLOWED_HOSTS` 时校验主机。
- 非所有者的列表和详情一律脱敏。
- 删除或收紧可见性前返回受影响的智能体，由前端确认。
- 测试：普通用户提交 stdio 返回 400、脱敏、白名单。

## PR E1：Skill 包卫生（前置，约 0.5 天）

- 实施 `docs/TODO.md` 中的“Skill 包卫生”一项：限制 zip 总大小、文件数、单文件大小和路径深度，拒绝符号链接与隐藏控制文件。

## PR C：Skill 所有权与重名（3–4 天）

- 迁移：
  - 新建 `da_skill` 表，为现有 folder 各生成一条系统记录。
  - `da_skill_document` 增加 `skill_id`；智能体快照和草稿把 `skill_folders` 改为 `skill_ids`。
  - 已发布的快照用迁移脚本一次性改写。
- 存储：用户 Skill 写到数据目录 `user-skills/<id>/`；导入时按 `(owner, name)` 判断是新建还是更新。
- 运行时：`resolve_agent_skill_runtime` 按 id 解析出挂载源与挂载名，并在运行时校验 Skill 对智能体所有者仍然可见，否则明确失败。
- 智能体保存时，拒绝同名 Skill，也拒绝与保留挂载名重名。
- 前端：Skills 页增加所有者和可见性；导入入口对所有人开放；智能体的 Skill 选择器显示“名称 · 所有者”。
- 文档：更新 `AGENTS.md` 中 Skill 根目录的说明（沙箱内的调用约定不变）和部署手册（用户 Skill 的数据目录与备份）。

## PR E2：端到端冒烟

在本地栈（MySQL `dataagent_qa`、Redis、沙箱镜像）上依次验证：
1. 普通用户 A 创建私有智能体，上传 Skill，添加 http MCP，然后发布。
2. B 看不到这些资源；A 设为 `selected` 授权给 B 后，B 能对话。
3. B 复制 A 的智能体，副本中只保留 B 看得到的引用。
4. A 与 B 上传同名 Skill，互不覆盖。
5. A 把 Skill 改为私有后，B 的副本运行明确失败。

## 回退

- 每个 PR 自带 Alembic downgrade。上线前先运行 `scripts/backup-db.sh`。
- 用户 Skill 的数据目录要纳入备份说明。
