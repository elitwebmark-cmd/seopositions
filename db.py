"""SQLite layer — raw, no ORM."""
import sqlite3, os, json
from contextlib import contextmanager
from pathlib import Path

DB_PATH = os.environ.get("DB_PATH", "/data/ranktracker.db")
Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  domain TEXT NOT NULL,
  gl TEXT DEFAULT 'ua',
  hl TEXT DEFAULT 'uk',
  location TEXT DEFAULT 'Ukraine',
  device TEXT DEFAULT 'desktop',
  schedule_cron TEXT DEFAULT '0 6 * * *',
  active INTEGER DEFAULT 1,
  created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS keywords(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  keyword TEXT NOT NULL,
  tag TEXT DEFAULT '',
  volume INTEGER DEFAULT 0,
  active INTEGER DEFAULT 1,
  UNIQUE(project_id, keyword)
);
CREATE TABLE IF NOT EXISTS snapshots(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  started_at TEXT DEFAULT (datetime('now')),
  finished_at TEXT,
  status TEXT DEFAULT 'running',
  total INTEGER DEFAULT 0,
  done INTEGER DEFAULT 0,
  error TEXT
);
CREATE TABLE IF NOT EXISTS targets(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  location TEXT NOT NULL,
  hl TEXT NOT NULL DEFAULT 'uk',
  label TEXT DEFAULT '',
  active INTEGER DEFAULT 1,
  UNIQUE(project_id, location, hl)
);
CREATE TABLE IF NOT EXISTS rankings(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
  keyword_id INTEGER NOT NULL REFERENCES keywords(id) ON DELETE CASCADE,
  target_id INTEGER REFERENCES targets(id) ON DELETE CASCADE,
  position INTEGER,
  url TEXT,
  title TEXT,
  serp_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_rank_snap ON rankings(snapshot_id);
CREATE INDEX IF NOT EXISTS idx_rank_kw ON rankings(keyword_id);
CREATE INDEX IF NOT EXISTS idx_rank_tgt ON rankings(target_id);
CREATE INDEX IF NOT EXISTS idx_snap_proj ON snapshots(project_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_targets_proj ON targets(project_id);
"""

# Ukrainian regional capitals — Google Ads Geo Targets canonical format
UA_LOCATIONS = [
    ("Ukraine", "Україна (загалом)"),
    ("Kyiv, Kyiv city, Ukraine", "Київ"),
    ("Kharkiv, Kharkiv Oblast, Ukraine", "Харків"),
    ("Odesa, Odesa Oblast, Ukraine", "Одеса"),
    ("Dnipro, Dnipropetrovsk Oblast, Ukraine", "Дніпро"),
    ("Donetsk, Donetsk Oblast, Ukraine", "Донецьк"),
    ("Zaporizhzhia, Zaporizhzhia Oblast, Ukraine", "Запоріжжя"),
    ("Lviv, Lviv Oblast, Ukraine", "Львів"),
    ("Kryvyi Rih, Dnipropetrovsk Oblast, Ukraine", "Кривий Ріг"),
    ("Mykolaiv, Mykolaiv Oblast, Ukraine", "Миколаїв"),
    ("Sevastopol, Sevastopol city, Ukraine", "Севастополь"),
    ("Mariupol, Donetsk Oblast, Ukraine", "Маріуполь"),
    ("Luhansk, Luhansk Oblast, Ukraine", "Луганськ"),
    ("Vinnytsia, Vinnytsia Oblast, Ukraine", "Вінниця"),
    ("Simferopol, Autonomous Republic of Crimea, Ukraine", "Сімферополь"),
    ("Kherson, Kherson Oblast, Ukraine", "Херсон"),
    ("Chernihiv, Chernihiv Oblast, Ukraine", "Чернігів"),
    ("Poltava, Poltava Oblast, Ukraine", "Полтава"),
    ("Cherkasy, Cherkasy Oblast, Ukraine", "Черкаси"),
    ("Khmelnytskyi, Khmelnytskyi Oblast, Ukraine", "Хмельницький"),
    ("Chernivtsi, Chernivtsi Oblast, Ukraine", "Чернівці"),
    ("Zhytomyr, Zhytomyr Oblast, Ukraine", "Житомир"),
    ("Sumy, Sumy Oblast, Ukraine", "Суми"),
    ("Rivne, Rivne Oblast, Ukraine", "Рівне"),
    ("Ivano-Frankivsk, Ivano-Frankivsk Oblast, Ukraine", "Івано-Франківськ"),
    ("Kropyvnytskyi, Kirovohrad Oblast, Ukraine", "Кропивницький"),
    ("Ternopil, Ternopil Oblast, Ukraine", "Тернопіль"),
    ("Lutsk, Volyn Oblast, Ukraine", "Луцьк"),
    ("Uzhhorod, Zakarpattia Oblast, Ukraine", "Ужгород"),
]

LANGUAGES = [
    ("uk", "Українська"),
    ("ru", "Російська"),
    ("en", "English"),
    ("pl", "Polska"),
    ("de", "Deutsch"),
]

def conn():
    c = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    c.execute("PRAGMA journal_mode=WAL")
    return c

@contextmanager
def tx():
    c = conn()
    try:
        yield c
        c.commit()
    finally:
        c.close()

def init_db():
    with tx() as c:
        c.executescript(SCHEMA)
        # Migration: add target_id column if missing (for pre-targets installs)
        cols = [r[1] for r in c.execute("PRAGMA table_info(rankings)").fetchall()]
        if "target_id" not in cols:
            c.execute("ALTER TABLE rankings ADD COLUMN target_id INTEGER REFERENCES targets(id)")
        # Auto-create a default target per project (from project's location/hl) if none
        for p in c.execute("SELECT id, location, hl FROM projects").fetchall():
            existing = c.execute("SELECT id FROM targets WHERE project_id=?", (p[0],)).fetchone()
            if not existing:
                label = (p[1] or "Ukraine").split(",")[0]
                c.execute(
                    "INSERT INTO targets(project_id, location, hl, label) VALUES (?,?,?,?)",
                    (p[0], p[1] or "Ukraine", p[2] or "uk", label),
                )
                tid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
                # Back-fill existing rankings for this project's snapshots
                c.execute("""
                    UPDATE rankings SET target_id=?
                    WHERE target_id IS NULL AND snapshot_id IN (
                        SELECT id FROM snapshots WHERE project_id=?
                    )
                """, (tid, p[0]))

def query(sql, params=()):
    c = conn()
    try:
        return [dict(r) for r in c.execute(sql, params).fetchall()]
    finally:
        c.close()

def one(sql, params=()):
    rs = query(sql, params)
    return rs[0] if rs else None

def execute(sql, params=()):
    with tx() as c:
        cur = c.execute(sql, params)
        return cur.lastrowid
