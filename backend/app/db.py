from __future__ import annotations
import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

from app.config import DB_PATH
from app import config as _config_mod

SCHEMA = """
CREATE TABLE IF NOT EXISTS lines (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS shifts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  business_day TEXT NOT NULL,
  name TEXT NOT NULL,
  sealed INTEGER NOT NULL DEFAULT 0,
  sealed_at TEXT,
  UNIQUE(business_day, name)
);
CREATE TABLE IF NOT EXISTS chutes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  line_id INTEGER NOT NULL,
  code TEXT NOT NULL,
  title TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT '口门开',
  soft_cap INTEGER NOT NULL DEFAULT 200,
  UNIQUE(line_id, code),
  FOREIGN KEY(line_id) REFERENCES lines(id)
);
CREATE TABLE IF NOT EXISTS route_rules (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  dest_code TEXT NOT NULL UNIQUE,
  chute_id INTEGER NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  FOREIGN KEY(chute_id) REFERENCES chutes(id)
);
CREATE TABLE IF NOT EXISTS scans (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  barcode TEXT NOT NULL,
  dest_code TEXT NOT NULL DEFAULT '',
  shift_id INTEGER NOT NULL,
  line_id INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT '待路由',
  chute_id INTEGER,
  bag_id INTEGER,
  divert_batch_id INTEGER,
  scanned_at TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  FOREIGN KEY(shift_id) REFERENCES shifts(id),
  FOREIGN KEY(line_id) REFERENCES lines(id),
  FOREIGN KEY(chute_id) REFERENCES chutes(id)
);
CREATE TABLE IF NOT EXISTS route_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  scan_id INTEGER NOT NULL,
  chute_id INTEGER NOT NULL,
  action TEXT NOT NULL,
  created_at TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  FOREIGN KEY(scan_id) REFERENCES scans(id),
  FOREIGN KEY(chute_id) REFERENCES chutes(id)
);
CREATE TABLE IF NOT EXISTS divert_batches (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  shift_id INTEGER NOT NULL,
  code TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL DEFAULT '回流中',
  created_at TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  FOREIGN KEY(shift_id) REFERENCES shifts(id)
);
CREATE TABLE IF NOT EXISTS bags (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  shift_id INTEGER NOT NULL,
  chute_id INTEGER NOT NULL,
  code TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL DEFAULT '集包中',
  sealed_at TEXT,
  note TEXT NOT NULL DEFAULT '',
  FOREIGN KEY(shift_id) REFERENCES shifts(id),
  FOREIGN KEY(chute_id) REFERENCES chutes(id)
);
CREATE TABLE IF NOT EXISTS audit_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  ref_id INTEGER,
  action TEXT NOT NULL,
  created_at TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chute_capacity_ledger (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  chute_id INTEGER NOT NULL,
  scan_id INTEGER,
  delta INTEGER NOT NULL DEFAULT 0,
  kind TEXT NOT NULL,
  policy TEXT NOT NULL DEFAULT '硬拒',
  cap_snapshot INTEGER,
  count_before INTEGER,
  count_after INTEGER NOT NULL DEFAULT 0,
  over_cap INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  FOREIGN KEY(chute_id) REFERENCES chutes(id)
);
CREATE INDEX IF NOT EXISTS idx_cap_ledger_chute ON chute_capacity_ledger(chute_id, id);
"""

# 进程内已经补齐过表结构的数据库路径，避免每次连接都跑 DDL。
_prepared_paths: set[str] = set()
_prepare_lock = threading.Lock()


def resolved_db_path() -> Path:
    """运行期解析数据库路径。

    优先级：
    1. SORTBAY_DB 环境变量（测试常在 import 后用 monkeypatch.setenv 改写）；
    2. 测试直接 monkeypatch 的 ``app.db.DB_PATH``；
    3. ``app.config.DB_PATH``（同样兼容 monkeypatch app.config.DB_PATH）。
    """
    env = os.environ.get("SORTBAY_DB")
    if env:
        return Path(env)
    db_global = globals().get("DB_PATH")
    if db_global is not None and Path(db_global) != Path(_IMPORT_TIME_DB_PATH):
        return Path(db_global)
    return Path(_config_mod.DB_PATH)


_IMPORT_TIME_DB_PATH = DB_PATH


def connect() -> sqlite3.Connection:
    path = resolved_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=15000")
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    """对当前连接指向的库幂等补齐表结构（含容量账本）并播种基线数据。

    测试常在 import app 之后用 monkeypatch/环境变量把 SORTBAY_DB 指向全新的
    tmp 文件，甚至在每个用例前删除同一个库文件。因此这里不能只依赖“本进程
    是否已准备过该路径”的内存标记：每次都用 sqlite_master 探活，缺表（或文件
    被删后重建）时在进程锁内二次确认并补齐，保证换库/删库后立刻可用。
    """
    key = str(resolved_db_path())

    def schema_present() -> bool:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM sqlite_master "
            "WHERE type='table' AND name='chute_capacity_ledger'"
        ).fetchone()
        return int(row["c"]) == 1

    try:
        if schema_present():
            _prepared_paths.add(key)
            return
    except sqlite3.Error:
        # 例如文件刚被删除/目录不可见：走下面的加锁重建。
        pass

    with _prepare_lock:
        try:
            present = schema_present()
        except sqlite3.Error:
            present = False
        if not present:
            conn.executescript(SCHEMA)
            row = conn.execute("SELECT COUNT(*) AS c FROM lines").fetchone()
            if int(row["c"]) == 0:
                from app.seed import seed_conn

                seed_conn(conn)
            # 立即提交种子，避免并发首连时其它连接看不到未提交的基线。
            conn.commit()
    _prepared_paths.add(key)


@contextmanager
def db():
    conn = connect()
    try:
        ensure_schema(conn)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def write_immediate(max_attempts: int = 25):
    """以 BEGIN IMMEDIATE 开启串行写事务。

    并发双落入时，后到者会在写锁上等待，拿到锁后重新读取在口数与软容量，
    从而保证“检查容量 + 写入落入”是原子的，不会双破容量。
    """
    import time

    conn = connect()
    conn.isolation_level = None  # 关闭 sqlite3 隐式事务，显式 BEGIN
    try:
        ensure_schema(conn)
        delay = 0.004
        last_err: Exception | None = None
        for _ in range(max_attempts):
            try:
                conn.execute("BEGIN IMMEDIATE")
                last_err = None
                break
            except sqlite3.OperationalError as exc:  # database is locked/busy
                last_err = exc
                text = str(exc).lower()
                if "locked" not in text and "busy" not in text:
                    raise
                time.sleep(delay)
                delay = min(delay * 1.6, 0.2)
        if last_err is not None:
            raise last_err
        try:
            yield conn
            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
    finally:
        conn.close()


def init_db() -> None:
    with db() as conn:
        conn.executescript(SCHEMA)


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None


def rows_to_list(rows) -> list[dict]:
    return [dict(r) for r in rows]
