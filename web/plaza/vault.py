"""交易所密钥加密。主密钥只放环境变量或本机文件，不进页面，也不进仓库。"""

from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings

_SALT = b"hype-exchange-v1"


def _master_material() -> str:
    raw = os.environ.get("HYPE_MASTER_KEY", "").strip()
    if raw:
        return raw
    path = Path(settings.BASE_DIR) / "data" / "master.key"
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = Fernet.generate_key().decode()
    path.write_text(raw, encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return raw


def _fernet() -> Fernet:
    raw = _master_material()
    try:
        return Fernet(raw.encode())
    except (ValueError, TypeError):
        digest = hashlib.pbkdf2_hmac("sha256", raw.encode(), _SALT, 200_000, dklen=32)
        return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(text: str) -> str:
    return _fernet().encrypt(text.encode()).decode()


def decrypt(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("密钥无法解密") from exc
