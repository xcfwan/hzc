#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

echo "== HZC 一键升级 =="

COMPOSE_CMD=""
if command -v docker-compose >/dev/null 2>&1; then
  COMPOSE_CMD="docker-compose"
elif command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  COMPOSE_CMD="docker compose"
else
  echo "[x] docker compose / docker-compose 不可用。"
  exit 1
fi

if ! command -v git >/dev/null 2>&1; then
  echo "[x] git 未安装，请先安装 Git。"
  exit 1
fi

if [ ! -d .git ]; then
  echo "[x] 当前目录不是 Git 仓库：$(pwd)"
  exit 1
fi

echo "[i] 拉取最新代码..."
git fetch origin main

LOCAL_BEFORE="$(git rev-parse HEAD)"
REMOTE_HEAD="$(git rev-parse origin/main)"
echo "[i] local(before)=${LOCAL_BEFORE:0:7} remote=${REMOTE_HEAD:0:7}"

echo "[i] 强制同步到最新版（会覆盖本地代码改动）..."
git reset --hard origin/main

LOCAL_AFTER="$(git rev-parse HEAD)"
if [ "$LOCAL_AFTER" != "$REMOTE_HEAD" ]; then
  echo "[x] 升级失败：代码未对齐 origin/main"
  echo "    local(after)=${LOCAL_AFTER:0:7} remote=${REMOTE_HEAD:0:7}"
  exit 2
fi
echo "[ok] 代码已对齐 origin/main: ${LOCAL_AFTER:0:7}"

# Sync APP_VERSION in .env to repository default, avoid stale pinned old version
TARGET_VERSION="$(python3 - <<'PY'
import re
from pathlib import Path
p = Path('app/config.py')
s = p.read_text(encoding='utf-8', errors='ignore')
m = re.search(r'app_version\s*:\s*str\s*=\s*os\.getenv\("APP_VERSION",\s*"([0-9.]+)"\)', s)
print(m.group(1) if m else "")
PY
)"
if [ -n "$TARGET_VERSION" ]; then
  if [ -f .env ]; then
    if grep -q '^APP_VERSION=' .env; then
      sed -i "s/^APP_VERSION=.*/APP_VERSION=${TARGET_VERSION}/" .env
    else
      echo "APP_VERSION=${TARGET_VERSION}" >> .env
    fi
  else
    echo "APP_VERSION=${TARGET_VERSION}" > .env
  fi
  echo "[i] 已同步 .env APP_VERSION=${TARGET_VERSION}"
fi

echo "[i] 重建并更新容器..."
$COMPOSE_CMD up -d --build

echo "[i] 健康检查 /api/meta ..."
APP_META=""
fetch_meta(){
  # We are in helper container; 127.0.0.1 points to helper itself.
  # So probe target service container via compose exec first.
  if $COMPOSE_CMD exec -T hetzner-traffic-guard python3 - <<'PY' 2>/dev/null
import urllib.request
print(urllib.request.urlopen('http://127.0.0.1:1227/api/meta', timeout=3).read().decode('utf-8', 'ignore'))
PY
  then
    return 0
  fi

  # fallback: try host-gateway alias when available
  if command -v curl >/dev/null 2>&1; then
    curl -fsS "http://host.docker.internal:1227/api/meta" || true
  elif command -v wget >/dev/null 2>&1; then
    wget -qO- "http://host.docker.internal:1227/api/meta" || true
  else
    python3 - <<'PY' || true
import urllib.request
try:
    print(urllib.request.urlopen('http://host.docker.internal:1227/api/meta', timeout=3).read().decode('utf-8', 'ignore'))
except Exception:
    pass
PY
  fi
}
for i in $(seq 1 20); do
  APP_META="$(fetch_meta)"
  if [ -n "$APP_META" ]; then
    break
  fi
  sleep 2
done

if [ -z "$APP_META" ]; then
  echo "[x] 升级后健康检查失败：/api/meta 无响应"
  $COMPOSE_CMD ps || true
  exit 3
fi

echo "[ok] 升级完成"
echo "状态："
$COMPOSE_CMD ps

echo "meta: $(echo "$APP_META" | tr -d '\n' | head -c 180)"
echo "访问: http://$(hostname -I | awk '{print $1}'):1227"
