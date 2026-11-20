# 知识、记忆与用户反馈设计

日期：2026-11-19（按已批准的 knowledge-memory prototype 更新）
计划：[implementation plan](../plans/2026-11-19-experience-library-plan.md)

## 当前状态与问题

现有经验库把人工知识和每日总结混在一起，仅有正文、助手/平台范围和候选状态；全文注入上限为 2000 字。新界面将人工维护的「知识」与自动提炼的「记忆」分开，审核时可以完整编辑。

## 产品与接口

- 主导航新增管理员专属 `/feedback`、`/knowledge`、`/memories`，记忆显示待审核总数。不设组标题。设置移除运营、评测菜单，保留评测路由，移除旧经验库页面与路由。
- 复用 `da_agent_experience` 与管理员 `/api/v1/nl2sql-admin/experiences` API：新增 `kind`（knowledge/memory）、`title`（40 字）、`when_text`（120 字）、`inject_mode`（always/on_demand）；正文仍存 `content`，API 为 `body`（500 字）。范围为 global/agent，无用户级。
- 新迁移将旧行全部转 memory，candidate → pending、retired → disabled，platform → global、assistant → agent；旧正文保留，以前 40 字补标题，适用条件留空供审核补充，默认按需读取。
- POST 仅能新增 active 知识；记忆仅每日总结收取器创建，初始 pending、原助手范围、on_demand。PATCH 可编辑或通过/忽略/停用/恢复；知识不能 pending，已审核记忆不能退回 pending。DELETE 为永久删除。
- 列表支持 kind、status、scope、agent_id、关键词及分页。预览 API `/experiences/injection-preview?agent_id=...` 返回同一算法计算的生效条目、待审核数、预算及实际提示词。
- 管理 API 继续复用 router 的 require_admin。页面和智能体编辑页卡片只对管理员开放；卡片只读，点击打开详情抽屉，通过管理链接跳至独立页面。不扩展编辑角色权限。
- 列表用 Element Plus，标题与灰色适用条件组成一行记录，安静的范围、注入方式、时间列，编辑 + 更多操作；详情与编辑用抽屉。记忆待审核采用左队列、右编辑表单，来源总结与主站会话可跳转，Widget 来源只展示问题和回答。

## 注入与运行时

- 保留现有部署开关（默认关闭）与每日总结调度。内置总结助手不消费这些条目；技能输出 title、when、body、source_task_ids。
- active 知识和记忆共用 1000 字 always 额度；全局优先，各范围内按更新时间与 ID 降序。预算计入实际条目全文格式（标题、适用条件、正文及分隔符），超额条目跳过并记录日志，后续短条目仍可入选。
- on_demand 按更新时间与 ID 降序最多 30 条，提示词仅列 `- 标题（适用条件）→ 路径`。全文写到本次执行段独享的工作区同级目录，复用 sandbox skills 只读 bind 挂载到 `/opt/skills/_knowledge_memory/`，不作为技能注册、不增加挂载类型。执行段结束后清理，避免跨任务覆盖与过期内容残留。
- 提示词明确条目不得覆盖当前请求、技能约定或权限规则。卡片预算和预览均使用运行时相同函数；开关关闭时明确标示不注入。

## 范围与取舍

涉及 Vue 管理界面、FastAPI、MySQL Alembic、DataAgent task executor、总结技能；不修改 Pi 协议或 Docker 挂载实现。不做个人记忆、语义检索、自动审批、编辑角色写权限。删除后条目不再存在；总结后续重写同样候选可能重新收取。
