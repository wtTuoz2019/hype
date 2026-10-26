import json
from datetime import datetime
from unittest.mock import patch

import leader_stats
from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from plaza.models import Leader, LeaderSnapshot, TradeSignal
from plaza.services import leader_view


class PageTests(TestCase):
    def test_home_reads_cache_only(self):
        with patch("plaza.services.collect_leader", side_effect=AssertionError("不应在打开页面时请求")):
            response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "后台刷新")

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
