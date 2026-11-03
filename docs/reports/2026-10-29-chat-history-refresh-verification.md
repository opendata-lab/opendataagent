# 初始化历史加载覆盖实时消息修复

日期：2026-10-29。来源：OpenDataWorks 门户现有数据真实问数补测。

门户实际问数时，初始话题列表请求迟到后重新加载消息，曾把正在运行的用户/助手消息替换为空列表，完成后又从历史恢复。`useNl2SqlChat.js` 现使用已有运行版本和当前话题校验历史请求，忙碌期间不覆盖消息；过期历史的成功与失败响应都忽略。API、任务协议、模型和运行时不变。

## 验证

在仓库根目录成功 `nvm use`（Node 22.22.0）后执行：

```sh
npm --prefix dataagent-frontend test -- src/views/intelligence/__tests__/useNl2SqlChat.spec.js src/widget/__tests__/WidgetChat.spec.js
npm --prefix dataagent-frontend run build:widget
```

2 个文件 47 项通过；widget 构建通过（29.62s）。新增回归覆盖初始化列表迟到、历史成功/失败迟到、旧话题响应在新会话后返回。

真实门户前端 3000 / DataAgent 8900 / widget 3001 / MCP 8801，MySQL 127.0.0.1:3306 的 dataagent 会话库，Redis 127.0.0.1:6379；Python 使用 dataagent-backend/.venv-py313，运行引擎 pi_agent_core，复用既有 DeepSeek deepseek-v4-pro 凭据。

修复前的真实样例库聚合请求完整执行并持久化，结果 12 单、668.90 元与只读 SQL 一致。修复后从门户提交“你好，请直接回复 smoke-ok。”，用户消息保持显示，最终回答正常呈现；任务 task_c32d4243fdb24b768e606e3f 为 finished，最终用户/助手消息持久化。测试话题清理，必要本地服务和助手配置按用户要求保留供继续使用。

未改本仓库已有 Docker/发布文件；本次没有修改后端或生产子容器拓扑，未重新验收该拓扑。无新增延期事项。

## 2026-11-03 提交前复核

再次执行相同的两组前端测试，共 49 项通过（历史引擎 18 项、widget 31 项；widget 已另行新增问题卡片用例）。完整前端 build 与 build:widget 均通过。运行时与 API 未改动；本轮未为该历史竞态额外引入数据库或模型测试。此前真实链路结果保留如上。
