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

if [ "$LOCAL_BEFORE" != "$REMOTE_HEAD" ]; then
  echo "[i] 强制同步到最新版（会覆盖本地代码改动）..."
  git reset --hard origin/main
fi

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

if [ "$LOCAL_BEFORE" = "$REMOTE_HEAD" ]; then
  echo "[i] 代码已是最新，执行轻量重载以应用版本号/环境变量..."
  $COMPOSE_CMD up -d >/dev/null 2>&1 || true
else
  echo "[i] 重建并更新容器..."
  $COMPOSE_CMD up -d --build
fi

echo "[i] 健康检查 /api/ping ..."
APP_META=""
LAST_ERR=""
fetch_meta(){
  # We are in helper container; 127.0.0.1 points to helper itself.
  # Probe target service container namespace first.
  if OUT="$($COMPOSE_CMD exec -T hetzner-traffic-guard python3 - <<'PY' 2>/dev/null
import urllib.request
try:
    print(urllib.request.urlopen('http://127.0.0.1:1227/api/ping', timeout=4).read().decode('utf-8', 'ignore'))
except Exception as e:
    print(f"__ERR__:{e}")
PY
)"; then
    echo "$OUT"
    return 0
  fi

  # fallback: try host-gateway alias when available
  if command -v curl >/dev/null 2>&1; then
    curl -fsS --connect-timeout 4 --max-time 5 "http://host.docker.internal:1227/api/ping" || true
  elif command -v wget >/dev/null 2>&1; then
    wget -qO- --timeout=5 "http://host.docker.internal:1227/api/ping" || true
  else
    python3 - <<'PY' || true
import urllib.request
try:
    print(urllib.request.urlopen('http://host.docker.internal:1227/api/ping', timeout=4).read().decode('utf-8', 'ignore'))
except Exception as e:
    print(f"__ERR__:{e}")
PY
  fi
}
for i in $(seq 1 45); do
  APP_META="$(fetch_meta)"
  if echo "$APP_META" | grep -q '"app_version"'; then
    break
  fi
  if [ -n "$APP_META" ]; then
    LAST_ERR="$APP_META"
  fi
  APP_META=""
  sleep 2
done

if [ -z "$APP_META" ]; then
  echo "[x] 升级后健康检查失败：/api/ping 无响应"
  [ -n "$LAST_ERR" ] && echo "[x] 最近探测结果: $(echo "$LAST_ERR" | tail -n 1)"
  $COMPOSE_CMD ps || true
  $COMPOSE_CMD logs --tail=40 hetzner-traffic-guard || true
  exit 3
fi

echo "[ok] 升级完成"

echo "[i] 清理历史镜像与构建缓存（保留当前运行所需）..."
set +e
# 1) 清理悬空镜像（<none>）
docker image prune -f >/dev/null 2>&1
# 2) 清理无用构建缓存（7天前）
docker builder prune -f --filter "until=168h" >/dev/null 2>&1
# 3) 尝试删除本项目旧镜像（保留 compose 当前正在用的镜像）
INUSE_IDS="$($COMPOSE_CMD images -q 2>/dev/null | sort -u)"
for repo in "hetzner-traffic-guard" "hzc-hetzner-traffic-guard"; do
  ALL_IDS="$(docker images --format '{{.Repository}} {{.ID}}' | awk -v r="$repo" '$1==r{print $2}' | sort -u)"
  for img in $ALL_IDS; do
    echo "$INUSE_IDS" | grep -q "$img" && continue
    docker rmi "$img" >/dev/null 2>&1 || true
  done
done
set -e

echo "状态："
$COMPOSE_CMD ps

echo "meta: $(echo "$APP_META" | tr -d '\n' | head -c 180)"
echo "访问: http://$(hostname -I | awk '{print $1}'):1227"
