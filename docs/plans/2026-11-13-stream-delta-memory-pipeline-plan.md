# 流式打字增量（content.delta）Redis Stream 管道化实施计划

日期：2026-11-13
状态：待评审（RFC）
关联设计：[2026-11-13-stream-delta-memory-pipeline-design.md](../design/2026-11-13-stream-delta-memory-pipeline-design.md)

---

## 1. 任务拆解与实施步骤

### Task 1: 封装 Redis Stream 流管道适配器（`TaskStreamRedisAdapter`）
- **文件路径**：`dataagent-backend/core/task_stream.py`
- **使用成熟库**：`redis.asyncio`（环境现有 `redis>=5.2.0`），基于原生 Redis Streams 协议：
  - `publish_event(task_id, record)`：调用 `xadd(stream_key, fields, maxlen=2000, approximate=True)`。
  - `read_stream(task_id, last_id="0-0", block_ms=10000)`：调用 `xread({stream_key: last_id}, count=100, block=block_ms)`。
  - `set_task_expiration(task_id, ttl=300)`：调用 `expire(stream_key, 300)` 实现自动回收。
- **降级保护**：当 Redis 未配置或不可用时，优雅回退到当前数据库逻辑。

### Task 2: 改造事件写入端 `PiEventWriter`
- **文件路径**：`dataagent-backend/core/pi_event_writer.py`
- **核心逻辑**：
  - 若 `event_type == 'content.delta'`：
    - 跳过 `store.append_sdk_record(...)`（不落 MySQL）；
    - 直推 Redis Stream；
  - 若为持久业务事件（`content.completed`、`tool.*`、`run.*` 等）：
    - 正常落库 `store.append_sdk_record(...)`；
    - 同步推入 Redis Stream 供前端即时呈现；
  - 任务进入终态时，设置 Redis Key 的 TTL（300秒）。

### Task 3: 改造 SSE 端点 `_stream_sdk_events`
- **文件路径**：`dataagent-backend/api/routes.py`
- **核心逻辑**：
  - 检查 Redis 中是否存在活跃任务流：
    - 若存在：使用 `xread` 毫秒级消费流事件，替代原有的 `while True + sleep(1)` 轮询；
    - 若 Redis 不可用或历史流已过期（已完成历史任务）：保留原有的数据库查询作为安全兜底。

### Task 4: 收敛 `topic_task_store.py` 中的批量删除
- **文件路径**：`dataagent-backend/core/topic_task_store.py`
- **核心逻辑**：
  - 由于不再写入任何 `content.delta` 到 MySQL，简化 `_delete_task_content_deltas`，移除多余的清理日志与连接。

---

## 2. 影响文件一览

| 模块 | 文件路径 | 变更类型 | 说明 |
| :--- | :--- | :--- | :--- |
| **Backend** | `dataagent-backend/core/task_stream.py` | 新增 | 基于 `redis.asyncio` 的 Redis Stream 管道适配器 |
| **Backend** | `dataagent-backend/core/pi_event_writer.py` | 修改 | `content.delta` 旁路分流，不入库 |
| **Backend** | `dataagent-backend/api/routes.py` | 修改 | SSE 端点接入 Redis Stream 实时推送 |
| **Backend** | `dataagent-backend/core/topic_task_store.py` | 修改 | 收敛 `content.delta` 删除链路 |
| **Tests** | `dataagent-backend/tests/test_task_stream_redis.py` | 新增 | Redis Stream 写入、读取、重连回放单元测试 |
| **Tests** | `dataagent-backend/tests/test_pi_event_writer.py` | 修改 | 验证分流与不入库断言 |

---

## 3. 验证与回归计划

1. **单元测试与契约测试**：
   - 验证 `content.delta` 事件 100% 旁路，数据库写入记录计数为 0；
   - 验证 Redis Stream 在断线重连（`after_id`）下能正确回放；
   - 运行前端现有契约测试：`deltaReplayEquivalence.spec.js`，保证前端渲染结果与终态文本 100% 等价。
2. **本地环境验证**：
   - 启动本地后端与 Redis；
   - 发起真实问答，观察打字流平滑度与延迟；
   - 在打字过程中按 F5 刷新页面，验证瞬间恢复与继续推流；
   - 任务完成后查询 `SELECT count(*) FROM da_agent_sdk_record WHERE event_type = 'content.delta'` 验证为 0。

---

## 4. 回滚方案（Backout）

- 配置开关：`DATAAGENT_STREAM_REDIS_PIPELINE_ENABLED`（默认 true）。
- 若出现任何 Redis 网络波动或异常，配置置为 `false`，系统自动切回原有数据库写入和兜底轮询，业务零感知。
