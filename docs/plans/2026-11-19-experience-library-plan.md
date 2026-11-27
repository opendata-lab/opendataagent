# 知识实施计划

日期：2026-11-19 初版（已实施）；2026-11-26 整体修订
设计：[design](../design/2026-11-19-experience-library-design.md)

初版（知识/记忆分页、审核流、按需文件、反馈用户列）已实施，验证记录见 [待办](../TODO.md#知识与记忆验证补全)。以下为 2026-11-26 修订的实施，分两个 PR。

## PR 1：工具分类按名称精确匹配（修复现有缺陷，先合入）

- `dataagent-frontend/packages/agent-conversation/src/core/toolPresentation.js`：`describeToolAction` 改为按内置工具名（大小写不敏感）精确映射 `bash`/`read`/`write`/`edit`/`ls`/`grep`/`find`/`glob`/`skill`；其余工具一律为通用工具（显示名称与扳手图标），删除名称子串与参数形状推断。保留 `mcp__<server>__<tool>` 的显示名格式化。
- `ui/ToolOutput.vue`：图标随新分类；结构化结果（SQL、图表、Python）不变。
- 测试（`packages/agent-conversation/src/__tests__` 中对应 spec）：用本地会话记录里的真实名称断言——`odw_search_tables`、`search_ontology`、`get_ontology`、`odw_query_readonly`、`AskUserQuestion` 为通用工具；`find`、`grep` 为搜索；`bash` 为执行命令；`smoke_read_large` 不再被当作读取文件。
- 验证：`nvm use`；`npx vitest run --cache=false <touched specs>`；`npm run build`；本地打开一个含 MCP 调用的历史会话目视确认图标。
- 回退：撤销该 PR。

## PR 2：只保留知识

1. **Alembic**：删除 `kind`、`inject_mode`（memory 行原样保留为知识）；`content` 改 MEDIUMTEXT（保留字符集、排序规则与可空性）。downgrade：先查 `OCTET_LENGTH(content) > 65535` 的行，有则拒绝降级并提示；`kind` 按来源恢复（有来源任务的为 memory，其余 knowledge），`inject_mode` 恢复为 on_demand（原值不保留，迁移说明写明）。MySQL DDL 会隐式提交，每步可单独重跑。
2. **后端 `core/experience.py`**：正文上限 20000（总结产出仍 500）；删除 kind / inject_mode 校验与分支、always 注入与 `MAX_INJECTED_CHARS`；pending 只允许每日总结产生；`build_injection` 只生成目录（上限 100，超出注明数量）与文件（首行 `# 标题`、次行适用场景），路径函数共用；提示词加入“扩展关键词 grep、分段 read”的约定；`collect_candidates` 不再传 kind / inject_mode；`INJECTION_FOLDER` 改为 `_knowledge`；`injection_preview` 返回资格判断结果。
3. **`models/schemas.py` / `api/admin_routes.py`**：删除请求与响应模型中的 kind、inject_mode，`_row_to_item` 同步；body 上限 20000（PATCH 只改状态时不校验正文）；列表去掉 kind 筛选。
4. **每日总结 skill** `.claude/skills/experience-digest/SKILL.md`：用词改为“知识”；删除“不要写业务口径”，改为“口径须有对话依据（用户纠正或确认）并列出来源任务”；同步相关测试。
5. **前端管理页**：
   - `KnowledgeMemoryView.vue`：单一知识列表（来源与状态筛选、待审核队列、大编辑器 + 上传 .md/.txt）；文案“知识与记忆”→“知识”；
   - `router/index.js`：删除 `/memories` 路由与菜单项，待审核数显示在“知识”菜单；
   - `ExperienceDrawer.vue`（加宽、去 kind）、`ExperienceFields.vue`、`entryForm.js`、`AgentExperienceCards.vue`（去额度与注入方式）、`IntelligentQueryView.vue` 相关文案、`composables/experienceCounts`。
6. **对话展示**：`toolPresentation.js` 增加知识类（路径或命令含 `/opt/skills/_knowledge/`）：read → “查阅知识”（标题取自输出首行），grep/find/bash grep → “搜索知识”；`ToolOutput.vue` 增加书本图标。后端测试断言 `INJECTION_ROOT` 与前端常量一致。
7. **测试**：
   - 后端：长度校验（含 PATCH 只改状态）；管理员添加直接生效、每日总结为待审核、不能退回 pending；目录不含其他智能体专属、停用与待审核条目；目录上限与超出提示；文件首行标题、目录与文件路径一致；预览与运行在开关关闭、每日总结智能体时一致；迁移 up/down（含 memory 行转换、超长行拒绝降级）。
   - 前端：上传填正文、审核流、无 `/memories`；知识类工具展示（read/grep/find/bash 指向知识目录，普通文件读取不受影响）。
8. **本地端到端**（schema `dataagent_qa`，MySQL `127.0.0.1:3326`，Redis，真实模型，Docker 沙箱）：上传几千字口径文档 + 审核通过几条每日总结知识；用不含知识原词的口语化问题提问，确认模型通过目录或扩展关键词 grep 找到知识、分段 read 并用于回答；对话中显示“搜索知识 / 查阅知识”与书本图标；手动触发一次每日总结，确认产出待审核知识（可含有依据的口径）。

## 上线与回退

- 迁移与新后端、前端同一次部署；部署开关仍控制总结与注入。
- 回退：先关闭开关，回退代码并降级迁移（超长正文会阻止降级，需先处理）；降级前备份表。
- `docs/TODO.md`：完成后更新“知识与记忆验证补全”；后续项见“知识运行前检索与钩子注入”“插件管理与导入”“MCP 工具名命名空间”。
