from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from pydantic import BaseModel

from app.config import settings
from app.service import monitor
from app.telegram_control import TelegramControl

app = FastAPI(title="Hetzner Traffic Guard")
app.mount('/static', StaticFiles(directory='app/static'), name='static')
templates = Jinja2Templates(directory='app/templates')

scheduler = AsyncIOScheduler(timezone=settings.timezone)
tg_control = TelegramControl(monitor)


class CreateServerReq(BaseModel):
    name: str
    server_type: str
    location: str
    image: str | int


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
    image_id: int


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
    return templates.TemplateResponse('index.html', {'request': request, 'safe_mode': settings.safe_mode})


@app.get('/api/servers')
async def servers():
    if not settings.hetzner_token:
        raise HTTPException(status_code=500, detail='HETZNER_TOKEN missing')
    return await monitor.collect()


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


@app.post('/api/qb_node')
async def qb_node_set(req: QBNodeReq):
    return await monitor.qb_node_set(req.server_id, req.url, req.username, req.password)


@app.delete('/api/qb_node/{server_id}')
async def qb_node_delete(server_id: int):
    return monitor.qb_node_delete(server_id)


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
    return await monitor.create_server_manual(name=req.name, server_type=req.server_type, location=req.location, image=req.image)


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
