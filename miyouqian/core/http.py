# -*- coding: utf-8 -*-
"""HTTP 客户端封装。"""

from __future__ import annotations

import time
from typing import Any

import httpx

# HTTPTransport(retries=N) 只覆盖建立连接阶段（ConnectError / ConnectTimeout），
# 连接建立后的读超时、被对端中断不会重试，需要在这里补上。
READ_RETRIES = 2
RETRY_BACKOFF_SECONDS = 1.5
RETRYABLE_ERRORS = (httpx.ReadTimeout, httpx.ReadError, httpx.RemoteProtocolError)


class ApiError(RuntimeError):
    """接口或网络错误。"""


class ApiClient:
    def __init__(self, timeout: float = 30.0) -> None:
        transport = httpx.HTTPTransport(retries=3)
        self._client = httpx.Client(
            timeout=timeout,
            transport=transport,
            follow_redirects=True,
        )

    def get_json(self, url: str, **kwargs: Any) -> dict[str, Any]:
        return self._request_json("GET", url, **kwargs)

    def post_json(self, url: str, **kwargs: Any) -> dict[str, Any]:
        return self._request_json("POST", url, **kwargs)

    def get_json_with_headers(self, url: str, **kwargs: Any) -> tuple[dict[str, Any], httpx.Headers]:
        return self._request_json_with_headers("GET", url, **kwargs)

    def get_json_any_status(self, url: str, **kwargs: Any) -> tuple[dict[str, Any], httpx.Headers]:
        """GET 并解析 JSON，HTTP 4xx/5xx 不抛错，供错误信息写在响应体里的接口使用。"""
        return self._request_json_with_headers("GET", url, check_status=False, **kwargs)

    def post_json_with_headers(self, url: str, **kwargs: Any) -> tuple[dict[str, Any], httpx.Headers]:
        return self._request_json_with_headers("POST", url, **kwargs)

    def _request_json(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        data, _headers = self._request_json_with_headers(method, url, **kwargs)
        return data

    def _request_json_with_headers(
        self, method: str, url: str, check_status: bool = True, retries: int | None = None, **kwargs: Any
    ) -> tuple[dict[str, Any], httpx.Headers]:
        """retries 是读超时等错误的额外重试次数，缺省时只有 GET 重试，POST 不重试。

        POST 读超时时服务端可能已经处理了请求，重发会造成重复提交；调用方也可以显式传
        retries=0 关闭重试（如验证码识别：重试会白等数倍时间，还可能消耗掉同一个 challenge）。
        """
        if retries is None:
            retries = READ_RETRIES if method == "GET" else 0
        try:
            response = self._send(method, url, retries, **kwargs)
            if check_status:
                response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as exc:
            raise ApiError(f"网络请求失败: {url} ({exc})") from exc
        except ValueError as exc:
            raise ApiError(f"接口返回不是 JSON: {url}") from exc
        if not isinstance(data, dict):
            raise ApiError(f"接口返回格式异常: {url}")
        return data, response.headers

    def _send(self, method: str, url: str, retries: int, **kwargs: Any) -> httpx.Response:
        attempt = 0
        while True:
            try:
                return self._client.request(method, url, **kwargs)
            except RETRYABLE_ERRORS:
                if attempt >= retries:
                    raise
                attempt += 1
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "ApiClient":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
