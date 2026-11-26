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

设计见 [修订](../design/2026-11-19-experience-library-design.md#2026-11-26-修订知识文档化全部按需读取运行前检索草案待评审)。单个 PR：

- Alembic：删除 `inject_mode`；`content` 改 MEDIUMTEXT；加 ngram FULLTEXT 索引（downgrade 反向）。
- 后端 `core/experience.py`：按 kind 校验长度（knowledge 20000 / memory 500）；删除 `inject_mode`、`MAX_INJECTED_CHARS`、always 分支；新增 `search_experiences`（MATCH…AGAINST + 阈值 + 字数上限）；`build_injection` 生成分目录文件（先 mkdir，整数 id 文件名）；`collect_candidates` 不再传 inject_mode；`injection_preview` 接收可选 `question`。
- `models/schemas.py`：`ExperienceCreateRequest.kind` 允许 memory；body 上限按 kind；删除 inject_mode 字段（创建与更新）。
- `api/admin_routes.py`：预览接口加 `question` 参数。
- `core/task_executor.py`：用本轮问题 + 最近 1–2 条用户消息检索，结果放入 cell init；卡片恢复不检索；注入准备用 `asyncio.to_thread`。
- Pi：`protocol/frames.ts` 加 `retrieved_context`；`kernel/sdk-session.ts` 注册内联扩展，`before_agent_start` 返回 message；`npm run build`。
- 前端：`KnowledgeMemoryView.vue`（知识大编辑器 + 上传，记忆新增按钮、说明、“手动添加”来源）、`ExperienceDrawer.vue`（加宽、按 kind 的提示）、`ExperienceFields.vue`、`entryForm.js`、`AgentExperienceCards.vue`（去额度、预览可输入问题）、`api/dataagent.js` `getExperienceInjectionPreview`。
- 测试：长度校验；手动记忆直接生效；检索范围不含其他助手；中文与英文词（如 GMV、表名）都能命中；阈值与字数上限；追问用历史命中；卡片恢复不重复注入；Pi 扩展注入消息进入 checkpoint 且系统提示词不变；迁移 up/down；前端上传与预览。
- 本地端到端（schema `dataagent_qa`）：上传几千字知识 + 若干记忆，提相关问题与追问，确认注入消息、模型 grep/read 知识文件并用于回答、第二轮缓存读取 token 正常。
- TODO：知识切片与语义检索；运行中再检索（`search_memory` 工具）；确认生产 MySQL ≥5.7.6。
