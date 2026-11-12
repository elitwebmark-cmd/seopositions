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
CREATE TABLE IF NOT EXISTS rankings(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
  keyword_id INTEGER NOT NULL REFERENCES keywords(id) ON DELETE CASCADE,
  position INTEGER,
  url TEXT,
  title TEXT,
  serp_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_rank_snap ON rankings(snapshot_id);
CREATE INDEX IF NOT EXISTS idx_rank_kw ON rankings(keyword_id);
CREATE INDEX IF NOT EXISTS idx_snap_proj ON snapshots(project_id, started_at DESC);
"""

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
