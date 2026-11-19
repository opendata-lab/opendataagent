# 经验库与用户反馈计划

日期：2026-11-19
设计：[2026-11-19-experience-library-design.md](../design/2026-11-19-experience-library-design.md)
涉及：后端、管理端前端、Pi 运行时（总结会话）。OntoFoundry 在口径回流一步才涉及，另写设计。

实施前需用户确认设计第 7 节。

## 第一步：评价可见

1. Alembic 迁移：`da_agent_message.feedback_at`；`update_message_feedback` 写入时间。
2. `GET /admin/agents/{agent_id}/feedback`（分页、`dislike` 过滤、近 30 天计数），管理员权限。
3. 管理端助手详情「用户反馈」页签，链接到会话。
4. 测试：权限、过滤、计数、取消评价后的计数。

## 第二步：经验库

1. 迁移：`da_agent_experience`（`personal`/`assistant`/`platform` 三层、`owner_user_id`、`partition_key`）、`da_agent_experience_job`。
2. `core/experience_store.py`：读写、去重、版本、上限归档、证据撤销。
3. `core/experience_digest.py`：按助手和分区收集前一天任务与评价、截断与筛选。
4. 运行时：无工具总结会话入口（复用 Cell 的模型解析，不加载技能和 MCP），输出 JSON。
5. 调度：`_schedule_loop` 每日登记作业；作业领取、重试、额度。
6. 注入：`_build_system_prompt` 增加「历史经验参考」，条目随任务落库；全局开关；评测任务关闭。
7. 管理端「经验」页签与接口：列表、详情、启停、提升、编辑。
8. 测试：校验与敏感内容拒绝、去重合并、点踩撤销、三层隔离（个人层不外泄、Widget 站点不进入助手层）、助手层需 2 个不同用户证据、注入上限与层级优先、作业幂等。

## 第三步：口径回流

另写设计：DataAgent 以服务令牌调用 OntoFoundry `submit_feedback`，工作空间对应关系与令牌配置。

## 第四步：评估

评测框架中开关对比运行与跨分区泄漏用例；记录结果后决定是否扩大范围。

## 上线与回退

- 每一步独立发布；第二步默认关闭注入，评测通过后再开启。
- 回退：关闭全局开关即停止注入；作业可停用；表保留不影响业务。
