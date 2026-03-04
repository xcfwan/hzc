# HZC - Hetzner 流量保护

一个给 Hetzner 用户做“流量可视化 + 安全自动化”的小白友好项目。  
目标：**开箱即用、默认安全、出问题可定位**。

版本规则：`年.月.更新次数`（示例：`26.3.6`）。
每次发布更新，最后一位 +1。

---




## 1) 小白 10 分钟上手（推荐）

> 只需要：一台 Linux 服务器 + Docker + Hetzner API Token

### 第一步：拉代码

```bash
git clone https://github.com/liqiba/hzc.git
cd hzc
```

### 第二步：一键安装向导（直接复制下面三行）

```bash
cd hzc
chmod +x scripts/onekey.sh
./scripts/onekey.sh
```

如果提示权限问题，再用：

```bash
bash scripts/onekey.sh
```

你只需要先填一个必填项：
- `HETZNER_TOKEN`

其他都可以先回车跳过（后续再补）。

### 第三步：打开面板

```text
http://你的服务器IP:1227
```

### 第四步（后续升级）：一键升级

```bash
cd hzc
chmod +x scripts/upgrade.sh
./scripts/upgrade.sh
```

---


## 3) 默认安全逻辑（已简化）

项目默认是**保守模式**：

- `SAFE_MODE=true`：只告警，不自动删机
- `ROTATE_THRESHOLD=0.98`：接近阈值才触发
- `CHECK_INTERVAL_MINUTES=5`：每 5 分钟检测一次

这意味着：
- 新手可以先观察，不会误删
- 看明白后再关闭 安全模式 开自动化

---


## 4) 功能清单（当前可用）

- 服务器列表（状态、IP、规格、流量）
- 已用流量与 24h 流量（真实值，不再粗暴显示 0）
- 每日流量柱状图（悬浮即显详情）
- qB 实时/累计/任务数监控（按服务器配置）
- 快照创建 / 删除 / 重命名
- 服务器改名、重置密码、手动重建
- Telegram 命令入口（按配置启用）
- Telegram 一键版本号/一键升级按钮（🏷️版本号 / 🚀一键升级）

---

## 5) qB 小白配置

在 Web 页面每台服务器后面点 **配置qB**：

- URL：`http://IP:8080`
- 用户名
- 密码

保存后会自动测试并开始实时显示。

---

## 6) 常见问题（FAQ）

### Q1: 页面是 0 流量，但官方后台有流量？

已修复 Hetzner 新版 metrics 结构兼容。若仍异常：

1. 强刷浏览器（Mac: `Cmd+Shift+R`）
2. 看容器日志：
   ```bash
   docker logs -f hetzner-traffic-guard
   ```

### Q2: 每日流量图“无数据”？

已改为按小时聚合成日统计，兼容性更好。新数据产生后会自动显示。

### Q3: tooltip 卡顿/位置错位？

已改成即时 tooltip（非浏览器原生 title），并调整显示位置防止溢出。

---

## 7) 手动部署（进阶）

```bash
cp .env.example .env
# 编辑 .env
docker compose up -d --build
```

---

## 8) 关键环境变量

- `HETZNER_TOKEN`：必填
- `TRAFFIC_LIMIT_TB`：套餐月流量（默认20）
- `ROTATE_THRESHOLD`：触发阈值（默认0.98）
- `CHECK_INTERVAL_MINUTES`：检测间隔（默认5）
- `SAFE_MODE`：默认 true（只告警）
- `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`：可选（推荐在 Web 的“TG配置”里填写，更适合小白）

qB（全局默认，可不填）：
- `QB_URL`
- `QB_USERNAME`
- `QB_PASSWORD`

---

## 9) 升级（小白一键）

推荐直接执行：

```bash
cd hzc
chmod +x scripts/upgrade.sh
./scripts/upgrade.sh
```

脚本会自动完成：
- 拉取最新代码
- 强制同步到 `origin/main`（覆盖本地代码改动）
- 重建并重启最新 Docker 容器
- 显示运行状态

升级后请在 Web 顶部查看版本号（例如 `26.3.6`），用于判断是否已成功更新到最新。

---

## 10) 免责声明

关闭 `SAFE_MODE` 后，自动化动作可能涉及重建与删除，请先在测试环境验证。
