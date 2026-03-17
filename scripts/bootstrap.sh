#!/usr/bin/env bash
set -euo pipefail

REPO_URL="https://github.com/liqiba/hzc.git"
TARGET_DIR="${HZC_DIR:-/opt/hzc}"
BRANCH="${HZC_BRANCH:-main}"
ACTION="${1:-menu}"

if [ ! -d "$TARGET_DIR/.git" ]; then
  echo "[i] 克隆仓库到 $TARGET_DIR ..."
  git clone --depth=1 -b "$BRANCH" "$REPO_URL" "$TARGET_DIR"
else
  echo "[i] 检测到已存在目录，更新代码 ..."
  git -C "$TARGET_DIR" fetch origin "$BRANCH"
  git -C "$TARGET_DIR" reset --hard "origin/$BRANCH"
fi

chmod +x "$TARGET_DIR/scripts/onekey.sh"

# compatibility: keep /opt/hzc as canonical path for in-container helper upgrade
if [ "$TARGET_DIR" != "/opt/hzc" ] && [ ! -e /opt/hzc ]; then
  ln -s "$TARGET_DIR" /opt/hzc 2>/dev/null || true
fi

case "$ACTION" in
  menu)
    exec "$TARGET_DIR/scripts/onekey.sh"
    ;;
  install|upgrade|uninstall|status)
    exec "$TARGET_DIR/scripts/onekey.sh" "$ACTION"
    ;;
  *)
    echo "用法: bash bootstrap.sh [menu|install|upgrade|uninstall|status]"
    exit 1
    ;;
esac
