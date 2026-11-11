# 沙箱网络模式配置支持 (bridge 与 host) 设计

日期：2026-11-11。范围：`dataagent-backend`、`dataagent-runtime-pi`、`deploy/` 与相关文档及测试。

## 问题

在企业内网、VPN 或受限网络环境中，用户为技能访问数据库或安装特定依赖开启了沙箱网络（`DATAAGENT_SANDBOX_NETWORK_ENABLED=true`）。排查确认沙箱容器已按预期以 `--network=bridge` 启动，但容器内依然无法连通外部数据库 IP 或执行 `pip install`：
1. **Docker 默认 bridge 限制**：`--network=bridge` 依赖宿主机 iptables/NAT 转发与 `docker0` 虚拟网桥。在内网特定路由策略、VPN 客户端路由表劫持、容器间网段冲突（如 `172.17.0.0/16`）或关闭 ip forwarding 的宿主机上，bridge 流量无法路由至目标机。
2. **缺乏网络模式选择**：当前代码硬编码 `network = c.networkEnabled === true ? "bridge" : "none"`，用户无法切换到宿主机网络栈（`--network=host`）。而在该类部署下，宿主机本身网络、路由与 DNS 完全正常，使用 `host` 模式可直接绕过 bridge 的 NAT 与路由限制。

## 方案

抽取沙箱网络模式配置 `DATAAGENT_SANDBOX_NETWORK_MODE`，支持 `bridge`（默认）与 `host`。

### 1. 配置定义与解析 (`dataagent-backend/config.py`)

- 新增配置字段：
  ```python
  dataagent_sandbox_network_mode: Literal["bridge", "host"] = "bridge"
  ```
- 校验规则：
  - 仅接受 `"bridge"` 或 `"host"`（大小写不敏感，如 `"HOST"` -> `"host"`）。
  - 其他非法值抛出清晰的 ValueError 阻止启动，避免静默使用未知或不安全的网络模式。
  - 默认为 `"bridge"`，保证旧有行为完全向后兼容。

### 2. 运行时传参 (`dataagent-backend/core/sandbox_config.py`)

- `build_sandbox_config()` 返回的沙箱配置字典中增加 `"networkMode": getattr(cfg, "dataagent_sandbox_network_mode", "bridge")`。
- 控制面启动日志 (`main.py`) 同步打印 `network_mode`。

### 3. Pi 运行时沙箱启动 (`dataagent-runtime-pi/src/sandbox/docker-sandbox-manager.ts`)

- `SandboxConfig` 接口新增 `networkMode?: "bridge" | "host";`。
- 构造函数进行校验：若提供 `networkMode` 必须为 `"bridge"` 或 `"host"`，否则抛出错误。
- 启动容器网络参数计算规则（严格遵循断网优先）：
  - 当 `c.networkEnabled !== true` 时，**无论 `networkMode` 为何值，一律强制为 `--network=none`**，严守断网隔离底线。
  - 当 `c.networkEnabled === true` 时：
    - `c.networkMode === "host"` -> `--network=host`
    - 其他情况（默认） -> `--network=bridge`
- 诊断日志 `logDiagnostic` 打印 `sandbox start ... network=${network} networkEnabled=${String(c.networkEnabled)} networkMode=${c.networkMode ?? "bridge"}`。

### 4. 部署与文档 (`deploy/`)

- `deploy/docker-compose.yml` 在 `dataagent-backend.environment` 中声明：
  ```yaml
  DATAAGENT_SANDBOX_NETWORK_ENABLED: ${DATAAGENT_SANDBOX_NETWORK_ENABLED:-false}
  DATAAGENT_SANDBOX_NETWORK_MODE: ${DATAAGENT_SANDBOX_NETWORK_MODE:-bridge}
  ```
- `deploy/.env.example` 补充注释说明。
- `deploy/README.md` 补充 `DATAAGENT_SANDBOX_NETWORK_MODE` 的适用场景说明（VPN/内网直连选用 host）。

## 取舍与安全性

1. **默认值保持 bridge**：默认行为不变更，普通环境依然使用标准 bridge 隔离。
2. **fail-closed 隔离保证**：只有当 `DATAAGENT_SANDBOX_NETWORK_ENABLED=true` 明确开启时，`networkMode` 才会生效；开关为 `false` 时绝对保持 `none`。
3. **其他容器安全沙箱限制不受影响**：`--read-only`、`--cap-drop=ALL`、`--security-opt=no-new-privileges`、内存与 CPU 限制等依旧全部生效。
