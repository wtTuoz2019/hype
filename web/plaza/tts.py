"""服务端中文语音：浏览器 speechSynthesis 在 Chrome 上经常无声，改走 edge-tts。"""

from __future__ import annotations

import asyncio
import hashlib
import threading
import time
from pathlib import Path

from django.conf import settings

VOICE = "zh-CN-XiaoxiaoNeural"
MAX_CHARS = 80
_CACHE_TTL = 6 * 3600
_CACHE_LOCK = threading.Lock()
_MEM: dict[str, tuple[float, bytes]] = {}
_LAST_PURGE = 0.0
_PURGE_EVERY = 15 * 60  # 最多每 15 分钟扫盘清一次


def _cache_dir() -> Path:
    try:
        base = Path(settings.BASE_DIR)
    except Exception:  # noqa: BLE001
        base = Path(__file__).resolve().parents[1]
    root = base / "data" / "tts_cache"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _key(text: str) -> str:
    return hashlib.sha256(f"{VOICE}\n{text}".encode("utf-8")).hexdigest()[:32]


def purge_tts_cache(*, force: bool = False) -> int:
    """删除过期 mp3；返回删除数量。合成时顺带调用，无需单独 cron。"""
    global _LAST_PURGE
    now = time.time()
    if not force and now - _LAST_PURGE < _PURGE_EVERY:
        return 0
    _LAST_PURGE = now
    removed = 0
    root = _cache_dir()
    for path in root.glob("*.mp3"):
        try:
            age = now - path.stat().st_mtime
            if age >= _CACHE_TTL:
                path.unlink(missing_ok=True)
                removed += 1
        except OSError:
            continue
    with _CACHE_LOCK:
        dead = [key for key, (ts, _) in _MEM.items() if now - ts >= _CACHE_TTL]
        for key in dead:
            _MEM.pop(key, None)
    return removed


async def _edge_bytes(text: str) -> bytes:
    import edge_tts

    communicate = edge_tts.Communicate(text, VOICE)
    parts: list[bytes] = []
    async for chunk in communicate.stream():
        if chunk.get("type") == "audio" and chunk.get("data"):
            parts.append(chunk["data"])
    return b"".join(parts)


def _synthesize_uncached(text: str) -> bytes:
    return asyncio.run(_edge_bytes(text))


def synthesize(text: str) -> bytes:
    cleaned = " ".join(str(text or "").split())
    if not cleaned:
        raise ValueError("empty")
    if len(cleaned) > MAX_CHARS:
        cleaned = cleaned[:MAX_CHARS]
    key = _key(cleaned)
    now = time.time()
    purge_tts_cache()
    with _CACHE_LOCK:
        hit = _MEM.get(key)
        if hit and now - hit[0] < _CACHE_TTL:
            return hit[1]
    path = _cache_dir() / f"{key}.mp3"
    if path.exists() and now - path.stat().st_mtime < _CACHE_TTL:
        data = path.read_bytes()
        with _CACHE_LOCK:
            _MEM[key] = (now, data)
        return data

    def _job() -> bytes:
        return _synthesize_uncached(cleaned)

    # 独立线程 + asyncio.run，避免跟 Django/gunicorn 事件循环冲突
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        data = pool.submit(_job).result(timeout=40)
    if not data:
        raise RuntimeError("empty audio")
    try:
        path.write_bytes(data)
    except OSError:
        pass
    with _CACHE_LOCK:
        _MEM[key] = (now, data)
        if len(_MEM) > 300:
            oldest = sorted(_MEM.items(), key=lambda item: item[1][0])[:80]
            for dead, _ in oldest:
                _MEM.pop(dead, None)
    return data
