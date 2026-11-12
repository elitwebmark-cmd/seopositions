"""Snapshot runner — scrapes serper for all (target × keyword) pairs."""
import asyncio
from db import tx, one, query
import serper

async def run_snapshot(project_id: int) -> int:
    proj = one("SELECT * FROM projects WHERE id=?", (project_id,))
    if not proj:
        raise ValueError(f"project {project_id} not found")
    targets = query("SELECT * FROM targets WHERE project_id=? AND active=1", (project_id,))
    if not targets:
        raise ValueError("no active targets — add at least one (city + language)")
    kws = query("SELECT id, keyword FROM keywords WHERE project_id=? AND active=1", (project_id,))
    if not kws:
        raise ValueError("no active keywords")
    total = len(targets) * len(kws)
    with tx() as c:
        snap_id = c.execute(
            "INSERT INTO snapshots(project_id, total, status) VALUES (?,?,?)",
            (project_id, total, "running"),
        ).lastrowid
    try:
        all_results = []
        for tgt in targets:
            tgt_proj = {
                "domain": proj["domain"],
                "gl": proj["gl"],
                "hl": tgt["hl"],
                "location": tgt["location"],
                "device": proj["device"],
            }
            pairs = [(k["id"], k["keyword"]) for k in kws]
            res = await serper.run_batch(pairs, tgt_proj)
            for kw_id, pos, url, title, raw in res:
                all_results.append((tgt["id"], kw_id, pos, url, title, raw))
        with tx() as c:
            for tgt_id, kw_id, pos, url, title, raw in all_results:
                c.execute(
                    "INSERT INTO rankings(snapshot_id, keyword_id, target_id, position, url, title, serp_json) VALUES (?,?,?,?,?,?,?)",
                    (snap_id, kw_id, tgt_id, pos, url, title, raw),
                )
            c.execute(
                "UPDATE snapshots SET finished_at=datetime('now'), status='done', done=? WHERE id=?",
                (len(all_results), snap_id),
            )
        # Notify TG subscribers
        try:
            import tgbot
            await tgbot.send_project_report(project_id, snap_id)
        except Exception as e:
            print(f"tg notify err: {e}")
        return snap_id
    except Exception as e:
        with tx() as c:
            c.execute("UPDATE snapshots SET status='error', error=?, finished_at=datetime('now') WHERE id=?",
                      (str(e), snap_id))
        raise

def run_snapshot_sync(project_id: int):
    return asyncio.run(run_snapshot(project_id))
