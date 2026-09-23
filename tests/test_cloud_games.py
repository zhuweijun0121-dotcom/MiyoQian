# -*- coding: utf-8 -*-

import unittest
from unittest.mock import Mock, patch

from miyouqian.constants import CLOUD_GAMES
from miyouqian.tasks.cloud_games import CloudGameCheckin


def wallet_response(free_time: int, send_freetime: int = 0) -> dict:
    return {
        "retcode": 0,
        "data": {
            "free_time": {"free_time": str(free_time), "send_freetime": str(send_freetime)},
            "play_card": {"short_msg": "未开通"},
            "coin": {"coin_num": "0"},
        },
    }


class CloudGameCheckinWalletDisplayTest(unittest.TestCase):
    def setUp(self) -> None:
        self.game = CLOUD_GAMES["zzz"]

    def run_game(self, side_effect) -> tuple[dict, Mock]:
        client = Mock()
        client.get_json.side_effect = side_effect
        with patch("miyouqian.tasks.cloud_games.time.sleep"):
            result = CloudGameCheckin(client, {}, {})._run_game(self.game, "test-token")
        return result, client

    @staticmethod
    def summary(result: dict) -> str:
        return "\n".join(result["messages"])

    def test_delayed_wallet_reports_latest_free_time(self) -> None:
        result, client = self.run_game([wallet_response(8), wallet_response(23)])
        summary = self.summary(result)

        self.assertEqual(client.get_json.call_count, 2)
        self.assertIn("云绝区零 签到成功，获得 15 分钟免费时长", summary)
        self.assertIn("云绝区零 当前免费时长 23分钟，畅玩卡状态 未开通，拥有邦邦点 0 枚", summary)
        self.assertNotIn("当前免费时长 8分钟", summary)

    def test_immediate_send_freetime_reports_post_award_balance(self) -> None:
        # 按 2026-09-15 实况固化：首查 free_time=38、send_freetime=15，
        # 客户端与复核查询证实发放后余额为 53
        result, client = self.run_game([wallet_response(38, 15)])
        summary = self.summary(result)

        self.assertEqual(client.get_json.call_count, 1)
        self.assertIn("云绝区零 签到成功，获得 15 分钟免费时长", summary)
        self.assertIn("云绝区零 当前免费时长 53分钟", summary)

    def test_retry_failure_keeps_first_wallet(self) -> None:
        result, client = self.run_game(
            [wallet_response(8), {"retcode": -100, "message": "token 失效"}]
        )
        summary = self.summary(result)

        self.assertEqual(client.get_json.call_count, 2)
        self.assertIn("云绝区零 今日已签到或免费时长已达上限", summary)
        self.assertIn("云绝区零 当前免费时长 8分钟", summary)

    def test_retry_exception_does_not_fail_checkin(self) -> None:
        result, client = self.run_game([wallet_response(8), RuntimeError("网络异常")])
        summary = self.summary(result)

        self.assertEqual(client.get_json.call_count, 2)
        self.assertIn("云绝区零 今日已签到或免费时长已达上限", summary)
        self.assertIn("云绝区零 当前免费时长 8分钟", summary)
        self.assertEqual(result["failed"], [])


if __name__ == "__main__":
    unittest.main()
