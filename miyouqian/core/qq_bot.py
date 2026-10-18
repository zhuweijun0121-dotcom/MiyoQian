# -*- coding: utf-8 -*-
"""QQ 开放平台（QQ 机器人官方）推送通道。

和项目原有的 `onebot.py` 无关：OneBot 对接的是第三方 QQ 机器人框架的 HTTP API，
这里对接 QQ 开放平台官方 OpenAPI，用 AppID + AppSecret 换 access_token 后发消息。

官方接口有两个容易踩的点：

1. 业务错误写在 HTTP 200 的响应体 `err_code` 里，只看状态码会把失败当成功；
2. `user_openid` / `group_openid` 没有查询接口，只能靠机器人收到消息时被动获取，
   所以 `bind_openid` 会连一次 WebSocket 网关，等用户发验证码来换 OpenID。
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from typing import Any

import httpx

DEFAULT_API_BASE = "https://api.bot.qq.com"
DEFAULT_TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"

# 群聊与单聊事件（@机器人 消息、机器人入群、被添加好友）：1 << 25
GROUP_AND_C2C_INTENT = 33554432

# 换 token 时提前刷新的秒数，避免边界过期
TOKEN_REFRESH_MARGIN_SECONDS = 60

# 一个进程内共享 token 缓存：一次推送可能同时发私聊和群聊，没必要重复换 token
_token_lock = threading.Lock()
_token_cache: dict[tuple[str, str], tuple[str, float]] = {}


class QQBotError(RuntimeError):
    """QQ 开放平台调用失败。"""


def message_format_of(channel: dict[str, Any]) -> str:
    """消息格式，只认 markdown，其余一律按纯文本处理。"""
    return "markdown" if str(channel.get("message_format") or "").strip().lower() == "markdown" else "text"


def api_base_of(channel: dict[str, Any]) -> str:
    return str(channel.get("api_base") or "").strip().rstrip("/") or DEFAULT_API_BASE


def auth_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"QQBot {token}",
        "Content-Type": "application/json; charset=utf-8",
    }


def build_message_payload(message_format: str, text: str) -> dict[str, Any]:
    """纯文本用 msg_type=0；markdown 用 msg_type=2，内容放在 `markdown.content`。"""
    if message_format == "markdown":
        return {"msg_type": 2, "markdown": {"content": text}}
    return {"msg_type": 0, "content": text}


class QQBot:
    """按渠道配置调用 QQ 官方 OpenAPI：换 token + 发消息。"""

    def __init__(self, channel: dict[str, Any], http: httpx.Client | None = None) -> None:
        self.channel = channel
        self.app_id = str(channel.get("app_id") or "").strip()
        self.client_secret = str(channel.get("client_secret") or "").strip()
        self.api_base = api_base_of(channel)
        self._http = http or httpx.Client(timeout=20.0, follow_redirects=True)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "QQBot":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @property
    def user_openid(self) -> str:
        return str(self.channel.get("user_openid") or "").strip()

    @property
    def group_openid(self) -> str:
        return str(self.channel.get("group_openid") or "").strip()

    def targets(self) -> list[tuple[str, str]]:
        targets: list[tuple[str, str]] = []
        if self.user_openid:
            targets.append(("私聊", f"{self.api_base}/v2/users/{self.user_openid}/messages"))
        if self.group_openid:
            targets.append(("群聊", f"{self.api_base}/v2/groups/{self.group_openid}/messages"))
        return targets

    def send(self, message: str) -> list[str]:
        """按配置发消息，返回每个目标的结果；私聊和群聊互相独立。

        只有一边失败时不算整体失败（返回里带上失败原因），两个都失败才抛错，
        避免推送结果里报「成功」而用户其实什么都没收到。
        """
        text = str(message or "").strip()
        if not text:
            raise QQBotError("消息内容为空，QQ 机器人不允许发送空消息")
        targets = self.targets()
        if not targets:
            raise QQBotError("请先绑定私聊 OpenID 或群 OpenID")

        token = self.access_token()
        payload = build_message_payload(message_format_of(self.channel), text)
        results: list[str] = []
        failures: list[str] = []
        for label, url in targets:
            try:
                response = self._http.post(url, headers=auth_headers(token), json=payload)
                parse_response(response, f"发送{label}消息")
            except Exception as exc:  # 单目标失败不影响另一个目标
                failures.append(f"{label}失败 ({exc})")
            else:
                results.append(f"{label}成功")
        if not results:
            raise QQBotError("；".join(failures))
        return results + failures

    def access_token(self) -> str:
        """取 access_token，进程内按 AppID 缓存到快过期为止。"""
        if not self.app_id or not self.client_secret:
            raise QQBotError("请先填写 AppID 和 AppSecret")
        key = (self.app_id, self.client_secret)
        now = time.time()
        with _token_lock:
            cached = _token_cache.get(key)
            if cached and cached[1] > now:
                return cached[0]
        token, expires_in = fetch_access_token(self._http, self.app_id, self.client_secret)
        with _token_lock:
            _token_cache[key] = (token, now + max(expires_in - TOKEN_REFRESH_MARGIN_SECONDS, 30))
        return token


def fetch_access_token(
    client: httpx.Client,
    app_id: str,
    client_secret: str,
    token_url: str = DEFAULT_TOKEN_URL,
) -> tuple[str, int]:
    """换取 access_token，返回 `(token, expires_in 秒)`。"""
    if not app_id or not client_secret:
        raise QQBotError("请先填写 AppID 和 AppSecret")
    try:
        response = client.post(token_url, json={"appId": app_id, "clientSecret": client_secret})
    except httpx.HTTPError as exc:
        raise QQBotError(f"获取 access_token 失败: {exc}") from exc
    data = parse_response(response, "获取 access_token")
    token = str(data.get("access_token") or "")
    if not token:
        raise QQBotError("获取 access_token 失败: 响应里没有 access_token")
    return token, parse_expires_in(data.get("expires_in"))


def parse_expires_in(value: Any) -> int:
    """`expires_in` 官方有时返回字符串，统一成整数秒。"""
    try:
        seconds = int(float(value))
    except (TypeError, ValueError):
        return 7200
    return seconds if seconds > 0 else 7200


def parse_response(response: httpx.Response, action: str) -> dict[str, Any]:
    """校验 HTTP 状态码和响应体业务错误码，返回解析后的 JSON。

    官方失败时 HTTP 可能仍是 200、业务错误只在 `err_code` / `code` 里，
    所以不能只看状态码。
    """
    if response.status_code >= 400:
        raise QQBotError(f"{action}失败: HTTP {response.status_code} {response_body_text(response)}")
    try:
        data = response.json()
    except ValueError as exc:
        raise QQBotError(f"{action}失败: 响应不是 JSON") from exc
    if not isinstance(data, dict):
        raise QQBotError(f"{action}失败: 响应格式异常")
    err_code = data.get("err_code", data.get("code"))
    if err_code not in (None, 0, "0"):
        message = str(data.get("message") or "").strip()
        suffix = f"：{message}" if message else ""
        raise QQBotError(f"{action}失败: err_code={err_code}{suffix}")
    return data


def response_body_text(response: httpx.Response) -> str:
    try:
        text = response.text.strip()
    except Exception:  # 响应体读取失败不该盖住原始错误
        return ""
    return f"({text[:200]})" if text else ""


# ---------------------------------------------------------------------------
# OpenID 绑定：没有查询接口，只能连网关等机器人收到消息
# ---------------------------------------------------------------------------
def bind_openid(
    *,
    app_id: str,
    client_secret: str,
    verify_code: str,
    target: str = "user",
    api_base: str = DEFAULT_API_BASE,
    token_url: str = DEFAULT_TOKEN_URL,
    timeout: float = 90.0,
    gateway_url: str = "",
    connect: Callable[..., Any] | None = None,
    on_event: Callable[[str], None] | None = None,
    cancel: Callable[[], bool] | None = None,
) -> str:
    """连上网关等用户发验证码，返回对应的 OpenID。

    - `target="user"`：等私聊消息（C2C_MESSAGE_CREATE），必须带上验证码
    - `target="group"`：等群内 @机器人 消息或机器人入群事件（GROUP_ADD_ROBOT）

    验证码由调用方先生成并展示。这是阻塞调用，只在 Web 控制台点「绑定」时用；
    日常推送走 HTTP，不需要长连接。`gateway_url` 留空时按官方接口获取。
    """
    if connect is None:
        try:
            from websockets.sync.client import connect as websocket_connect
        except ImportError as exc:
            raise QQBotError("缺少 websockets 依赖，无法自动绑定，请先执行 uv sync") from exc
        connect = websocket_connect

    if not verify_code:
        raise QQBotError("缺少绑定验证码")
    if not app_id or not client_secret:
        raise QQBotError("请先填写 AppID 和 AppSecret")

    is_group = target == "group"
    label = "群聊" if is_group else "私聊"
    notify = on_event or (lambda _message: None)
    cancelled = cancel or (lambda: False)
    deadline = time.monotonic() + max(float(timeout), 5.0)

    def remaining() -> float:
        left = deadline - time.monotonic()
        if cancelled():
            raise QQBotError("绑定已取消")
        if left <= 0:
            raise QQBotError(_timeout_message(is_group, timeout))
        return left

    base = api_base.rstrip("/") or DEFAULT_API_BASE
    with httpx.Client(timeout=20.0, follow_redirects=True) as client:
        token, _ = fetch_access_token(client, app_id, client_secret, token_url)
        if not gateway_url:
            gateway_url = str(
                parse_response(
                    client.get(f"{base}/gateway", headers=auth_headers(token)), "获取 QQ 网关地址"
                ).get("url")
                or ""
            )
    if not gateway_url:
        raise QQBotError("获取 QQ 网关地址失败: 响应里没有 url")

    heartbeat_stop = threading.Event()
    heartbeat: threading.Thread | None = None
    try:
        with connect(gateway_url, max_size=None, open_timeout=min(20.0, max(remaining(), 1.0))) as socket:
            hello = json.loads(socket.recv(timeout=remaining()))
            if hello.get("op") != 10:
                raise QQBotError(f"QQ 网关握手失败: 收到 op={hello.get('op')}，期望 Hello(10)")
            socket.send(
                json.dumps(
                    {
                        "op": 2,
                        "d": {
                            "token": f"QQBot {token}",
                            "intents": GROUP_AND_C2C_INTENT,
                            "shard": [0, 1],
                        },
                    }
                )
            )
            notify(f"已连接 QQ 网关，等待{label}验证码")
            heartbeat, current_sequence = start_heartbeat(socket, heartbeat_interval(hello), heartbeat_stop)

            while True:
                payload = json.loads(socket.recv(timeout=remaining()))
                op = payload.get("op")
                if op == 9:
                    raise QQBotError(
                        f"QQ 机器人鉴权失败：未开通{label}事件权限，请到 QQ 开放平台申请后重试"
                    )
                if op != 0:
                    continue
                if isinstance(payload.get("s"), int):
                    current_sequence(payload["s"])
                data = payload.get("d")
                if not isinstance(data, dict):
                    continue
                openid = match_openid(str(payload.get("t") or ""), data, verify_code, is_group, notify)
                if openid:
                    return openid
    except TimeoutError as exc:
        raise QQBotError(_timeout_message(is_group, timeout)) from exc
    except QQBotError:
        raise
    except Exception as exc:
        raise QQBotError(f"连接 QQ 网关失败: {exc}") from exc
    finally:
        heartbeat_stop.set()
        if heartbeat is not None:
            heartbeat.join(timeout=2)


def match_openid(
    event: str,
    data: dict[str, Any],
    verify_code: str,
    is_group: bool,
    notify: Callable[[str], None],
) -> str:
    """从网关事件里取 OpenID，事件类型不匹配时返回空字符串。"""
    if is_group:
        if event in {"GROUP_AT_MESSAGE_CREATE", "GROUP_MESSAGE_CREATE"}:
            group_openid = str(data.get("group_openid") or "")
            if not group_openid:
                return ""
            if verify_code and verify_code in str(data.get("content") or ""):
                return group_openid
            notify("已收到群消息，但验证码不匹配，请发送页面上显示的验证码")
            return ""
        if event == "GROUP_ADD_ROBOT":
            group_openid = str(data.get("group_openid") or "")
            if not group_openid:
                notify("已收到机器人入群事件，但没拿到群 OpenID，请改为在群里 @机器人 发送验证码")
            return group_openid
        return ""

    if event == "C2C_MESSAGE_CREATE":
        author = data.get("author")
        user_openid = str(author.get("user_openid") or "") if isinstance(author, dict) else ""
        if not user_openid:
            return ""
        if verify_code and verify_code in str(data.get("content") or ""):
            return user_openid
        notify("已收到私聊消息，但验证码不匹配，请发送页面上显示的验证码")
        return ""
    if event == "FRIEND_ADD":
        # 被加好友不能证明对方就是配置推送的人，必须靠验证码确认，所以这里只提示不绑定
        notify("已收到添加好友事件，请继续私聊机器人发送页面上显示的验证码")
        return ""
    return ""


def heartbeat_interval(hello: dict[str, Any]) -> float:
    data = hello.get("d")
    interval = data.get("heartbeat_interval") if isinstance(data, dict) else None
    try:
        seconds = float(interval) / 1000
    except (TypeError, ValueError):
        return 45.0
    return max(seconds, 1.0)


def start_heartbeat(
    socket: Any,
    interval: float,
    stop: threading.Event,
) -> tuple[threading.Thread, Callable[[int], None]]:
    """起后台心跳线程，返回 `(线程, 更新序列号的回调)`。

    网关要求心跳的 d 带最新收到的序列号 s，所以序列号在主循环里回写进来。
    """
    sequence = {"value": None}

    def beat() -> None:
        while not stop.wait(interval):
            try:
                socket.send(json.dumps({"op": 1, "d": sequence["value"]}))
            except Exception:
                return  # 连接已关闭，主循环会自己发现

    def update(value: int) -> None:
        sequence["value"] = value

    thread = threading.Thread(target=beat, name="miyouqian-qq-heartbeat", daemon=True)
    thread.start()
    return thread, update


def generate_verify_code(length: int = 6) -> str:
    """生成一次性验证码，用于确认发消息的人就是配置推送的人。"""
    import secrets

    return "".join(secrets.choice("0123456789") for _ in range(max(length, 4)))


def _timeout_message(is_group: bool, timeout: float) -> str:
    seconds = int(timeout)
    if is_group:
        return f"绑定超时：请在 {seconds} 秒内把机器人拉进群，或在群里 @机器人 发送页面上的验证码"
    return f"绑定超时：请在 {seconds} 秒内私聊机器人发送页面上的验证码"
