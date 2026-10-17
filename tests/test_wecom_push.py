# -*- coding: utf-8 -*-

import json
import pathlib
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
import unittest
from unittest.mock import patch

import httpx

from miyouqian.core.config import load_config, normalize_push_channels, save_config
from miyouqian.service import notifier


class WeComConfigTest(unittest.TestCase):
    def test_existing_channel_defaults_to_markdown(self):
        channels = normalize_push_channels({"channels": [{"provider": "wecombot", "enable": True}]})
        self.assertIs(channels[0]["plain_text"], False)

    def test_plain_text_is_normalized_as_boolean(self):
        for value, expected in [(True, True), (False, False), ("true", True), ("false", False)]:
            with self.subTest(value=value):
                channels = normalize_push_channels({"channels": [
                    {"provider": "wecombot", "enable": True, "plain_text": value},
                ]})
                self.assertIs(channels[0]["plain_text"], expected)

    def test_empty_disabled_channel_only_keeps_explicit_text_preference(self):
        for value in (False, True):
            with self.subTest(value=value):
                channels = normalize_push_channels({"channels": [
                    {"provider": "wecombot", "enable": False, "plain_text": value},
                ]})
                self.assertEqual(len(channels), int(value))

    def test_plain_text_survives_save_and_reload(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "config.yaml"
            for value in (True, False):
                with self.subTest(value=value):
                    config = {"push": {"channels": [
                        {"provider": "wecombot", "enable": True, "plain_text": value},
                    ]}}
                    save_config(path, config)
                    self.assertIs(load_config(path)["push"]["channels"][0]["plain_text"], value)


class WeComMessageTest(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self.sent_at = []
        self.responses = []
        self.now = 0.0
        self.webhook = "https://example.invalid/" + self.id()
        self.enterContext(patch.object(notifier.time, "monotonic", side_effect=lambda: self.now))
        self.enterContext(patch.object(notifier.time, "sleep", side_effect=self.advance))
        self.client = self.enterContext(httpx.Client(transport=httpx.MockTransport(self.respond)))

    def advance(self, seconds):
        self.now += seconds

    def respond(self, request):
        self.sent.append(json.loads(request.content))
        self.sent_at.append(self.now)
        if self.responses:
            response = self.responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return response
        return httpx.Response(200, json={"errcode": 0, "errmsg": "ok"})

    def send(self, content, plain_text=True, exchange=False):
        channel = {"webhook": self.webhook, "plain_text": plain_text}
        prefix = "build_exchange_" if exchange else "build_push_"
        with patch.object(notifier, prefix + "text", return_value=content), \
                patch.object(notifier, prefix + "markdown", return_value=content):
            if exchange:
                notifier._send_exchange(self.client, "wecombot", channel, "标题", "商品", {}, {}, True)
            else:
                notifier._send(self.client, "wecombot", channel, "标题", "日志", True)

    def contents(self):
        return [p[p["msgtype"]]["content"] for p in self.sent]

    def test_short_messages_keep_format_and_content_in_both_paths(self):
        for plain_text in (False, True):
            for exchange in (False, True):
                with self.subTest(plain_text=plain_text, exchange=exchange):
                    self.sent.clear()
                    self.send("普通文本 **Markdown** 😀", plain_text, exchange)
                    kind = "text" if plain_text else "markdown"
                    self.assertEqual(self.sent, [{"msgtype": kind, kind: {"content": "普通文本 **Markdown** 😀"}}])

    def test_long_messages_preserve_every_character_in_both_paths(self):
        content = "  开始\n\n" + "中文😀 long line " * 900 + "\n\n最后唯一标记 END  \n"
        for plain_text, limit in ((False, 4096), (True, 2048)):
            for exchange in (False, True):
                with self.subTest(plain_text=plain_text, exchange=exchange):
                    self.sent.clear()
                    self.send(content, plain_text, exchange)
                    parts = self.contents()
                    self.assertGreater(len(parts), 1)
                    self.assertEqual("".join(parts), content)
                    self.assertTrue(all(0 < len(p.encode("utf-8")) <= limit for p in parts))

    def test_byte_boundaries_and_empty_content(self):
        for plain_text, limit in ((False, 4096), (True, 2048)):
            cases = ["", "a" * limit, "a" * (limit + 1), "中" * (limit // 3) + "😀", "😀" * (limit // 4 + 1)]
            for content in cases:
                with self.subTest(plain_text=plain_text, length=len(content)):
                    self.sent.clear()
                    self.send(content, plain_text)
                    self.assertEqual("".join(self.contents()), content)
                    self.assertTrue(all(len(p.encode("utf-8")) <= limit for p in self.contents()))
                    self.assertEqual(len(self.sent), 1 if len(content.encode("utf-8")) <= limit else 2)

    def test_split_prefers_complete_lines_and_preserves_blank_lines(self):
        lines = ["甲" * 300 + "\n", "\n", "乙" * 300 + "\n", "丙" * 300 + "\n"]
        self.send("".join(lines))
        self.assertEqual(self.contents(), ["".join(lines[:3]), lines[3]])

    def test_same_robot_is_paced_across_separate_notifications(self):
        self.send("a" * 5000)
        self.send("next")
        self.assertEqual(len(self.sent_at), 4)
        for earlier, later in zip(self.sent_at, self.sent_at[1:]):
            self.assertGreaterEqual(later - earlier, 3.0)

    def test_failed_part_stops_delivery_and_reports_partial_success(self):
        failures = [httpx.Response(200, json={"errcode": 45009, "errmsg": "rate limited"}),
                    httpx.Response(503), httpx.ReadTimeout("timeout")]
        for failure in failures:
            with self.subTest(failure=failure):
                self.sent.clear()
                self.responses = [httpx.Response(200, json={"errcode": 0}), failure]
                with self.assertRaisesRegex(ValueError, r"第 2/3 条.*已成功发送 1 条"):
                    self.send("x" * 5000)
                self.assertEqual(len(self.sent), 2)

    def test_invalid_success_response_is_not_reported_as_success(self):
        for response in [httpx.Response(200, text="not json"),
                         httpx.Response(200, json=[]),
                         httpx.Response(200, json={"errmsg": "missing code"})]:
            with self.subTest(response=response):
                self.responses = [response]
                with self.assertRaises(ValueError):
                    self.send("short")

    def test_api_error_cannot_echo_webhook_secrets_into_logs(self):
        self.responses = [httpx.Response(200, json={"errcode": 40000, "errmsg": "request: " + self.webhook})]
        with self.assertRaises(ValueError) as caught:
            self.send("short")
        self.assertIn("40000", str(caught.exception))
        self.assertNotIn(self.webhook, str(caught.exception))

    def test_concurrent_notifications_do_not_interleave(self):
        entered = threading.Event()
        release = threading.Event()
        second_started = threading.Event()
        second_request = threading.Event()
        seen = []

        def respond(request):
            content = json.loads(request.content)["text"]["content"]
            seen.append(content)
            if content.startswith("A") and not entered.is_set():
                entered.set()
                self.assertTrue(release.wait(timeout=2))
            if content.startswith("B"):
                second_request.set()
            return httpx.Response(200, json={"errcode": 0})

        def send(content, second=False):
            if second:
                second_started.set()
            with httpx.Client(transport=httpx.MockTransport(respond)) as client:
                notifier.send_wecom(client, self.webhook, "text", content)

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(send, "A" * 5000)
            try:
                self.assertTrue(entered.wait(timeout=2))
                second = pool.submit(send, "B" * 5000, True)
                self.assertTrue(second_started.wait(timeout=2))
                self.assertFalse(second_request.wait(timeout=0.05))
            finally:
                release.set()
            first.result(timeout=2)
            second.result(timeout=2)
        self.assertEqual([p[0] for p in seen], ["A", "A", "A", "B", "B", "B"])
        self.assertEqual("".join(seen), "A" * 5000 + "B" * 5000)

    def test_wecom_failure_is_reported_and_other_channels_continue(self):
        self.responses = [httpx.Response(200, json={"errcode": 45009, "errmsg": "rate limited"})]
        config = {"push": {"enable": True, "channels": [
            {"provider": "wecombot", "enable": True, "webhook": self.webhook, "plain_text": True},
            {"provider": "dingrobot", "enable": True, "webhook": "https://example.invalid/ding"},
        ]}}
        client = httpx.Client(transport=httpx.MockTransport(self.respond))
        with patch.object(notifier.httpx, "Client", return_value=client):
            result = notifier.send_push(config, "标题", "日志")
        self.assertIn("wecombot: 失败", result)
        self.assertIn("45009", result)
        self.assertIn("dingrobot: 成功", result)
        self.assertEqual(len(self.sent), 2)


if __name__ == "__main__":
    unittest.main()
