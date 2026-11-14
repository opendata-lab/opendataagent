# 历史 content.delta 行压缩设计

日期：2026-11-13
状态：待评审
关联计划：[2026-11-13-legacy-content-delta-compaction-plan.md](../plans/2026-11-13-legacy-content-delta-compaction-plan.md)
相关：[content.delta 内存直推设计](2026-11-13-content-delta-live-stream-design.md)（#39）

## 1. 现状

- #39 之后，流式增量不再落库；中断的块只存一行 `content.delta`，`data.text` 为全量文字。
- #39 之前，以 `error` / `suspended` 结束的任务保留了逐 token 的 `content.delta` 行（`finished` 任务的增量在完成时删除，存量由迁移 `20260911_000024` 清理）。
- 打开会话时，前端按消息的 `records` 回放重建（`packages/agent-conversation/src/core/message.js` `buildV2StateFromStoredRecords`），这些增量行会随消息一并下发并逐条回放。

本地实测（2026-11-13，库 `dataagent`）：

| 项 | 数值 |
| --- | --- |
| 含增量行的中断任务 | 7 个（`error` 4、`suspended` 3） |
| 增量行 | 约 61,000 行，约 4.7 MB，占全部记录的 88% |
| 单个会话 `topic_47941a2a…` | 消息接口 4.0 MB，其中 3.66 MB 是 19,471 行增量 |

## 2. 问题

存量中断任务的增量行让大会话打开慢：下载数 MB、逐条回放上万条记录。新数据不会再出现，只需一次性处理存量。

## 3. 方案

新增 Alembic 数据迁移，对**终态任务**（`finished` / `error` / `suspended`）按块压缩增量行。活跃任务（含等待卡片）不动。

- **块键**：`(turn_index, data.turn_id, data.content_id, data.kind)`。Pi 运行时在一轮内为每个内容块分配唯一的 `content_id`（`c-<contentIndex>`），该键唯一标识一个块。
- **已有 `content.completed` 的块**：删除该块全部增量行。`content.completed.text` 已是完整文字，所有读取方都用它覆盖块内容。
- **没有 `content.completed` 的块**（被打断）：保留该块第一行增量（位置不变，块的出现顺序不变），把它改写为 `data.text` = 按序累积的全文（遇到已有 `text` 的行按覆盖处理），删除其余增量行。这与 #39 之后新数据的格式一致。
- **跳过**：`content_id` 为空的行（读取方对空键不做块合并，压缩会改变渲染），保持原样。
- **执行方式**：逐任务处理，每个任务一个短事务（AUTOCOMMIT 块内分别提交），删除按 id 分批；可重复执行（第二次运行时已无可压缩的行）。
- **downgrade**：空操作。删除的增量行无法也无需恢复（内容已由 `content.completed` 或合并后的 `text` 行完整保留）。

## 4. 等价性

压缩前后，后端历史投影 `_project_sdk_records` 与前端 reducer 对同一任务得到相同的块文字：
- 有 `content.completed` 的块：两者都以 `completed.text` 覆盖，增量是否存在不影响结果；
- 被打断的块：两者对 `text` 都按覆盖处理（#39 引入），一行全文等于原先逐条拼接的结果。

## 5. 影响

- 无表结构变更、无 API 变更、无前端改动。
- 迁移在部署时随 `alembic upgrade head` 执行；本地 61,000 行量级为秒级。
- 会话分页加载（`docs/TODO.md`）仍有价值，但不是本次问题的主因，保留为后续事项。
