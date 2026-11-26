# 知识、记忆与用户反馈设计

日期：2026-11-19（按已批准的 knowledge-memory prototype 更新）
计划：[implementation plan](../plans/2026-11-19-experience-library-plan.md)

## 当前状态与问题

现有经验库把人工知识和每日总结混在一起，仅有正文、助手/平台范围和候选状态；全文注入上限为 2000 字。新界面将人工维护的「知识」与自动提炼的「记忆」分开，审核时可以完整编辑。

## 产品与接口

- 主导航新增管理员专属 `/feedback`、`/knowledge`、`/memories`，记忆显示待审核总数。不设组标题。设置移除运营、评测菜单，保留评测路由，移除旧经验库页面与路由。
- 管理员反馈列表在「评价时间」旁显示「用户」。接口增加可空的 `user_display_name`，读取会话存储的登录显示名。页面优先显示该名称，其次显示现有 `user_id`（登录账号或 Widget 外部用户 ID），两者为空显示「匿名用户」。身份来自会话归属，沿用现有权限和数据表。
- 复用 `da_agent_experience` 与管理员 `/api/v1/nl2sql-admin/experiences` API：新增 `kind`（knowledge/memory）、`title`（40 字）、`when_text`（120 字）、`inject_mode`（always/on_demand）；正文仍存 `content`，API 为 `body`（500 字）。范围为 global/agent，无用户级。
- 新迁移将旧行全部转 memory，candidate → pending、retired → disabled，platform → global、assistant → agent；旧正文保留，以前 40 字补标题，适用条件留空供审核补充，默认按需读取。
- POST 仅能新增 active 知识；记忆仅每日总结收取器创建，初始 pending、原助手范围、on_demand。PATCH 可编辑或通过/忽略/停用/恢复；知识不能 pending，已审核记忆不能退回 pending。DELETE 为永久删除。
- 列表支持 kind、status、scope、agent_id、关键词及分页。预览 API `/experiences/injection-preview?agent_id=...` 返回同一算法计算的生效条目、待审核数、预算及实际提示词。
- 管理 API 继续复用 router 的 require_admin。页面和智能体编辑页卡片只对管理员开放；卡片只读，点击打开详情抽屉，通过管理链接跳至独立页面。不扩展编辑角色权限。
- 列表用 Element Plus，标题与灰色适用条件组成一行记录，安静的范围、注入方式、时间列，编辑 + 更多操作；详情与编辑用抽屉。记忆待审核采用左队列、右编辑表单。总结、平台与 Widget 来源，以及反馈中引用的会话，都用普通会话链接打开；管理员打开他人的会话为[只读](2026-11-19-unified-conversation-history-design.md)。助手页不常驻额度条，只在始终注入超额或部署未开启注入时提示。

## 注入与运行时

- 保留现有部署开关（默认关闭）与每日总结调度。内置总结助手不消费这些条目；技能输出 title、when、body、source_task_ids。
- active 知识和记忆共用 1000 字 always 额度；全局优先，各范围内按更新时间与 ID 降序。预算计入实际条目全文格式（标题、适用条件、正文及分隔符），超额条目跳过并记录日志，后续短条目仍可入选。
- on_demand 按更新时间与 ID 降序最多 30 条，提示词仅列 `- 标题（适用条件）→ 路径`。全文写到本次执行段独享的工作区同级目录，复用 sandbox skills 只读 bind 挂载到 `/opt/skills/_knowledge_memory/`，不作为技能注册、不增加挂载类型。执行段结束后清理，避免跨任务覆盖与过期内容残留。
- 提示词明确条目不得覆盖当前请求、技能约定或权限规则。卡片预算和预览均使用运行时相同函数；开关关闭时明确标示不注入。

## 范围与取舍

涉及 Vue 管理界面、FastAPI、MySQL Alembic、DataAgent task executor、总结技能；不修改 Pi 协议或 Docker 挂载实现。不做个人记忆、语义检索、自动审批、编辑角色写权限。删除后条目不再存在；总结后续重写同样候选可能重新收取。

## 2026-11-26 修订：知识文档化、全部按需读取、运行前检索（草案，待评审）

### 动机

- 同类产品（GPTs / Claude Projects 的 Knowledge、扣子知识库）把“知识”定义为上传的长文档、检索使用；“记忆”指从对话沉淀的短条目（ChatGPT Memory、Windsurf memories）。现状两者都是 ≤500 字短条目，“知识”名不副实，用户理解成本高。
- 现状 on_demand 索引知识与记忆混排取最新 30 条（`build_injection`、`MAX_INDEX_LINES`），记忆增多会把知识挤出索引。
- “始终注入”与 1000 字预算让配置变复杂；用户决定（2026-11-26）全部改为按需读取，但相关条目要一定送到模型面前。
- 原则（用户 2026-11-26）：不自造轮子，优先用 Pi 与 MySQL 已有机制，降低后续改动面。

### 方案

1. **知识 = 文档**：正文上限 20000 字；`content` 由 TEXT（65,535 字节，2 万汉字 utf8mb4 约 60KB，会超）改为 MEDIUMTEXT。前端支持上传 .md/.txt（浏览器读取后填入正文，不新增上传接口）。仍是 global/agent 范围、管理员维护。
2. **记忆 = 短条目**：≤500 字。管理员可手动新增（直接 active，来源显示“手动添加”）；每日总结提炼的仍为 pending 待审核。
3. **取消始终注入**：删除 `inject_mode` 列、1000 字预算及卡片/预览中的额度展示。迁移说明提醒管理员：原 always 的硬性规则应移入智能体系统提示词。
4. **检索（后端，MySQL 自带全文索引）**：
   - 在 `da_agent_experience(title, when_text, content)` 上建 `FULLTEXT ... WITH PARSER ngram`（MySQL 5.7.6+ 内置；本地 8.0.46 已确认插件 ACTIVE、`ngram_token_size=2`）。
   - `search_experiences(agent_id, query, limit)`：`MATCH ... AGAINST (... IN NATURAL LANGUAGE MODE)` 排序，范围沿用 `active_experiences_for`（`status='active' AND (scope='global' OR agent_id=当前助手)`），不会读到其他助手的专属条目；低于阈值的丢弃。不手写切词与打分。
   - 查询文本：本轮问题 + 最近 1–2 条用户消息（`_build_history` 已有），解决“按渠道拆一下”这类追问丢上下文。
5. **注入（Pi 官方扩展钩子 `before_agent_start`）**：
   - 后端把检索结果随 `cell.init` 下发（新字段，如 `retrieved_context`）；`sdk-session.ts` 的资源加载器注册一个内联扩展（`loadExtensionFromFactory`），在 `before_agent_start` 返回 `message`（customType `dataagent-retrieval`）。
   - Pi 把该消息追加进对话记录、以 user 角色发给模型（`convertToLlm`），不改系统提示词，前缀缓存不受影响；它随会话存进断点存档（`cell.ts` 保存全部 entries），卡片恢复与会话恢复自然带上。
   - 卡片恢复（prompt 为 interaction JSON）不下发检索结果，扩展不注入；`messageType !== "user"` 也跳过。
   - 内容：命中记忆全文（top 5，合计 ≤2000 字）；命中知识只写“标题（适用条件）→ 路径”（≤20 篇）；无命中不注入。
   - 不用 `context` 钩子：它每次 LLM 调用临时改消息、不落盘，下一轮前文变化同样破坏缓存。
6. **呈现与按需读取（Pi 自带 grep/read）**：沙箱只读挂载 `/opt/skills/_knowledge_memory/` 分 `knowledge/<id>.md`、`memory/<id>.md`，文件首部写标题与适用条件，仅含本助手可见条目；写入前建子目录，文件名只用整数 id。系统提示词（固定文本，不随问题变）说明：先看注入的相关条目，不足时 grep 这两个目录，长文档先 grep 定位再用 read 的 offset/limit 分段读。
7. **预览**：智能体页预览可输入问题，显示会命中的记忆/知识及注入消息；不输入问题时显示条目列表。与运行时调用同一检索函数；注入文件准备移到 `asyncio.to_thread`。

### 扩展点与取舍

- 检索逻辑只在后端（可访问数据库与用户身份，预览与运行一致）；Pi 侧只负责按官方钩子注入，不含业务逻辑。
- 后续若需“运行中按新发现再检索”，加一个 `search_memory` 工具，经 Pi→后端请求帧调用同一函数。
- 换向量检索时只改 `search_experiences`。ngram 对同义改写召回有限；知识文档多（数十篇、每篇上万字）时需切片与语义检索，记入 TODO。
