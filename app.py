"""FastAPI entry — admin + dashboard."""
import os, json, secrets
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, Request, Form, HTTPException, Depends, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import db, runner

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "change-me")
SECRET_KEY = os.environ.get("SECRET_KEY", secrets.token_hex(32))

app = FastAPI(title="Rank Tracker")
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, max_age=60*60*24*30)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

scheduler = AsyncIOScheduler()

# ------------ auth ------------
def require_auth(request: Request):
    if not request.session.get("admin"):
        raise HTTPException(303, headers={"Location": "/login"})
    return True

# ------------ startup ------------
@app.on_event("startup")
async def startup():
    db.init_db()
    load_schedules()
    scheduler.start()

def load_schedules():
    for p in db.query("SELECT id, schedule_cron FROM projects WHERE active=1 AND schedule_cron!=''"):
        try:
            tr = CronTrigger.from_crontab(p["schedule_cron"])
            scheduler.add_job(runner.run_snapshot, tr, args=[p["id"]], id=f"proj_{p['id']}", replace_existing=True)
        except Exception as e:
            print(f"schedule err proj {p['id']}: {e}")

# ------------ auth routes ------------
@app.get("/login", response_class=HTMLResponse)
async def login_get(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "err": None})

@app.post("/login")
async def login_post(request: Request, password: str = Form(...)):
    if password == ADMIN_PASSWORD:
        request.session["admin"] = True
        return RedirectResponse("/", 303)
    return templates.TemplateResponse("login.html", {"request": request, "err": "Невірний пароль"})

@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", 303)

# ------------ dashboard ------------
@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, project_id: Optional[int] = None):
    require_auth(request)
    projects = db.query("SELECT * FROM projects ORDER BY name")
    if not projects:
        return RedirectResponse("/admin", 303)
    pid = project_id or projects[0]["id"]
    project = db.one("SELECT * FROM projects WHERE id=?", (pid,))
    snaps = db.query("SELECT * FROM snapshots WHERE project_id=? AND status='done' ORDER BY started_at DESC LIMIT 60", (pid,))
    return templates.TemplateResponse("dashboard.html", {
        "request": request, "projects": projects, "project": project, "snaps": snaps,
    })

# ------------ API: dashboard data ------------
@app.get("/api/project/{pid}/data")
async def api_data(pid: int, request: Request, snapshot: Optional[int] = None, compare: Optional[int] = None):
    require_auth(request)
    latest = db.one("SELECT id, started_at FROM snapshots WHERE project_id=? AND status='done' ORDER BY started_at DESC LIMIT 1", (pid,))
    snap_id = snapshot or (latest["id"] if latest else None)
    if not snap_id:
        return {"keywords": [], "snapshots": [], "history": [], "current_snap": None, "compare_snap": None}
    rows = db.query("""
        SELECT k.id, k.keyword, k.tag, k.volume, r.position, r.url, r.title
        FROM keywords k
        LEFT JOIN rankings r ON r.keyword_id=k.id AND r.snapshot_id=?
        WHERE k.project_id=? AND k.active=1
        ORDER BY (CASE WHEN r.position IS NULL THEN 999 ELSE r.position END), k.keyword
    """, (snap_id, pid))
    compare_data = {}
    if compare:
        for r in db.query("SELECT keyword_id, position FROM rankings WHERE snapshot_id=?", (compare,)):
            compare_data[r["keyword_id"]] = r["position"]
    for r in rows:
        r["prev_position"] = compare_data.get(r["id"])
        if r["position"] and r["prev_position"]:
            r["delta"] = r["prev_position"] - r["position"]  # positive = up
        else:
            r["delta"] = None
    # History: top3/top10/top20/top50 counts per snapshot
    history = db.query("""
        SELECT s.id snap_id, s.started_at,
          SUM(CASE WHEN r.position BETWEEN 1 AND 3 THEN 1 ELSE 0 END) t3,
          SUM(CASE WHEN r.position BETWEEN 1 AND 10 THEN 1 ELSE 0 END) t10,
          SUM(CASE WHEN r.position BETWEEN 1 AND 20 THEN 1 ELSE 0 END) t20,
          SUM(CASE WHEN r.position BETWEEN 1 AND 50 THEN 1 ELSE 0 END) t50,
          SUM(CASE WHEN r.position BETWEEN 1 AND 100 THEN 1 ELSE 0 END) t100,
          COUNT(DISTINCT r.keyword_id) tracked,
          AVG(CASE WHEN r.position IS NOT NULL THEN r.position END) avg_pos
        FROM snapshots s LEFT JOIN rankings r ON r.snapshot_id=s.id
        WHERE s.project_id=? AND s.status='done'
        GROUP BY s.id ORDER BY s.started_at
    """, (pid,))
    snaps = db.query("SELECT id, started_at FROM snapshots WHERE project_id=? AND status='done' ORDER BY started_at DESC", (pid,))
    return {"keywords": rows, "snapshots": snaps, "history": history, "current_snap": snap_id, "compare_snap": compare}

# ------------ keyword history for chart ------------
@app.get("/api/keyword/{kid}/history")
async def kw_history(kid: int, request: Request):
    require_auth(request)
    return db.query("""
        SELECT s.started_at, r.position, r.url
        FROM rankings r JOIN snapshots s ON s.id=r.snapshot_id
        WHERE r.keyword_id=? AND s.status='done' ORDER BY s.started_at
    """, (kid,))

# ------------ admin ------------
@app.get("/admin", response_class=HTMLResponse)
async def admin_view(request: Request):
    require_auth(request)
    projects = db.query("SELECT * FROM projects ORDER BY created_at DESC")
    return templates.TemplateResponse("admin.html", {"request": request, "projects": projects})

@app.get("/admin/project/{pid}", response_class=HTMLResponse)
async def admin_project(pid: int, request: Request):
    require_auth(request)
    project = db.one("SELECT * FROM projects WHERE id=?", (pid,))
    if not project:
        raise HTTPException(404)
    kws = db.query("SELECT * FROM keywords WHERE project_id=? ORDER BY keyword", (pid,))
    snaps = db.query("SELECT * FROM snapshots WHERE project_id=? ORDER BY started_at DESC LIMIT 20", (pid,))
    return templates.TemplateResponse("project.html", {"request": request, "project": project, "keywords": kws, "snapshots": snaps})

@app.post("/admin/project/new")
async def project_new(request: Request, name: str = Form(...), domain: str = Form(...),
                      gl: str = Form("ua"), hl: str = Form("uk"), location: str = Form("Ukraine"),
                      device: str = Form("desktop"), schedule_cron: str = Form("0 6 * * *")):
    require_auth(request)
    pid = db.execute(
        "INSERT INTO projects(name,domain,gl,hl,location,device,schedule_cron) VALUES (?,?,?,?,?,?,?)",
        (name, domain, gl, hl, location, device, schedule_cron),
    )
    load_schedules()
    return RedirectResponse(f"/admin/project/{pid}", 303)

@app.post("/admin/project/{pid}/update")
async def project_update(pid: int, request: Request, name: str = Form(...), domain: str = Form(...),
                          gl: str = Form("ua"), hl: str = Form("uk"), location: str = Form("Ukraine"),
                          device: str = Form("desktop"), schedule_cron: str = Form(""), active: int = Form(1)):
    require_auth(request)
    db.execute("""UPDATE projects SET name=?, domain=?, gl=?, hl=?, location=?, device=?, schedule_cron=?, active=? WHERE id=?""",
               (name, domain, gl, hl, location, device, schedule_cron, active, pid))
    try: scheduler.remove_job(f"proj_{pid}")
    except: pass
    load_schedules()
    return RedirectResponse(f"/admin/project/{pid}", 303)

@app.post("/admin/project/{pid}/delete")
async def project_delete(pid: int, request: Request):
    require_auth(request)
    db.execute("DELETE FROM projects WHERE id=?", (pid,))
    try: scheduler.remove_job(f"proj_{pid}")
    except: pass
    return RedirectResponse("/admin", 303)

@app.post("/admin/project/{pid}/keywords")
async def kw_bulk(pid: int, request: Request, keywords: str = Form(...), tag: str = Form("")):
    require_auth(request)
    seen = set()
    for line in keywords.splitlines():
        kw = line.strip()
        if not kw or kw in seen: continue
        seen.add(kw)
        try:
            db.execute("INSERT INTO keywords(project_id, keyword, tag) VALUES (?,?,?)", (pid, kw, tag))
        except Exception: pass
    return RedirectResponse(f"/admin/project/{pid}", 303)

@app.post("/admin/keyword/{kid}/delete")
async def kw_del(kid: int, request: Request):
    require_auth(request)
    k = db.one("SELECT project_id FROM keywords WHERE id=?", (kid,))
    db.execute("DELETE FROM keywords WHERE id=?", (kid,))
    return RedirectResponse(f"/admin/project/{k['project_id']}", 303)

@app.post("/admin/project/{pid}/run")
async def run_now(pid: int, request: Request, bg: BackgroundTasks):
    require_auth(request)
    async def _wrap():
        try: await runner.run_snapshot(pid)
        except Exception as e: print("run err", e)
    bg.add_task(_wrap)
    return RedirectResponse(f"/admin/project/{pid}", 303)

@app.get("/healthz")
async def health():
    return {"ok": True}
