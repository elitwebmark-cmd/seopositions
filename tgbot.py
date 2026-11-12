"""Telegram bot — morning reports after each snapshot + self-service subscribe."""
import os, asyncio, httpx
from db import tx, one, query

TOKEN = os.environ.get("TG_BOT_TOKEN", "").strip()
API = f"https://api.telegram.org/bot{TOKEN}"
PUBLIC_URL = os.environ.get("PUBLIC_URL", "").rstrip("/")


async def tg_api(method, **kwargs):
    if not TOKEN:
        return None
    async with httpx.AsyncClient() as c:
        try:
            r = await c.post(f"{API}/{method}", json=kwargs, timeout=40)
            return r.json()
        except Exception as e:
            print(f"tg_api {method} err: {e}")
            return None


async def send(chat_id, text):
    return await tg_api("sendMessage", chat_id=chat_id, text=text,
                        parse_mode="HTML", disable_web_page_preview=True)


def _escape(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def format_report(project, target, snap_id, prev_snap_id=None):
    r = one("""
        SELECT COUNT(DISTINCT k.id) total,
          SUM(CASE WHEN r.position BETWEEN 1 AND 3 THEN 1 ELSE 0 END) t3,
          SUM(CASE WHEN r.position BETWEEN 1 AND 10 THEN 1 ELSE 0 END) t10,
          SUM(CASE WHEN r.position BETWEEN 1 AND 20 THEN 1 ELSE 0 END) t20,
          SUM(CASE WHEN r.position BETWEEN 1 AND 50 THEN 1 ELSE 0 END) t50,
          SUM(CASE WHEN r.position BETWEEN 1 AND 100 THEN 1 ELSE 0 END) t100,
          AVG(CASE WHEN r.position IS NOT NULL THEN r.position END) avg_p
        FROM keywords k
        LEFT JOIN rankings r ON r.keyword_id=k.id AND r.snapshot_id=? AND r.target_id=?
        WHERE k.project_id=? AND k.active=1
    """, (snap_id, target['id'], project['id']))
    # Compare with previous
    movers = []
    if prev_snap_id:
        cur = {x['keyword_id']: x['position'] for x in query(
            "SELECT keyword_id, position FROM rankings WHERE snapshot_id=? AND target_id=?",
            (snap_id, target['id']))}
        prv = {x['keyword_id']: x['position'] for x in query(
            "SELECT keyword_id, position FROM rankings WHERE snapshot_id=? AND target_id=?",
            (prev_snap_id, target['id']))}
        for kid, c_pos in cur.items():
            p_pos = prv.get(kid)
            if c_pos != p_pos:
                kw = one("SELECT keyword FROM keywords WHERE id=?", (kid,))
                if kw:
                    movers.append((kw['keyword'], p_pos, c_pos))
    def delta(p, c):
        if p is None and c is None: return 0
        if p is None: return -1000  # new tracked
        if c is None: return 999  # lost from top-100
        return p - c  # positive = moved up

    movers.sort(key=lambda m: -abs(delta(m[1], m[2])))
    ups = [m for m in movers if m[1] is not None and m[2] is not None and m[2] < m[1]][:5]
    downs = [m for m in movers if m[1] is not None and m[2] is not None and m[2] > m[1]][:5]
    new_lost = [m for m in movers if m[2] is None and m[1] is not None][:5]
    new_found = [m for m in movers if m[1] is None and m[2] is not None][:5]

    t = f"<b>📊 {_escape(project['name'])}</b> · {_escape(target['label'])} · <code>{target['hl'].upper()}</code>\n"
    t += f"<i>Зйом #{snap_id}</i>\n\n"
    t += f"🏆 ТОП 3: <b>{r['t3'] or 0}</b>\n"
    t += f"🎯 ТОП 10: <b>{r['t10'] or 0}</b>\n"
    t += f"📈 ТОП 20: <b>{r['t20'] or 0}</b>\n"
    t += f"👀 ТОП 100: <b>{r['t100'] or 0}</b> / {r['total']}\n"
    if r['avg_p']:
        t += f"📊 Середня: <b>{r['avg_p']:.1f}</b>\n"

    if ups:
        t += "\n🟢 <b>Виросли:</b>\n"
        for kw, p, c in ups:
            t += f"• {_escape(kw)}: {p} → {c} (<b>+{p-c}</b>)\n"
    if downs:
        t += "\n🔴 <b>Впали:</b>\n"
        for kw, p, c in downs:
            t += f"• {_escape(kw)}: {p} → {c} (<b>-{c-p}</b>)\n"
    if new_found:
        t += "\n🆕 <b>Зайшли в ТОП 100:</b>\n"
        for kw, p, c in new_found:
            t += f"• {_escape(kw)}: #{c}\n"
    if new_lost:
        t += "\n⚠️ <b>Випали з ТОП 100:</b>\n"
        for kw, p, c in new_lost:
            t += f"• {_escape(kw)} (було {p})\n"

    if PUBLIC_URL:
        t += f'\n<a href="{PUBLIC_URL}/?project_id={project["id"]}">📊 Відкрити дешборд</a>'
    return t


async def send_project_report(project_id, snap_id):
    if not TOKEN:
        return
    proj = one("SELECT * FROM projects WHERE id=?", (project_id,))
    targets = query("SELECT * FROM targets WHERE project_id=? AND active=1", (project_id,))
    if not proj or not targets:
        return
    prev = one("""SELECT id FROM snapshots WHERE project_id=? AND status='done' AND id<?
                  ORDER BY id DESC LIMIT 1""", (project_id, snap_id))
    prev_id = prev['id'] if prev else None
    subs = query("SELECT * FROM tg_subscribers WHERE project_id=?", (project_id,))
    for sub in subs:
        for tgt in targets:
            if sub.get('target_id') and sub['target_id'] != tgt['id']:
                continue
            try:
                await send(sub['chat_id'], format_report(proj, tgt, snap_id, prev_id))
            except Exception as e:
                print(f"tg send err chat={sub['chat_id']}: {e}")


def _is_allowed(chat_id, username):
    """Check if user is in whitelist. If matched by username, back-fill chat_id."""
    # by chat_id first
    row = one("SELECT * FROM tg_whitelist WHERE chat_id=?", (chat_id,))
    if row:
        return True
    # by username
    if username:
        row = one("SELECT * FROM tg_whitelist WHERE lower(username)=lower(?)", (username,))
        if row:
            with tx() as c:
                c.execute("UPDATE tg_whitelist SET chat_id=? WHERE id=?", (chat_id, row['id']))
            return True
    return False


async def handle_update(update):
    msg = update.get("message") or update.get("edited_message")
    if not msg:
        return
    chat_id = msg["chat"]["id"]
    username = msg.get("from", {}).get("username", "") or ""
    text = (msg.get("text") or "").strip()

    if not _is_allowed(chat_id, username):
        await send(chat_id, f"🚫 Доступ закритий.\n\nТвій chat_id: <code>{chat_id}</code>\nUsername: <code>@{_escape(username) if username else '—'}</code>\n\nПопроси адміна додати тебе в whitelist.")
        return

    if text.startswith("/start") or text == "/help":
        projects = query("SELECT id, name FROM projects WHERE active=1 ORDER BY name")
        t = "👋 <b>Rank Tracker Bot</b>\n\n<b>Проєкти:</b>\n"
        for p in projects:
            t += f"• <code>{p['id']}</code> — {_escape(p['name'])}\n"
        t += "\n<b>Команди:</b>\n"
        t += "<code>/sub ID</code> — підписатись (щоденний звіт)\n"
        t += "<code>/unsub ID</code> — відписатись\n"
        t += "<code>/my</code> — мої підписки\n"
        t += "<code>/now ID</code> — надіслати звіт зараз\n"
        await send(chat_id, t)

    elif text.startswith("/sub"):
        parts = text.split()
        if len(parts) < 2 or not parts[1].isdigit():
            await send(chat_id, "Використання: <code>/sub project_id</code>")
            return
        pid = int(parts[1])
        p = one("SELECT name FROM projects WHERE id=?", (pid,))
        if not p:
            await send(chat_id, f"Проєкт #{pid} не знайдено")
            return
        try:
            with tx() as c:
                c.execute("INSERT OR IGNORE INTO tg_subscribers(chat_id, username, project_id) VALUES (?,?,?)",
                          (chat_id, username, pid))
            await send(chat_id, f"✅ Підписано на <b>{_escape(p['name'])}</b>.\nЗвіти після кожного зйому.")
        except Exception as e:
            await send(chat_id, f"Помилка: {_escape(e)}")

    elif text.startswith("/unsub"):
        parts = text.split()
        if len(parts) < 2 or not parts[1].isdigit():
            await send(chat_id, "Використання: <code>/unsub project_id</code>")
            return
        pid = int(parts[1])
        with tx() as c:
            c.execute("DELETE FROM tg_subscribers WHERE chat_id=? AND project_id=?", (chat_id, pid))
        await send(chat_id, "✅ Відписано")

    elif text.startswith("/my"):
        subs = query("""SELECT s.project_id, p.name FROM tg_subscribers s
                        JOIN projects p ON p.id=s.project_id WHERE s.chat_id=?""", (chat_id,))
        if not subs:
            await send(chat_id, "Немає активних підписок. Напиши /start")
        else:
            t = "<b>Твої підписки:</b>\n"
            for s in subs:
                t += f"• <code>{s['project_id']}</code> — {_escape(s['name'])}\n"
            await send(chat_id, t)

    elif text.startswith("/now"):
        parts = text.split()
        if len(parts) < 2 or not parts[1].isdigit():
            await send(chat_id, "Використання: <code>/now project_id</code>")
            return
        pid = int(parts[1])
        snap = one("SELECT id FROM snapshots WHERE project_id=? AND status='done' ORDER BY id DESC LIMIT 1", (pid,))
        if not snap:
            await send(chat_id, "Немає завершених зйомів по цьому проєкту")
            return
        prev = one("SELECT id FROM snapshots WHERE project_id=? AND status='done' AND id<? ORDER BY id DESC LIMIT 1",
                   (pid, snap['id']))
        proj = one("SELECT * FROM projects WHERE id=?", (pid,))
        targets = query("SELECT * FROM targets WHERE project_id=? AND active=1", (pid,))
        for tgt in targets:
            try:
                await send(chat_id, format_report(proj, tgt, snap['id'], prev['id'] if prev else None))
            except Exception as e:
                print(f"report err: {e}")


async def poll_loop():
    if not TOKEN:
        print("TG_BOT_TOKEN not set — bot disabled")
        return
    offset = 0
    print("TG bot: polling started")
    while True:
        try:
            r = await tg_api("getUpdates", offset=offset, timeout=25)
            if r and r.get("ok"):
                for u in r.get("result", []):
                    offset = u["update_id"] + 1
                    try:
                        await handle_update(u)
                    except Exception as e:
                        print(f"update handler err: {e}")
        except Exception as e:
            print(f"tg poll err: {e}")
            await asyncio.sleep(5)
