# 知识、记忆与用户反馈实施计划

日期：2026-11-19
设计：[design](../design/2026-11-19-experience-library-design.md)

1. 新迁移 000037 重塑经验字段、范围与状态；保留旧正文与来源，提供降级映射。
2. 更新 core/experience.py、schemas.py 与 admin_routes.py：CRUD、筛选、校验、硬删除、共享注入预览，更新总结技能与收取器。
3. task_executor 构建本段只读按需文件，复用 skills bind 挂载，写入同源提示词并在段结束后清理。
4. 主布局加入管理导航与待审核计数，设置移除运营和评测菜单；新知识/记忆页、共享表单/来源/详情、智能体只读卡片和预览。
5. 更新针对性后端和前端回归：字段、审核流、权限、范围隔离、预算跳过、30 条上限、只读挂载集成、菜单和表单。

## 验证

依任务约束仅运行：
- `cd dataagent-backend && .venv-py313/bin/python -m pytest tests/test_admin_routes.py tests/test_experience*.py -q`，使用已有 MySQL opt-in 配置。
- `nvm use` 后在 dataagent-frontend 执行 `npx vitest run --cache=false <touched specs>` 与 `npm run build`。
- 结果：Python 3.13 在本机 Docker MySQL（`127.0.0.1:3326`）的独立临时库通过 39 项目标测试，另通过存量记录迁移和回退检查。Node 22.22.0 下前端 82 项目标测试和生产构建通过。尚未运行使用 Redis、真实模型、Docker 沙箱和 SSE 的完整智能问数烟测，见[待办](../TODO.md#知识与记忆验证补全)。

## 反馈列表用户列

从反馈查询已有的会话关联读取 `auth_username`，通过管理员反馈接口返回，然后在反馈表格渲染用户列。用 MySQL 反馈集成测试、FeedbackView 组件测试和本地浏览器验证回退顺序。新字段可空，无数据库迁移，回退时撤销本次前后端变更即可。

## 上线与回退

先部署并执行 Alembic，再启动新后端/前端；无需额外服务或 Pi 重编译。部署开关仍控制总结/注入。回退先关闭注入，回退代码并降级迁移；知识与记忆降级后统一按旧经验解释，新增元数据会丢失，先备份表。仅本分支小步提交，不推送、不建 PR。

## 2026-11-26 修订实施（草案，待评审）

设计见 [修订](../design/2026-11-19-experience-library-design.md#2026-11-26-修订只保留知识目录加按需读取草案待评审)。单个 PR：

- Alembic：删除 `kind`、`inject_mode`（memory 行原样保留为知识）；`content` 改 MEDIUMTEXT（保留字符集、排序规则与可空性）。downgrade：先查 `OCTET_LENGTH(content) > 65535` 的行，有则拒绝降级并提示；`kind` 按来源恢复（有来源对话的为 memory，其余 knowledge），`inject_mode` 恢复为 on_demand（原值不保留，迁移说明写明）。MySQL DDL 会隐式提交，每步可单独重跑。发布：迁移与新后端同一次部署。
- 后端 `core/experience.py`：正文上限 20000；删除 kind / inject_mode 校验与分支、always 注入与 `MAX_INJECTED_CHARS`；pending 只允许每日总结产生；`build_injection` 只生成目录（上限 100 条，超出注明数量）与文件；提示词加入“扩展关键词 grep、分段 read”的说明；`collect_candidates` 不再传 kind / inject_mode；`INJECTION_FOLDER` 改为 `_knowledge`；`injection_preview` 增加资格判断结果。
- `models/schemas.py`：删除请求与响应模型中的 kind、inject_mode；body 上限 20000（PATCH 只改状态时不校验正文）。
- `api/admin_routes.py`：列表去掉 kind 筛选。
- `.claude/skills/experience-digest/SKILL.md`：用词“经验/经验库”→“知识”；删除“不要写业务口径”一条，改为“口径须有对话依据（用户纠正或确认）并列出来源任务”；单条正文上限沿用 500 字（总结产出保持短条目）。同步更新引用该 skill 文案的测试。
- 前端：`KnowledgeMemoryView.vue`（单一知识列表：来源与状态筛选、待审核入口、大编辑器 + 上传；文案“知识与记忆”→“知识”）、`router/index.js`（删除 `/memories` 路由与菜单项，待审核数显示在“知识”菜单上）、`ExperienceDrawer.vue`（加宽、去 kind）、`ExperienceFields.vue`、`entryForm.js`、`AgentExperienceCards.vue`（去额度）、`IntelligentQueryView.vue` 中的相关文案。
- 测试：长度校验（含 PATCH 只改状态）；管理员添加直接生效、每日总结为待审核；目录不含其他助手专属、停用与待审核条目；目录上限与超出提示；文件与目录路径一致；预览与运行在功能关闭、每日总结助手时一致；迁移 up/down（含 memory 行转换、超长行拒绝降级）；前端上传与审核。
- 本地端到端（schema `dataagent_qa`）：上传几千字知识 + 审核通过几条每日总结知识，用口语化问题提问（不含知识原词），确认模型看目录或扩展关键词 grep 找到知识、分段 read 并用于回答。
- TODO（`docs/TODO.md`）：运行前检索 + Pi 钩子注入（见设计“后续”）；通用钩子与插件机制；知识切片与语义检索。
