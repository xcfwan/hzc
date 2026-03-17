# HZC - Hetzner 流量保护面板

面向 Hetzner 的轻量运维工具：
**流量监控 + 重建策略 + 快照管理 + Telegram 一键运维 + Web 一键升级**。

> 设计目标：简单、稳、可追踪、可恢复。

---

## 功能亮点

- 服务器状态与流量总览（含每日趋势）
- 手动重建（保留原 IP 重建新机）
- 自动重建策略（按阈值触发）
- 快照管理（创建 / 删除 / 重命名）
- 删除服务器（可选保留 IPv4/IPv6）
- Telegram 机器人快捷操作
- Web / TG 一键升级 + 升级日志

---

## 页面截图

### 仪表盘

![HZC Dashboard](docs/screenshots/dashboard.jpg)

### 手机端创建弹窗（已支持可滚动）

![Mobile Create Modal](docs/screenshots/mobile-create-modal.jpg)

---

## 快速开始（3步）

### 0) 用 Docker / Docker Compose 安装本项目

> 前提：你的机器已具备 Docker 环境（Docker 或 docker-compose 任一可用）

**方式A：一条命令（推荐）**

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/liqiba/hzc/main/scripts/bootstrap.sh) install
```

**方式B：手动 compose 安装**

```bash
git clone https://github.com/liqiba/hzc.git
cd hzc
cp .env.example .env
cp docker-compose.template.yml docker-compose.yml
# 编辑 .env，至少填写 HETZNER_TOKEN

# 启动（两种命令二选一）
docker-compose up -d --build
# 或
docker compose up -d --build
```

### 1) 一条命令启动（推荐）

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/liqiba/hzc/main/scripts/bootstrap.sh)
```

可直接指定动作：

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/liqiba/hzc/main/scripts/bootstrap.sh) install
bash <(curl -fsSL https://raw.githubusercontent.com/liqiba/hzc/main/scripts/bootstrap.sh) upgrade
bash <(curl -fsSL https://raw.githubusercontent.com/liqiba/hzc/main/scripts/bootstrap.sh) uninstall
bash <(curl -fsSL https://raw.githubusercontent.com/liqiba/hzc/main/scripts/bootstrap.sh) status
```

### 2) 本地脚本方式

```bash
git clone https://github.com/liqiba/hzc.git
cd hzc
chmod +x scripts/onekey.sh
./scripts/onekey.sh
```

脚本内置菜单：安装/升级/卸载/状态检查。也可直接子命令：

```bash
./scripts/onekey.sh install
./scripts/onekey.sh upgrade
./scripts/onekey.sh uninstall
./scripts/onekey.sh status
```

> 先填 `HETZNER_TOKEN`，其它配置可后续在页面补充。

### 3) 打开面板

```text
http://你的服务器IP:1227
```

---

## 一键升级

### Web / TG 一键升级

- 页面顶部：`🚀 一键升级`
- Telegram：`/upgrade`

### 命令行升级（兜底）

```bash
cd hzc
./scripts/upgrade.sh
```

升级逻辑：
- 拉取 `origin/main`
- 已最新则不重复升级
- 有新版本自动重建容器
- 升级后自动健康检查 `/api/ping`
- 自动清理部分历史镜像/构建缓存（降低磁盘堆积）

---

## 默认安全策略

默认参数：
- `SAFE_MODE=true`（只告警，不自动执行危险动作）
- `ROTATE_THRESHOLD=0.98`
- `CHECK_INTERVAL_MINUTES=5`

建议先观察，再逐步放开自动化。

---

## 常用环境变量

### 必填

- `HETZNER_TOKEN`

### 常用

- `TRAFFIC_LIMIT_TB`（默认 20）
- `ROTATE_THRESHOLD`（默认 0.98）
- `CHECK_INTERVAL_MINUTES`（默认 5）
- `SAFE_MODE`（默认 true）
- `APP_VERSION`（前端显示版本号）

### Telegram（可选）

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

### qB（可选）

- `QB_URL`
- `QB_USERNAME`
- `QB_PASSWORD`

---

## 常见问题

### 1) 页面样式旧 / 按钮位置异常

先强刷浏览器：
- Windows/Linux: `Ctrl + Shift + R`
- macOS: `Cmd + Shift + R`

### 2) 一键升级“触发了但版本没变”

优先看：
- `/api/ping`
- TG 的“升级日志”（`/upgradelog`）

### 3) 升级失败怎么查

```bash
docker logs -f hetzner-traffic-guard
```

并结合：
```bash
cd hzc
./scripts/upgrade.sh
```

---

## 免责声明

关闭 `SAFE_MODE` 后，自动动作可能涉及重建/删除。  
请先在测试环境验证，再用于生产。
