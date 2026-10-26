#!/usr/bin/env python3
"""监控钱包管理页。

首次打开先设置管理员账号，登录后可以添加多个 Hyperliquid 钱包。
名单写入 hype/data/hype.db，信号进程会读到。

用法:
  python3 hype/admin.py
  浏览器打开 http://127.0.0.1:8765
"""

from __future__ import annotations

import hmac
import os
import secrets
from pathlib import Path

from flask import Flask, redirect, request, session, url_for

from leader_stats import PERIODS, get_leader
from wallet_store import (
    DATA_DIR,
    add_wallet,
    check_admin,
    create_admin,
    has_admin,
    load_wallets,
    remove_wallet,
    set_enabled,
    set_want_copy,
)

ROOT = Path(__file__).resolve().parent
SECRET_PATH = DATA_DIR / "session.key"
HOST = "127.0.0.1"
PORT = 8765

app = Flask(__name__)


def _session_secret() -> str:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if SECRET_PATH.exists():
        return SECRET_PATH.read_text(encoding="utf-8").strip()
    key = secrets.token_hex(32)
    SECRET_PATH.write_text(key, encoding="utf-8")
    try:
        os.chmod(SECRET_PATH, 0o600)
    except OSError:
        pass
    return key


app.secret_key = _session_secret()


def _csrf() -> str:
    token = session.get("csrf")
    if not token:
        token = secrets.token_hex(16)
        session["csrf"] = token
    return token


def _check_csrf() -> bool:
    sent = request.form.get("csrf") or ""
    saved = session.get("csrf") or ""
    return bool(saved) and hmac.compare_digest(sent, saved)


def _esc(text: object) -> str:
    raw = "" if text is None else str(text)
    return (
        raw.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _page(title: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{_esc(title)}</title>
  <style>
    :root {{
      --bg: #0b0e11;
      --card: #1e2329;
      --line: #2b3139;
      --text: #eaecef;
      --muted: #848e9c;
      --yellow: #f0b90b;
      --green: #0ecb81;
      --red: #f6465d;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 16px 28px;
      border-bottom: 1px solid var(--line);
    }}
    header b {{ font-size: 16px; }}
    a {{ color: var(--yellow); text-decoration: none; }}
    main {{ max-width: 1080px; margin: 0 auto; padding: 28px 20px 60px; }}
    .leader {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) 240px;
      gap: 12px 20px;
      align-items: center;
    }}
    .who {{ display: flex; gap: 8px; align-items: center; margin-bottom: 8px; }}
    .who strong {{ font-size: 18px; }}
    .metrics {{ display: flex; gap: 28px; flex-wrap: wrap; }}
    .metric span {{ color: var(--muted); font-size: 12px; }}
    .metric b {{ display: block; font-size: 22px; font-variant-numeric: tabular-nums; }}
    .up {{ color: var(--green); }}
    .down {{ color: var(--red); }}
    .spark, .chart {{ width: 100%; height: auto; display: block; }}
    .card-actions {{ display: flex; gap: 8px; justify-content: flex-end; grid-column: 1 / -1; }}
    .stats {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }}
    .tabs {{ display: flex; gap: 8px; margin: 8px 0 12px; }}
    .tabs a {{ color: var(--muted); background: transparent; border: 1px solid var(--line); border-radius: 8px; padding: 6px 12px; }}
    .tabs a.on {{ color: #1e2329; background: var(--yellow); border-color: var(--yellow); }}
    .sub {{ color: var(--muted); font-size: 12px; }}
    .card {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 20px;
      margin-bottom: 16px;
    }}
    h1 {{ font-size: 22px; margin: 0 0 8px; }}
    p.note {{ color: var(--muted); margin: 0 0 16px; }}
    label {{ display: block; color: var(--muted); margin: 12px 0 6px; }}
    input {{
      width: 100%;
      background: #0b0e11;
      color: var(--text);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px 12px;
    }}
    .row {{ display: grid; grid-template-columns: 180px 1fr auto; gap: 12px; align-items: end; }}
    button, .btn {{
      background: var(--yellow);
      color: #1e2329;
      border: 0;
      border-radius: 8px;
      padding: 10px 16px;
      font-weight: 700;
      cursor: pointer;
    }}
    button.ghost, .btn.ghost {{
      background: transparent;
      color: var(--text);
      border: 1px solid var(--line);
      font-weight: 600;
    }}
    button.danger {{ background: transparent; color: var(--red); border: 1px solid var(--red); }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ text-align: left; padding: 12px 8px; border-bottom: 1px solid var(--line); }}
    th {{ color: var(--muted); font-weight: 600; }}
    .tag {{
      display: inline-block;
      border-radius: 999px;
      padding: 2px 8px;
      font-size: 12px;
    }}
    .on {{ color: var(--green); background: rgba(14, 203, 129, 0.12); }}
    .off {{ color: var(--muted); background: #2b3139; }}
    .err {{ color: var(--red); margin: 0 0 12px; }}
    .ops {{ display: flex; gap: 8px; }}
    .ops form {{ margin: 0; }}
    code {{ color: #f0b90b; }}
    @media (max-width: 720px) {{
      .row, .leader, .stats {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <header>
    <b><a href="/" style="color:inherit">跟单广场</a></b>
    <nav>{'<a href="/logout">退出</a>' if session.get("admin") else ""}</nav>
  </header>
  <main>{body}</main>
</body>
</html>"""


def _need_login():
    if not has_admin():
        return redirect(url_for("setup"))
    if not session.get("admin"):
        return redirect(url_for("login"))
    return None


@app.get("/")
def home():
    gate = _need_login()
    if gate:
        return gate
    wallets = load_wallets()
    rows = []
    for row in wallets:
        flag = "on" if row["enabled"] else "off"
        text = "监控中" if row["enabled"] else "已暂停"
        action = "disable" if row["enabled"] else "enable"
        action_label = "暂停" if row["enabled"] else "恢复"
        rows.append(
            f"""<tr>
              <td><span class="tag {flag}">{text}</span></td>
              <td>{_esc(row["name"] or "-")}</td>
              <td><code>{_esc(row["address"])}</code></td>
              <td>{_esc(row["id"])}</td>
              <td>
                <div class="ops">
                  <form method="post" action="/wallets/{_esc(row["id"])}/{action}">
                    <input type="hidden" name="csrf" value="{_esc(_csrf())}">
                    <button class="ghost" type="submit">{action_label}</button>
                  </form>
                  <form method="post" action="/wallets/{_esc(row["id"])}/remove">
                    <input type="hidden" name="csrf" value="{_esc(_csrf())}">
                    <button class="danger" type="submit">删除</button>
                  </form>
                </div>
              </td>
            </tr>"""
        )
    table = (
        "<p class='note'>还没有钱包。</p>"
        if not rows
        else f"""<table>
          <thead><tr><th>状态</th><th>名称</th><th>地址</th><th>编号</th><th></th></tr></thead>
          <tbody>{''.join(rows)}</tbody>
        </table>"""
    )
    error = _esc(request.args.get("error") or "")
    err = f'<p class="err">{error}</p>' if error else ""
    body = f"""
      <h1>监控钱包</h1>
      <p class="note">登录管理员 {_esc(session.get("admin"))}。这里添加的钱包会进入监控名单，运行 <code>python3 hype/signals.py</code> 后发出永续信号。跟单下单还没接。</p>
      <div class="card">
        {err}
        <form method="post" action="/wallets">
          <input type="hidden" name="csrf" value="{_esc(_csrf())}">
          <div class="row">
            <div>
              <label>名称</label>
              <input name="name" placeholder="主号" maxlength="40">
            </div>
            <div>
              <label>钱包地址</label>
              <input name="address" placeholder="0x..." required>
            </div>
            <button type="submit">添加钱包</button>
          </div>
        </form>
      </div>
      <div class="card">{table}</div>
    """
    return _page("监控钱包", body)


@app.post("/wallets")
def add():
    gate = _need_login()
    if gate:
        return gate
    if not _check_csrf():
        return redirect(url_for("home", error="页面已过期，请再提交一次"))
    try:
        add_wallet(request.form.get("address") or "", request.form.get("name") or "")
    except ValueError as exc:
        return redirect(url_for("home", error=str(exc)))
    return redirect(url_for("home"))


@app.post("/wallets/<wallet_id>/enable")
@app.post("/wallets/<wallet_id>/disable")
@app.post("/wallets/<wallet_id>/remove")
def change(wallet_id: str):
    gate = _need_login()
    if gate:
        return gate
    if not _check_csrf():
        return redirect(url_for("home", error="页面已过期，请再提交一次"))
    action = request.path.rsplit("/", 1)[-1]
    try:
        if action == "enable":
            set_enabled(wallet_id, True)
        elif action == "disable":
            set_enabled(wallet_id, False)
        else:
            remove_wallet(wallet_id)
    except KeyError:
        return redirect(url_for("home", error="找不到这个钱包"))
    return redirect(url_for("home"))


@app.route("/setup", methods=["GET", "POST"])
def setup():
    if has_admin():
        return redirect(url_for("login"))
    error = ""
    if request.method == "POST":
        if not _check_csrf():
            error = "页面已过期，请再提交一次"
        else:
            password = request.form.get("password") or ""
            if password != (request.form.get("password2") or ""):
                error = "两次密码不一致"
            else:
                try:
                    create_admin(request.form.get("username") or "", password)
                except ValueError as exc:
                    error = str(exc)
                else:
                    session["admin"] = (request.form.get("username") or "").strip()
                    return redirect(url_for("home"))
    err = f'<p class="err">{_esc(error)}</p>' if error else ""
    body = f"""
      <h1>设置管理员</h1>
      <p class="note">第一次打开时设置。这个账号只保存在本机数据库里，用来登录管理页、添加监控钱包。</p>
      <div class="card">
        {err}
        <form method="post">
          <input type="hidden" name="csrf" value="{_esc(_csrf())}">
          <label>管理员账号</label>
          <input name="username" required maxlength="32" autocomplete="username">
          <label>密码</label>
          <input name="password" type="password" required minlength="8" autocomplete="new-password">
          <label>再输入一次密码</label>
          <input name="password2" type="password" required minlength="8" autocomplete="new-password">
          <div style="margin-top:16px"><button type="submit">保存并进入</button></div>
        </form>
      </div>
    """
    return _page("设置管理员", body)


@app.route("/login", methods=["GET", "POST"])
def login():
    if not has_admin():
        return redirect(url_for("setup"))
    if session.get("admin"):
        return redirect(url_for("home"))
    error = ""
    if request.method == "POST":
        if not _check_csrf():
            error = "页面已过期，请再提交一次"
        elif check_admin(request.form.get("username") or "", request.form.get("password") or ""):
            session["admin"] = (request.form.get("username") or "").strip()
            return redirect(url_for("home"))
        else:
            error = "账号或密码不对"
    err = f'<p class="err">{_esc(error)}</p>' if error else ""
    body = f"""
      <h1>管理员登录</h1>
      <div class="card">
        {err}
        <form method="post">
          <input type="hidden" name="csrf" value="{_esc(_csrf())}">
          <label>账号</label>
          <input name="username" required autocomplete="username">
          <label>密码</label>
          <input name="password" type="password" required autocomplete="current-password">
          <div style="margin-top:16px"><button type="submit">登录</button></div>
        </form>
      </div>
    """
    return _page("登录", body)


@app.get("/logout")
def logout():
    session.pop("admin", None)
    return redirect(url_for("login"))


def main() -> None:
    print(f"管理页 http://{HOST}:{PORT}", flush=True)
    app.run(host=HOST, port=PORT, debug=False)


if __name__ == "__main__":
    main()
