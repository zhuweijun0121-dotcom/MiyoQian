# -*- coding: utf-8 -*-

import unittest
from unittest.mock import Mock, patch

import httpx

from miyouqian.core.config import normalize_push_channels
from miyouqian.service import notifier


class ServerChanUrlTest(unittest.TestCase):
    """Server酱³ 的地址拼装：uid 可以留空（从 SendKey 的 sctp{uid}t 前缀解析）。"""

    def test_uid_is_parsed_from_sendkey(self):
        url = notifier.serverchan_url({"sendkey": "sctp1234tAbCdEfGh"})
        self.assertEqual(url, "https://1234.push.ft07.com/send/sctp1234tAbCdEfGh.send")

    def test_explicit_uid_wins(self):
        url = notifier.serverchan_url({"sendkey": "sctp99tXYZ", "uid": "777"})
        self.assertEqual(url, "https://777.push.ft07.com/send/sctp99tXYZ.send")

    def test_blank_input_is_rejected(self):
        with self.assertRaises(ValueError):
            notifier.serverchan_url({})

    def test_unparsable_sendkey_is_rejected(self):
        with self.assertRaises(ValueError):
            notifier.serverchan_url({"sendkey": "no-uid-prefix"})

    def test_surrounding_whitespace_is_trimmed(self):
        url = notifier.serverchan_url({"sendkey": "  sctp5tK  ", "uid": " 5 "})
        self.assertEqual(url, "https://5.push.ft07.com/send/sctp5tK.send")

    def test_non_numeric_uid_cannot_redirect_the_request(self):
        # uid 会被拼进主机名，必须是纯数字，否则请求可能被发往其他域名
        for uid in ("evil.com/#", "1.evil.com", "1@evil.com", "１２３", "-1"):
            with self.subTest(uid=uid):
                with self.assertRaises(ValueError):
                    notifier.serverchan_url({"sendkey": "sctp1tA", "uid": uid})


class ServerChanSendTest(unittest.TestCase):
    def _client(self, payload, status_code=200):
        response = Mock()
        response.status_code = status_code
        response.json.return_value = payload
        client = Mock()
        client.post.return_value = response
        return client, response

    def test_success_sends_title_and_markdown(self):
        client, response = self._client({"code": 0, "message": ""})
        notifier.serverchan_send(client, {"sendkey": "sctp1tA"}, "标题", "# 正文")
        response.raise_for_status.assert_called_once()
        args, kwargs = client.post.call_args
        self.assertEqual(args[0], "https://1.push.ft07.com/send/sctp1tA.send")
        self.assertEqual(kwargs["json"], {"title": "标题", "desp": "# 正文"})

    def test_business_error_is_raised(self):
        # Server酱 把业务错误放在 HTTP 200 的 code 字段里，不能只看状态码
        client, _ = self._client({"code": 40001, "message": "bad sendkey"})
        with self.assertRaises(ValueError) as ctx:
            notifier.serverchan_send(client, {"sendkey": "sctp1tA"}, "标题", "正文")
        self.assertIn("bad sendkey", str(ctx.exception))

    def test_non_json_response_does_not_raise(self):
        client, response = self._client(None)
        response.json.side_effect = ValueError("not json")
        notifier.serverchan_send(client, {"sendkey": "sctp1tA"}, "标题", "正文")

    def test_http_error_does_not_leak_sendkey(self):
        # httpx 的 HTTPStatusError 文本带完整 URL，而 URL 里就是 SendKey
        sendkey = "sctp1tSECRETKEY"
        request = httpx.Request("POST", f"https://1.push.ft07.com/send/{sendkey}.send")
        client = Mock()
        client.post.return_value = httpx.Response(403, request=request)
        with self.assertRaises(Exception) as ctx:
            notifier.serverchan_send(client, {"sendkey": sendkey}, "标题", "正文")
        self.assertNotIn(sendkey, str(ctx.exception))
        self.assertIn("403", str(ctx.exception))
        self.assertIsNone(ctx.exception.__cause__)
        self.assertTrue(ctx.exception.__suppress_context__)


class ServerChanRoutingTest(unittest.TestCase):
    def setUp(self):
        self.send = self.enterContext(patch.object(notifier, "serverchan_send"))
        self.client = Mock()
        self.channel = {"sendkey": "sctp1tA"}

    def test_task_push_uses_serverchan(self):
        notifier._send(self.client, "serverchan", self.channel, "标题", "日志", True)
        self.send.assert_called_once_with(
            self.client, self.channel, "标题", notifier.build_push_markdown("标题", "日志", True),
        )

    def test_exchange_push_uses_serverchan(self):
        with patch.object(notifier, "build_exchange_markdown", return_value="**兑换**"):
            notifier._send_exchange(self.client, "serverchan", self.channel, "标题", "商品", {}, {}, True)
        self.send.assert_called_once_with(self.client, self.channel, "标题", "**兑换**")


class PushChannelConfigTest(unittest.TestCase):
    def test_serverchan_fields_are_normalized(self):
        channels = normalize_push_channels(
            {"channels": [{"provider": "serverchan", "enable": True, "sendkey": "sctp1tA", "uid": "1"}]}
        )
        self.assertEqual(len(channels), 1)
        self.assertEqual(channels[0]["provider"], "serverchan")
        self.assertEqual(channels[0]["sendkey"], "sctp1tA")
        self.assertEqual(channels[0]["uid"], "1")

    def test_disabled_channel_with_sendkey_is_kept(self):
        channels = normalize_push_channels(
            {"channels": [{"provider": "serverchan", "enable": False, "sendkey": "sctp1tA"}]}
        )
        self.assertEqual(len(channels), 1)
        self.assertIs(channels[0]["enable"], False)

    def test_disabled_empty_channel_is_dropped(self):
        channels = normalize_push_channels({"channels": [{"provider": "serverchan", "enable": False}]})
        self.assertEqual(channels, [])
