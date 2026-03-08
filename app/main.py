from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from pydantic import BaseModel
import asyncio

from app.config import settings
from app.service import monitor
from app.telegram_control import TelegramControl

app = FastAPI(title="Hetzner Traffic Guard")
app.add_middleware(GZipMiddleware, minimum_size=1024)
app.mount('/static', StaticFiles(directory='app/static'), name='static')


@app.middleware("http")
async def add_perf_headers(request: Request, call_next):
    resp = await call_next(request)
    path = request.url.path
    if path.startswith('/static/'):
        resp.headers['Cache-Control'] = 'public, max-age=604800, immutable'
    elif path.startswith('/api/'):
        resp.headers['Cache-Control'] = 'no-store'
    return resp
templates = Jinja2Templates(directory='app/templates')

scheduler = AsyncIOScheduler(timezone=settings.timezone)
tg_control = TelegramControl(monitor)


class CreateServerReq(BaseModel):
    name: str
    server_type: str
    location: str
    image: str | int
    primary_ip_id: int | None = None
    primary_ipv6_id: int | None = None


class SnapshotReq(BaseModel):
    description: str | None = None


class RenameSnapshotReq(BaseModel):
    description: str


class RenameServerReq(BaseModel):
    name: str


class QBNodeReq(BaseModel):
    server_id: int
    url: str
    username: str
    password: str


class RebuildReq(BaseModel):
    image_id: int | str


class DeleteServerReq(BaseModel):
    create_snapshot: bool = False
    keep_ipv4: bool = False
    keep_ipv6: bool = False
    keep_mode: str = "safe"  # safe|fast


class TelegramConfigReq(BaseModel):
    telegram_bot_token: str
    telegram_chat_id: str


class AutoPolicyReq(BaseModel):
    server_id: int
    enabled: bool = True
    threshold: float
    image_id: int | str | None = None


@app.on_event('startup')
async def startup_event():
    if settings.hetzner_token:
        scheduler.add_job(monitor.rotate_if_needed, 'interval', minutes=settings.check_interval_minutes, id='check-traffic', replace_existing=True)
        scheduler.start()
    if tg_control.enabled:
        import asyncio
        asyncio.create_task(tg_control.run())


@app.get('/', response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse('index.html', {
        'request': request,
        'safe_mode': settings.safe_mode,
        'app_version': settings.app_version,
    })


@app.get('/api/servers')
async def servers():
    if not settings.hetzner_token:
        raise HTTPException(status_code=500, detail='HETZNER_TOKEN missing')
    return await monitor.collect()


@app.get('/api/ping')
async def ping():
    return {"ok": True, "app_version": settings.app_version, "app_commit": settings.app_commit}


@app.get('/api/meta')
async def meta():
    if not settings.hetzner_token:
        raise HTTPException(status_code=500, detail='HETZNER_TOKEN missing')
    return await monitor.meta()


@app.get('/api/daily_stats')
async def daily_stats(days: int = 7):
    if not settings.hetzner_token:
        raise HTTPException(status_code=500, detail='HETZNER_TOKEN missing')
    return await monitor.daily_stats(days=days)


@app.get('/api/qb_status')
async def qb_status():
    return await monitor.qb_status()


@app.get('/api/qb_nodes')
async def qb_nodes():
    return monitor.qb_nodes()


@app.get('/api/qb_realtime')
async def qb_realtime():
    return await monitor.qb_realtime()


@app.post('/api/qb_node')
async def qb_node_set(req: QBNodeReq):
    return await monitor.qb_node_set(req.server_id, req.url, req.username, req.password)


@app.delete('/api/qb_node/{server_id}')
async def qb_node_delete(server_id: int):
    return monitor.qb_node_delete(server_id)


@app.get('/api/auto_policies')
async def auto_policies():
    return monitor.auto_policies()


@app.post('/api/auto_policy')
async def auto_policy_set(req: AutoPolicyReq):
    return monitor.auto_policy_set(req.server_id, req.enabled, req.threshold, req.image_id)


@app.delete('/api/auto_policy/{server_id}')
async def auto_policy_delete(server_id: int):
    return monitor.auto_policy_delete(server_id)


@app.get('/api/config/telegram')
async def telegram_config_get():
    return tg_control.get_telegram_config()


@app.put('/api/config/telegram')
async def telegram_config_set(req: TelegramConfigReq):
    return tg_control.set_telegram_config(req.telegram_bot_token, req.telegram_chat_id)


@app.post('/api/service/restart')
async def service_restart():
    cmd = "nohup bash -lc 'cd /opt/hzc && (docker-compose restart hetzner-traffic-guard || docker compose restart hetzner-traffic-guard)' >/tmp/hzc-restart.log 2>&1 &"
    p = await asyncio.create_subprocess_shell(cmd)
    await p.communicate()
    return {"ok": True, "message": "restart triggered"}


@app.post('/api/rotate/{server_id}')
async def rotate(server_id: int):
    if not settings.hetzner_token:
        raise HTTPException(status_code=500, detail='HETZNER_TOKEN missing')
    return await monitor.rotate_server(server_id)


@app.post('/api/rebuild/{server_id}')
async def rebuild(server_id: int, req: RebuildReq):
    if not settings.hetzner_token:
        raise HTTPException(status_code=500, detail='HETZNER_TOKEN missing')
    return await monitor.rebuild_with_snapshot_manual(server_id, req.image_id)


@app.post('/api/rebuild_full/{server_id}')
async def rebuild_full(server_id: int, req: RebuildReq):
    if not settings.hetzner_token:
        raise HTTPException(status_code=500, detail='HETZNER_TOKEN missing')
    return await monitor.rebuild_full_manual(server_id, req.image_id)


@app.get('/api/safe_mode')
async def safe_mode_get():
    return {"safe_mode": monitor.get_safe_mode()}


@app.put('/api/safe_mode')
async def safe_mode_set(enabled: bool):
    return monitor.set_safe_mode(enabled)


@app.get('/api/snapshot_estimate/{server_id}')
async def snapshot_estimate(server_id: int):
    if not settings.hetzner_token:
        raise HTTPException(status_code=500, detail='HETZNER_TOKEN missing')
    return await monitor.estimate_snapshot(server_id)


@app.post('/api/snapshot/{server_id}')
async def snapshot(server_id: int, req: SnapshotReq | None = None):
    if not settings.hetzner_token:
        raise HTTPException(status_code=500, detail='HETZNER_TOKEN missing')
    desc = req.description if req else None
    return await monitor.create_snapshot_manual(server_id, description=desc)


@app.post('/api/create_server')
async def create_server(req: CreateServerReq):
    if not settings.hetzner_token:
        raise HTTPException(status_code=500, detail='HETZNER_TOKEN missing')
    return await monitor.create_server_manual(
        name=req.name,
        server_type=req.server_type,
        location=req.location,
        image=req.image,
        primary_ip_id=req.primary_ip_id,
        primary_ipv6_id=req.primary_ipv6_id,
    )


@app.delete('/api/snapshot/{image_id}')
async def delete_snapshot(image_id: int):
    if not settings.hetzner_token:
        raise HTTPException(status_code=500, detail='HETZNER_TOKEN missing')
    return await monitor.delete_snapshot_manual(image_id)


@app.patch('/api/snapshot/{image_id}')
async def rename_snapshot(image_id: int, req: RenameSnapshotReq):
    if not settings.hetzner_token:
        raise HTTPException(status_code=500, detail='HETZNER_TOKEN missing')
    return await monitor.rename_snapshot_manual(image_id, req.description)


@app.post('/api/reset_password/{server_id}')
async def reset_password(server_id: int):
    if not settings.hetzner_token:
        raise HTTPException(status_code=500, detail='HETZNER_TOKEN missing')
    return await monitor.reset_password_and_notify(server_id)


@app.patch('/api/server/{server_id}/name')
async def rename_server(server_id: int, req: RenameServerReq):
    if not settings.hetzner_token:
        raise HTTPException(status_code=500, detail='HETZNER_TOKEN missing')
    return await monitor.rename_server_manual(server_id, req.name)


@app.post('/api/server/{server_id}/reboot')
async def reboot_server(server_id: int):
    if not settings.hetzner_token:
        raise HTTPException(status_code=500, detail='HETZNER_TOKEN missing')
    res = await monitor.op_server('reboot', server_id)
    return {'ok': True, 'server_id': server_id, 'result': res}


@app.post('/api/server/{server_id}/hard_reboot')
async def hard_reboot_server(server_id: int):
    if not settings.hetzner_token:
        raise HTTPException(status_code=500, detail='HETZNER_TOKEN missing')
    return await monitor.hard_reboot(server_id)


@app.post('/api/server/{server_id}/delete')
async def delete_server(server_id: int, req: DeleteServerReq):
    if not settings.hetzner_token:
        raise HTTPException(status_code=500, detail='HETZNER_TOKEN missing')
    return await monitor.delete_server_manual(
        server_id,
        create_snapshot=req.create_snapshot,
        keep_ipv4=req.keep_ipv4,
        keep_ipv6=req.keep_ipv6,
        keep_mode=req.keep_mode,
    )


@app.get('/api/action/{action_id}')
async def action_status(action_id: int):
    if not settings.hetzner_token:
        raise HTTPException(status_code=500, detail='HETZNER_TOKEN missing')
    return await monitor.get_action_status(action_id)
