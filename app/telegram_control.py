import asyncio
import httpx
from app.config import settings

BASE = "https://api.telegram.org"


class TelegramControl:
    def __init__(self, monitor):
        self.monitor = monitor
        self.token = settings.telegram_bot_token
        self.chat_id = str(settings.telegram_chat_id or "")
        self.offset = 0

    @property
    def enabled(self):
        return bool(self.token and self.chat_id)

    async def api(self, method: str, payload: dict | None = None):
        url = f"{BASE}/bot{self.token}/{method}"
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(url, json=payload or {})
            r.raise_for_status()
            return r.json()

    async def send(self, text: str, chat_id: str | None = None, reply_markup: dict | None = None):
        cid = chat_id or self.chat_id
        payload = {"chat_id": cid, "text": text}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        return await self.api("sendMessage", payload)

    @staticmethod
    def main_keyboard():
        return {
            "keyboard": [
                [{"text": "📋 服务器列表"}, {"text": "📊 系统状态"}, {"text": "📈 流量汇总"}],
                [{"text": "🧊 快照列表"}, {"text": "⚙️ qB状态"}, {"text": "❓帮助"}],
            ],
            "resize_keyboard": True,
            "is_persistent": True,
        }

    async def set_menu(self):
        cmds = [
            {"command": "start", "description": "命令帮助"},
            {"command": "list", "description": "服务器列表"},
            {"command": "status", "description": "系统状态"},
            {"command": "traffic", "description": "流量详情 /traffic <id>"},
            {"command": "today", "description": "今日流量 /today <id>"},
            {"command": "snapshots", "description": "快照列表"},
            {"command": "createsnapshot", "description": "创建快照"},
            {"command": "startserver", "description": "开机"},
            {"command": "stopserver", "description": "关机"},
            {"command": "reboot", "description": "重启"},
            {"command": "delete", "description": "删除"},
            {"command": "rebuild", "description": "重建"},
            {"command": "resetpwd", "description": "重置并获取密码"},
            {"command": "qbstatus", "description": "qB 节点状态"},
        ]
        await self.api("setMyCommands", {"commands": cmds})

    async def handle(self, text: str, chat_id: str):
        t = (text or "").strip()
        quick = {
            "📋 服务器列表": "/list",
            "📊 系统状态": "/status",
            "📈 流量汇总": "/report",
            "🧊 快照列表": "/snapshots",
            "⚙️ qB状态": "/qbstatus",
            "❓帮助": "/help",
        }
        t = quick.get(t, t)
        parts = t.split()
        cmd = parts[0].lower() if parts else ""

        if cmd in ["/start", "/help", "命令大全"]:
            return await self.send(
                "命令:\n"
                "/list 服务器列表\n/status 系统状态\n/traffic <ID> 流量详情\n/today <ID> 今日流量\n/report 流量汇总\n"
                "/snapshots 快照列表\n/createsnapshot <ID> [confirm] 创建快照\n/createfromsnapshot <snapshot_id> <type> <location> <name>\n"
                "/startserver <ID> /stopserver <ID> /reboot <ID>\n/delete <ID> confirm /rebuild <ID> [image]\n/resetpwd <ID> 重置并发送新密码\n"
                "/scheduleon /scheduleoff /schedulestatus (预留)\n/dnscheck /dnstest (预留)\n\n"
                "你也可以直接点下方按钮。",
                chat_id,
                reply_markup=self.main_keyboard(),
            )

        if cmd in ["/list", "/servers"]:
            return await self.send(await self.monitor.server_list_text(), chat_id)

        if cmd == "/status":
            rows = await self.monitor.collect()
            warn = len([r for r in rows if r["over_threshold"]])
            return await self.send(f"服务器: {len(rows)} 台\n超阈值: {warn} 台\nSAFE_MODE: {settings.safe_mode}", chat_id)

        if cmd == "/report":
            rows = await self.monitor.collect()
            total = sum(r.get("used_tb", 0) for r in rows)
            return await self.send(f"本月总出流量: {total:.2f} TB", chat_id)

        if cmd == "/qbstatus":
            rows = await self.monitor.collect()
            qrows = [r for r in rows if (r.get("qb") or {}).get("enabled")]
            if not qrows:
                return await self.send("qB监控未配置", chat_id)

            def _fmt_speed(v):
                return f"{v/1024/1024:.2f} MiB/s"
            def _fmt_total(v):
                return f"{v/1024/1024/1024/1024:.2f} TiB"

            lines = ["qB状态（全部节点）"]
            for r in qrows:
                q = r.get("qb", {})
                lines.append(
                    f"\n[{r.get('name')}|{r.get('id')}] {q.get('connection_status','unknown')}"
                    f"\n实时: ↑ {_fmt_speed(q.get('up_speed',0))} / ↓ {_fmt_speed(q.get('dl_speed',0))}"
                    f"\n累计: ↑ {_fmt_total(q.get('up_total',0))} / ↓ {_fmt_total(q.get('dl_total',0))}"
                    f"\n任务: {q.get('active_torrents',0)}/{q.get('all_torrents',0)}  DHT:{q.get('dht_nodes',0)}"
                )
            return await self.send("\n".join(lines)[:3800], chat_id)

        if cmd == "/traffic" and len(parts) >= 2:
            return await self.send(await self.monitor.traffic_text(int(parts[1])), chat_id)

        if cmd == "/today" and len(parts) >= 2:
            return await self.send(await self.monitor.today_text(int(parts[1])), chat_id)

        if cmd == "/snapshots":
            meta = await self.monitor.meta()
            arr = meta.get("snapshots", [])[:30]
            text = "\n".join([f"#{s['id']} {s.get('name','')} {s.get('size_gb',0)}GB" for s in arr]) or "暂无快照"
            return await self.send(text, chat_id)

        if cmd == "/createsnapshot" and len(parts) >= 2:
            sid = int(parts[1])
            est = await self.monitor.estimate_snapshot(sid)
            if len(parts) < 3 or parts[2].lower() != "confirm":
                return await self.send(
                    f"预估快照体积: {est.get('estimated_snapshot_size_gb',0):.2f}GB\n"
                    f"预估月费: €{est.get('estimated_monthly_eur',0):.2f}\n"
                    f"确认请发送: /createsnapshot {sid} confirm",
                    chat_id,
                )
            res = await self.monitor.create_snapshot_manual(sid)
            return await self.send(f"已提交: {res.get('message','ok')}", chat_id)

        if cmd == "/createfromsnapshot" and len(parts) >= 5:
            snap_id, stype, loc, name = parts[1], parts[2], parts[3], parts[4]
            res = await self.monitor.create_server_manual(name=name, server_type=stype, location=loc, image=int(snap_id))
            return await self.send(f"已创建: {res.get('server',{}).get('name',name)}", chat_id)

        if cmd == "/resetpwd" and len(parts) >= 2:
            sid = int(parts[1])
            res = await self.monitor.reset_password_and_notify(sid)
            return await self.send(f"已提交重置密码: {sid}\n{str(res)[:600]}", chat_id)

        if cmd in ["/startserver", "/stopserver", "/reboot", "/delete", "/rebuild"] and len(parts) >= 2:
            sid = int(parts[1])
            map_cmd = {
                "/startserver": "start",
                "/stopserver": "stop",
                "/reboot": "reboot",
                "/delete": "delete",
                "/rebuild": "rebuild",
            }[cmd]
            if map_cmd == "delete" and (len(parts) < 3 or parts[2] != "confirm"):
                return await self.send(f"危险操作确认: /delete {sid} confirm", chat_id)
            extra = parts[2] if map_cmd == "rebuild" and len(parts) >= 3 else None
            res = await self.monitor.op_server(map_cmd, sid, extra)
            return await self.send(f"操作已提交: {map_cmd} {sid}\n{str(res)[:800]}", chat_id)

        if cmd in ["/scheduleon", "/scheduleoff", "/schedulestatus", "/dnstest", "/dnscheck"]:
            return await self.send("该功能已预留，下一版接入。", chat_id)

        return await self.send("未识别命令，发送 /help 查看用法", chat_id)

    async def run(self):
        if not self.enabled:
            return
        await self.set_menu()
        await self.send("🤖 Hetzner Monitor 机器人已启动，发送 /start 查看命令", reply_markup=self.main_keyboard())
        while True:
            try:
                async with httpx.AsyncClient(timeout=60) as c:
                    r = await c.get(f"{BASE}/bot{self.token}/getUpdates", params={"timeout": 30, "offset": self.offset})
                    r.raise_for_status()
                    data = r.json().get("result", [])
                for u in data:
                    self.offset = u["update_id"] + 1
                    msg = u.get("message", {})
                    chat_id = str(msg.get("chat", {}).get("id", ""))
                    text = msg.get("text", "")
                    if chat_id and chat_id == self.chat_id:
                        await self.handle(text, chat_id)
                await asyncio.sleep(1)
            except Exception:
                await asyncio.sleep(3)
