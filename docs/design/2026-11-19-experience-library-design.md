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

## 2026-11-26 修订：只保留知识、全部按需读取、运行前检索（草案，待评审）

### 动机

- “记忆”在同类产品里是个人概念（ChatGPT Memory、Windsurf memories 都按用户沉淀），而我们的条目由管理员审核、对助手的所有用户生效，叫“记忆”容易误解。用户决定（2026-11-26）：取消“记忆”，只保留“知识”。
- 闭环：每日总结 → 待审核知识 → 管理员审核生效 → 运行时检索使用。使用时只查知识一处。
- 现状知识只能是 ≤500 字短条目，放不下文档；`inject_mode`（始终/按需）与 1000 字预算让配置变复杂。用户决定全部改为按需读取，但相关知识要一定送到模型面前。
- 原则（用户 2026-11-26）：不自造轮子，优先用 Pi 与 MySQL 已有机制，降低后续改动面。

### 方案

1. **只有知识一种条目**，来源两类：
   - 管理员添加：手写或上传 .md/.txt（浏览器读取后填入正文，不新增上传接口），直接生效；
   - 每日总结：提炼为待审核（pending）知识，附来源对话，管理员审核后生效。
   - 正文上限 20000 字，长短不限；`content` 由 TEXT（65,535 字节，2 万汉字 utf8mb4 约 60KB，会超）改为 MEDIUMTEXT。范围仍是 global/agent。
2. **删除 `kind` 与 `inject_mode`**：迁移把现有 memory 行原样转为知识（保留状态与来源）；删除两列及响应模型、行映射 `_row_to_item`、列表筛选中的相关字段；删除 1000 字预算及卡片/预览中的额度展示。迁移说明提醒管理员：原“始终注入”的硬性规则应移入智能体系统提示词。表名 `da_agent_experience` 与模块名不改，只改界面文案（“知识与记忆”→“知识”）。
3. **检索（后端，MySQL 自带全文索引）**：
   - 在 `(title, when_text, content)` 上建 `FULLTEXT ... WITH PARSER ngram`（MySQL 5.7.6+ 内置；本地 8.0.46 已确认插件 ACTIVE、`ngram_token_size=2`）。
   - `search_knowledge(agent_id, query)`：可见范围 `status='active' AND (scope='global' OR (scope='agent' AND agent_id=%s))` 写在同一条 SQL 里、先过滤后取前 N，不会读到其他助手的专属条目；`MATCH ... AGAINST (... IN NATURAL LANGUAGE MODE)` 取正分命中，按分数、更新时间确定排序。不设固定分数阈值：InnoDB 的 IDF 按全表算，条目很少时精确命中的分数也可能极低（Codex 2026-11-26 按 8.0.46 源码推算）。不手写切词与打分。
   - ngram 语义：中英文都按两字片段匹配（`GMV`→gm/mv；`dwd_order` 可能因 `dw` 命中 `dwd_customer`）；单个汉字查询无结果；默认停用词会去掉含停用词字母的片段（如 `id`）。第一期接受，按标识符精确匹配记入 TODO。`ngram_token_size` 只能在启动参数改，改后需重建索引。
   - 查询文本：本轮问题 + 最近 1–2 条用户消息（`_build_history` 已有），解决“按渠道拆一下”这类追问丢上下文。
4. **注入（Pi 官方扩展钩子 `before_agent_start`）**：
   - 后端把检索结果随 `cell.init` 下发（新字段 `retrieved_context`，Python 侧 `PiRunContext` 与 `to_init_payload()` 同步加）；`sdk-session.ts` 用公开的 `DefaultResourceLoader`（`extensionFactories: [内联扩展]`、`noExtensions: true`、内存设置）预先加载扩展，自定义加载器的 `getExtensions()` 返回该结果（`loadExtensionFromFactory` 未从包入口导出，不能用；Codex 2026-11-26 核实）。扩展在 `before_agent_start` 返回 `message`（customType `dataagent-retrieval`）。
   - Pi 把该消息追加进对话记录、以 user 角色发给模型（`convertToLlm`），不改系统提示词，前缀缓存不受影响；它随会话存进断点存档（`cell.ts` 保存全部 entries），卡片恢复与会话恢复自然带上。
   - 卡片恢复时 prompt 是 interaction JSON，但对 Pi 仍是 `messageType="user"`；由后端按同一任务的断点交互状态判断，不下发检索结果，扩展无内容即不注入。之后的普通新一轮照常检索。
   - 已注入的内容随会话保留：管理员停用、改范围或删除条目只影响之后的检索，不撤回已在会话里的内容（与已生成的回答一致）。挂载目录每个执行段按当前可见条目重建，卡片恢复也重建；已删除的知识路径会读不到，模型按普通读失败处理。
   - 内容按长度区分：正文 ≤500 字的命中条目给全文，更长的只给“标题（适用条件）→ 路径”；全文合计 ≤2000 字，总条数 ≤20；无命中不注入。
   - 不用 `context` 钩子：它每次 LLM 调用临时改消息、不落盘，下一轮前文变化同样破坏缓存。
5. **呈现与按需读取（Pi 自带 grep/read）**：沙箱只读挂载 `/opt/skills/_knowledge/`，文件名沿用现有的 `sha256(experience_id).md`（id 是 `exp_<hex>` 字符串），由写文件、注入消息、预览共用一个路径函数；文件首部写标题与适用条件，仅含本助手可见的生效知识；写入前建目录。系统提示词（固定文本，不随问题变）说明：先看注入的相关知识，不足时 grep 该目录，长文档先 grep 定位再用 read 的 offset/limit 分段读。
6. **预览**：智能体页预览可输入问题，显示会命中的知识及注入消息；不输入问题时显示条目列表（只返回元数据，不返回正文）。预览与运行共用资格判断（功能开关、排除每日总结助手）与最终的限额格式化函数；只输入单个问题的预览标注“单轮模拟（运行时还会结合最近对话）”。注入文件准备移到 `asyncio.to_thread`。

### 扩展点与取舍

- 检索逻辑只在后端（可访问数据库与用户身份，预览与运行一致）；Pi 侧只负责按官方钩子注入，不含业务逻辑。
- 后续若需“运行中按新发现再检索”，加一个 `search_knowledge` 工具，经 Pi→后端请求帧调用同一函数。
- 个人记忆（按用户沉淀）不在本设计范围，需要时另立主题。
- 换向量检索时只改 `search_knowledge`。ngram 对同义改写召回有限；知识文档多（数十篇、每篇上万字）时需切片与语义检索，记入 TODO。
