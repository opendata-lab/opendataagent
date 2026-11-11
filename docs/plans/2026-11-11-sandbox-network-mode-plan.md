# 沙箱网络模式配置支持 (bridge 与 host) 计划

日期：2026-11-11。关联：[设计](../design/2026-11-11-sandbox-network-mode-design.md)。

## 任务

1. **后端配置与校验** (`dataagent-backend/config.py`)
   - 添加 `dataagent_sandbox_network_mode: Literal["bridge", "host"] = "bridge"`。
   - 添加 `@field_validator("dataagent_sandbox_network_mode", mode="before")`，处理大小写转换并拒绝非法值。
   - 更新 `main.py` 启动日志，打印 `network_mode`。
2. **后端沙箱配置传递** (`dataagent-backend/core/sandbox_config.py`)
   - 在 `build_sandbox_config` 输出字典中携带 `"networkMode": getattr(cfg, "dataagent_sandbox_network_mode", "bridge")`。
3. **Pi 运行时沙箱支持** (`dataagent-runtime-pi/src/sandbox/docker-sandbox-manager.ts`)
   - `SandboxConfig` 声明 `networkMode?: "bridge" | "host"`。
   - 构造函数校验 `networkMode`。
   - `start()` 中根据 `networkEnabled` 与 `networkMode` 组合解析 `--network=host`、`--network=bridge` 或 `--network=none`。
   - 诊断日志记录 `networkMode`。
4. **编译与 Pi 测试更新**
   - 重新执行 `npm run build`。
   - 在 `dataagent-runtime-pi/test/docker_sandbox.test.ts` 中补充 `networkMode`（含 host、bridge、非法值、断网优先覆盖）单测。
5. **后端测试更新** (`dataagent-backend/tests/test_sandbox_config.py`)
   - 补充 `DATAAGENT_SANDBOX_NETWORK_MODE` 的解析、默认值、非法值抛错测试。
   - 验证 `build_sandbox_config` 输出中携带 `networkMode`。
6. **部署编排与文档**
   - 更新 `deploy/docker-compose.yml`、`deploy/.env.example` 与 `deploy/README.md`。
   - 更新 `docs/TODO.md`，记录该功能实现与 Issue #26 跟踪。

## 验证

- **单元测试**：
  - `dataagent-backend/.venv-py313/bin/python -m pytest dataagent-backend/tests/test_sandbox_config.py`
  - `. "$HOME/.nvm/nvm.sh" && nvm use && cd dataagent-runtime-pi && npm run build && node --test dist/test/docker_sandbox.test.js`
- **Compose 配置检查**：核对 `deploy/docker-compose.yml` 环境变量映射。
