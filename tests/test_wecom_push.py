# -*- coding: utf-8 -*-

import pathlib
import tempfile
import unittest
from unittest.mock import Mock, patch

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
        self.request = self.enterContext(patch.object(notifier, "request_json"))
        self.client = Mock()
        self.webhook = "https://example.invalid/webhook"

    def test_task_and_exchange_choose_the_requested_format(self):
        for plain_text in (None, False, True):
            for exchange in (False, True):
                with self.subTest(plain_text=plain_text, exchange=exchange):
                    self.request.reset_mock()
                    channel = {"webhook": self.webhook}
                    if plain_text is not None:
                        channel["plain_text"] = plain_text
                    prefix = "build_exchange_" if exchange else "build_push_"
                    with patch.object(notifier, prefix + "text", return_value="普通文本"), \
                            patch.object(notifier, prefix + "markdown", return_value="**Markdown**"):
                        if exchange:
                            notifier._send_exchange(self.client, "wecombot", channel, "标题", "商品", {}, {}, True)
                        else:
                            notifier._send(self.client, "wecombot", channel, "标题", "日志", True)
                    kind = "text" if plain_text else "markdown"
                    content = "普通文本" if plain_text else "**Markdown**"
                    self.request.assert_called_once_with(
                        self.client, "POST", self.webhook,
                        json={"msgtype": kind, kind: {"content": content}},
                    )

    def test_text_truncation_preserves_utf8_and_reports_truncation(self):
        for text in ("a" * 2049, "中" * 1000, "😀" * 1000, "中文😀" * 500):
            with self.subTest(prefix=text[:4]):
                result = notifier.truncate_wecom_text(text)
                self.assertLessEqual(len(result.encode("utf-8")), 2048)
                self.assertTrue(result.endswith("\n\n内容过长，已截断"))
                self.assertNotIn("\ufffd", result)
        for text in ("", "普通文本😀", "a" * 2048, "中" * 680 + "😀" * 2):
            self.assertEqual(notifier.truncate_wecom_text(text), text)

    def test_each_format_enforces_its_own_limit_in_both_send_paths(self):
        for plain_text, limit in ((False, 4096), (True, 2048)):
            for exchange in (False, True):
                with self.subTest(plain_text=plain_text, exchange=exchange):
                    channel = {"webhook": self.webhook, "plain_text": plain_text}
                    if exchange:
                        notifier._send_exchange(self.client, "wecombot", channel, "标题", "商品", {"message": "😀" * 2000}, {}, True)
                    else:
                        notifier._send(self.client, "wecombot", channel, "标题", "😀" * 2000, True)
                    payload = self.request.call_args.kwargs["json"]
                    content = payload[payload["msgtype"]]["content"]
                    self.assertLessEqual(len(content.encode("utf-8")), limit)
                    self.assertIn("内容过长，已截断", content)


if __name__ == "__main__":
    unittest.main()
