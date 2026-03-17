import asyncio
import time
import httpx
from app.config import settings
from app.runtime_config import RuntimeConfig

BASE = "https://api.telegram.org"


class TelegramControl:
    def __init__(self, monitor):
        self.monitor = monitor
        self.runtime = RuntimeConfig(settings.runtime_config_path)
        rc = self.runtime.get()
        self.token = str(rc.get("telegram_bot_token") or settings.telegram_bot_token or "")
        self.chat_id = str(rc.get("telegram_chat_id") or settings.telegram_chat_id or "")
        self.offset = int(rc.get("telegram_update_offset") or 0)

    @property
    def enabled(self):
        return bool(self.token and self.chat_id)

    def get_telegram_config(self):
        token = self.token or ""
        masked = (token[:6] + "..." + token[-4:]) if len(token) > 12 else ("***" if token else "")
        return {
            "enabled": self.enabled,
            "telegram_bot_token_masked": masked,
            "telegram_chat_id": self.chat_id or "",
            "source": "runtime_config_or_env",
        }

    def set_telegram_config(self, telegram_bot_token: str, telegram_chat_id: str):
        telegram_bot_token = (telegram_bot_token or "").strip()
        telegram_chat_id = str(telegram_chat_id or "").strip()
        self.runtime.update({
            "telegram_bot_token": telegram_bot_token,
            "telegram_chat_id": telegram_chat_id,
        })
        self.token = telegram_bot_token
        self.chat_id = telegram_chat_id
        return {"ok": True, "enabled": self.enabled, "need_restart": True}

    async def api(self, method: str, payload: dict | None = None):
        url = f"{BASE}/bot{self.token}/{method}"
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(url, json=payload or {})
            r.raise_for_status()
            return r.json()

    async def send(self, text: str, chat_id: str | None = None, reply_markup: dict | None = None):
        cid = chat_id or self.chat_id
        payload = {"chat_id": cid, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        return await self.api("sendMessage", payload)

    @staticmethod
    def main_keyboard():
        return {
            "keyboard": [
                [{"text": "📋 服务器列表"}, {"text": "📊 系统状态"}, {"text": "📈 流量汇总"}],
                [{"text": "🧊 快照列表"}, {"text": "⚙️ qB状态"}, {"text": "🏷️ 版本号"}],
                [{"text": "🛡️ 安全开关"}, {"text": "🚀 一键升级"}, {"text": "📜 升级日志"}],
                [{"text": "❓帮助"}],
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
            {"command": "version", "description": "当前项目版本"},
            {"command": "upgrade", "description": "一键升级到最新版"},
            {"command": "upgradelog", "description": "查看最近升级日志"},
            {"command": "safeon", "description": "开启安全模式"},
            {"command": "safeoff", "description": "关闭安全模式"},
            {"command": "safestatus", "description": "查看安全模式状态"},
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
            "🏷️ 版本号": "/version",
            "🛡️ 安全开关": "/safestatus",
            "🚀 一键升级": "/upgrade",
            "📜 升级日志": "/upgradelog",
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
                "/startserver <ID> /stopserver <ID> /reboot <ID>\n/delete <ID> confirm /rebuild <ID> <snapshot_id>\n/resetpwd <ID> 重置并发送新密码\n"
                "/version 查看版本 /upgrade 一键升级 /upgradelog 升级日志\n"
                "/safeon /safeoff /safestatus 安全模式开关\n"
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

        if cmd == "/version":
            return await self.send(f"当前版本: {settings.app_version}\n提交: {settings.app_commit}", chat_id)

        if cmd == "/upgrade":
            # prevent duplicate trigger caused by repeated delivery in short time
            now = int(time.time())
            rc = self.runtime.get()
            last_ts = int(rc.get("last_upgrade_trigger_ts") or 0)
            if now - last_ts < 25:
                return await self.send("已有升级请求刚触发，请勿重复点击（25秒内防抖）", chat_id)

            # run upgrade in helper container with a fixed lock name
            # stale running lock (>30min) will be force cleaned automatically
            upgrade_cmd = (
                "set -e; "
                "ROOT=''; for d in /opt/hzc /app .; do if [ -f \"$d/docker-compose.yml\" ] && [ -f \"$d/scripts/upgrade.sh\" ]; then ROOT=\"$d\"; break; fi; done; "
                "if [ -z \"$ROOT\" ]; then echo '__ROOT_NOT_FOUND__'; exit 15; fi; "
                "mkdir -p \"$ROOT/state\"; cd \"$ROOT\"; "
                "git fetch origin main >/dev/null 2>&1 || { echo '__FETCH_FAILED__'; exit 14; }; "
                "if ! command -v docker-compose >/dev/null 2>&1 && ! docker compose version >/dev/null 2>&1; then "
                "  if command -v docker >/dev/null 2>&1 && command -v apt-get >/dev/null 2>&1; then "
                "    apt-get update >/dev/null 2>&1 && apt-get install -y --no-install-recommends docker-compose-plugin docker-compose >/dev/null 2>&1 || true; "
                "  fi; "
                "fi; "
                "if command -v docker-compose >/dev/null 2>&1; then "
                "  TASK_NAME=hzc-upgrader-$(date +%s); "
                "  CID=$(docker-compose run -d --name $TASK_NAME --no-deps --entrypoint bash hetzner-traffic-guard -lc \"if ! command -v docker >/dev/null 2>&1; then apt-get update >/dev/null 2>&1 && apt-get install -y --no-install-recommends docker.io docker-compose >/dev/null 2>&1 || true; fi; command -v docker >/dev/null 2>&1 || { echo __NO_DOCKER_IN_HELPER__; exit 20; }; CROOT=''; for d in /opt/hzc /workspace /app /root/hzc .; do if [ -f \\\"$d/scripts/upgrade.sh\\\" ]; then CROOT=\\\"$d\\\"; break; fi; done; [ -n \\\"$CROOT\\\" ] || { echo __CONTAINER_ROOT_NOT_FOUND__; exit 18; }; mkdir -p \\\"$CROOT/state\\\"; cd \\\"$CROOT\\\"; timeout 1800 ./scripts/upgrade.sh > \\\"$CROOT/state/upgrade.log\\\" 2>&1\") || { echo '__RUN_FAILED__'; exit 19; }; "
                "  [ -n \"$CID\" ] || { echo '__RUN_FAILED__'; exit 19; }; "
                "elif docker compose version >/dev/null 2>&1; then "
                "  TASK_NAME=hzc-upgrader-$(date +%s); "
                "  CID=$(docker compose run -d --name $TASK_NAME --no-deps --entrypoint bash hetzner-traffic-guard -lc \"if ! command -v docker >/dev/null 2>&1; then apt-get update >/dev/null 2>&1 && apt-get install -y --no-install-recommends docker.io docker-compose >/dev/null 2>&1 || true; fi; command -v docker >/dev/null 2>&1 || { echo __NO_DOCKER_IN_HELPER__; exit 20; }; CROOT=''; for d in /opt/hzc /workspace /app /root/hzc .; do if [ -f \\\"$d/scripts/upgrade.sh\\\" ]; then CROOT=\\\"$d\\\"; break; fi; done; [ -n \\\"$CROOT\\\" ] || { echo __CONTAINER_ROOT_NOT_FOUND__; exit 18; }; mkdir -p \\\"$CROOT/state\\\"; cd \\\"$CROOT\\\"; timeout 1800 ./scripts/upgrade.sh > \\\"$CROOT/state/upgrade.log\\\" 2>&1\") || { echo '__RUN_FAILED__'; exit 19; }; "
                "  [ -n \"$CID\" ] || { echo '__RUN_FAILED__'; exit 19; }; "
                "elif ! command -v docker >/dev/null 2>&1; then echo '__NO_DOCKER__'; exit 17; "
                "else echo '__NO_COMPOSE__'; exit 13; fi; "
                "echo $CID"
            )
            p = await asyncio.create_subprocess_exec(
                "bash", "-lc", upgrade_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            out, err = await p.communicate()
            so = (out.decode("utf-8", errors="ignore") if out else "").strip()
            se = (err.decode("utf-8", errors="ignore") if err else "").strip()
            if p.returncode != 0:
                if "__UPGRADE_UPTODATE__" in so:
                    return await self.send("当前已是最新版本，无需升级。", chat_id)
                if "__NO_COMPOSE__" in so:
                    return await self.send("升级任务触发失败：未检测到 docker compose / docker-compose（已尝试自动安装）", chat_id)
                if "__NO_DOCKER__" in so:
                    return await self.send("升级任务触发失败：未检测到 docker，无法执行容器升级", chat_id)
                if "__CONTAINER_ROOT_NOT_FOUND__" in so:
                    return await self.send("升级任务触发失败：容器内未找到 scripts/upgrade.sh（挂载路径不兼容）", chat_id)
                if "__RUN_FAILED__" in so:
                    return await self.send("升级任务触发失败：升级任务容器启动失败（docker/compose执行异常）", chat_id)
                if "__NO_DOCKER_IN_HELPER__" in so:
                    return await self.send("升级任务触发失败：升级任务容器内 docker 不可用，已尝试安装仍失败", chat_id)
                if "__FETCH_FAILED__" in so:
                    return await self.send("升级任务触发失败：拉取远端版本信息失败，请稍后重试。", chat_id)
                if "__ROOT_NOT_FOUND__" in so:
                    return await self.send("升级任务触发失败：未找到项目目录（缺少 docker-compose.yml 或 scripts/upgrade.sh）。", chat_id)
                msg = (se or so or "unknown error")[-700:]
                return await self.send(f"升级任务触发失败：{msg}", chat_id)

            self.runtime.update({"last_upgrade_trigger_ts": now})
            cid = (out.decode("utf-8", errors="ignore") if out else "").strip().splitlines()[-1][:24]
            return await self.send(f"开始执行一键升级（拉取最新代码并重建容器）...\n升级任务已触发（task: {cid or 'n/a'}）。约30-120秒后生效。\n可点【🏷️ 版本号】或【📜 升级日志】确认。", chat_id)

        if cmd == "/upgradelog":
            if len(parts) >= 2 and parts[1].lower() == "full":
                p = await asyncio.create_subprocess_shell("bash -lc 'for f in /opt/hzc/state/upgrade.log /app/state/upgrade.log ./state/upgrade.log; do [ -f \"$f\" ] && { tail -n 160 \"$f\"; exit 0; }; done; echo no-log'", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                out, _ = await p.communicate()
                txt = (out.decode("utf-8", errors="ignore") or "no-log").strip()
                return await self.send(f"<code>{txt[-3200:]}</code>", chat_id)

            p = await asyncio.create_subprocess_shell("bash -lc 'for f in /opt/hzc/state/upgrade.log /app/state/upgrade.log ./state/upgrade.log; do [ -f \"$f\" ] && { tail -n 300 \"$f\"; exit 0; }; done; echo no-log'", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            out, _ = await p.communicate()
            txt = (out.decode("utf-8", errors="ignore") or "no-log").strip()
            lines = txt.splitlines()

            # Detect running upgrader task to avoid false "未确认成功" right after trigger
            rp = await asyncio.create_subprocess_shell(
                "bash -lc 'docker ps --format \"{{.Names}} {{.Status}}\" 2>/dev/null | grep "
                "-E ""^hzc-upgrader-[0-9]+ "" | head -n 1 || true'",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            rout, _ = await rp.communicate()
            running_line = (rout.decode("utf-8", errors="ignore") if rout else "").strip()
            running = bool(running_line)

            def pick(prefix: str):
                for ln in reversed(lines):
                    if prefix in ln:
                        return ln.strip()
                return ""

            has_ok = "[ok] 升级完成" in txt
            has_fail = "[x]" in txt

            if running and not has_ok and not has_fail:
                status = "⏳ 升级进行中"
            elif has_ok:
                status = "✅ 成功"
            else:
                status = "⚠️ 未确认成功"

            head_line = pick("代码已对齐 origin/main")
            if not head_line:
                head_line = pick("升级失败：代码未对齐 origin/main")

            if "[x] 升级后健康检查失败" in txt:
                health_ok = "否"
            elif "[i] 健康检查 /api/ping ..." in txt and has_ok:
                health_ok = "是"
            elif running:
                health_ok = "进行中"
            else:
                health_ok = "未识别"

            fail_line = pick("[x]")
            ps_line = pick("hetzner-traffic-guard")
            if running and not ps_line:
                ps_line = running_line

            if txt.strip() == "no-log":
                fail_line = fail_line or "未找到升级日志文件（/opt/hzc/state/upgrade.log）"

            msg = (
                f"📜 升级摘要\n"
                f"状态：{status}\n"
                f"代码：{head_line or '未识别'}\n"
                f"健康检查：{health_ok}\n"
                f"服务：{ps_line or '未识别'}"
            )
            if fail_line:
                msg += f"\n失败点：{fail_line}"
            msg += "\n\n如需完整日志请回复：/upgradelog full"
            return await self.send(msg[:3500], chat_id)

        if cmd == "/safeon":
            self.monitor.set_safe_mode(True)
            return await self.send("已开启 SAFE_MODE（仅告警，不自动执行重建）", chat_id)

        if cmd == "/safeoff":
            self.monitor.set_safe_mode(False)
            return await self.send("已关闭 SAFE_MODE（自动策略可执行）", chat_id)

        if cmd == "/safestatus":
            sm = self.monitor.get_safe_mode()
            return await self.send(f"当前 SAFE_MODE: {'ON' if sm else 'OFF'}", chat_id)

        if cmd == "/qbstatus":
            rows = await self.monitor.collect()
            qrows = [r for r in rows if (r.get("qb") or {}).get("enabled")]
            if not qrows:
                return await self.send("qB监控未配置", chat_id)

            def _fmt_speed(v):
                return f"{v/1024/1024:.2f} MiB/s"
            def _fmt_total(v):
                return f"{v/1024/1024/1024/1024:.2f} TiB"
            def _task_bar(a, t):
                t = max(1, int(t or 0))
                a = max(0, min(int(a or 0), t))
                w = 8
                fill = int(round(a / t * w))
                return "🟦" * fill + "⬜" * (w - fill)

            lines = ["⚙️ <b>qB状态（全部节点）</b>"]
            for r in qrows:
                q = r.get("qb", {})
                conn = q.get('connection_status','unknown')
                conn_icon = "🟢" if conn == 'connected' else "🟠"
                a = q.get('active_torrents',0)
                t = q.get('all_torrents',0)
                lines.append(
                    f"\n<b>{r.get('name')}</b> <code>{r.get('id')}</code> {conn_icon}{conn}"
                    f"\n⬆️ {_fmt_speed(q.get('up_speed',0))} · ⬇️ {_fmt_speed(q.get('dl_speed',0))}"
                    f"\n📦 ⬆️ {_fmt_total(q.get('up_total',0))} · ⬇️ {_fmt_total(q.get('dl_total',0))}"
                    f"\n🧩 任务 {a}/{t}  {_task_bar(a,t)}  · DHT {q.get('dht_nodes',0)}"
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
            if map_cmd == "rebuild":
                if len(parts) < 3:
                    return await self.send(f"请指定已有快照ID：/rebuild {sid} <snapshot_id>", chat_id)
                res = await self.monitor.rebuild_with_snapshot_manual(sid, int(parts[2]))
                return await self.send(f"操作已提交: rebuild {sid} snapshot#{parts[2]}\n{str(res)[:800]}", chat_id)
            res = await self.monitor.op_server(map_cmd, sid)
            return await self.send(f"操作已提交: {map_cmd} {sid}\n{str(res)[:800]}", chat_id)

        if cmd in ["/scheduleon", "/scheduleoff", "/schedulestatus", "/dnstest", "/dnscheck"]:
            return await self.send("该功能已预留，下一版接入。", chat_id)

        return await self.send("未识别命令，发送 /help 查看用法", chat_id)

    async def run(self):
        if not self.enabled:
            return
        initialized = False
        host = '-'
        while True:
            try:
                if not initialized:
                    try:
                        await self.set_menu()
                    except Exception:
                        pass
                    try:
                        p = await asyncio.create_subprocess_shell("bash -lc 'hostname'", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                        out, _ = await p.communicate()
                        host = (out.decode("utf-8", errors="ignore").strip() if out else "-")
                    except Exception:
                        host = '-'
                    try:
                        await self.send(f"🤖 Hetzner Monitor 机器人已启动（{host}），发送 /start 查看命令", reply_markup=self.main_keyboard())
                    except Exception:
                        pass
                    initialized = True

                async with httpx.AsyncClient(timeout=60) as c:
                    r = await c.get(f"{BASE}/bot{self.token}/getUpdates", params={"timeout": 30, "offset": self.offset})
                    r.raise_for_status()
                    data = r.json().get("result", [])
                for u in data:
                    self.offset = u["update_id"] + 1
                    self.runtime.update({"telegram_update_offset": self.offset})
                    msg = u.get("message", {})
                    chat_id = str(msg.get("chat", {}).get("id", ""))
                    text = msg.get("text", "")
                    if chat_id and chat_id == self.chat_id:
                        await self.handle(text, chat_id)
                await asyncio.sleep(1)
            except Exception as e:
                try:
                    print(f"[tg-loop] recoverable error: {e}")
                except Exception:
                    pass
                await asyncio.sleep(3)
