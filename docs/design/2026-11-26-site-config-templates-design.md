# 现场 nginx 配置不随升级覆盖 设计

## 现状与问题

`deploy/docker/nginx/default.conf` 绑定挂载为前端容器的 nginx 配置，文档让现场按需修改（路由、超时、域名、证书）。它由仓库跟踪并原样打进离线包，把新离线包同步到部署目录就会覆盖现场修改。只有 `deploy/.env` 不受影响，因为仓库和离线包只带 `.env.example`。OpenDataWorks 的 `frontend.conf` 在 2026-11-26 上线时出现了同样的覆盖，两个仓库一起修。

## 方案

- 仓库只跟踪 `deploy/docker/nginx/default.conf.example`（内容仍与 `dataagent-frontend/nginx.conf` 一致）；`default.conf` 加入 `.gitignore`。
- `scripts/lib/site-config.sh` 的 `ensure_site_configs` 缺文件时从 `.example` 复制，已有文件不动；`start.sh`、`restart.sh` 调用。原 `start.sh` 从 `dataagent-frontend/nginx.conf` 复制或 `touch` 空文件的逻辑删除（离线包里没有前端源码，空文件会让 nginx 没有站点配置）。
- `scripts/create-offline-package.sh` 排除 `default.conf`，只打包模板。
- 顺带：`deploy/README.md`「被其他门户内嵌」去掉门户用不到的 `/api/v1/nl2sql-admin/` 转发。

## 取舍

模板之后的改动不会自动进入现场，升级说明需要写明，由运维对照 `.example` 合并；与 `.env.example` 相同。以 git 检出部署且改过 `default.conf` 的服务器，`git pull` 会因本地修改拒绝更新，先另存再拉取。

与 OpenDataWorks 同名设计配套：opendataworks `docs/design/2026-11-26-site-config-templates-design.md`。
