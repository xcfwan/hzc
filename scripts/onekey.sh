#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

ensure_compat_path() {
  local cur
  cur="$(pwd)"
  if [ "$cur" = "/opt/hzc" ]; then
    return 0
  fi

  if [ ! -e /opt/hzc ]; then
    ln -s "$cur" /opt/hzc 2>/dev/null || true
  fi
}

pick_compose() {
  if command -v docker-compose >/dev/null 2>&1; then
    echo "docker-compose"
    return 0
  fi
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    echo "docker compose"
    return 0
  fi
  return 1
}

require_compose() {
  if ! COMPOSE_CMD="$(pick_compose)"; then
    echo "[x] 未检测到 docker compose / docker-compose"
    echo "    请先安装 Docker 与 Compose 后重试。"
    exit 1
  fi
}

ask() {
  local key="$1" prompt="$2" secret="${3:-0}"
  local cur val
  cur=$(grep -E "^${key}=" .env | head -n1 | cut -d'=' -f2- || true)
  if [ "$secret" = "1" ]; then
    read -r -s -p "$prompt${cur:+ [已存在]}: " val
    echo
  else
    read -r -p "$prompt${cur:+ [${cur}]}: " val
  fi
  if [ -n "${val:-}" ]; then
    sed -i "s#^${key}=.*#${key}=${val}#" .env
  fi
}

ensure_env() {
  [ -f .env ] || cp .env.example .env
}

do_install() {
  require_compose
  ensure_env

  echo "== HZC 一键安装/初始化 =="
  echo "提示：至少填写 Hetzner Token，其它可回车跳过。"

  ask "HETZNER_TOKEN" "请输入 Hetzner API Token(必填)" 1
  if [ -z "$(grep -E '^HETZNER_TOKEN=' .env | head -n1 | cut -d'=' -f2-)" ]; then
    echo "[x] HETZNER_TOKEN 不能为空"
    exit 1
  fi

  ask "TELEGRAM_BOT_TOKEN" "Telegram Bot Token(可留空)" 1
  ask "TELEGRAM_CHAT_ID" "Telegram Chat ID(可留空)"
  ask "ROTATE_THRESHOLD" "触发阈值(推荐0.98，默认0.98)"
  ask "CHECK_INTERVAL_MINUTES" "检测间隔分钟(默认5)"
  ask "SAFE_MODE" "安全模式(默认true，true=仅告警)"

  sed -i 's#^TRAFFIC_LIMIT_TB=.*#TRAFFIC_LIMIT_TB=20#' .env || true
  grep -q '^ROTATE_THRESHOLD=' .env || echo 'ROTATE_THRESHOLD=0.98' >> .env
  grep -q '^CHECK_INTERVAL_MINUTES=' .env || echo 'CHECK_INTERVAL_MINUTES=5' >> .env
  grep -q '^SAFE_MODE=' .env || echo 'SAFE_MODE=true' >> .env

  echo "[i] 正在构建并启动..."
  $COMPOSE_CMD up -d --build

  echo "[ok] 已启动"
  echo "访问: http://$(hostname -I | awk '{print $1}'):1227"
}

do_upgrade() {
  echo "== HZC 一键升级 =="
  chmod +x ./scripts/upgrade.sh
  ./scripts/upgrade.sh
}

do_uninstall() {
  require_compose
  echo "== HZC 卸载 =="
  read -r -p "确认卸载容器与网络？输入 YES 继续: " c
  if [ "${c:-}" != "YES" ]; then
    echo "已取消"
    return 0
  fi

  $COMPOSE_CMD down --remove-orphans

  read -r -p "是否同时删除数据目录 ./state ? (yes/NO): " d
  if [ "${d:-NO}" = "yes" ]; then
    rm -rf ./state
    echo "[ok] 已删除 ./state"
  fi
  echo "[ok] 卸载完成"
}

do_status() {
  require_compose
  echo "== HZC 状态 =="
  $COMPOSE_CMD ps || true
  echo "--- /api/ping ---"
  curl -fsS "http://127.0.0.1:1227/api/ping" || echo "服务未就绪"
}

print_menu() {
  cat <<'EOF'

请选择操作：
  1) 安装 / 初始化
  2) 升级
  3) 卸载
  4) 状态检查
  0) 退出
EOF
}

main() {
  ensure_compat_path

  case "${1:-}" in
    install) do_install; exit 0 ;;
    upgrade) do_upgrade; exit 0 ;;
    uninstall) do_uninstall; exit 0 ;;
    status) do_status; exit 0 ;;
    "" ) ;;
    * )
      echo "用法: $0 [install|upgrade|uninstall|status]"
      exit 1
      ;;
  esac

  while true; do
    print_menu
    read -r -p "输入编号: " op
    case "${op:-}" in
      1) do_install ;;
      2) do_upgrade ;;
      3) do_uninstall ;;
      4) do_status ;;
      0) echo "退出"; exit 0 ;;
      *) echo "无效输入" ;;
    esac
  done
}

main "$@"
