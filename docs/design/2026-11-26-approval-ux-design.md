# 写操作审批体验设计

日期：2026-11-26
计划：[implementation plan](../plans/2026-11-26-approval-ux-plan.md)

## 当前状态

- 智能体调用写操作工具（MCP 工具未标 `readOnlyHint`，或标了 `destructiveHint`）时，Pi 在 `beforeToolCall` 中拦截（`dataagent-runtime-pi/src/kernel/cell.ts` `needsApproval`），返回 “Not executed: waiting for approval” 并暂停执行段；后端写入 `permission_request` 卡片（`core/task_executor.py`），用户在卡片上点“允许 / 拒绝”（`POST /api/v1/nl2sql/tasks/{task_id}/permission-decision`，请求体已支持可选 `note`），允许后新执行段用新的调用 ID 执行该工具。
- 会话级权限模式已有：`default` / `acceptEdits`（非破坏性写操作不再询问）/ `bypassPermissions` / `plan`，由输入框的权限选择器设置（`Composer.vue`）。
- 卡片：标题“请确认写操作”，“工具：中文名（真实名）”（#80），可展开参数，按钮“允许 / 拒绝”。

## 问题（2026-11-26 用户与本地冒烟发现）

1. 等待审批的那次调用显示红色“执行失败”；允许后又新增一行“已执行”。同一个操作出现两行，第一行像出错。
2. 卡片标题“请确认写操作”不说明要做什么。
3. 没有“本会话内不再询问”：连续多个写操作（建表 → 建任务 → 发布）需要逐个点。
4. 拒绝时无法补充原因，智能体不知道该怎么改。

## 参考（同类产品）

- ChatGPT 开发者模式：按 `readOnlyHint` 判断写操作，确认前可展开 JSON 参数，可勾选“本会话内记住”，新会话重新询问。
- Claude.ai 连接器：每个工具可设“总是允许 / 每次询问 / 禁止”；提示以工具为主语（“X requests permission”），选项“允许一次 / 总是允许 / 拒绝”。
- Claude Code：一个工具调用一行，等待时在该行下方询问 “Do you want to proceed?”，选项“是 / 是且不再询问此类 / 否并说明怎么改”；允许后同一行显示执行结果。

## 方案

### 1. 一个调用一行

- Pi 拦截待审批调用时，`tool.completed` 带 `awaiting_approval: true`，`is_error: false`（不再算失败）。
- 允许后恢复执行时，`tool.started` / `tool.completed` 使用**原调用 ID**（暂停时记入 interaction payload 的 `tool_call_id`），而不是带运行前缀的 `request_id`。
- 后端历史投影与前端事件处理（`agentEvents/reducer.js`、`streamParser.js`）：同一调用 ID 再次开始时更新原行，不新增。
- 行状态：等待确认（黄色，与卡片同色）→ 正在执行 → 已执行 / 执行失败；拒绝为“已拒绝”（灰色），超时或取消为“未执行”（灰色）。行文字不变（工具中文名或名称）。
- 旧记录没有 `awaiting_approval` 标记，按原样显示，不加兼容分支。

### 2. 卡片

- 标题以操作为主语：“**{工具中文名或名称}** 需要你确认”；下一行“工具：中文名（真实名）”（#80 的规则不变）；参数可展开。
- 按钮：**允许**、**本会话内允许此工具**、**拒绝**。
- 点“拒绝”后出现可选输入框“告诉智能体换个做法（可选）”，确认后提交 `decision=deny` 与 `note`；不填也可直接拒绝。`note` 已随 interaction 结果交给模型（`user_response`），无需改协议。

### 3. 本会话内允许此工具

- 范围：当前会话（topic）。键为 `server_identity + 工具名`（MCP 服务配置变化即失效）。新会话重新询问。
- `destructiveHint: true` 的工具不提供该选项，始终逐次确认（与 `acceptEdits` 对破坏性操作仍询问一致）。
- 存储：会话表新增 JSON 列 `approved_tools_json`（工具键列表）。审批接口 `permission-decision` 增加可选字段 `remember: "session"`，仅在 `decision=allow` 且工具非破坏性时生效；后端写入该列。
- 运行：执行器把列表随 cell init 下发（`approved_tools`）；Pi 的 `needsApproval` 对列表内工具返回 false。
- 撤销：不做单独入口，开新会话即重置（2026-11-27 用户确认不需要）。

### 适用范围

主应用、Widget、SDK 共用 `PermissionCard.vue`、`ToolOutput.vue` 与事件处理，三者同时生效；OntoFoundry 等内嵌方在下一次 SDK 版本更新后生效。

## 取舍

- 不做全局“总是允许”（跨会话）：管理员登记的 MCP 写工具影响真实数据，跨会话授权需要权限管理界面，超出本期。
- 记住的粒度是工具而不是参数：与 Claude.ai 一致；参数级记忆（如“只允许这张表”）复杂且难解释。
- 不改权限模式与 `acceptEdits` 语义。
