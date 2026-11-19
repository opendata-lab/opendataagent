# 经验库与用户反馈计划

日期：2026-11-19
设计：[2026-11-19-experience-library-design.md](../design/2026-11-19-experience-library-design.md)
涉及：后端、管理端前端、Pi 运行时（总结会话）。OntoFoundry 无改动（口径回流暂不做）。

实施前需用户确认设计第 7 节。

## 第一步：评价可见

1. Alembic 迁移：`da_agent_message.feedback_at`；`update_message_feedback` 在任何变更（含取消）时写入时间。
2. `GET /admin/feedback`（分页，按助手、评价、时间筛选，计数），管理员权限。
3. 管理端独立菜单「用户反馈」，链接到会话。
4. 测试：权限、过滤、计数、取消评价。

## 第二步：经验库

1. 迁移：一张表 `da_agent_experience`。
2. `core/experience_store.py`：读写、去重、上限归档、评价变化时重算证据。
3. `core/experience_digest.py`：按 4.2 判定可信身份与分区，排除调试预览；收集前一天任务与评价，剥离回答中的表格、图表数据与数字，提取失败工具首行错误；每人 5 个、每助手 30 个上限。
4. 总结调用：复用 `provider_runtime.request_model_text`，个人层按用户、助手层按分区分别调用；输出 JSON 校验，证据归属由代码核对。
5. 后台作业：独立协程，Redis 锁续期，按 `(agent_id, partition_key)` 记录进度并补跑最多 7 天。
6. 注入：`_build_system_prompt` 增加分层呈现的「历史经验参考」；选中条目写入 `checkpoint.state["injected_experiences"]` 并在恢复时复用；全局开关加入管理设置白名单。
7. 管理端「经验」页签与接口：列表、详情、启停、提升（选择分区）、编辑。
8. 测试：可信身份判定（浏览器 Widget、匿名、预览被排除）、个人层隔离、助手层 2 用户计数与评价撤销重算、敏感内容拒绝、单人上限、补跑与锁、注入上限与恢复复用。

## 暂不做

效果评估、口径回流 OntoFoundry。

## 上线与回退

- 每一步独立发布；第二步默认关闭注入，由管理员开启并通过「用户反馈」观察。
- 回退：关闭全局开关即停止注入；作业可停用；表保留不影响业务。
