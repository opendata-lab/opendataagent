# 面向通用工具与外部调用的安全门控实现计划

日期：2026-11-13。关联：[设计](../design/2026-11-13-generic-tool-governance-architecture-design.md)。

---

## 任务拆解与涉及模块

### Phase 1: 控制面凭据管理与出站代理底座 (Egress Gateway)
1. **控制面凭据安全存储与解析** (`dataagent-backend/core/credentials/`)
   - 实现统一的凭据金库接口 `CredentialVault`，按用户/会话托管目标域名的外部凭据（GitHub Token, AWS Keys, OpenAPI Bearer Token）。
   - 凭据仅在控制面内存中保存与使用，严禁写入沙箱环境变量或挂载文件。
2. **轻量出站认证代理服务** (`dataagent-backend/core/egress_gateway.py` 或 sidecar)
   - 启动本地受控 HTTP/HTTPS 代理；
   - 匹配出站请求的目标域名：自动注入对应的 `Authorization` Header，实现沙箱无感的静态凭据脱敏；
   - 请求分类：将请求划分为只读（GET/HEAD）与写/破坏性请求（POST/PUT/DELETE/PATCH）。

### Phase 2: 沙箱通用环境与 CLI 统一认证代理 (`dataagent-auth-broker`)
3. **沙箱基础镜像通用 CLI 预装** (`dataagent-backend/Dockerfile.sandbox`)
   - 补充安装常用基础 CLI：`git`、`curl`；
   - 注入轻量通信脚本 `/usr/local/bin/dataagent-auth-broker`。
4. **CLI 工具认证通用映射配置** (`Dockerfile.sandbox` 或容器启动初始化)
   - 配置 Git：`git config --system core.askpass /usr/local/bin/dataagent-auth-broker`；
   - 配置通用 HTTP 代理环境变量：`HTTP_PROXY` 与 `HTTPS_PROXY` 指向出站代理网关。

### Phase 3: 外部写操作拦截与任务挂起/审批卡片联动
5. **写操作拦截与任务挂起 (Pi Runtime & Backend)**
   - 当 Egress Gateway 检测到外部写请求，或 `dataagent-auth-broker` 收到写操作（如 `git push`）凭据申请时：
   - 触发 Pi 运行时 `park("permission", ...)` 挂起当前任务段，持久化 Session 状态至 MySQL。
6. **前端通用操作审批卡片** (`dataagent-frontend/src/views/intelligence/OperationConfirmationCard.vue`)
   - 适配通用外部写操作的卡片展示：
     - **目标平台 / 服务**：如 `api.github.com`、`aws-s3://my-bucket`、`k8s-cluster`；
     - **操作行为**：如 `PUSH`、`DEPLOY`、`DELETE`、`MUTATE_API`；
     - **变更摘要与 Payload**：显示提交 Commit 摘要、请求 JSON 差异或影响范围；
   - 用户点击“**允许**”：控制面下发一次性短效 Token 或放行该出站写调用；
   - 用户点击“**拒绝**”：返回 403 阻断调用，命令以非零退出码终止，沙箱不发生任何外部变更。

---

## 阶段验证计划

1. **凭据脱敏注入冒烟测试**：
   - 在沙箱内执行 `curl https://api.github.com/user`；
   - 验证沙箱内未配置任何 Token 环境变量的前提下，出站网关成功注入 Token 并正确返回用户信息。
2. **只读操作放行测试**：
   - 在沙箱内执行依赖安装 `pip install ...` 或 `curl -O ...`，验证只读流量顺畅通行且无需人工确认。
3. **通用写操作卡片挂起与恢复测试**：
   - 模拟执行 `git push` 或通过 `curl -X POST` 触发写请求；
   - 验证任务精准挂起为 `parked` 状态，前端弹出 `OperationConfirmationCard`；
   - 验证用户批准后推送成功，用户拒绝后安全截断。

---

## 回滚策略（Backout）

- 出站代理采用环境变量开关 `DATAAGENT_EGRESS_GATEWAY_ENABLED=true/false` 治理；
- 若出现网络兼容性问题，关闭开关后沙箱立即回退为既有的默认物理断网模式（`--network=none`）或直接网络模式，核心任务不受影响。
