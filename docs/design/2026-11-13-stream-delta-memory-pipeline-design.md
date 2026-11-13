# 流式打字增量（content.delta）Redis Stream 管道化设计

日期：2026-11-13
状态：待评审（RFC）
关联计划：[2026-11-13-stream-delta-memory-pipeline-plan.md](../plans/2026-11-13-stream-delta-memory-pipeline-plan.md)

---

## 1. 现状与核心痛点

### 1.1 现状调用链路
当前系统（`dataagent-backend` + `dataagent-runtime-pi`）中，大模型生成的实时流事件处理流程如下：

```text
[Pi Cell (Node.js)] 
   │  stdio (NDJSON 帧)
   ▼
[core/pi_runtime.py]
   │  writer.ingest(event)
   ▼
[core/pi_event_writer.py]
   │  无差别调用 store.append_sdk_record(...)
   ▼
[MySQL: da_agent_sdk_record] ◄── [api/routes.py: _stream_sdk_events]
   - 每吐几个 token 产生一次 INSERT          - 前端 SSE 每隔 1 秒 SELECT 轮询一次
   - 一次回答写入 100 ~ 500 行碎记录        - 取出后转成 SSE 推给前端
   │
[任务正常完成 (task_status = 'finished')]
   ▼
[core/topic_task_store.py: _delete_task_content_deltas]
   - 执行 DELETE FROM da_agent_sdk_record WHERE task_id = %s AND event_type = 'content.delta'
   - 打印日志：task.store.content_deltas_deleted task_id=... deleted_rows=...
```

### 1.2 核心痛点
1. **写放大与 I/O 严重浪费（典型 Anti-Pattern）**：
   - 一次常规问答产生 100~500 次 MySQL `INSERT`，产生大量 redo log、undo log、binlog 及索引更新。
   - 任务一结束又立即执行整批 `DELETE`，再次产生事务日志并造成 InnoDB 表空间页碎片（Page Fragmentation），增加 Purge 线程压力。
2. **伪实时流（SSE 轮询延迟 1000ms）**：
   - SSE 端点不是实时事件推送，而是靠后台 `asyncio.sleep(1)` 每秒轮询一次数据库，导致前端打字效果有一秒一顿的卡顿感。
3. **并发扩展瓶颈**：
   - 并发任务上升时，高频小事务和瞬时大量 `DELETE` 挤占 MySQL 连接池和 IOPS，成为系统扩展的最大瓶颈。
4. **历史数据无需 delta**：
   - 单元测试（`deltaReplayEquivalence.spec.js`）及现有数据清理已证实：终态消息重构完全依赖 `content.completed`（内含完整 Markdown 文本），`content.delta` 在任务完成后毫无持久化价值。

---

## 2. 设计原则：采用成熟基础设施，不重复造轮子

针对“大模型流式打字增量”这一**高频、短命、即时消费、阅后即焚**的场景：

> **不自研手写脆弱的内存队列轮子（避免手写 dict + asyncio.Queue 带来的内存泄漏、单机跨进程无法共享等问题）。**
> **复用技术栈中现成、成熟的工业级基础设施：Redis 7.2 + `redis.asyncio` (Redis Streams)。**

### 为什么选择 Redis Streams？
1. **原生时序消息流**：`XADD` 天然生成递增的毫秒时序 ID，完全契约对应前端的 `after_id` 断线重连机制。
2. **定长防爆内存**：自带 `MAXLEN ~ 2000` 选项，超过容量自动剔除旧数据，无需一行代码管理缓冲区溢出。
3. **毫秒级阻塞监听**：`XREAD(..., block=...)` 是原生的发布/拉取机制，客户端连接即时拿到推送，延迟从 1000ms 降至 < 2ms。
4. **自动过期释放**：任务结束后直接 `EXPIRE task:stream:{task_id} 300`，Redis 原生保证 5 分钟后自动销毁，**零代码清理，零垃圾残留**。
5. **天然支持集群水平扩展**：未来后端部署多个 Pod 时，用户不管连接到哪个 Pod，都能从同一 Redis Stream 消费数据，无需黏性会话。

---

## 3. 详细架构设计

### 3.1 总体拓扑

```text
[Pi Cell (Node.js)] 
   │  stdio
   ▼
[core/pi_event_writer.py]
   │
   ├── [content.delta] ──> 【Redis Stream】(key: da:task:stream:{task_id})
   │                         - XADD 自动分配递增序列
   │                         - 不写 MySQL！彻底消灭 INSERT / DELETE
   │                         - 任务结束设置 EXPIRE 300s 自动回收
   │
   └── [业务持久事件]  ──> 【MySQL (da_agent_sdk_record)】
        (run.*, turn.*, tool.*, content.completed, 卡片事件)
        - 同时 XADD 广播到 Redis Stream 保证实时推送
        - 持久化供未来长期历史加载与图表展示
```

### 3.2 消费端与页面刷新处理（`api/routes.py`）

前端发起 `GET /api/v1/nl2sql/tasks/{task_id}/sdk-events/stream?after_id={seq}`：

1. **优先消费 Redis Stream**：
   - 使用成熟的 `redis.asyncio` 客户端：
     ```python
     streams = await redis.xread({stream_key: after_id_or_0}, count=100, block=ping_interval_ms)
     ```
   - 如果用户中途按 **F5 刷新页面**，前端重新连入（带 `after_id=0`），Redis Stream 会瞬间将该 key 中累积的所有事件一次性倒给前端，前端毫秒级复原当前已打出的字，随后继续阻塞监听后续新字。
2. **任务结束与自动消亡**：
   - 任务进入终态时，写入终态事件后设置 `EXPIRE da:task:stream:{task_id} 300`。
   - SSE 连接读到终态事件后正常结束关闭。
3. **已完成历史任务兜底（几天前或几小时前的会话）**：
   - 若 Redis Stream Key 已过期（或任务早已完成），SSE 直接从 MySQL `list_sdk_records` 快速返回持久事件。因为 `content.completed` 包含完整文本，历史重放完全一致。

---

## 4. 收益对比

| 评估维度 | 当前现状 (MySQL 存取+删除) | 优化方案 (Redis Stream) |
| :--- | :--- | :--- |
| **MySQL 写入量** | 一次问答 100~500 次写入 | **仅 3~5 次（只存宏观业务事件）** |
| **MySQL 空间与碎片** | 产生大量 InnoDB 碎片，需 DELETE | **0 临时数据，0 碎片，无清理负担** |
| **推流延迟** | 1000ms（每秒轮询一次数据库） | **< 2ms（Redis 内存级阻塞推送）** |
| **打字丝滑度** | 一秒一顿 | **平滑流畅逐字展现** |
| **中途按 F5 刷新** | 查 MySQL 慢速回放 | **Redis Stream 瞬时毫秒级恢复** |
| **集群扩展能力** | 数据库高并发 IO 瓶颈 | **天然支持多节点并发分发** |

---

## 5. 兼容性与风险评估
- **无表结构变更**：不修改 MySQL 任何表定义，无需 Alembic 迁移。
- **前端 100% 兼容**：输出的 SSE 格式（`id: <seq_id>`, `event: agent_event`, `data: <json>`）保持不变。
- **依赖库评估**：直接使用已有的 `redis>=5.2.0`（`redis.asyncio`），不引入任何不可控的第三方轮子。
