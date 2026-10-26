#!/usr/bin/env python3
"""监控钱包和管理员账号。存在 hype/data/hype.db。

每次改钱包都会同步写出 hype/data/wallets.json，信号进程读的是同一份名单。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
STORE_PATH = DATA_DIR / "wallets.json"
DB_PATH = DATA_DIR / "hype.db"

_LOCK = threading.Lock()
_READY = False


def norm_address(addr: str) -> str:
    text = (addr or "").strip().lower()
    if not (
        text.startswith("0x")
        and len(text) == 42
        and all(c in "0123456789abcdef" for c in text[2:])
    ):
        raise ValueError(f"地址格式不对: {addr}")
    return text


def _make_id(address: str) -> str:
    return "hl-" + address[2:10]


def _connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _load_json_wallets() -> list[dict]:
    if not STORE_PATH.exists():
        return []
    try:
        raw = json.loads(STORE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows = raw.get("wallets") if isinstance(raw, dict) else None
    if not isinstance(rows, list):
        return []
    out: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            address = norm_address(str(row.get("address") or ""))
        except ValueError:
            continue
        out.append(
            {
                "id": str(row.get("id") or _make_id(address)),
                "name": str(row.get("name") or "").strip(),
                "address": address,
                "enabled": bool(row.get("enabled", True)),
                "created_ms": int(row.get("created_ms") or 0),
            }
        )
    return out


def _row_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "address": row["address"],
        "enabled": bool(row["enabled"]),
        "want_copy": bool(row["want_copy"]) if "want_copy" in row.keys() else False,
        "created_ms": int(row["created_ms"] or 0),
    }


def _mirror(conn: sqlite3.Connection) -> None:
    rows = [_row_dict(row) for row in conn.execute("SELECT * FROM wallet ORDER BY created_ms, address")]
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps({"wallets": rows}, ensure_ascii=False, indent=2) + "\n"
    tmp = STORE_PATH.with_suffix(".json.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, STORE_PATH)


def init_db() -> None:
    global _READY
    if _READY and DB_PATH.exists():
        return
    with _LOCK:
        if _READY and DB_PATH.exists():
            return
        conn = _connect()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS admin (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    username TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    created_ms INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS wallet (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL DEFAULT '',
                    address TEXT NOT NULL UNIQUE,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_ms INTEGER NOT NULL
                );
                """
            )
            cols = [info[1] for info in conn.execute("PRAGMA table_info(wallet)")]
            if "want_copy" not in cols:
                conn.execute(
                    "ALTER TABLE wallet ADD COLUMN want_copy INTEGER NOT NULL DEFAULT 0"
                )
            count = conn.execute("SELECT COUNT(*) FROM wallet").fetchone()[0]
            if count == 0:
                for row in _load_json_wallets():
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO wallet (id, name, address, enabled, created_ms)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            row["id"],
                            row["name"],
                            row["address"],
                            1 if row["enabled"] else 0,
                            row["created_ms"] or int(time.time() * 1000),
                        ),
                    )
            conn.commit()
            _mirror(conn)
        finally:
            conn.close()
        _READY = True


def load_wallets(path: Path = STORE_PATH) -> list[dict]:
    del path
    init_db()
    with _LOCK:
        conn = _connect()
        try:
            return [_row_dict(row) for row in conn.execute("SELECT * FROM wallet ORDER BY created_ms, address")]
        finally:
            conn.close()


def save_wallets(wallets: list[dict], path: Path = STORE_PATH) -> None:
    del path
    init_db()
    with _LOCK:
        conn = _connect()
        try:
            conn.execute("DELETE FROM wallet")
            for row in wallets:
                address = norm_address(str(row.get("address") or ""))
                conn.execute(
                    """
                    INSERT INTO wallet (id, name, address, enabled, want_copy, created_ms)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(row.get("id") or _make_id(address)),
                        str(row.get("name") or "").strip(),
                        address,
                        1 if row.get("enabled", True) else 0,
                        1 if row.get("want_copy") else 0,
                        int(row.get("created_ms") or int(time.time() * 1000)),
                    ),
                )
            conn.commit()
            _mirror(conn)
        finally:
            conn.close()


def find_wallet(wallets: list[dict], key: str) -> dict | None:
    text = key.strip().lower()
    try:
        address = norm_address(text)
    except ValueError:
        address = ""
    for row in wallets:
        if row["id"] == key.strip() or row["address"] == address or row["id"] == text:
            return row
    return None


def add_wallet(address: str, name: str = "", path: Path = STORE_PATH) -> tuple[dict, bool]:
    del path
    addr = norm_address(address)
    init_db()
    with _LOCK:
        conn = _connect()
        try:
            found = conn.execute("SELECT * FROM wallet WHERE address = ?", (addr,)).fetchone()
            if found is not None:
                row = _row_dict(found)
                if name.strip() and row["name"] != name.strip():
                    conn.execute("UPDATE wallet SET name = ? WHERE address = ?", (name.strip(), addr))
                    conn.commit()
                    row["name"] = name.strip()
                    _mirror(conn)
                return row, False
            row = {
                "id": _make_id(addr),
                "name": name.strip(),
                "address": addr,
                "enabled": True,
                "created_ms": int(time.time() * 1000),
            }
            conn.execute(
                """
                INSERT INTO wallet (id, name, address, enabled, created_ms)
                VALUES (?, ?, ?, ?, ?)
                """,
                (row["id"], row["name"], row["address"], 1, row["created_ms"]),
            )
            conn.commit()
            _mirror(conn)
            return row, True
        finally:
            conn.close()


def set_enabled(key: str, enabled: bool, path: Path = STORE_PATH) -> dict:
    del path
    init_db()
    with _LOCK:
        conn = _connect()
        try:
            rows = [_row_dict(row) for row in conn.execute("SELECT * FROM wallet")]
            row = find_wallet(rows, key)
            if row is None:
                raise KeyError(key)
            conn.execute(
                "UPDATE wallet SET enabled = ? WHERE address = ?",
                (1 if enabled else 0, row["address"]),
            )
            conn.commit()
            row["enabled"] = enabled
            _mirror(conn)
            return row
        finally:
            conn.close()


def set_want_copy(key: str, want: bool, path: Path = STORE_PATH) -> dict:
    del path
    init_db()
    with _LOCK:
        conn = _connect()
        try:
            rows = [_row_dict(row) for row in conn.execute("SELECT * FROM wallet")]
            row = find_wallet(rows, key)
            if row is None:
                raise KeyError(key)
            conn.execute(
                "UPDATE wallet SET want_copy = ? WHERE address = ?",
                (1 if want else 0, row["address"]),
            )
            conn.commit()
            row["want_copy"] = want
            _mirror(conn)
            return row
        finally:
            conn.close()


def remove_wallet(key: str, path: Path = STORE_PATH) -> dict:
    del path
    init_db()
    with _LOCK:
        conn = _connect()
        try:
            rows = [_row_dict(row) for row in conn.execute("SELECT * FROM wallet")]
            row = find_wallet(rows, key)
            if row is None:
                raise KeyError(key)
            conn.execute("DELETE FROM wallet WHERE address = ?", (row["address"],))
            conn.commit()
            _mirror(conn)
            return row
        finally:
            conn.close()


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)
    return salt.hex() + "$" + digest.hex()


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, digest_hex = stored.split("$", 1)
        salt = bytes.fromhex(salt_hex)
    except (ValueError, TypeError):
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)
    return hmac.compare_digest(digest.hex(), digest_hex)


def has_admin() -> bool:
    init_db()
    with _LOCK:
        conn = _connect()
        try:
            return conn.execute("SELECT 1 FROM admin WHERE id = 1").fetchone() is not None
        finally:
            conn.close()


def create_admin(username: str, password: str) -> None:
    name = username.strip()
    if len(name) < 2 or len(name) > 32:
        raise ValueError("管理员账号需要 2 到 32 个字符")
    if any(ch.isspace() for ch in name):
        raise ValueError("管理员账号不能包含空格")
    if len(password) < 8:
        raise ValueError("密码至少 8 位")
    init_db()
    with _LOCK:
        conn = _connect()
        try:
            if conn.execute("SELECT 1 FROM admin WHERE id = 1").fetchone() is not None:
                raise ValueError("管理员账号已经设置过了")
            conn.execute(
                "INSERT INTO admin (id, username, password_hash, created_ms) VALUES (1, ?, ?, ?)",
                (name, hash_password(password), int(time.time() * 1000)),
            )
            conn.commit()
        finally:
            conn.close()


def check_admin(username: str, password: str) -> bool:
    init_db()
    with _LOCK:
        conn = _connect()
        try:
            row = conn.execute("SELECT username, password_hash FROM admin WHERE id = 1").fetchone()
        finally:
            conn.close()
    if row is None:
        return False
    if row["username"] != username.strip():
        return False
    return verify_password(password, row["password_hash"])
