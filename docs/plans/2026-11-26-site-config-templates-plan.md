# 现场 nginx 配置不随升级覆盖 计划

设计：[2026-11-26-site-config-templates-design.md](../design/2026-11-26-site-config-templates-design.md)

## 步骤

1. `git mv deploy/docker/nginx/default.conf deploy/docker/nginx/default.conf.example`；`.gitignore` 加 `deploy/docker/nginx/default.conf`。
2. 新增 `scripts/lib/site-config.sh`；`start.sh` 用 `ensure_site_configs` 替换旧的复制/`touch` 逻辑，`restart.sh` 同样调用。
3. `create-offline-package.sh` 排除 `./docker/nginx/default.conf`。
4. `deploy/README.md`：说明现场配置与模板合并；内嵌转发表去掉 `nl2sql-admin`。

## 验证

`tests/test_deploy_scripts.py`：同步检查改读模板；离线包只含模板与 `site-config.sh`；首次 `start.sh` 生成 `default.conf`；已有文件不被覆盖。`bash -n` 检查脚本。

## 上线与回退

已有部署同步新包后 `default.conf` 不变；新部署首次启动生成。回退到旧包会再次覆盖，回退前先备份该文件。
