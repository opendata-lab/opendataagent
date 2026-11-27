# 知识实施计划

日期：2026-11-19 初版（已实施）；2026-11-26 整体修订
设计：[design](../design/2026-11-19-experience-library-design.md)

初版（知识/记忆分页、审核流、按需文件、反馈用户列）已实施，验证记录见 [待办](../TODO.md#知识与记忆验证补全)。以下为 2026-11-26 修订的实施，分两个 PR。

## PR 1：工具图标与文字分开确定（修复现有缺陷，先合入）

- `dataagent-frontend/packages/agent-conversation/src/core/toolPresentation.js`：`describeToolAction` 返回的 `kind`（文字）只对内置工具名精确匹配（含 `AskUserQuestion`、`ExitPlanMode` 与 `Tool` 技能回退），其余为通用工具、文字为工具名；新增 `iconKind`：内置工具同 `kind`，其余按名称拆词后匹配动作词（见设计）。删除名称子串与参数形状推断对文字的影响。
- `ui/ToolOutput.vue`：图标改用 `iconKind`；新增下载、问号、清单三个图标路径；删除“名称含 image → 生成图片”的文字推断；结构化结果不变。
- 测试：更新 `src/views/intelligence/__tests__/toolPresentation.spec.js`（含 `Tool` 技能回退），新增 `packages/agent-conversation/src/__tests__/toolPresentation.spec.js`，用本地会话记录里的真实名称断言文字与图标：`odw_search_tables`/`search_ontology` → 文字为工具名、放大镜；`odw_query_readonly` → 数据库；`get_ontology`/`odw_get_table_ddl` → 文档；`odw_export_metadata` → 导出；`odw_resolve_datasource` → 扳手；`AskUserQuestion` → 向用户提问；`ExitPlanMode` → 提交计划；`find` → 搜索文件；`bash` → 执行命令。`historyRender.spec.js` 中“输出更新不重新分类”的断言保持通过。
- 验证：`nvm use`；`npx vitest run --cache=false <touched specs>`；`npm run test:sdk -- --cache=false`；`npm run build`；本地打开一个含 MCP 调用的历史会话目视确认图标。
- MCP 工具中文名（同一 PR）：Pi 注册 MCP 工具时解析 `title` → `annotations.title` → 工具名，`tool.started` 携带可选 `tool_title`（含审批恢复）；`contracts/agent-events` 与 `sdk-block-projection` 契约、后端历史投影、SDK reducer / `blockToToolProp` / `ToolOutput` / 类型声明同步；验证 Pi MCP 测试、后端事件与投影契约测试、前端全量单测、`build`、`build:widget`、`test:sdk-types`。完整烟测：本地真实模型调用一次带 `title` 的 MCP 工具，核对实时、刷新后的历史与 Widget 显示一致。OntoFoundry 更新内置 SDK 包（SDK 版本 0.3.0 → 0.3.1）。
- 回退：撤销该 PR。

## PR 2：只保留知识

1. **Alembic**：先删除索引 `idx_da_agent_experience_kind`，再删除 `kind`、`inject_mode`（memory 行原样保留为知识），新建 `(status, updated_at)` 索引；`content` 改 MEDIUMTEXT（保留字符集、排序规则与可空性）。`downgrade()` 直接抛错，提示恢复升级前备份并使用对应版本应用（与时区迁移、`20261027_000030` 一致），不写反向 SQL。MySQL DDL 会隐式提交，每步可单独重跑（按列与索引是否存在判断）。
2. **后端 `core/experience.py`**：正文上限 20000（总结产出仍 500）；删除 kind / inject_mode 校验与分支、always 注入与 `MAX_INJECTED_CHARS`；创建 pending 时必须带 `digest_topic_id`，pending 可反复编辑、已审核不能退回 pending；`build_injection` 生成目录（上限 100，超出注明数量）与全部可见生效知识的文件（首行 `# 标题`、次行适用场景，不随目录截断），路径函数共用；`_row_to_item` 返回派生 `source`；关键词搜索覆盖正文；`build_session_records` 仅取当天最新 30 个已完成任务（不含失败与评价、不按评价/失败优先），按 `## 会话 <topic_id>` 分组（各会话按最早入选轮次、组内按对话顺序排列）并在任务头保留 `topic_id`；`digest_agent_payload` 描述中的“记忆页面”改为“知识页面”（迁移 `20261127_000047` 同步更新未自定义的内置记录）；提示词加入“扩展关键词 grep、分段 read”的约定；`collect_candidates` 不再传 kind / inject_mode；`INJECTION_FOLDER` 保持 `_knowledge_memory`；`injection_preview` 返回资格判断结果。
3. **`models/schemas.py` / `api/admin_routes.py`**：删除请求与响应模型中的 kind、inject_mode，`_row_to_item` 同步；body 上限 20000（PATCH 只改状态时不校验正文）；列表去掉 kind 筛选；`Experience` 响应加 `source`。
4. **每日总结 skill** `.claude/skills/experience-digest/SKILL.md`：用词改为“知识”；删除“不要写业务口径”，改为“口径须有对话依据（用户纠正或确认）、引用用户原话、注明是否可能只适用于特定任务或部门，并列出来源任务”；同步相关测试。
5. **前端管理页**：
   - `KnowledgeMemoryView.vue`：单一知识列表（状态标签页、待审核队列、大编辑器，正文上限 20000 字）；搜索框提示“搜索标题、适用场景或正文”；文案“知识与记忆”→“知识”；
   - `ExperienceSources.vue`：显示条件由 `item.kind === 'memory'` 改为 `item.source === 'digest'`；
   - `router/index.js`：删除 `/memories` 路由与菜单项，`/knowledge` 去掉 `props: { kind }`，待审核数显示在“知识”菜单；
   - `ExperienceDrawer.vue`（加宽、去 kind）、`ExperienceFields.vue`（正文占位文案去掉“不要写业务口径定义”，上限随来源）、`entryForm.js`、`AgentExperienceCards.vue`（去额度与注入方式）、`IntelligentQueryView.vue` 相关文案、`composables/experienceCounts`。
6. **对话展示**：`toolPresentation.js` 对 `read`/`grep`/`find` 按路径边界增加 `knowledge_read` / `knowledge_search`（详情不含哈希路径，不解析标题，不识别 bash）；`ToolOutput.vue` 在图标、摘要文字、描述、状态文字、面板可展开五处加分支。后端测试断言 `INJECTION_ROOT` 与前端常量一致。
7. **测试**：
   - 后端：修复已失效的 `tests/test_experience.py`（引用已删除的 `DIGEST_USER_ID` 与 `da_agent_topic.auth_user_id`，改按 `agent_id = DIGEST_AGENT_ID` 查询）；超过 100 条时第 101 条文件存在且目录有超出提示；正文搜索；创建 pending 无 `digest_topic_id` 被拒绝；长度校验（含 PATCH 只改状态）；管理员添加直接生效、每日总结为待审核、不能退回 pending；目录不含其他智能体专属、停用与待审核条目；目录上限与超出提示；文件首行标题、目录与文件路径一致；预览与运行在开关关闭、每日总结智能体时一致；迁移 upgrade（含 memory 行转换）与 downgrade 抛错。
   - 前端：大编辑器与字数上限、审核流、来源区块按 `source` 显示；更新 `router/__tests__/index.spec.js`、`router/__tests__/authGuard.spec.js`、`IntelligentQueryView.spec.js` 中的 `/memories`；知识类工具展示（知识目录有无结尾斜杠、相似前缀目录不误判、普通文件读取与 bash 不受影响）。
8. **本地端到端**（schema `dataagent_qa`，MySQL `127.0.0.1:3326`，Redis，真实模型，Docker 沙箱）：粘贴几千字口径文档 + 审核通过几条每日总结知识；用不含知识原词的口语化问题提问，确认模型通过目录或扩展关键词 grep 找到知识、分段 read 并用于回答；对话中显示“搜索知识 / 查阅知识”与书本图标；手动触发一次每日总结，确认产出待审核知识（可含有依据的口径）。

## 上线与回退

- 迁移与新后端、前端同一次部署；部署开关仍控制总结与注入。
- 回退：先关闭开关，用上线前备份恢复数据库并切回旧版本；`alembic downgrade` 会直接报错。上线前必须备份。
- `docs/TODO.md`：完成后更新“知识与记忆验证补全”；后续项见“知识运行前检索与钩子注入”“插件管理与导入”“MCP 工具名命名空间”。
