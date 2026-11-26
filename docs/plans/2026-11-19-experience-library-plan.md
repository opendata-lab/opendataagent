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

- 后端 `core/experience.py`：按 kind 校验长度（knowledge 20000 / memory 500）；POST 允许 kind=memory（active）；删除 `inject_mode`、`MAX_INJECTED_CHARS`、always 分支；新增 `search_experiences`；`build_injection` 改为生成分目录文件 + 预检索节；`injection_preview` 接收 `question`。
- Alembic：删除 `da_agent_experience.inject_mode`（downgrade 恢复为 on_demand）。
- `core/task_executor.py`：把 `params.question` 传入注入准备。
- API schema / `admin_routes.py`：去掉 inject_mode 字段，预览接口加 `question` 参数。
- 前端：`KnowledgeMemoryView.vue`（知识大编辑器 + 上传、记忆新增按钮与说明）、`ExperienceFields.vue`、`entryForm.js`、`AgentExperienceCards.vue`（去额度、预览输入问题）。
- 测试：长度校验、手动记忆直接生效、检索范围不含其他助手、bigram 排序、分目录文件、无命中不加节、迁移；前端上传填正文、无注入方式字段。
- 本地端到端：上传几千字知识 + 若干记忆，提相关问题，确认提示词含命中记忆、模型 read/grep 知识文件并用于回答（沙箱内 grep 可达挂载目录需实测）。
- TODO：知识切片与语义检索；运行中再检索（Pi `context` 钩子 / `search_memory` 工具）。
