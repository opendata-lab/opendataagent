# 写操作审批体验实施计划

设计：[design](../design/2026-11-26-approval-ux-design.md)。单个 PR。

## 改动

1. **Pi**（`dataagent-runtime-pi/src/kernel/cell.ts`、`event-normalizer.ts`、`protocol/frames.ts`）
   - 拦截待审批调用：记下调用 ID；该调用的 `tool.completed` 带 `awaiting_approval: true`、`is_error: false`；park payload 带 `tool_call_id`。
   - 恢复执行：`tool.started` / `tool.completed` 用 payload 中的原 `tool_call_id`。
   - cell init 新增 `approved_tools: string[]`（`server_identity:tool_name`）；`needsApproval` 对其中的非破坏性工具为 false。
   - 契约：`contracts/agent-events/v1` 增加 `awaiting_approval`；测试覆盖拦截、恢复同 ID、会话授权跳过审批、破坏性工具仍审批。
2. **后端**
   - Alembic：`da_agent_topic.approved_tools_json`（JSON，可空）。downgrade 删除该列。
   - `models/schemas.py` `PermissionDecisionRequest.remember: Optional[Literal["session"]]`；`api/routes.py` `_record_permission_decision`：`allow` + `remember=session` + 非破坏性工具时把工具键写入会话；破坏性工具忽略并照常允许本次。
   - `core/task_executor.py`：读取会话 `approved_tools_json` 下发；卡片数据增加 `destructive`（供前端决定是否显示“本会话内允许”）与 `tool_call_id`。
   - `core/topic_task_store.py` `_project_sdk_records`：同一 `tool_id` 再次开始时更新原块；`awaiting_approval` 写入块。
   - 契约 `contracts/sdk-block-projection/cases.json` 增加“等待 → 允许 → 执行”“等待 → 拒绝”两例，前后端契约测试同步。
3. **前端（SDK 包，主应用与 Widget 共用）**
   - `core/agentEvents/reducer.js`、`core/streamParser.js`：按调用 ID 合并；`awaiting_approval` 状态；拒绝/超时状态。
   - `ui/ToolOutput.vue`：状态文案“等待确认 / 正在执行 / 已执行 / 执行失败 / 已拒绝 / 未执行”与对应颜色。
   - `ui/PermissionCard.vue`：标题“{名} 需要你确认”；按钮“允许 / 本会话内允许此工具（非破坏性才显示）/ 拒绝”；拒绝后可选说明输入框；提交 `remember` 与 `note`。
   - 决策请求的调用处（主应用 `useNl2SqlChat`/SDK `useConversation`/Widget）透传 `remember`、`note`。
   - 类型声明 `types/index.d.ts` 同步。
4. **文档**：`docs/TODO.md` 中“等待审批的工具调用显示为执行失败”移入已完成并链接本 PR。

## 验证

- Pi：`npm run build && npm test`（含新增用例）。
- 后端：相关 pytest（路由、执行器、投影契约、迁移 up/down，MySQL 用自建自删的临时库 `approval_test_*`）。
- 前端：`npx vitest run --cache=false`、`npm run build`、`npm run build:widget`、`npm run test:sdk-types`。
- 本地端到端（schema `dataagent_qa`，真实模型，Claude 评审时执行）：主应用与 Widget 各一次：等待显示一行“等待确认” → 允许后同一行“已执行”；“本会话内允许”后同一工具再次调用不再询问；拒绝并填写说明后智能体据此调整；刷新后历史一致。

## 上线与回退

- 迁移与新版本同次发布；新列可空，回退执行 downgrade 删除该列（仅丢失会话内授权，下次重新询问）。
- 外部内嵌方（OntoFoundry）随下一次 SDK 版本更新获得新卡片。
