# -*- coding: utf-8 -*-

import unittest
from unittest.mock import Mock

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
