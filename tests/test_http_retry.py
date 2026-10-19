# -*- coding: utf-8 -*-
"""ApiClient 对读超时等错误的重试：只重试 GET，POST 和验证码识别请求不重试。"""

import unittest
from typing import Any, Callable
from unittest.mock import Mock, patch

import httpx

from miyouqian.core import captcha, http
from miyouqian.core.http import ApiClient, ApiError


def make_client(handler: Callable[[httpx.Request], httpx.Response]) -> ApiClient:
    client = ApiClient()
    client._client.close()
    client._client = httpx.Client(transport=httpx.MockTransport(handler))
    return client


def flaky_handler(failures: list[Exception]) -> tuple[Callable[[httpx.Request], httpx.Response], list[str]]:
    """前几次按顺序抛出 failures 里的异常，之后返回成功；同时记录收到的请求方法。"""
    calls: list[str] = []
    pending = list(failures)

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        if pending:
            raise pending.pop(0)
        return httpx.Response(200, json={"retcode": 0})

    return handler, calls


def read_timeout() -> httpx.ReadTimeout:
    return httpx.ReadTimeout("The read operation timed out")


class ApiClientRetryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.sleep = self.enterContext(patch.object(http.time, "sleep"))

    def test_get_read_timeout_is_retried_until_it_succeeds(self) -> None:
        handler, calls = flaky_handler([read_timeout(), read_timeout()])

        data = make_client(handler).get_json("https://example.com/info")

        self.assertEqual({"retcode": 0}, data)
        self.assertEqual(["GET", "GET", "GET"], calls)

    def test_backoff_grows_between_attempts(self) -> None:
        handler, _ = flaky_handler([read_timeout(), read_timeout()])

        make_client(handler).get_json("https://example.com/info")

        delays = [call.args[0] for call in self.sleep.call_args_list]
        self.assertEqual([http.RETRY_BACKOFF_SECONDS, http.RETRY_BACKOFF_SECONDS * 2], delays)

    def test_get_gives_up_after_the_retry_budget_with_the_usual_api_error(self) -> None:
        handler, calls = flaky_handler([read_timeout()] * 10)

        with self.assertRaises(ApiError) as ctx:
            make_client(handler).get_json("https://example.com/info")

        self.assertEqual(http.READ_RETRIES + 1, len(calls))
        self.assertIn("网络请求失败: https://example.com/info", str(ctx.exception))
        self.assertIn("read operation timed out", str(ctx.exception))

    def test_dropped_connection_and_protocol_errors_are_retried_too(self) -> None:
        for error in (httpx.ReadError("connection reset"), httpx.RemoteProtocolError("server disconnected")):
            with self.subTest(error=type(error).__name__):
                handler, calls = flaky_handler([error])

                self.assertEqual({"retcode": 0}, make_client(handler).get_json("https://example.com/info"))
                self.assertEqual(2, len(calls))

    def test_post_is_never_retried(self) -> None:
        # POST 读超时时服务端可能已经处理了请求（签到、兑换），重发会重复提交
        handler, calls = flaky_handler([read_timeout()])

        with self.assertRaises(ApiError):
            make_client(handler).post_json("https://example.com/exchange", json={})

        self.assertEqual(["POST"], calls)
        self.sleep.assert_not_called()

    def test_retries_can_be_switched_off_per_call(self) -> None:
        handler, calls = flaky_handler([read_timeout()])

        with self.assertRaises(ApiError):
            make_client(handler).get_json("https://example.com/info", retries=0)

        self.assertEqual(1, len(calls))

    def test_headers_variants_share_the_same_retry_behavior(self) -> None:
        handler, calls = flaky_handler([read_timeout()])

        data, _headers = make_client(handler).get_json_any_status("https://example.com/info")

        self.assertEqual({"retcode": 0}, data)
        self.assertEqual(2, len(calls))

    def test_http_errors_and_bad_json_are_not_retried(self) -> None:
        for response in (httpx.Response(500, json={}), httpx.Response(200, text="not json")):
            with self.subTest(status=response.status_code):
                calls: list[str] = []

                def handler(request: httpx.Request, response: httpx.Response = response) -> httpx.Response:
                    calls.append(request.method)
                    return response

                with self.assertRaises(ApiError):
                    make_client(handler).get_json("https://example.com/info")
                self.assertEqual(1, len(calls))


class CaptchaRequestsAreNotRetriedTest(unittest.TestCase):
    """识别请求读超时后重发会白等数倍时间，还可能消耗掉同一个 challenge（打码狗按次计费）。"""

    def solve(self, channel: dict[str, Any], client: Mock) -> None:
        config = {"captcha": {"enable": True, "channels": [channel]}}
        captcha.solve_game_captcha(client, config, "gt", "challenge", emit=lambda _message: None)

    def test_damagou_request_disables_retries(self) -> None:
        client = Mock()
        client.get_json.return_value = {"status": "0", "data": "c|v"}

        self.solve({"provider": "damagou", "enable": True, "userkey": "key"}, client)

        self.assertEqual(0, client.get_json.call_args.kwargs["retries"])

    def test_custom_request_disables_retries(self) -> None:
        client = Mock()
        client.get_json_any_status.return_value = ({"code": 0, "data": {"validate": "v"}}, {})

        self.solve({"provider": "custom", "enable": True, "url": "https://gt.example.com/nine"}, client)

        self.assertEqual(0, client.get_json_any_status.call_args.kwargs["retries"])


if __name__ == "__main__":
    unittest.main()
