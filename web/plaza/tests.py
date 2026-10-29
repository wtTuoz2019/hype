import hashlib
import hmac
import json
import os
import time
from datetime import datetime, timedelta
from unittest.mock import patch

import leader_stats
from cryptography.fernet import Fernet
from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from plaza.copy_plan import LotState, decide
from plaza.hyperbot import sync_hot
from plaza.copy_run import process_signal
from plaza.models import CopyLog, CopyLot, ExchangeAccount, Follow, Leader, LeaderSnapshot, TradeSignal
from plaza.services import leader_view
from plaza.vault import decrypt


class PageTests(TestCase):
    def test_home_reads_cache_only(self):
        with patch("plaza.services.collect_leader", side_effect=AssertionError("不应在打开页面时请求")):
            response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "开单快讯")

    def test_signal_filter(self):
        leader = Leader.objects.create(name="主号", address="0x" + "11" * 20)
        TradeSignal.objects.create(
            leader=leader, sig_id="a", coin="HYPE", action="open", side="long", label="开多", time_ms=1
        )
        TradeSignal.objects.create(
            leader=leader, sig_id="b", coin="BTC", action="close", side="short", label="平空", time_ms=2
        )
        response = self.client.get("/signals/", {"coin": "hype", "label": "开多"})
        self.assertContains(response, "HYPE")
        self.assertNotContains(response, "BTC")
        feed = self.client.get("/signals/feed/", {"after": 0, "coin": "HYPE", "label": "开多"})
        body = feed.json()
        self.assertEqual([row["coin"] for row in body["signals"]], ["HYPE"])

    def test_close_time_is_readable(self):
        leader = Leader.objects.create(name="主号", address="0x" + "22" * 20, enabled=True)
        when_ms = 1_760_000_000_000
        LeaderSnapshot.objects.create(
            leader=leader,
            full=True,
            payload=json.dumps(
                {
                    "periods": {"7d": {"pnl": 1, "roi": 0.01, "equity": 10, "volume": 3, "curve": []}},
                    "positions": [],
                    "closes": [{"time": when_ms, "coin": "HYPE", "dir": "Close Long", "px": "1", "sz": "1", "pnl": 2}],
                    "upnl": 0,
                    "win_rate": 1,
                    "close_count": 1,
                }
            ),
        )
        response = self.client.get(f"/leaders/{leader.id}/")
        text = datetime.fromtimestamp(when_ms / 1000).strftime("%m-%d %H:%M:%S")
        self.assertContains(response, text)
        self.assertNotContains(response, str(when_ms))

    def test_staff_can_add_wallet_without_waiting_on_network(self):
        user = User.objects.create_user("boss", password="long-enough-pass", is_staff=True)
        self.client.force_login(user)
        address = "0x" + "ab" * 20
        with patch("plaza.views.threading.Thread") as thread:
            response = self.client.post("/wallets/add/", {"name": "测试", "address": address})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Leader.objects.filter(address=address, name="测试", enabled=True).exists())
        thread.assert_called_once()
        thread.return_value.start.assert_called_once()

    def test_member_cannot_manage_wallets(self):
        user = User.objects.create_user("member", password="long-enough-pass")
        self.client.force_login(user)
        self.assertEqual(self.client.get("/wallets/").status_code, 403)

    @override_settings(DEBUG=False)
    def test_public_first_user_is_not_admin(self):
        response = self.client.post(
            "/register/",
            {"username": "ada", "password": "long-enough-pass", "password2": "long-enough-pass"},
        )
        self.assertEqual(response.status_code, 302)
        user = User.objects.get(username="ada")
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)

    def test_missing_snapshot_is_waiting(self):
        leader = Leader.objects.create(name="空", address="0x" + "33" * 20)
        self.assertTrue(leader_view(leader)["waiting"])


class DexTests(TestCase):
    def test_empty_dex_is_skipped_until_rescan(self):
        calls = []

        def fake(body, timeout=20):
            calls.append(dict(body))
            if body.get("type") == "perpDexs":
                return [None, {"name": "xyz"}]
            if body.get("dex") == "xyz":
                return {"marginSummary": {"accountValue": "0"}, "assetPositions": []}
            return {"marginSummary": {"accountValue": "10"}, "assetPositions": []}

        leader_stats._dex_cache["at"] = 0
        leader_stats._hot_dex.clear()
        original = leader_stats.post_info
        leader_stats.post_info = fake
        try:
            leader_stats.clearinghouses("0x" + "44" * 20)
            calls.clear()
            leader_stats.clearinghouses("0x" + "44" * 20)
        finally:
            leader_stats.post_info = original
            leader_stats._dex_cache["at"] = 0
            leader_stats._hot_dex.clear()
        dexes = [item.get("dex", "") for item in calls if item.get("type") == "clearinghouseState"]
        self.assertEqual(dexes, [""])
        self.assertFalse(any(item.get("type") == "perpDexs" for item in calls))


class ExchangeTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._old_master = os.environ.get("HYPE_MASTER_KEY")
        os.environ["HYPE_MASTER_KEY"] = Fernet.generate_key().decode()

    @classmethod
    def tearDownClass(cls):
        if cls._old_master is None:
            os.environ.pop("HYPE_MASTER_KEY", None)
        else:
            os.environ["HYPE_MASTER_KEY"] = cls._old_master
        super().tearDownClass()

    def _user(self, name: str):
        user = User.objects.create_user(name, password="long-enough-pass")
        self.client.force_login(user)
        return user

    def _add(self, venue: str, name: str, api_key: str, secret: str):
        return self.client.post(
            "/accounts/add/",
            {"venue": venue, "name": name, "api_key": api_key, "api_secret": secret},
            follow=True,
        )

    def test_binance_and_gate_keys_are_stored_encrypted(self):
        self._user("trader")
        binance_key = "BINANCEKEY1234567890ABCD"
        binance_secret = "BINANCESECRET1234567890WXYZ"
        gate_key = "GATEKEY1234567890EFGH"
        gate_secret = "GATESECRET1234567890IJKL"
        saved = self._add("binance", "币安主号", binance_key, binance_secret)
        self.assertContains(saved, "****ABCD")
        self.assertNotContains(saved, binance_key)
        self.assertNotContains(saved, binance_secret)
        saved = self._add("gate", "Gate 主号", gate_key, gate_secret)
        self.assertContains(saved, "****EFGH")
        self.assertNotContains(saved, gate_key)
        self.assertNotContains(saved, gate_secret)
        row = ExchangeAccount.objects.get(venue="binance")
        self.assertEqual(decrypt(row.key_enc), binance_key)
        self.assertEqual(decrypt(row.secret_enc), binance_secret)
        self.assertNotEqual(row.key_enc, binance_key)
        self.assertEqual(ExchangeAccount.objects.filter(venue="gate").count(), 1)

    def test_follow_uses_only_own_account(self):
        owner = self._user("owner")
        self._add("binance", "我的币安", "OWNERKEY1234567890ABCD", "OWNERSECRET1234567890WXYZ")
        own = ExchangeAccount.objects.get(user=owner)
        other = User.objects.create_user("other", password="long-enough-pass")
        foreign = ExchangeAccount.objects.create(
            user=other,
            venue="gate",
            name="别人的",
            key_last4="ZZZZ",
            key_fp="fp-other",
            key_enc="enc",
            secret_enc="enc",
        )
        leader = Leader.objects.create(name="主号", address="0x" + "55" * 20, enabled=True)
        self.client.force_login(owner)
        denied = self.client.post(f"/leaders/{leader.id}/follow/", {"account": foreign.id}, follow=True)
        self.assertContains(denied, "请选择一个可用的币安或 Gate 账户")
        self.assertFalse(Follow.objects.filter(user=owner, active=True).exists())
        ok = self.client.post(f"/leaders/{leader.id}/follow/", {"account": own.id}, follow=True)
        self.assertContains(ok, "****ABCD")
        follow = Follow.objects.get(user=owner, leader=leader)
        self.assertTrue(follow.active)
        self.assertEqual(follow.account_id, own.id)
        me = self.client.get("/me/")
        self.assertContains(me, "币安")
        self.assertContains(me, "****ABCD")
        self.client.post(f"/leaders/{leader.id}/follow/", {"stop": "1"})
        follow.refresh_from_db()
        self.assertFalse(follow.active)


class _FakeVenue:
    def __init__(self):
        self.orders = []
        self.eq = 10_000.0
        self.mk = 40.0

    def equity(self):
        return self.eq

    def mark(self, base):
        return self.mk

    def limits(self, base):
        return 0.01, 5.0

    def ensure(self, base):
        return None

    def order(self, base, side, qty, reduce_only):
        self.orders.append((base, side, qty, reduce_only))


class CopyPlanTests(TestCase):
    def _open(self, **kwargs):
        base = dict(
            action="open",
            side="long",
            leader_delta=100,
            leader_now=100,
            px=40,
            mark=40,
            follower_equity=10_000,
            leader_equity=700_000,
            lot=LotState(),
            min_qty=0.01,
            min_notional=5,
        )
        base.update(kwargs)
        return decide(**base)

    def test_open_uses_capped_equity_ratio(self):
        decision = self._open(follower_equity=100_000)
        self.assertEqual(decision.status, "sent")
        self.assertAlmostEqual(decision.intents[0].qty, 5.0)
        self.assertAlmostEqual(decision.lot.ratio, 0.05)

    def test_add_keeps_frozen_ratio(self):
        decision = self._open(action="add", lot=LotState("long", 1, 0.01, 40, 0), leader_delta=50)
        self.assertAlmostEqual(decision.intents[0].qty, 0.5)

    def test_does_not_chase_a_worse_open(self):
        decision = self._open(mark=40.3)
        self.assertEqual(decision.intents, [])
        self.assertIn("不追", decision.detail)

    def test_close_ignores_price_and_minimum(self):
        decision = decide(
            action="close",
            side="long",
            leader_delta=100,
            leader_now=0,
            px=50,
            mark=80,
            follower_equity=10_000,
            leader_equity=700_000,
            lot=LotState("long", 0.004, 0.01, 40, 0),
            min_qty=1,
            min_notional=100,
        )
        self.assertEqual(decision.status, "sent")
        self.assertAlmostEqual(decision.intents[0].qty, 0.004)
        self.assertEqual(decision.lot.qty, 0)

    def test_small_reduce_waits(self):
        decision = decide(
            action="reduce",
            side="long",
            leader_delta=1,
            leader_now=99,
            px=40,
            mark=40,
            follower_equity=10_000,
            leader_equity=700_000,
            lot=LotState("long", 2, 0.02, 40, 0),
            min_qty=1,
            min_notional=0,
        )
        self.assertEqual(decision.intents, [])
        self.assertGreater(decision.lot.pending, 0)

    def test_existing_position_is_not_opened(self):
        decision = self._open(action="add")
        self.assertEqual(decision.intents, [])
        self.assertIn("已有仓", decision.detail)

    def test_bump_over_equity_cap_is_skipped(self):
        decision = self._open(min_qty=100, min_notional=0, px=40, follower_equity=1_000)
        self.assertEqual(decision.intents, [])
        self.assertIn("25%", decision.detail)

    def test_worker_places_one_order_and_ignores_earlier_signal(self):
        leader = Leader.objects.create(name="主号", address="0x" + "66" * 20)
        LeaderSnapshot.objects.create(
            leader=leader,
            payload='{"periods":{"all":{"equity":700000}}}',
            full=True,
        )
        account = ExchangeAccount.objects.create(
            user=User.objects.create_user("copier", password="long-enough-pass"),
            venue="binance",
            name="币安",
            key_last4="ABCD",
            key_fp="fp-copy",
            key_enc="x",
            secret_enc="y",
        )
        follow = Follow.objects.create(
            user=account.user, leader=leader, account=account, active=True, armed_ms=2_000
        )
        early = TradeSignal.objects.create(
            leader=leader,
            sig_id="early",
            time_ms=1_000,
            coin="HYPE",
            action="open",
            side="long",
            label="开多",
            delta_szi="100",
            szi="100",
            px="40",
        )
        venue = _FakeVenue()
        process_signal(early.id, venue_for=lambda account: venue)
        self.assertEqual(venue.orders, [])
        self.assertEqual(CopyLog.objects.get(follow=follow).detail, "早于开始跟单")
        fresh = TradeSignal.objects.create(
            leader=leader,
            sig_id="fresh",
            time_ms=3_000,
            coin="HYPE",
            action="open",
            side="long",
            label="开多",
            delta_szi="100",
            szi="100",
            px="40",
        )
        process_signal(fresh.id, venue_for=lambda account: venue)
        self.assertEqual(len(venue.orders), 1)
        self.assertEqual(venue.orders[0][0], "HYPE")
        self.assertEqual(venue.orders[0][1], "long")
        self.assertFalse(venue.orders[0][3])
        lot = CopyLot.objects.get(follow=follow, coin="HYPE")
        self.assertAlmostEqual(lot.qty, 100 * (10_000 / 700_000), places=6)


class HotSyncTests(TestCase):
    def test_sync_keeps_profitable_hot_addresses_only(self):
        manual = "0x" + "11" * 20
        Leader.objects.create(name="主号", address=manual, enabled=True, source="")
        dropped = "0x" + "22" * 20
        Leader.objects.create(name="旧热门", address=dropped, enabled=True, source="hyperbot")
        paused = "0x" + "33" * 20
        Leader.objects.create(name="已停", address=paused, enabled=False, source="hyperbot")
        rows = [
            {"address": "0x" + "AB" * 20, "realizedPnl": 1200, "remark": "盈利甲"},
            {"address": "0x" + "44" * 20, "realizedPnl": -5, "remark": "亏损"},
            {"address": manual, "realizedPnl": -100, "remark": "不要改主号"},
            {"address": paused, "realizedPnl": 50, "remark": "不要自动打开"},
            {"address": "bad", "realizedPnl": 10, "remark": "坏地址"},
        ]
        result = sync_hot(rows)
        self.assertEqual(result["created"], 1)
        self.assertEqual(result["disabled"], 1)
        self.assertTrue(Leader.objects.get(address=manual, source="").enabled)
        self.assertEqual(Leader.objects.get(address=manual).name, "主号")
        self.assertFalse(Leader.objects.get(address=dropped).enabled)
        self.assertFalse(Leader.objects.get(address=paused).enabled)
        added = Leader.objects.get(address="0x" + "ab" * 20)
        self.assertEqual(added.name, "盈利甲")
        self.assertEqual(added.source, "hyperbot")
        self.assertTrue(added.enabled)
        self.assertFalse(Leader.objects.filter(name="亏损").exists())

    def test_manual_add_is_not_removed_by_hot_sync(self):
        user = User.objects.create_user("boss", password="long-enough-pass", is_staff=True)
        self.client.force_login(user)
        address = "0x" + "cd" * 20
        with patch("plaza.views.threading.Thread") as thread:
            response = self.client.post("/wallets/add/", {"name": "手工", "address": address})
        self.assertEqual(response.status_code, 302)
        thread.return_value.start.assert_called_once()
        sync_hot([])
        leader = Leader.objects.get(address=address)
        self.assertTrue(leader.enabled)
        self.assertEqual(leader.source, "")
        self.assertEqual(leader.name, "手工")
        seeded = Leader.objects.get(address="0xf97ad6704baec104d00b88e0c157e2b7b3a1ddd1")
        seeded.delete()
        sync_hot([])
        self.assertFalse(
            Leader.objects.filter(address="0xf97ad6704baec104d00b88e0c157e2b7b3a1ddd1").exists()
        )


def _telegram_query(payload, token):
    pairs = [f"{key}={payload[key]}" for key in sorted(payload)]
    secret = hashlib.sha256(token.encode()).digest()
    signed = dict(payload)
    signed["hash"] = hmac.new(secret, "\n".join(pairs).encode(), hashlib.sha256).hexdigest()
    return signed


class ScopePageTests(TestCase):
    def test_pages_are_the_alphascope_screens(self):
        home = self.client.get("/")
        self.assertContains(home, "聪明钱包开单快讯")
        self.assertContains(home, "开单语音提醒")
        self.assertNotContains(home, "我的跟单")
        self.assertContains(self.client.get("/leaderboard/"), "聪明钱包排行榜")
        self.assertContains(self.client.get("/analysis/"), "综合分析")
        self.assertContains(self.client.get("/pricing/"), "会员中心")
        self.assertContains(self.client.get("/wallet/?id=B2"), "钱包详情")

    def test_real_tape_respects_delay_and_uses_cached_stats(self):
        from plaza.models import LeaderSnapshot

        leader = Leader.objects.create(name="实盘甲", address="0x" + "ab" * 20, enabled=True)
        now_ms = int(time.time() * 1000)
        fresh = TradeSignal.objects.create(
            leader=leader, sig_id="fresh", time_ms=now_ms, coin="BTC", action="open", side="long", label="开多", delta_szi="1", px="1000"
        )
        recent = TradeSignal.objects.create(
            leader=leader,
            sig_id="recent",
            time_ms=now_ms - 2 * 60 * 1000,
            coin="ETH",
            action="open",
            side="short",
            label="开空",
            delta_szi="2",
            px="1000",
        )
        older = TradeSignal.objects.create(
            leader=leader,
            sig_id="older",
            time_ms=now_ms - 40 * 60 * 1000,
            coin="SOL",
            action="open",
            side="long",
            label="开多",
            delta_szi="3",
            px="1000",
        )
        LeaderSnapshot.objects.create(
            leader=leader,
            payload=json.dumps(
                {
                    "periods": {"30d": {"pnl": 1200, "roi": 0.12, "equity": 11200, "curve": [[1, 0], [2, 1500], [3, 1200]]}},
                    "positions": [{"coin": "BTC", "szi": 1.5, "entry": "60000", "upnl": 80}],
                    "closes": [{"coin": "ETH", "dir": "Close Short", "px": "3000", "pnl": 40}],
                    "win_rate": 0.5,
                    "close_count": 4,
                }
            ),
            full=True,
        )
        with patch("plaza.services.collect_leader", side_effect=AssertionError("打开页面不应请求链上")):
            free = self.client.get("/api/tape/").json()
        free_ids = [row["id"] for row in free["events"]]
        self.assertIn(older.id, free_ids)
        self.assertNotIn(fresh.id, free_ids)
        self.assertNotIn(recent.id, free_ids)
        self.assertIn("wallet_rev", free)
        self.assertNotIn("wallets", free)
        listed = self.client.get("/api/tape/?wallets=1&events=0").json()
        card = next(row for row in listed["wallets"] if row["id"] == str(leader.id))
        self.assertEqual(card["roi"], 12.0)
        self.assertNotIn("positions", card)
        self.assertGreater(card["dd"], 0)
        detail = self.client.get(f"/api/wallet/?id={leader.id}").json()["wallet"]
        self.assertEqual(detail["positions"][0]["coin"], "BTC")
        self.assertEqual(detail["history"][0]["side"], "平空")
        self.assertIn("when", detail["history"][0])

        self.client.post("/api/membership/", data=json.dumps({"pro": True}), content_type="application/json")
        pro_ids = [row["id"] for row in self.client.get("/api/tape/").json()["events"]]
        self.assertIn(recent.id, pro_ids)
        self.assertNotIn(fresh.id, pro_ids)
        # 增量：id 大于 after，且已过 Pro 延迟（按创建顺序 fresh < recent < older）
        incr = self.client.get(f"/api/tape/?after={fresh.id}").json()
        self.assertTrue(incr["incremental"])
        incr_ids = [row["id"] for row in incr["events"]]
        self.assertIn(recent.id, incr_ids)
        self.assertIn(older.id, incr_ids)
        self.assertNotIn(fresh.id, incr_ids)
        self.assertNotIn("wallets", incr)
        light = self.client.get("/api/tape/?events=0&wallets=1").json()
        self.assertEqual(light["events"], [])
        self.assertTrue(light["wallets"])
        before = TradeSignal.objects.count()
        preview = self.client.post("/api/tape/test/")
        self.assertEqual(preview.json()["event"]["id"], recent.id)
        self.assertEqual(TradeSignal.objects.count(), before)

    def test_admin_login_does_not_show_on_frontend(self):
        User.objects.create_superuser("admin", "admin@example.com", "long-enough-pass")
        self.assertTrue(self.client.login(username="admin", password="long-enough-pass"))
        home = self.client.get("/")
        self.assertContains(home, "Telegram 登录")
        self.assertContains(home, '"user": null')
        self.assertNotContains(home, "@admin")

    @override_settings(TELEGRAM_BOT_TOKEN="123:ABC", TELEGRAM_BOT_USERNAME="AlphaScopeBot")
    def test_telegram_login_checks_signature(self):
        payload = {
            "id": "42",
            "first_name": "Ada",
            "username": "ada",
            "auth_date": str(int(time.time())),
        }
        signed = _telegram_query(payload, "123:ABC")
        response = self.client.get("/auth/telegram/callback", signed)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(User.objects.filter(username="tg_42").exists())
        home = self.client.get("/")
        self.assertContains(home, "Ada")
        self.assertContains(home, '"handle": "ada"')
        self.assertEqual(self.client.get("/auth/telegram/callback", signed).status_code, 403)
        bad = dict(signed)
        bad["hash"] = "0" * 64
        self.assertEqual(self.client.get("/auth/telegram/callback", bad).status_code, 403)

    @override_settings(TELEGRAM_BOT_TOKEN="123:ABC", TELEGRAM_BOT_USERNAME="AlphaScopeBot")
    def test_telegram_start_renders_widget_page(self):
        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b'{"ok":true,"result":{"username":"AlphaScopeBot","id":123}}'

        with patch("urllib.request.urlopen", return_value=_Resp()):
            response = self.client.get("/auth/telegram/start/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-telegram-login")
        self.assertContains(response, "data-auth-url")
        self.assertContains(response, "AlphaScopeBot")
        self.assertContains(response, "/auth/telegram/callback")

    @override_settings(TELEGRAM_BOT_TOKEN="123:ABC", TELEGRAM_BOT_USERNAME="AlphaScopeBot")
    def test_telegram_login_post_onauth(self):
        payload = {
            "id": "77",
            "first_name": "Cara",
            "username": "cara",
            "auth_date": str(int(time.time())),
        }
        signed = _telegram_query(payload, "123:ABC")
        response = self.client.post(
            "/auth/telegram/",
            data=json.dumps(signed),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["user"]["label"], "Cara")
        self.assertEqual(response.json()["user"]["handle"], "cara")
        home = self.client.get("/")
        self.assertContains(home, "Cara")

    def test_okx_wallet_login_requires_signature(self):
        from eth_account import Account
        from eth_account.messages import encode_defunct
        from plaza.models import Profile

        account = Account.create()
        address = account.address.lower()
        challenge = self.client.post(
            "/auth/wallet/nonce/",
            data=json.dumps({"address": address}),
            content_type="application/json",
        )
        self.assertEqual(challenge.status_code, 200)
        payload = challenge.json()
        signed = account.sign_message(encode_defunct(text=payload["message"]))
        ok = self.client.post(
            "/auth/wallet/verify/",
            data=json.dumps(
                {
                    "address": address,
                    "nonce": payload["nonce"],
                    "message": payload["message"],
                    "signature": signed.signature.hex(),
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.json()["user"]["label"], f"{address[:6]}…{address[-4:]}")
        profile = Profile.objects.get(wallet_address=address)
        self.assertEqual(profile.wallet_login_count, 1)
        home = self.client.get("/")
        self.assertContains(home, address[:6])
        # 同一 nonce 不能再用
        again = self.client.post(
            "/auth/wallet/verify/",
            data=json.dumps(
                {
                    "address": address,
                    "nonce": payload["nonce"],
                    "message": payload["message"],
                    "signature": signed.signature.hex(),
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(again.status_code, 403)
        # 假签名拒绝
        other = Account.create()
        bad_sig = other.sign_message(encode_defunct(text=payload["message"]))
        fresh = self.client.post(
            "/auth/wallet/nonce/",
            data=json.dumps({"address": address}),
            content_type="application/json",
        ).json()
        denied = self.client.post(
            "/auth/wallet/verify/",
            data=json.dumps(
                {
                    "address": address,
                    "nonce": fresh["nonce"],
                    "message": fresh["message"],
                    "signature": bad_sig.signature.hex(),
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(denied.status_code, 403)

    def test_countdown_follows_real_signal_delay(self):
        from plaza.tape import FREE_DELAY, PRO_DELAY

        leader = Leader.objects.create(name="倒计时", address="0x" + "cd" * 20, enabled=True)
        now_ms = int(time.time() * 1000)
        # 还有约 10 分钟才对免费用户可见
        TradeSignal.objects.create(
            leader=leader,
            sig_id="soon",
            time_ms=now_ms - (FREE_DELAY - 600) * 1000,
            coin="BTC",
            action="open",
            side="long",
            label="开多",
            delta_szi="1",
            px="1000",
        )
        feed = self.client.get("/api/tape/?events=0").json()
        self.assertIsNotNone(feed["next_visible_at"])
        left = feed["next_visible_at"] - int(time.time())
        self.assertGreater(left, 500)
        self.assertLess(left, 700)
        # 延迟窗里没有信号时不应假装从头倒满 30 分钟
        TradeSignal.objects.all().delete()
        empty = self.client.get("/api/tape/?events=0").json()
        self.assertIsNone(empty["next_visible_at"])

        # 活跃行情：应以最新一条起算，而不是贴着 cutoff 的最早一条（否则永远只剩几秒）
        TradeSignal.objects.create(
            leader=leader,
            sig_id="old-edge",
            time_ms=now_ms - (PRO_DELAY - 5) * 1000,
            coin="ETH",
            action="open",
            side="long",
            label="开多",
            delta_szi="1",
            px="1000",
        )
        TradeSignal.objects.create(
            leader=leader,
            sig_id="fresh-open",
            time_ms=now_ms - 3 * 1000,
            coin="BTC",
            action="open",
            side="long",
            label="开多",
            delta_szi="1",
            px="1000",
        )
        session = self.client.session
        session["tape_pro"] = True
        session.save()
        pro_feed = self.client.get("/api/tape/?events=0").json()
        pro_left = pro_feed["next_visible_at"] - int(time.time())
        self.assertGreater(pro_left, 50)
        self.assertLessEqual(pro_left, PRO_DELAY)

    def test_today_focus_not_swamped_by_recent_adds(self):
        leader = Leader.objects.create(name="焦点钱包", address="0x" + "ef" * 20, enabled=True)
        now_ms = int(time.time() * 1000)
        # 一笔更大的开多，时间稍早
        TradeSignal.objects.create(
            leader=leader,
            sig_id="big-open",
            time_ms=now_ms - 40 * 60 * 1000,
            coin="BTC",
            action="open",
            side="long",
            label="开多",
            delta_szi="10",
            px="1000",
        )
        # 最近 80 条全是小加空，模拟刷屏
        for i in range(80):
            TradeSignal.objects.create(
                leader=leader,
                sig_id=f"add-{i}",
                time_ms=now_ms - 35 * 60 * 1000 + i,
                coin="ETH",
                action="add",
                side="short",
                label="加空",
                delta_szi="0.1",
                px="10",
            )
        feed = self.client.get("/api/tape/").json()
        self.assertTrue(feed["today_focus"])
        self.assertEqual(feed["today_focus"][0]["act"], "开多")
        self.assertEqual(feed["today_focus"][0]["symbol"], "BTC")

    @override_settings(TELEGRAM_BOT_TOKEN="123:ABC", TELEGRAM_BOT_USERNAME="AlphaScopeBot")
    def test_telegram_login_reuses_existing_username(self):
        User.objects.create_user(username="tg_99", password="unused-pass-99")
        payload = {
            "id": "99",
            "first_name": "Bob",
            "username": "bob",
            "auth_date": str(int(time.time())),
        }
        signed = _telegram_query(payload, "123:ABC")
        response = self.client.get("/auth/telegram/callback", signed)
        self.assertEqual(response.status_code, 302)
        home = self.client.get("/")
        self.assertContains(home, "Bob")
