# Hetzner Traffic Guard

一个 Docker 化项目：监控 Hetzner 每台 VPS 当月外网出流量，接近阈值时自动快照+重建+删除旧机；并提供 Web 面板与 Telegram 通知。

## 一键使用（推荐）
```bash
git clone https://github.com/liqiba/hzc.git
cd hzc
bash scripts/onekey.sh
```

脚本会交互式让你填写：
- Hetzner API Token
- Telegram Bot Token / Chat ID（可选）
- 阈值和检测间隔

启动后访问：`http://<你的服务器IP>:1227`

---

## 功能
- 每 `CHECK_INTERVAL_MINUTES` 分钟检测流量
- 超阈值（`ROTATE_THRESHOLD`）自动轮换服务器
- Web 面板展示每台机器流量占比，支持手动重建
- Telegram 通知轮换结果

## 手动方式
```bash
cp .env.example .env
# 编辑 .env 填写 HETZNER_TOKEN 等

docker compose up -d --build
```

## 关键环境变量
- `HETZNER_TOKEN`: Hetzner Cloud API Token
- `TRAFFIC_LIMIT_TB`: 套餐月流量（默认20）
- `ROTATE_THRESHOLD`: 触发阈值（建议先 0.98）
- `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`: Telegram 通知

## GitHub 发布
```bash
git init
git add .
git commit -m "init hetzner traffic guard"
git branch -M main
git remote add origin git@github.com:<you>/<repo>.git
git push -u origin main
```

## 注意
- 自动重建会删除旧机，务必先在测试项目验证。
- 建议先将 `ROTATE_THRESHOLD` 调高到 `0.98` 做灰度。
