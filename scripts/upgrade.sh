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

echo "[i] 拉取最新代码..."
git fetch origin main

echo "[i] 强制同步到最新版（会覆盖本地代码改动）..."
git reset --hard origin/main

echo "[i] 重建并滚动更新容器（不中断脚本）..."
$COMPOSE_CMD up -d --build

echo "[ok] 升级完成"
echo "状态："
$COMPOSE_CMD ps

echo "访问: http://$(hostname -I | awk '{print $1}'):1227"
