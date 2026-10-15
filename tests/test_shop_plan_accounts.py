# -*- coding: utf-8 -*-

import pathlib
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

import yaml

from miyouqian.core.config import (
    bind_shop_plan_accounts,
    find_account_index_by_stuid,
    load_config,
    normalize_config,
    save_config,
)
from miyouqian.service import web

A = {"name": "a", "stuid": "1001", "cookie": "cookie-A"}
B = {"name": "b", "stuid": "1002", "cookie": "cookie-B"}
C = {"name": "c", "stuid": "1003", "cookie": "cookie-C"}


def make_plan(goods_id: str, account_index: int, account_stuid: str = "", **extra) -> dict:
    return {
        "goods_id": goods_id,
        "account_index": account_index,
        "account_stuid": account_stuid,
        "uid": f"role-of-{account_index}",
        "exchange_at": 1700000000,
        "device_fp": "fp",
        **extra,
    }


def make_config(accounts: list[dict], plans: list[dict]) -> dict:
    config = {"accounts": [dict(account) for account in accounts], "shop_exchange": {"plans": plans}}
    normalize_config(config)
    return config


class BindShopPlanAccountsTest(unittest.TestCase):
    def test_plans_follow_their_accounts_after_a_deletion(self):
        # 账号列表 [A, B, C] 删掉 A 后，原来下标 1、2 的计划必须跟着 B、C 走
        config = make_config([B, C], [make_plan("p-b", 1, B["stuid"]), make_plan("p-c", 2, C["stuid"])])
        bind_shop_plan_accounts(config)
        by_goods = {plan["goods_id"]: plan for plan in config["shop_exchange"]["plans"]}
        self.assertEqual(by_goods["p-b"]["account_index"], 0)
        self.assertEqual(by_goods["p-c"]["account_index"], 1)
        self.assertTrue(by_goods["p-b"]["enable"] and by_goods["p-c"]["enable"])

    def test_plan_of_a_deleted_account_is_disabled_instead_of_rebound(self):
        config = make_config([B, C], [make_plan("p-a", 0, A["stuid"])])
        bind_shop_plan_accounts(config)
        plan = config["shop_exchange"]["plans"][0]
        self.assertFalse(plan["enable"])
        self.assertIn("已删除", plan["last_result"])
        # 绑定不能被改写，否则之后又会指向别的账号
        self.assertEqual(plan["account_stuid"], A["stuid"])
        self.assertIsNone(find_account_index_by_stuid(config["accounts"], plan["account_stuid"]))

    def test_paused_plan_of_a_deleted_account_keeps_its_last_result(self):
        config = make_config([B], [make_plan("p-a", 0, A["stuid"], enable=False, last_result="上次成功")])
        bind_shop_plan_accounts(config)
        plan = config["shop_exchange"]["plans"][0]
        self.assertFalse(plan["enable"])
        self.assertEqual(plan["last_result"], "上次成功")

    def test_legacy_plan_is_bound_to_the_account_at_its_index(self):
        config = make_config([A, B], [make_plan("legacy", 1)])
        bind_shop_plan_accounts(config)
        self.assertEqual(config["shop_exchange"]["plans"][0]["account_stuid"], B["stuid"])

    def test_nothing_is_disabled_when_no_account_has_a_stuid(self):
        # 凭证文件缺失时所有账号的 stuid 都是空的，不能因此把计划全停掉
        config = make_config([{"name": "a"}, {"name": "b"}], [make_plan("p", 1, B["stuid"])])
        bind_shop_plan_accounts(config)
        plan = config["shop_exchange"]["plans"][0]
        self.assertTrue(plan["enable"])
        self.assertEqual(plan["account_index"], 1)


class LoadConfigBindingTest(unittest.TestCase):
    def test_load_config_keeps_plans_enabled_and_bound(self):
        # stuid 在凭证文件里，load_config 第一次 normalize 时还拿不到，不能在那时就判定账号已删除
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "config.yaml"
            save_config(path, make_config([A, B, C], [make_plan("p-c", 2, C["stuid"])]))
            plan = load_config(path)["shop_exchange"]["plans"][0]
            self.assertTrue(plan["enable"])
            self.assertEqual((plan["account_index"], plan["account_stuid"]), (2, C["stuid"]))

    def test_reordering_accounts_in_config_yaml_moves_plans_with_them(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "config.yaml"
            save_config(path, make_config([A, B, C], [make_plan("p-c", 2, C["stuid"])]))
            public = yaml.safe_load(path.read_text(encoding="utf-8"))
            public["accounts"].reverse()
            path.write_text(yaml.safe_dump(public, allow_unicode=True), encoding="utf-8")
            config = load_config(path)
            plan = config["shop_exchange"]["plans"][0]
            self.assertEqual(config["accounts"][plan["account_index"]]["stuid"], C["stuid"])
            self.assertEqual(plan["account_index"], 0)


class WebAppPlanAccountTest(unittest.TestCase):
    def make_app(self, accounts: list[dict]) -> web.WebApp:
        app = web.WebApp.__new__(web.WebApp)
        app.lock = threading.RLock()
        app.config = {"accounts": [dict(account) for account in accounts], "shop_exchange": {}}
        app.log = lambda *args, **kwargs: None
        return app

    def test_account_is_resolved_by_stuid_even_when_the_index_is_stale(self):
        app = self.make_app([B, C])
        self.assertEqual(app._plan_account_index(make_plan("p", 2, C["stuid"])), 1)

    def test_missing_account_is_an_error_instead_of_another_account(self):
        app = self.make_app([B, C])
        with self.assertRaises(ValueError):
            app._plan_account_index(make_plan("p", 0, A["stuid"]))

    def test_plan_without_stuid_falls_back_to_the_index(self):
        app = self.make_app([B, C])
        self.assertEqual(app._plan_account_index(make_plan("p", 1)), 1)

    @patch.object(web, "ApiClient", MagicMock())
    @patch.object(web, "ShopExchange")
    def test_exchange_uses_the_cookie_of_the_plans_own_account(self, shop_exchange):
        shop_exchange.return_value.exchange_with_retry.return_value = {
            "ok": True, "message": "ok", "retcode": 0, "attempt": 1,
        }
        app = self.make_app([B, C])
        app.shop_exchange_once(make_plan("p-c", 2, C["stuid"]))
        self.assertEqual(shop_exchange.call_args.args[2]["cookie"], C["cookie"])

    @patch.object(web, "ApiClient", MagicMock())
    @patch.object(web, "ShopExchange")
    def test_exchange_is_refused_when_the_plans_account_is_gone(self, shop_exchange):
        app = self.make_app([B, C])
        with self.assertRaises(ValueError):
            app.shop_exchange_once(make_plan("p-a", 0, A["stuid"]))
        shop_exchange.assert_not_called()


if __name__ == "__main__":
    unittest.main()
