# -*- coding: utf-8 -*-
"""QQ 开放平台官方机器人推送通道的测试。"""

import json
import pathlib
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, Mock, patch

import httpx

from miyouqian.core import qq_bot
from miyouqian.core.config import normalize_push_channels, should_keep_push_channel
from miyouqian.service import notifier, web


def make_channel(**extra) -> dict:
    return {
        "provider": "qqbot",
        "enable": True,
        "app_id": "123456",
        "client_secret": "secret",
        "user_openid": "",
        "group_openid": "",
        "message_format": "text",
        "api_base": "",
        **extra,
    }


def json_response(payload, status_code: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code,
        json=payload,
        request=httpx.Request("POST", "https://api.bot.qq.com/test"),
    )


def make_context_manager(bot: Mock) -> Mock:
    """`with QQBot(...) as bot` 拿到的是 __enter__ 的返回值，不是 mock 本身。"""
    holder = Mock()
    holder.__enter__ = Mock(return_value=bot)
    holder.__exit__ = Mock(return_value=False)
    return holder


def stop_logger_file_sink() -> None:
    """关掉 loguru 的日志文件句柄，否则 Windows 删不掉临时目录。"""
    from miyouqian.core import logs

    logs.logger.remove()
    # 不清掉这个标记，下一次 configure_logger 会以为同一个路径已配置过而不再建 sink
    logs._configured_path = None


class PayloadTest(unittest.TestCase):
    def test_text_uses_msg_type_zero(self):
        self.assertEqual(qq_bot.build_message_payload("text", "hi"), {"msg_type": 0, "content": "hi"})

    def test_markdown_uses_msg_type_two(self):
        self.assertEqual(
            qq_bot.build_message_payload("markdown", "hi"),
            {"msg_type": 2, "markdown": {"content": "hi"}},
        )

    def test_only_markdown_switches_the_format(self):
        self.assertEqual(qq_bot.message_format_of({"message_format": "MARKDOWN"}), "markdown")
        for value in ("", "text", "html", None):
            with self.subTest(value=value):
                self.assertEqual(qq_bot.message_format_of({"message_format": value}), "text")


class ParseResponseTest(unittest.TestCase):
    def test_business_error_in_a_200_response_is_raised(self):
        # 官方失败时 HTTP 仍是 200，业务错误只在 err_code 里，不能只看状态码
        with self.assertRaises(qq_bot.QQBotError) as ctx:
            qq_bot.parse_response(json_response({"err_code": 1100101, "message": "内容敏感"}), "发送消息")
        self.assertIn("1100101", str(ctx.exception))
        self.assertIn("内容敏感", str(ctx.exception))

    def test_token_endpoint_style_code_error_is_also_caught(self):
        # /app/getAppAccessToken 的业务错误用的是 code 而不是 err_code
        with self.assertRaises(qq_bot.QQBotError) as ctx:
            qq_bot.parse_response(json_response({"code": 100007, "message": "appid invalid"}), "获取 access_token")
        self.assertIn("100007", str(ctx.exception))

    def test_ok_err_code_is_accepted(self):
        self.assertEqual(qq_bot.parse_response(json_response({"err_code": 0, "id": "m1"}), "发送")["id"], "m1")

    def test_a_successful_send_response_has_no_err_code(self):
        self.assertEqual(qq_bot.parse_response(json_response({"id": "m1", "timestamp": "t"}), "发送")["id"], "m1")

    def test_http_error_is_raised_with_the_body(self):
        with self.assertRaises(qq_bot.QQBotError) as ctx:
            qq_bot.parse_response(json_response({"message": "bad"}, status_code=401), "发送消息")
        self.assertIn("401", str(ctx.exception))

    def test_non_json_response_is_reported(self):
        response = httpx.Response(
            200, text="<html></html>", request=httpx.Request("POST", "https://api.bot.qq.com/x")
        )
        with self.assertRaises(qq_bot.QQBotError):
            qq_bot.parse_response(response, "发送消息")


class ExpiresInTest(unittest.TestCase):
    def test_string_and_number_are_both_accepted(self):
        self.assertEqual(qq_bot.parse_expires_in("7200"), 7200)
        self.assertEqual(qq_bot.parse_expires_in(1800), 1800)

    def test_invalid_value_falls_back_to_the_documented_two_hours(self):
        for value in ("", None, "abc", 0, -5):
            with self.subTest(value=value):
                self.assertEqual(qq_bot.parse_expires_in(value), 7200)


class TokenTest(unittest.TestCase):
    def setUp(self):
        qq_bot._token_cache.clear()

    def test_missing_credentials_are_rejected_before_any_request(self):
        client = Mock()
        with self.assertRaises(qq_bot.QQBotError):
            qq_bot.fetch_access_token(client, "", "secret")
        client.post.assert_not_called()

    def test_token_is_cached_and_not_fetched_again(self):
        client = Mock()
        client.post.return_value = json_response({"access_token": "tok-1", "expires_in": "7200"})
        bot = qq_bot.QQBot(make_channel(), http=client)
        self.assertEqual(bot.access_token(), "tok-1")
        self.assertEqual(bot.access_token(), "tok-1")
        self.assertEqual(client.post.call_count, 1)

    def test_expired_token_is_refreshed(self):
        client = Mock()
        client.post.side_effect = [
            json_response({"access_token": "tok-1", "expires_in": "7200"}),
            json_response({"access_token": "tok-2", "expires_in": "7200"}),
        ]
        bot = qq_bot.QQBot(make_channel(), http=client)
        self.assertEqual(bot.access_token(), "tok-1")
        # 手动把缓存改成已过期，模拟 2 小时后再次推送
        qq_bot._token_cache[("123456", "secret")] = ("tok-1", 0)
        self.assertEqual(bot.access_token(), "tok-2")
        self.assertEqual(client.post.call_count, 2)

    def test_token_request_body_matches_the_official_schema(self):
        client = Mock()
        client.post.return_value = json_response({"access_token": "tok", "expires_in": 7200})
        qq_bot.fetch_access_token(client, "123456", "secret")
        args, kwargs = client.post.call_args
        self.assertEqual(args[0], qq_bot.DEFAULT_TOKEN_URL)
        self.assertEqual(kwargs["json"], {"appId": "123456", "clientSecret": "secret"})

    def test_credentials_are_required_before_sending(self):
        bot = qq_bot.QQBot(make_channel(app_id="", user_openid="u1"), http=Mock())
        with self.assertRaises(qq_bot.QQBotError):
            bot.send("hello")


class SendTest(unittest.TestCase):
    def setUp(self):
        qq_bot._token_cache.clear()

    def make_bot(self, **channel_extra) -> tuple[qq_bot.QQBot, Mock]:
        client = Mock()
        client.post.side_effect = [
            json_response({"access_token": "tok", "expires_in": 7200}),
            json_response({"id": "msg-1"}),
            json_response({"id": "msg-2"}),
        ]
        return qq_bot.QQBot(make_channel(**channel_extra), http=client), client

    def test_no_target_is_rejected(self):
        bot, client = self.make_bot()
        with self.assertRaises(qq_bot.QQBotError):
            bot.send("hello")
        client.post.assert_not_called()

    def test_empty_message_is_rejected(self):
        bot, client = self.make_bot(user_openid="u1")
        with self.assertRaises(qq_bot.QQBotError):
            bot.send("   ")
        client.post.assert_not_called()

    def test_private_message_uses_the_users_endpoint(self):
        bot, client = self.make_bot(user_openid="user-openid-1")
        bot.send("hello")
        urls = [call.args[0] for call in client.post.call_args_list]
        self.assertIn("https://api.bot.qq.com/v2/users/user-openid-1/messages", urls)
        message_call = client.post.call_args_list[1]
        self.assertEqual(message_call.kwargs["json"], {"msg_type": 0, "content": "hello"})
        self.assertEqual(message_call.kwargs["headers"]["Authorization"], "QQBot tok")

    def test_group_message_uses_the_groups_endpoint(self):
        bot, client = self.make_bot(group_openid="group-openid-1")
        bot.send("hello")
        urls = [call.args[0] for call in client.post.call_args_list]
        self.assertIn("https://api.bot.qq.com/v2/groups/group-openid-1/messages", urls)

    def test_both_targets_are_sent(self):
        bot, client = self.make_bot(user_openid="u1", group_openid="g1")
        self.assertEqual(bot.send("hello"), ["私聊成功", "群聊成功"])
        self.assertEqual(client.post.call_count, 3)

    def test_one_failing_target_does_not_block_the_other(self):
        client = Mock()
        client.post.side_effect = [
            json_response({"access_token": "tok", "expires_in": 7200}),
            json_response({"err_code": 11253, "message": "no permission"}),
            json_response({"id": "msg-2"}),
        ]
        bot = qq_bot.QQBot(make_channel(user_openid="u1", group_openid="g1"), http=client)
        result = bot.send("hello")
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0], "群聊成功")
        self.assertTrue(result[1].startswith("私聊失败"))
        self.assertIn("11253", result[1])

    def test_all_targets_failing_raises(self):
        client = Mock()
        client.post.side_effect = [
            json_response({"access_token": "tok", "expires_in": 7200}),
            json_response({"err_code": 11253, "message": "no permission"}),
            json_response({"err_code": 11253, "message": "no permission"}),
        ]
        bot = qq_bot.QQBot(make_channel(user_openid="u1", group_openid="g1"), http=client)
        with self.assertRaises(qq_bot.QQBotError):
            bot.send("hello")

    def test_markdown_format_is_used_when_configured(self):
        bot, client = self.make_bot(user_openid="u1", message_format="markdown")
        bot.send("**hello**")
        self.assertEqual(
            client.post.call_args_list[1].kwargs["json"],
            {"msg_type": 2, "markdown": {"content": "**hello**"}},
        )

    def test_custom_api_base_replaces_the_default_host(self):
        bot, client = self.make_bot(user_openid="u1", api_base="https://sandbox.api.sgroup.qq.com/")
        bot.send("hello")
        urls = [call.args[0] for call in client.post.call_args_list]
        self.assertIn("https://sandbox.api.sgroup.qq.com/v2/users/u1/messages", urls)


class OpenIdMatchingTest(unittest.TestCase):
    def test_private_message_needs_a_matching_code(self):
        notify = Mock()
        data = {"author": {"user_openid": "u1"}, "content": "1234"}
        self.assertEqual(qq_bot.match_openid("C2C_MESSAGE_CREATE", data, "1234", False, notify), "u1")
        self.assertEqual(qq_bot.match_openid("C2C_MESSAGE_CREATE", data, "9999", False, notify), "")
        notify.assert_called()

    def test_friend_add_event_carries_the_openid_directly(self):
        self.assertEqual(qq_bot.match_openid("FRIEND_ADD", {"openid": "u2"}, "1234", False, Mock()), "u2")

    def test_group_message_needs_a_matching_code(self):
        data = {"group_openid": "g1", "content": "1234"}
        self.assertEqual(qq_bot.match_openid("GROUP_AT_MESSAGE_CREATE", data, "1234", True, Mock()), "g1")
        self.assertEqual(qq_bot.match_openid("GROUP_MESSAGE_CREATE", data, "0000", True, Mock()), "")

    def test_group_add_robot_event_carries_the_openid_directly(self):
        self.assertEqual(qq_bot.match_openid("GROUP_ADD_ROBOT", {"group_openid": "g2"}, "1234", True, Mock()), "g2")

    def test_a_private_event_never_satisfies_a_group_bind(self):
        data = {"author": {"user_openid": "u1"}, "content": "1234"}
        self.assertEqual(qq_bot.match_openid("C2C_MESSAGE_CREATE", data, "1234", True, Mock()), "")

    def test_unrelated_events_are_ignored(self):
        self.assertEqual(qq_bot.match_openid("READY", {}, "1234", False, Mock()), "")


class VerifyCodeTest(unittest.TestCase):
    def test_code_is_numeric_and_long_enough(self):
        code = qq_bot.generate_verify_code()
        self.assertEqual(len(code), 6)
        self.assertTrue(code.isdigit())


class FakeGateway:
    """假网关：按顺序吐出预设的帧，帧用完就阻塞，交给超时逻辑处理。"""

    def __init__(self, frames: list[dict]) -> None:
        self.pending = [json.dumps(frame) for frame in frames]
        self.sent: list[dict] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def send(self, raw: str) -> None:
        self.sent.append(json.loads(raw))

    def recv(self, timeout: float | None = None) -> str:
        if not self.pending:
            raise TimeoutError
        return self.pending.pop(0)


def make_gateway_factory(gateway: FakeGateway):
    return lambda *args, **kwargs: gateway


class BindGatewayTest(unittest.TestCase):
    """用假网关跑一遍握手/心跳/事件解析，确认绑定流程真的能拿到 OpenID。"""

    def run_bind(self, events: list[dict], target: str = "user", timeout: float = 5.0, cancel=None):
        frames = [{"op": 10, "d": {"heartbeat_interval": 60000}}] + events
        gateway = FakeGateway(frames)
        with patch.object(qq_bot, "fetch_access_token", return_value=("tok-1", 7200)), patch.object(
            qq_bot, "start_heartbeat", return_value=(None, lambda _s: None)
        ):
            openid = qq_bot.bind_openid(
                app_id="1",
                client_secret="s",
                verify_code="246810",
                target=target,
                timeout=timeout,
                # 直接给网关地址，避免测试打到真实接口
                gateway_url="wss://fake/gateway",
                connect=make_gateway_factory(gateway),
                cancel=cancel,
            )
        return openid, gateway.sent

    def test_private_bind_identifies_then_takes_the_openid_from_the_message(self):
        event = {
            "op": 0,
            "s": 42,
            "t": "C2C_MESSAGE_CREATE",
            "d": {"author": {"user_openid": "U-1"}, "content": "246810"},
        }
        openid, sent = self.run_bind([event])
        self.assertEqual(openid, "U-1")
        # 第一个包必须是 op=2 的鉴权包，否则网关不会推事件
        self.assertEqual(sent[0]["op"], 2)
        self.assertEqual(sent[0]["d"]["token"], "QQBot tok-1")
        self.assertEqual(sent[0]["d"]["intents"], qq_bot.GROUP_AND_C2C_INTENT)

    def test_group_bind_reads_the_group_openid(self):
        event = {
            "op": 0,
            "s": 7,
            "t": "GROUP_AT_MESSAGE_CREATE",
            "d": {"group_openid": "G-1", "content": "246810"},
        }
        openid, _ = self.run_bind([event], target="group")
        self.assertEqual(openid, "G-1")

    def test_robot_added_to_group_is_enough(self):
        event = {"op": 0, "s": 3, "t": "GROUP_ADD_ROBOT", "d": {"group_openid": "G-3"}}
        openid, _ = self.run_bind([event], target="group")
        self.assertEqual(openid, "G-3")

    def test_a_message_with_the_wrong_code_is_skipped(self):
        wrong = {
            "op": 0, "s": 1, "t": "C2C_MESSAGE_CREATE",
            "d": {"author": {"user_openid": "U-WRONG"}, "content": "000000"},
        }
        right = {
            "op": 0, "s": 2, "t": "C2C_MESSAGE_CREATE",
            "d": {"author": {"user_openid": "U-RIGHT"}, "content": "验证码 246810"},
        }
        openid, _ = self.run_bind([wrong, right])
        self.assertEqual(openid, "U-RIGHT")

    def test_authentication_failure_is_reported_clearly(self):
        with self.assertRaises(qq_bot.QQBotError) as ctx:
            self.run_bind([{"op": 9, "d": None}])
        self.assertIn("鉴权失败", str(ctx.exception))

    def test_missing_group_event_times_out_with_a_hint(self):
        with self.assertRaises(qq_bot.QQBotError) as ctx:
            self.run_bind([], target="group", timeout=5)
        self.assertIn("绑定超时", str(ctx.exception))
        self.assertIn("5 秒", str(ctx.exception))
        self.assertIn("@机器人", str(ctx.exception))

    def test_cancellation_stops_the_wait(self):
        with self.assertRaises(qq_bot.QQBotError) as ctx:
            self.run_bind([], timeout=60, cancel=lambda: True)
        self.assertIn("取消", str(ctx.exception))

    def test_missing_websockets_dependency_gives_a_clear_error(self):
        with patch.dict("sys.modules", {"websockets.sync.client": None}):
            with self.assertRaises(qq_bot.QQBotError) as ctx:
                qq_bot.bind_openid(app_id="1", client_secret="s", verify_code="246810")
        self.assertIn("websockets", str(ctx.exception))


class HeartbeatTest(unittest.TestCase):
    def test_heartbeat_sends_the_latest_sequence(self):
        sent: list[dict] = []

        class Socket:
            def send(self, raw):
                sent.append(json.loads(raw))

        stop = threading.Event()
        thread, update = qq_bot.start_heartbeat(Socket(), 0.01, stop)
        update(99)
        time.sleep(0.05)
        stop.set()
        thread.join(timeout=2)
        self.assertTrue(sent, "心跳应该至少发过一次")
        self.assertTrue(all(frame["op"] == 1 for frame in sent))
        self.assertIn({"op": 1, "d": 99}, sent)

    def test_heartbeat_interval_is_parsed_from_hello(self):
        self.assertEqual(qq_bot.heartbeat_interval({"d": {"heartbeat_interval": 30000}}), 30.0)
        self.assertEqual(qq_bot.heartbeat_interval({"d": {"heartbeat_interval": "5000"}}), 5.0)
        self.assertEqual(qq_bot.heartbeat_interval({}), 45.0)
        # 间隔过小会让网关判定为刷心跳，兜底到 1 秒
        self.assertEqual(qq_bot.heartbeat_interval({"d": {"heartbeat_interval": 10}}), 1.0)


class PushChannelConfigTest(unittest.TestCase):
    def test_qqbot_fields_are_normalized(self):
        channels = normalize_push_channels(
            {
                "channels": [
                    {
                        "provider": "qqbot",
                        "enable": True,
                        "app_id": "1",
                        "client_secret": "s",
                        "user_openid": "u",
                        "group_openid": "g",
                        "message_format": "markdown",
                    }
                ]
            }
        )
        self.assertEqual(len(channels), 1)
        self.assertEqual(channels[0]["provider"], "qqbot")
        self.assertEqual(channels[0]["message_format"], "markdown")
        self.assertEqual(channels[0]["user_openid"], "u")
        self.assertEqual(channels[0]["api_base"], "")

    def test_unknown_message_format_falls_back_to_text(self):
        channels = normalize_push_channels(
            {"channels": [{"provider": "qqbot", "enable": True, "message_format": "html"}]}
        )
        self.assertEqual(channels[0]["message_format"], "text")

    def test_empty_disabled_channel_is_dropped(self):
        # message_format 的默认值不该让一个从没配置过的空通道一直被保留
        self.assertEqual(normalize_push_channels({"channels": [{"provider": "qqbot", "enable": False}]}), [])

    def test_disabled_channel_with_credentials_is_kept(self):
        channels = normalize_push_channels({"channels": [{"provider": "qqbot", "enable": False, "app_id": "1"}]})
        self.assertEqual(len(channels), 1)
        self.assertIs(channels[0]["enable"], False)

    def test_explicit_markdown_keeps_a_disabled_channel(self):
        self.assertTrue(
            should_keep_push_channel({"provider": "qqbot", "enable": False, "message_format": "markdown"})
        )
        channels = normalize_push_channels(
            {"channels": [{"provider": "qqbot", "enable": False, "message_format": "markdown"}]}
        )
        self.assertEqual(len(channels), 1)

    def test_qq_onebot_channel_is_unaffected(self):
        channels = normalize_push_channels(
            {
                "channels": [
                    {
                        "provider": "qq",
                        "enable": True,
                        "push_url": "http://127.0.0.1:5700",
                        "access_token": "t",
                        "send_id": "1",
                        "msg_type": "group",
                    }
                ]
            }
        )
        self.assertEqual(channels[0]["provider"], "qq")
        self.assertEqual(channels[0]["push_url"], "http://127.0.0.1:5700")


class NotifierRoutingTest(unittest.TestCase):
    @patch.object(notifier, "send_qq_bot_message")
    def test_task_push_uses_qqbot_with_the_plain_text_build(self, send):
        channel = make_channel()
        notifier._send(Mock(), "qqbot", channel, "标题", "日志", True)
        send.assert_called_once_with(channel, notifier.build_push_text("标题", "日志", True))

    @patch.object(notifier, "send_qq_bot_message")
    def test_exchange_push_uses_qqbot(self, send):
        with patch.object(notifier, "build_exchange_text", return_value="兑换正文"):
            notifier._send_exchange(Mock(), "qqbot", make_channel(), "标题", "商品", {}, {}, True)
        send.assert_called_once_with(make_channel(), "兑换正文")

    def test_partial_failure_is_surfaced(self):
        bot = MagicMock()
        bot.send.return_value = ["群聊成功", "私聊失败 (没有权限)"]
        with patch.object(notifier, "QQBot", return_value=make_context_manager(bot)) as factory:
            with self.assertRaises(ValueError) as ctx:
                notifier.send_qq_bot_message(make_channel(), "日志")
        self.assertIn("私聊失败", str(ctx.exception))
        factory.assert_called_once()

    def test_full_success_does_not_raise(self):
        bot = MagicMock()
        bot.send.return_value = ["私聊成功"]
        with patch.object(notifier, "QQBot", return_value=make_context_manager(bot)):
            notifier.send_qq_bot_message(make_channel(), "日志")


class EnsurePushChannelTest(unittest.TestCase):
    def test_missing_channel_is_created_with_defaults(self):
        config = {"push": {"channels": []}}
        channel = web.ensure_push_channel(config, "qqbot")
        self.assertEqual(channel["provider"], "qqbot")
        self.assertEqual(channel["message_format"], "text")
        self.assertIn(channel, config["push"]["channels"])

    def test_existing_channel_is_returned_untouched(self):
        existing = {"provider": "qqbot", "enable": False, "app_id": "1"}
        config = {"push": {"channels": [existing]}}
        self.assertIs(web.ensure_push_channel(config, "qqbot"), existing)
        self.assertEqual(len(config["push"]["channels"]), 1)


class WebAppBindTest(unittest.TestCase):
    def make_app(self) -> web.WebApp:
        app = web.WebApp.__new__(web.WebApp)
        app.lock = threading.RLock()
        app.config = {"accounts": [], "push": {"channels": []}}
        app.config_path = "unused.yaml"
        app.qq_bind_state = {"running": False, "status": "idle"}
        app._qq_bind_cancel = threading.Event()
        app.log = lambda *args, **kwargs: None
        return app

    def test_missing_credentials_are_rejected(self):
        app = self.make_app()
        with self.assertRaises(ValueError):
            app.start_qq_bind({"target": "user", "app_id": "", "client_secret": "s"})
        with self.assertRaises(ValueError):
            app.start_qq_bind({"target": "user", "app_id": "1", "client_secret": ""})

    def test_invalid_target_is_rejected(self):
        app = self.make_app()
        with self.assertRaises(ValueError):
            app.start_qq_bind({"target": "channel", "app_id": "1", "client_secret": "s"})

    def test_second_bind_is_refused_while_one_is_running(self):
        app = self.make_app()
        app.qq_bind_state = {"running": True, "status": "starting"}
        with self.assertRaises(RuntimeError):
            app.start_qq_bind({"target": "user", "app_id": "1", "client_secret": "s"})

    def test_cancel_requires_a_running_bind(self):
        app = self.make_app()
        with self.assertRaises(RuntimeError):
            app.cancel_qq_bind()

    def test_cancel_sets_the_flag_for_the_worker(self):
        app = self.make_app()
        app.qq_bind_state = {"running": True, "status": "starting", "message": ""}
        app.cancel_qq_bind()
        self.assertTrue(app._qq_bind_cancel.is_set())
        # 状态由 worker 收尾，避免两边同时改状态互相覆盖
        self.assertTrue(app.qq_bind_state["running"])
        self.assertEqual(app.qq_bind_state["message"], "正在取消绑定")

    def test_successful_bind_writes_the_openid_back_to_the_config(self):
        app = self.make_app()
        with patch.object(web, "save_config"), patch.object(web, "bind_qq_openid", return_value="user-openid-9"):
            app._qq_bind_worker("user", "1", "s", "", "123456")
        channel = app.config["push"]["channels"][0]
        self.assertEqual(channel["user_openid"], "user-openid-9")
        self.assertEqual(app.qq_bind_state["status"], "success")
        self.assertFalse(app.qq_bind_state["running"])

    def test_group_bind_writes_the_group_openid(self):
        app = self.make_app()
        with patch.object(web, "save_config"), patch.object(web, "bind_qq_openid", return_value="group-openid-9"):
            app._qq_bind_worker("group", "1", "s", "", "123456")
        self.assertEqual(app.config["push"]["channels"][0]["group_openid"], "group-openid-9")
        self.assertEqual(app.qq_bind_state["field"], "group_openid")

    def test_failing_bind_records_the_error(self):
        app = self.make_app()
        with patch.object(web, "bind_qq_openid", side_effect=qq_bot.QQBotError("没有权限")):
            app._qq_bind_worker("group", "1", "s", "", "123456")
        self.assertEqual(app.qq_bind_state["status"], "error")
        self.assertIn("没有权限", app.qq_bind_state["message"])

    def test_cancelled_bind_does_not_persist_the_openid(self):
        app = self.make_app()
        app._qq_bind_cancel.set()
        with patch.object(web, "save_config") as save, patch.object(
            web, "bind_qq_openid", return_value="user-openid-9"
        ):
            app._qq_bind_worker("user", "1", "s", "", "123456")
        save.assert_not_called()
        self.assertEqual(app.qq_bind_state["status"], "error")


class ConfigSecretMaskingTest(unittest.TestCase):
    def test_client_secret_is_masked_in_config_change_logs(self):
        self.assertTrue(web.is_sensitive_config_path("push.channels[0].client_secret"))
        # AppID 不是敏感信息，方便在日志里核对是不是配错了机器人
        self.assertFalse(web.is_sensitive_config_path("push.channels[0].app_id"))


class HttpRouteTest(unittest.TestCase):
    """把 /api/qqbot/* 接到真实 HTTP 处理流程上跑一遍，确认路由和鉴权都对得上。"""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmpdir.cleanup)
        self.config_path = pathlib.Path(self.tmpdir.name) / "config.yaml"
        self.app = web.WebApp(self.config_path, host="127.0.0.1", port=0)
        self.app.log = lambda *args, **kwargs: None
        # 没设过密码时控制台强制认证，测试里先设一个再用会话 cookie 访问
        self.token = self.app.auth_setup("test-password-123")
        web.Handler.app = self.app
        self.server = web.ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
        self.addCleanup(self.server.server_close)
        self.addCleanup(stop_logger_file_sink)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def request(self, method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
        response = httpx.request(
            method,
            f"{self.base}{path}",
            json=payload,
            headers={
                "Content-Type": "application/json",
                "Cookie": f"{web.AUTH_COOKIE}={self.token}",
            },
            timeout=10,
        )
        try:
            return response.status_code, response.json()
        except ValueError:
            return response.status_code, {}

    def test_config_round_trips_a_qqbot_channel(self):
        status, config = self.request("GET", "/api/config")
        self.assertEqual(status, 200)
        config["push"]["channels"] = [
            {
                "provider": "qqbot",
                "enable": True,
                "app_id": "102000000",
                "client_secret": "top-secret",
                "user_openid": "U1",
                "group_openid": "",
                "message_format": "markdown",
                "api_base": "",
            }
        ]
        self.assertEqual(self.request("POST", "/api/config", config)[0], 200)

        channel = self.request("GET", "/api/config")[1]["push"]["channels"][0]
        self.assertEqual(channel["provider"], "qqbot")
        self.assertEqual(channel["app_id"], "102000000")
        self.assertEqual(channel["client_secret"], "top-secret")
        self.assertEqual(channel["user_openid"], "U1")
        self.assertEqual(channel["message_format"], "markdown")

    def test_bind_status_starts_idle(self):
        status, body = self.request("GET", "/api/qqbot/bind/status")
        self.assertEqual(status, 200)
        self.assertFalse(body["running"])

    def test_bind_without_credentials_is_a_400(self):
        status, body = self.request("POST", "/api/qqbot/bind", {"target": "user", "app_id": "", "client_secret": ""})
        self.assertEqual(status, 400)
        self.assertIn("AppID", body["error"])

    def test_cancel_without_a_running_bind_is_a_400(self):
        status, body = self.request("POST", "/api/qqbot/bind/cancel", {})
        self.assertEqual(status, 400)
        self.assertIn("没有进行中", body["error"])

    def test_bind_endpoint_starts_a_worker_and_reports_the_code(self):
        config = self.request("GET", "/api/config")[1]
        config["push"]["channels"] = [{"provider": "qqbot", "enable": True, "app_id": "1", "client_secret": "s"}]
        self.request("POST", "/api/config", config)

        started = threading.Event()

        def fake_bind(**kwargs):
            started.set()
            return "U-OPENID"

        with patch.object(web, "bind_qq_openid", side_effect=fake_bind):
            status, body = self.request(
                "POST", "/api/qqbot/bind", {"target": "user", "app_id": "1", "client_secret": "s"}
            )
            self.assertEqual(status, 200)
            # worker 跑得很快时响应里可能已经结束了，所以只校验验证码本身
            self.assertEqual(len(body["verify_code"]), 6)
            self.assertTrue(started.wait(5))
            state = {}
            for _ in range(100):
                _, state = self.request("GET", "/api/qqbot/bind/status")
                if not state["running"]:
                    break
                time.sleep(0.05)
        self.assertEqual(state["status"], "success")
        self.assertEqual(state["openid"], "U-OPENID")
        self.assertEqual(state["field"], "user_openid")
        # 绑定结果要落盘，用户刷新页面后不用再绑一次
        self.assertEqual(self.request("GET", "/api/config")[1]["push"]["channels"][0]["user_openid"], "U-OPENID")


if __name__ == "__main__":
    unittest.main()
