"""Serper.dev API client."""
import os, json, httpx, asyncio
from urllib.parse import urlparse

API_URL = "https://google.serper.dev/search"
API_KEY = os.environ.get("SERPER_API_KEY", "")

def domain_of(url: str) -> str:
    try:
        d = urlparse(url).netloc.lower()
        return d[4:] if d.startswith("www.") else d
    except Exception:
        return ""

def matches(result_url: str, target_domain: str) -> bool:
    td = target_domain.lower().strip()
    if td.startswith("http"):
        td = urlparse(td).netloc
    td = td[4:] if td.startswith("www.") else td
    rd = domain_of(result_url)
    return rd == td or rd.endswith("." + td)

async def search_one(client: httpx.AsyncClient, keyword: str, gl: str, hl: str, location: str, device: str):
    payload = {"q": keyword, "gl": gl, "hl": hl, "num": 100, "device": device}
    if location:
        payload["location"] = location
    r = await client.post(
        API_URL,
        headers={"X-API-KEY": API_KEY, "Content-Type": "application/json"},
        json=payload, timeout=30,
    )
    r.raise_for_status()
    return r.json()

def find_position(serp: dict, target_domain: str):
    organic = serp.get("organic", []) or []
    for item in organic:
        url = item.get("link", "")
        if matches(url, target_domain):
            return item.get("position"), url, item.get("title", "")
    return None, None, None

async def run_batch(keywords, project, concurrency: int = 5):
    """keywords: list of (kw_id, keyword). Returns list of (kw_id, position, url, title, raw)."""
    sem = asyncio.Semaphore(concurrency)
    out = []
    async with httpx.AsyncClient() as client:
        async def task(kw_id, kw):
            async with sem:
                try:
                    serp = await search_one(client, kw, project["gl"], project["hl"], project["location"], project["device"])
                    pos, url, title = find_position(serp, project["domain"])
                    out.append((kw_id, pos, url, title, json.dumps({"organic": serp.get("organic", [])[:20]}, ensure_ascii=False)))
                except Exception as e:
                    out.append((kw_id, None, None, f"ERR: {e}", None))
        await asyncio.gather(*[task(k[0], k[1]) for k in keywords])
    return out
