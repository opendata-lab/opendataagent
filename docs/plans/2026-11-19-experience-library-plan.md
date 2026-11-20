# 经验库与用户反馈计划

日期：2026-11-19
设计：[2026-11-19-experience-library-design.md](../design/2026-11-19-experience-library-design.md)
涉及：后端、管理端前端、内置技能。OntoFoundry 与 Pi 运行时无改动。

## 第一步：用户反馈菜单与管理员查看会话

1. 迁移：`da_agent_message.feedback_at`；评价变更时写入。
2. `GET /admin/feedback`：筛选与计数；`GET /admin/topics/{topic_id}/messages`：只读。均需管理员。
3. 管理端菜单「用户反馈」，点击打开只读会话。
4. 测试：权限、筛选、计数、只读查看他人会话。

## 第二步：经验库

1. 迁移：`da_agent_experience`。
2. 内置助手「经验总结」与技能 `experience-digest`。
3. 会话记录文件生成：任务问题、中间过程、回答、评价，长度上限，排除预览。
4. 每日作业：按助手建会话、上传文件、发起任务；任务结束读取 `output/experience_candidates.json` 写入候选；同日不重复。
5. 管理接口与菜单「经验库」：审核（助手级、平台级、忽略）、编辑、停用、新增，链接总结会话。
6. 注入：`_build_system_prompt` 附加生效经验（2000 字上限）；全局开关。
7. 测试：记录文件内容与截断、预览排除、同日不重复、候选校验与入库、审核流转、注入与开关。

## 上线与回退

开关默认关闭；关闭即停止注入，表保留不影响业务。
