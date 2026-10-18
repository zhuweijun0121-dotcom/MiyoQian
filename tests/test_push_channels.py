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


class PushPlusTest(unittest.TestCase):
    """pushplus 内容过大等业务错误仍是 HTTP 200，必须看 code 字段才能判断成败。"""

    def _client(self, payload, status_code=200):
        response = Mock()
        response.status_code = status_code
        response.json.return_value = payload
        response.raise_for_status = Mock()
        client = Mock()
        client.request.return_value = response
        return client, response

    def test_success_does_not_raise(self):
        client, _ = self._client({"code": 200, "msg": "success", "data": "请求成功"})
        notifier.request_pushplus(client, "https://www.pushplus.plus/send", {"token": "t"})

    def test_business_error_is_raised_even_though_http_status_is_200(self):
        client, _ = self._client({"code": 999, "msg": "服务端验证错误", "data": "发送内容过大，不能超过2万字"})
        with self.assertRaises(ValueError) as ctx:
            notifier.request_pushplus(client, "https://www.pushplus.plus/send", {"token": "t"})
        self.assertIn("发送内容过大", str(ctx.exception))

    def test_non_json_response_does_not_raise(self):
        client, response = self._client(None)
        response.json.side_effect = ValueError("not json")
        notifier.request_pushplus(client, "https://www.pushplus.plus/send", {"token": "t"})

    def test_html_too_large_falls_back_to_markdown(self):
        # html 版本因为过大被拒绝后，_send 应该改用更紧凑的 markdown 重试，而不是直接报失败
        oversized = self._client({"code": 999, "msg": "服务端验证错误", "data": "发送内容过大，不能超过2万字"})[1]
        ok = self._client({"code": 200, "msg": "success", "data": "请求成功"})[1]
        client = Mock()
        client.request.side_effect = [oversized, ok]
        notifier._send(client, "pushplus", {"token": "t"}, "标题", "日志", True)
        self.assertEqual(client.request.call_count, 2)
        self.assertEqual(client.request.call_args_list[1].kwargs["json"]["template"], "markdown")


class NormalizeMessageLinesTest(unittest.TestCase):
    """推送内容里的「正在...」只是进度提示，紧跟的结果行已经说明成败，应该在推送里省掉。"""

    def test_progress_only_lines_are_dropped(self):
        message = "\n".join(
            [
                "# 账号 1/1: 账号1",
                "正在浏览: 帖子A",
                "阅读成功: 帖子A",
                "正在点赞: 帖子A",
                "点赞成功: 帖子A",
            ]
        )
        self.assertEqual(
            notifier.normalize_message_lines(message),
            ["# 账号 1/1: 账号1", "阅读成功: 帖子A", "点赞成功: 帖子A"],
        )

    def test_result_lines_and_blank_lines_are_kept(self):
        message = "正在签到\n\n签到成功，奖励 60原石\n"
        self.assertEqual(notifier.normalize_message_lines(message), ["签到成功，奖励 60原石"])


class TaskProgressStatusTest(unittest.TestCase):
    """推送里游戏签到/米游币任务的进度条：跑完了就算完成，不因为部分失败就整体变红。"""

    def test_mixed_result_after_all_attempts_counts_as_done(self) -> None:
        # 3 次尝试，2 次失败 1 次成功：应该算「完成」（蓝色），失败比例交给分段进度条体现。
        status = notifier.summarize_attempts(done=1, failed=2, total=3)
        self.assertEqual("完成", status["label"])
        self.assertEqual("#168df5", status["color"])
        self.assertEqual(33, status["done_percent"])
        self.assertEqual(67, status["failed_percent"])

    def test_total_failure_is_still_labeled_failed(self) -> None:
        status = notifier.summarize_attempts(done=0, failed=3, total=3)
        self.assertEqual("失败 3", status["label"])
        self.assertEqual("#b83b4b", status["color"])

    def test_html_progress_bar_renders_both_color_segments(self) -> None:
        bar = notifier.segmented_progress_html(33, 67, "完成", "#168df5")
        self.assertIn("#168df5 0 33%", bar)
        self.assertIn("#b83b4b 33% 100%", bar)

    def test_text_progress_bar_uses_hash_and_x_markers(self) -> None:
        self.assertEqual("[###xxxxxxx] 33%", notifier.segmented_text_bar(33, 67))

    def test_push_title_reflects_failure_instead_of_always_saying_completed(self) -> None:
        lines = [
            "# 账号 1/1: 沐晨",
            "游戏签到汇总：成功 2，失败 1，跳过 0",
            "游戏失败项：原神 触发验证码",
        ]
        with patch.object(notifier, "send_push", return_value="sent") as send_push:
            notifier.send_task_push({"push": {"enable": True}}, lines)
        self.assertEqual("米游签任务失败", send_push.call_args.args[1])
        self.assertFalse(send_push.call_args.kwargs["success"])


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
