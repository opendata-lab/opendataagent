#!/bin/sh
set -eu
runtime_uid=${DATAAGENT_RUNTIME_UID:-1000}
runtime_gid=${DATAAGENT_RUNTIME_GID:-1000}
case "$runtime_uid:$runtime_gid" in *[!0-9:]*|0:*|*:0) echo "Runtime UID/GID must be positive integers" >&2; exit 1;; esac
mkdir -p /dataagent_runtime /app/.claude/skills
if [ ! -f /app/.claude/skills/.bundled-version ] || ! cmp -s /opt/dataagent-bundled-skills/.bundled-version /app/.claude/skills/.bundled-version; then
  cp -a /opt/dataagent-bundled-skills/. /app/.claude/skills/
fi
chown -R "$runtime_uid:$runtime_gid" /dataagent_runtime /app/.claude/skills
chmod -R u+rwX /dataagent_runtime /app/.claude/skills
runtime_group=$(getent group "$runtime_gid" | cut -d: -f1 || true)
if [ -z "$runtime_group" ]; then groupadd -g "$runtime_gid" dataagent; runtime_group=dataagent; fi
runtime_user=$(getent passwd "$runtime_uid" | cut -d: -f1 || true)
if [ -z "$runtime_user" ]; then useradd -u "$runtime_uid" -g "$runtime_group" -M -d /dataagent_runtime dataagent; runtime_user=dataagent; fi
if [ ! -S /var/run/docker.sock ]; then echo "Docker socket missing" >&2; exit 1; fi
socket_gid=$(stat -c %g /var/run/docker.sock)
socket_group=$(getent group "$socket_gid" | cut -d: -f1 || true)
if [ -z "$socket_group" ]; then groupadd -g "$socket_gid" dataagent-docker; socket_group=dataagent-docker; fi
usermod -g "$runtime_group" -a -G "$socket_group" "$runtime_user"
gosu "$runtime_user" docker info >/dev/null
exec gosu "$runtime_user" sh -c 'alembic upgrade head && exec uvicorn main:app --host 0.0.0.0 --port 8900'
