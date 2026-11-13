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


THRESHOLDS = [3, 5, 10, 100]  # TOP tiers we report crossings for

def _delta_str(p, c):
    if p is None and c is None: return ""
    if p is None: return f"нова → #{c}"
    if c is None: return f"#{p} → вилетіла"
    d = p - c
    return f"#{p} → #{c} ({'+' if d>0 else ''}{d})"


def _chunk_msgs(lines, header="", max_len=3800):
    """Split long content into multiple messages by lines. Header repeated on each."""
    out = []; cur = header
    for ln in lines:
        if len(cur) + len(ln) + 1 > max_len:
            out.append(cur)
            cur = header + ln + "\n"
        else:
            cur += ln + "\n"
    if cur.strip() and cur != header:
        out.append(cur)
    return out


def format_report(project, target, snap_id, prev_snap_id=None):
    """Return a LIST of messages (telegram-safe chunks)."""
    # Current rankings with keyword + tag
    rows = query("""
        SELECT k.id kid, k.keyword, k.tag, k.volume, r.position
        FROM keywords k
        LEFT JOIN rankings r ON r.keyword_id=k.id AND r.snapshot_id=? AND r.target_id=?
        WHERE k.project_id=? AND k.active=1
    """, (snap_id, target['id'], project['id']))
    cur_map = {r['kid']: r['position'] for r in rows}
    prev_map = {}
    if prev_snap_id:
        for r in query("SELECT keyword_id, position FROM rankings WHERE snapshot_id=? AND target_id=?",
                       (prev_snap_id, target['id'])):
            prev_map[r['keyword_id']] = r['position']

    # Aggregate KPIs
    total = len(rows)
    def in_top(n, pos_map): return sum(1 for p in pos_map.values() if p is not None and p <= n)
    cur_kpi = {t: in_top(t, cur_map) for t in [3,5,10,20,50,100]}
    prev_kpi = {t: in_top(t, prev_map) for t in [3,5,10,20,50,100]}
    def avg(pos_map):
        vals = [p for p in pos_map.values() if p is not None]
        return sum(vals)/len(vals) if vals else None
    cur_avg = avg(cur_map); prev_avg = avg(prev_map)

    # ---- Message 1: Header + KPIs ----
    hdr = f"<b>📊 {_escape(project['name'])}</b> · {_escape(target['label'])} · <code>{target['hl'].upper()}</code>\n<i>Зйом #{snap_id}</i>\n\n"
    def dlt(c, p):
        if not p: return ""
        d = c - p
        if d == 0: return ""
        return f" <b>{'+' if d>0 else ''}{d}</b>"
    m1 = hdr + "🏆 <b>Розподіл по ТОПу:</b>\n"
    m1 += f"🥇 ТОП 3: <b>{cur_kpi[3]}</b>{dlt(cur_kpi[3], prev_kpi.get(3,0))}\n"
    m1 += f"🏅 ТОП 5: <b>{cur_kpi[5]}</b>{dlt(cur_kpi[5], prev_kpi.get(5,0))}\n"
    m1 += f"🎯 ТОП 10: <b>{cur_kpi[10]}</b>{dlt(cur_kpi[10], prev_kpi.get(10,0))}\n"
    m1 += f"📈 ТОП 20: <b>{cur_kpi[20]}</b>{dlt(cur_kpi[20], prev_kpi.get(20,0))}\n"
    m1 += f"📉 ТОП 50: <b>{cur_kpi[50]}</b>{dlt(cur_kpi[50], prev_kpi.get(50,0))}\n"
    m1 += f"👀 ТОП 100: <b>{cur_kpi[100]}</b> / {total}{dlt(cur_kpi[100], prev_kpi.get(100,0))}\n"
    if cur_avg:
        avg_d = ""
        if prev_avg:
            d = cur_avg - prev_avg
            if abs(d) > 0.1: avg_d = f" ({'+' if d>0 else ''}{d:.1f})"
        m1 += f"\n📊 Середня позиція: <b>{cur_avg:.1f}</b>{avg_d}"
    if PUBLIC_URL:
        m1 += f'\n\n🔗 <a href="{PUBLIC_URL}/?project_id={project["id"]}">Дешборд</a>'
    messages = [m1]

    if not prev_snap_id:
        messages.append("ℹ️ <i>Це перший зйом — порівняння буде доступне після наступного запуску.</i>")
        return messages

    # ---- Classify crossings ----
    entered = {t: [] for t in THRESHOLDS}  # entered TOP T (ранкуються краще)
    left = {t: [] for t in THRESHOLDS}     # left TOP T (ранкуються гірше)
    # map kid → (keyword, tag)
    kw_info = {r['kid']: (r['keyword'], r['tag'] or '') for r in rows}

    for kid, c in cur_map.items():
        p = prev_map.get(kid)
        if c == p: continue
        kw, tag = kw_info.get(kid, (f"#{kid}", ''))
        entry = (kw, tag, p, c)
        # entered: smallest T that newly contains
        for t in THRESHOLDS:
            if c is not None and c <= t and (p is None or p > t):
                entered[t].append(entry)
                break
        # left: largest T that newly doesn't contain
        for t in reversed(THRESHOLDS):
            if p is not None and p <= t and (c is None or c > t):
                left[t].append(entry)
                break

    # ---- Message 2: Entered TOPs ----
    entered_total = sum(len(v) for v in entered.values())
    if entered_total:
        lines = []
        for t, label, icon in [(3, "ТОП 3", "🥇"), (5, "ТОП 5", "🏅"), (10, "ТОП 10", "🎯"), (100, "ТОП 100", "🆕")]:
            lst = entered[t]
            if not lst: continue
            lines.append(f"\n<b>{icon} Зайшли в {label} ({len(lst)}):</b>")
            for kw, tag, p, c in lst[:20]:
                tag_s = f" <code>[{_escape(tag)}]</code>" if tag else ""
                lines.append(f"• {_escape(kw)}{tag_s} — {_delta_str(p, c)}")
            if len(lst) > 20:
                lines.append(f"<i>…+{len(lst)-20} запитів</i>")
        head = hdr + f"🟢 <b>РОСТ ({entered_total} запитів)</b>\n"
        messages.extend(_chunk_msgs(lines, head))

    # ---- Message 3: Left TOPs ----
    left_total = sum(len(v) for v in left.values())
    if left_total:
        lines = []
        for t, label, icon in [(3, "ТОП 3", "🥇"), (5, "ТОП 5", "🏅"), (10, "ТОП 10", "🎯"), (100, "ТОП 100", "💥")]:
            lst = left[t]
            if not lst: continue
            lines.append(f"\n<b>{icon} Вилетіли з {label} ({len(lst)}):</b>")
            for kw, tag, p, c in lst[:20]:
                tag_s = f" <code>[{_escape(tag)}]</code>" if tag else ""
                lines.append(f"• {_escape(kw)}{tag_s} — {_delta_str(p, c)}")
            if len(lst) > 20:
                lines.append(f"<i>…+{len(lst)-20} запитів</i>")
        head = hdr + f"🔴 <b>ПАДІННЯ ({left_total} запитів)</b>\n"
        messages.extend(_chunk_msgs(lines, head))

    # ---- Message 4: By tag ----
    tag_stats = {}
    for r in rows:
        tag = r['tag'] or 'без тегу'
        st = tag_stats.setdefault(tag, {'count':0, 't3':0, 't10':0, 't100':0, 'pos':[]})
        st['count'] += 1
        p = r['position']
        if p:
            if p <= 3: st['t3'] += 1
            if p <= 10: st['t10'] += 1
            if p <= 100: st['t100'] += 1
            st['pos'].append(p)
    if len(tag_stats) > 1:
        lines = []
        for tag in sorted(tag_stats.keys(), key=lambda x: -tag_stats[x]['t10']):
            s = tag_stats[tag]
            avg_p = sum(s['pos'])/len(s['pos']) if s['pos'] else None
            avg_s = f", сер. <b>{avg_p:.1f}</b>" if avg_p else ""
            lines.append(f"\n<b>[{_escape(tag)}]</b> ({s['count']} зап.)")
            lines.append(f"ТОП 3: <b>{s['t3']}</b> · ТОП 10: <b>{s['t10']}</b> · ТОП 100: <b>{s['t100']}</b>/{s['count']}{avg_s}")
        head = hdr + f"🏷 <b>ПО ТЕГАМ</b>\n"
        messages.extend(_chunk_msgs(lines, head))

    return messages


def format_cross_region(project, targets, snap_id, prev_snap_id=None):
    """Compare metrics across all targets for one snapshot."""
    if len(targets) < 2:
        return None
    lines = []
    lines.append(f"<b>🌍 Порівняння регіонів</b>\n")
    lines.append(f"<i>Зйом #{snap_id}</i>\n")
    # Compute stats per target
    stats = []
    for t in targets:
        pos_rows = query("""SELECT position FROM rankings
                            WHERE snapshot_id=? AND target_id=?""", (snap_id, t['id']))
        vals = [r['position'] for r in pos_rows if r['position'] is not None]
        kpis = {
            't3': sum(1 for v in vals if v <= 3),
            't5': sum(1 for v in vals if v <= 5),
            't10': sum(1 for v in vals if v <= 10),
            't100': len(vals),
            'avg': sum(vals)/len(vals) if vals else None,
            'label': t['label'], 'hl': t['hl'], 'tid': t['id']
        }
        # previous avg for delta
        if prev_snap_id:
            p_rows = query("""SELECT position FROM rankings WHERE snapshot_id=? AND target_id=?""",
                           (prev_snap_id, t['id']))
            p_vals = [r['position'] for r in p_rows if r['position'] is not None]
            kpis['prev_avg'] = sum(p_vals)/len(p_vals) if p_vals else None
            kpis['prev_t10'] = sum(1 for v in p_vals if v <= 10)
        stats.append(kpis)
    stats.sort(key=lambda s: (-(s['t10']), s['avg'] or 999))
    for s in stats:
        avg_s = f"<b>{s['avg']:.1f}</b>" if s['avg'] else "—"
        if s.get('prev_avg') and s['avg']:
            d = s['avg'] - s['prev_avg']
            if abs(d) > 0.1:
                avg_s += f" ({'+' if d>0 else ''}{d:.1f})"
        t10_d = ""
        if s.get('prev_t10') is not None:
            d = s['t10'] - s['prev_t10']
            if d: t10_d = f" ({'+' if d>0 else ''}{d})"
        lines.append(f"\n📍 <b>{_escape(s['label'])}</b> · <code>{s['hl'].upper()}</code>")
        lines.append(f"ТОП 3: <b>{s['t3']}</b> · ТОП 5: <b>{s['t5']}</b> · ТОП 10: <b>{s['t10']}</b>{t10_d}")
        lines.append(f"В індексі: <b>{s['t100']}</b> · Середня: {avg_s}")
    return "\n".join(lines)


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
    cross = format_cross_region(proj, targets, snap_id, prev_id)
    for sub in subs:
        # Cross-region first (overview across all targets)
        if cross and not sub.get('target_id'):
            try: await send(sub['chat_id'], cross)
            except Exception as e: print(f"cross err: {e}")
        for tgt in targets:
            if sub.get('target_id') and sub['target_id'] != tgt['id']:
                continue
            try:
                msgs = format_report(proj, tgt, snap_id, prev_id)
                for m in msgs:
                    await send(sub['chat_id'], m)
                    await asyncio.sleep(0.3)  # don't hit tg rate limit
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
        prev_id = prev['id'] if prev else None
        cross = format_cross_region(proj, targets, snap['id'], prev_id)
        if cross:
            try: await send(chat_id, cross)
            except: pass
        for tgt in targets:
            try:
                for m in format_report(proj, tgt, snap['id'], prev_id):
                    await send(chat_id, m)
                    await asyncio.sleep(0.3)
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
