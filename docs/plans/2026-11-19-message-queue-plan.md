# 运行中发送消息的排队与立即发送计划

日期：2026-11-19
设计：[2026-11-19-message-queue-design.md](../design/2026-11-19-message-queue-design.md)
涉及：前端对话组件（`dataagent-frontend/packages/agent-conversation`）。OntoFoundry 随后更新打包的 SDK。

## 任务

1. `src/transport/errors.js`：`ConversationError.status`；`types/index.d.ts` 同步。
2. `src/core/useConversation.js`：`send` 的 `quietBusy` 选项。
3. `src/ui/QueuePanel.vue`（新增）：队列列表与「立即」「编辑」「删除」。
4. `src/ui/ConversationRoot.vue`：运行中发送转入队列、终态时发送或暂停、立即发送、编辑、删除、409 重试、切换会话清空。
5. `src/ui/Composer.vue`：运行中有内容时显示「加入队列」按钮。
6. `README.md`、`docs/bff-protocol.md`。

## 验证

- `src/__tests__/messageQueue.spec.js`：运行中排队、成功后按顺序发送；「立即」先取消再发送所选消息；失败后暂停并可立即发送；编辑与删除；409 重试且不报错。
- 全量前端 vitest、SDK 类型检查（React 18/19）。

## 上线与回退

- 随前端镜像与 SDK 版本发布；OntoFoundry 更新打包 SDK 后生效。
- 回退：恢复旧版组件即可，无数据变化。
