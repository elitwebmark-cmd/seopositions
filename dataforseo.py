"""DataForSEO — Google Ads Search Volume API."""
import os, base64, httpx, asyncio
from db import tx, query, one

LOGIN = os.environ.get("DATAFORSEO_LOGIN", "")
PASSWORD = os.environ.get("DATAFORSEO_PASSWORD", "")
API = "https://api.dataforseo.com/v3/keywords_data/google_ads/search_volume/live"

# Ukraine location
LOCATION_CODE = int(os.environ.get("DATAFORSEO_LOCATION", "2804"))

def _auth_header():
    tok = base64.b64encode(f"{LOGIN}:{PASSWORD}".encode()).decode()
    return {"Authorization": f"Basic {tok}", "Content-Type": "application/json"}


async def fetch_volumes(keywords, language="uk", location_code=None):
    """Return dict keyword -> volume (int or None)."""
    if not LOGIN or not PASSWORD:
        raise RuntimeError("DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD not set")
    if not keywords:
        return {}
    loc = location_code or LOCATION_CODE
    out = {}
    # DataForSEO limit: 1000 keywords per task
    async with httpx.AsyncClient(timeout=120) as client:
        for i in range(0, len(keywords), 700):
            chunk = keywords[i:i+700]
            body = [{"keywords": chunk, "location_code": loc, "language_code": language}]
            r = await client.post(API, headers=_auth_header(), json=body)
            r.raise_for_status()
            data = r.json()
            tasks = data.get("tasks", []) or []
            for t in tasks:
                for row in (t.get("result") or []):
                    kw = (row.get("keyword") or "").strip().lower()
                    vol = row.get("search_volume")
                    if kw:
                        out[kw] = vol
    return out


async def refresh_project_volumes(project_id):
    """Fetch volume for all keywords in project, update db."""
    proj = one("SELECT * FROM projects WHERE id=?", (project_id,))
    if not proj:
        raise ValueError("project not found")
    kws = query("SELECT id, keyword FROM keywords WHERE project_id=? AND active=1", (project_id,))
    if not kws:
        return 0
    keywords = [k["keyword"] for k in kws]
    # Use project's hl for language
    lang = proj["hl"] or "uk"
    volumes = await fetch_volumes(keywords, language=lang)
    updated = 0
    with tx() as c:
        for k in kws:
            v = volumes.get(k["keyword"].lower())
            if v is not None:
                c.execute("UPDATE keywords SET volume=? WHERE id=?", (v, k["id"]))
                updated += 1
    return updated


async def refresh_keyword_volume(keyword_id):
    """Refresh volume for a single keyword."""
    k = one("SELECT k.id, k.keyword, p.hl FROM keywords k JOIN projects p ON p.id=k.project_id WHERE k.id=?", (keyword_id,))
    if not k:
        return None
    volumes = await fetch_volumes([k["keyword"]], language=k["hl"] or "uk")
    v = volumes.get(k["keyword"].lower())
    if v is not None:
        with tx() as c:
            c.execute("UPDATE keywords SET volume=? WHERE id=?", (v, keyword_id))
    return v
