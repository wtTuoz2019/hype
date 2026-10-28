"""OKX 钱包登录：服务端发 nonce，校验 personal_sign，不索取私钥。"""

from __future__ import annotations

import re
import secrets
from datetime import timedelta

from django.utils import timezone
from eth_account import Account
from eth_account.messages import encode_defunct

from .models import WalletLoginNonce

ADDR_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
NONCE_TTL = timedelta(minutes=10)


def norm_address(value: str) -> str:
    address = (value or "").strip()
    if not ADDR_RE.match(address):
        raise ValueError("invalid address")
    return address.lower()


def short_address(address: str) -> str:
    address = (address or "").strip()
    if len(address) < 12:
        return address
    return f"{address[:6]}…{address[-4:]}"


def build_login_message(*, host: str, address: str, nonce: str, issued_at: str) -> str:
    return (
        "AlphaScope 登录\n"
        "\n"
        "请签名以证明你拥有该 OKX 钱包。不会发起转账，也不会索取助记词或私钥。\n"
        "\n"
        f"域名: {host}\n"
        f"地址: {address}\n"
        f"随机码: {nonce}\n"
        f"时间: {issued_at}"
    )


def issue_nonce(address: str) -> WalletLoginNonce:
    address = norm_address(address)
    now = timezone.now()
    WalletLoginNonce.objects.filter(address=address, used=False, expires_at__lt=now).delete()
    return WalletLoginNonce.objects.create(
        address=address,
        nonce=secrets.token_hex(16),
        expires_at=now + NONCE_TTL,
    )


def recover_address(message: str, signature: str) -> str:
    sig = (signature or "").strip()
    if not sig.startswith("0x"):
        sig = "0x" + sig
    recovered = Account.recover_message(encode_defunct(text=message), signature=sig)
    return norm_address(recovered)


def consume_nonce(address: str, nonce: str) -> WalletLoginNonce | None:
    address = norm_address(address)
    row = (
        WalletLoginNonce.objects.filter(address=address, nonce=nonce, used=False)
        .order_by("-created_at")
        .first()
    )
    if row is None:
        return None
    if row.expires_at < timezone.now():
        return None
    row.used = True
    row.save(update_fields=["used"])
    return row
