#!/bin/bash

# DataAgent 统一账号数据迁移
# 功能：执行 scripts/migrate-accounts.sql，把旧会话、旧授权名单迁到统一账号（可重复执行）。
# 用法：scripts/migrate-accounts.sh
# 顺序：拉取新镜像（不启动）→ scripts/backup-db.sh（需要新镜像里的 mysqldump）→ scripts/start.sh（Alembic 自动建表）→ 本脚本
#       → python -m core.accounts create-user --username admin --password '<密码>' --admin
# 在 dataagent-backend 镜像内用 mysql 客户端执行，宿主机无需安装 MySQL 客户端。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DEPLOY_DIR="$REPO_ROOT/deploy"
ENV_FILE="$DEPLOY_DIR/.env"
SQL_FILE="$SCRIPT_DIR/migrate-accounts.sql"

# shellcheck source=/dev/null
source "$SCRIPT_DIR/lib/container-runtime.sh"

[ -f "$SQL_FILE" ] || { echo "❌ 缺少 $SQL_FILE"; exit 1; }
detect_compose_cmd || { echo "❌ 未找到可用的 compose 命令"; exit 1; }
ENV_FLAG_ARGS=()
if [ "$COMPOSE_SUPPORTS_ENV_FILE" = true ] && [ -f "$ENV_FILE" ]; then
    ENV_FLAG_ARGS=(--env-file "$ENV_FILE")
fi

echo "🔄 执行统一账号数据迁移"
pushd "$DEPLOY_DIR" >/dev/null
"${COMPOSE_CMD[@]}" -f docker-compose.yml "${ENV_FLAG_ARGS[@]}" run --rm -T --no-deps --entrypoint sh dataagent-backend -c \
    'MYSQL_PWD="$MYSQL_PASSWORD" mysql --table -h "$MYSQL_HOST" -P "$MYSQL_PORT" -u "$MYSQL_USER" "$SESSION_MYSQL_DATABASE"' \
    < "$SQL_FILE"
popd >/dev/null

echo "✅ 迁移完成。接着设置管理员：docker compose -f deploy/docker-compose.yml exec dataagent-backend python -m core.accounts create-user --username admin --password '<密码>' --admin"
