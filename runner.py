"""Snapshot runner."""
import asyncio
from db import tx, one, query
import serper

async def run_snapshot(project_id: int) -> int:
    proj = one("SELECT * FROM projects WHERE id=?", (project_id,))
    if not proj:
        raise ValueError(f"project {project_id} not found")
    kws = query("SELECT id, keyword FROM keywords WHERE project_id=? AND active=1", (project_id,))
    if not kws:
        raise ValueError("no active keywords")
    with tx() as c:
        snap_id = c.execute(
            "INSERT INTO snapshots(project_id, total, status) VALUES (?,?,?)",
            (project_id, len(kws), "running"),
        ).lastrowid
    try:
        pairs = [(k["id"], k["keyword"]) for k in kws]
        results = await serper.run_batch(pairs, proj)
        with tx() as c:
            for kw_id, pos, url, title, raw in results:
                c.execute(
                    "INSERT INTO rankings(snapshot_id, keyword_id, position, url, title, serp_json) VALUES (?,?,?,?,?,?)",
                    (snap_id, kw_id, pos, url, title, raw),
                )
            c.execute(
                "UPDATE snapshots SET finished_at=datetime('now'), status='done', done=? WHERE id=?",
                (len(results), snap_id),
            )
        return snap_id
    except Exception as e:
        with tx() as c:
            c.execute("UPDATE snapshots SET status='error', error=?, finished_at=datetime('now') WHERE id=?",
                      (str(e), snap_id))
        raise

def run_snapshot_sync(project_id: int):
    return asyncio.run(run_snapshot(project_id))
