# -*- coding: utf-8 -*-
"""验证码识别渠道。"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .http import ApiClient

DAMAGOU_URL = "http://api.damagou.top/apiv1/jiyanRecognize.html"
CUSTOM_DEFAULT_TIMEOUT = 120
CUSTOM_MAX_ATTEMPTS = 3
CUSTOM_MAX_WAIT_SECONDS = 60
CUSTOM_DEFAULT_RETRY_AFTER = 5
# 限流 / 排队类错误：按 Retry-After 等待后重试
CUSTOM_RETRY_WITH_DELAY = {42901, 50301, 50302}
# 上游或服务内部的瞬时错误：短暂停顿后重试
CUSTOM_RETRY_IMMEDIATELY = {50001, 50000}
PROVIDER_LABELS = {
    "damagou": "打码狗",
    "custom": "自定义接口",
}
DAMAGOU_BALANCE_INSUFFICIENT_MESSAGE = "打码狗余额不足，请充值后重试"


@dataclass(frozen=True)
class CaptchaSolution:
    validate: str
    challenge: str


def is_enabled(config: dict[str, Any]) -> bool:
    return _active_channel(config) is not None


def active_provider_label(config: dict[str, Any]) -> str:
    channel = _active_channel(config)
    if not channel:
        return "验证码渠道"
    provider = str(channel.get("provider") or "")
    return PROVIDER_LABELS.get(provider, provider or "验证码渠道")


def solve_game_captcha(
    client: ApiClient,
    config: dict[str, Any],
    gt: str,
    challenge: str,
    emit: Callable[[str], None] | None = None,
) -> CaptchaSolution | None:
    return _solve(client, config, gt, challenge, None, emit)


def solve_bbs_captcha(
    client: ApiClient,
    config: dict[str, Any],
    gt: str,
    challenge: str,
    geetest_success: int | None = None,
    emit: Callable[[str], None] | None = None,
) -> CaptchaSolution | None:
    return _solve(client, config, gt, challenge, geetest_success, emit)


def _solve(
    client: ApiClient,
    config: dict[str, Any],
    gt: str,
    challenge: str,
    geetest_success: int | None,
    emit: Callable[[str], None] | None,
) -> CaptchaSolution | None:
    channel = _active_channel(config)
    if not channel:
        return None
    if channel.get("provider") == "custom":
        return _solve_custom(client, config, channel, gt, challenge, emit)
    return _solve_damagou(client, config, channel, gt, challenge, geetest_success, emit)


def _solve_damagou(
    client: ApiClient,
    config: dict[str, Any],
    channel: dict[str, Any],
    gt: str,
    challenge: str,
    geetest_success: int | None,
    emit: Callable[[str], None] | None,
) -> CaptchaSolution | None:
    try:
        params: dict[str, Any] = {
            "userkey": str(channel.get("userkey") or "").strip(),
            "gt": gt,
            "challenge": challenge,
            "isJson": "2",
        }
        captcha_type = str(channel.get("type") or "").strip()
        if captcha_type:
            params["type"] = captcha_type
        if geetest_success == 0:
            params["success"] = 0
        data = client.get_json(DAMAGOU_URL, params=params, timeout=float(channel.get("timeout") or 60))
        if is_damagou_balance_insufficient(data):
            if emit:
                emit(DAMAGOU_BALANCE_INSUFFICIENT_MESSAGE)
            return None
        solution = _parse_damagou_response(data)
        if not solution and emit:
            emit(f"{active_provider_label(config)}验证码识别失败: {data.get('msg') or '未知错误'}")
        return solution
    except Exception as exc:
        if emit:
            emit(f"{active_provider_label(config)}验证码识别失败: {exc}")
        return None


def _solve_custom(
    client: ApiClient,
    config: dict[str, Any],
    channel: dict[str, Any],
    gt: str,
    challenge: str,
    emit: Callable[[str], None] | None,
) -> CaptchaSolution | None:
    """调用遵循《自定义打码接口规范》的服务，规范见 webui/custom-captcha-api.md。"""
    label = active_provider_label(config)

    def fail(reason: str) -> None:
        if emit:
            emit(f"{label}验证码识别失败: {reason}")

    try:
        url = str(channel.get("url") or "").strip()
        params = {"gt": gt, "challenge": challenge}
        api_key = str(channel.get("api_key") or "").strip()
        headers = {"X-API-Key": api_key} if api_key else {}
        timeout = float(channel.get("timeout") or CUSTOM_DEFAULT_TIMEOUT)
        for attempt in range(1, CUSTOM_MAX_ATTEMPTS + 1):
            data, response_headers = client.get_json_any_status(
                url, params=params, headers=headers, timeout=timeout
            )
            code = _response_code(data)
            if code == 0:
                solution = _parse_custom_response(data, challenge)
                if not solution:
                    fail("接口返回成功但缺少 data.validate")
                return solution
            if attempt < CUSTOM_MAX_ATTEMPTS:
                if code in CUSTOM_RETRY_WITH_DELAY:
                    delay = _retry_after_seconds(response_headers)
                    if delay <= CUSTOM_MAX_WAIT_SECONDS:
                        time.sleep(delay)
                        continue
                elif code in CUSTOM_RETRY_IMMEDIATELY:
                    time.sleep(1)
                    continue
            fail(_format_custom_error(data, code))
            return None
    except Exception as exc:
        fail(str(exc))
    return None


def _response_code(data: Mapping[str, Any]) -> int | None:
    try:
        return int(data.get("code"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _retry_after_seconds(headers: Mapping[str, str]) -> int:
    try:
        return max(int(headers.get("Retry-After", CUSTOM_DEFAULT_RETRY_AFTER)), 1)
    except (TypeError, ValueError):
        return CUSTOM_DEFAULT_RETRY_AFTER


def _format_custom_error(data: Mapping[str, Any], code: int | None) -> str:
    text = f"[{code if code is not None else '?'}] {data.get('message') or '未知错误'}"
    request_id = str(data.get("request_id") or "").strip()
    return f"{text} (request_id={request_id})" if request_id else text


def _parse_custom_response(data: Mapping[str, Any], request_challenge: str) -> CaptchaSolution | None:
    payload = data.get("data")
    if not isinstance(payload, dict):
        return None
    validate = str(payload.get("validate") or "").strip()
    if not validate:
        return None
    # 规范保证 data.challenge 原样回显请求值，缺失时以请求值兜底
    challenge = str(payload.get("challenge") or "").strip() or request_challenge
    return CaptchaSolution(validate=validate, challenge=challenge)


def _channel_configured(channel: Mapping[str, Any]) -> bool:
    provider = str(channel.get("provider") or "")
    if provider == "damagou":
        return bool(str(channel.get("userkey") or "").strip())
    if provider == "custom":
        return bool(str(channel.get("url") or "").strip())
    return False


def _active_channel(config: dict[str, Any]) -> dict[str, Any] | None:
    captcha_config = config.get("captcha", {})
    if not captcha_config.get("enable"):
        return None
    channels = captcha_config.get("channels") or []
    if not isinstance(channels, list):
        return None
    for channel in channels:
        if not isinstance(channel, dict):
            continue
        if channel.get("enable") and _channel_configured(channel):
            return channel
    return None


def _parse_damagou_response(data: dict[str, Any]) -> CaptchaSolution | None:
    if str(data.get("status")) != "0":
        return None
    raw = str(data.get("data") or "")
    if "|" not in raw:
        return None
    challenge, validate = raw.split("|", 1)
    challenge = challenge.strip()
    validate = validate.strip()
    if not challenge or not validate:
        return None
    return CaptchaSolution(validate=validate, challenge=challenge)


def is_damagou_balance_insufficient(data: dict[str, Any]) -> bool:
    """判断打码狗错误信息中是否包含“余额”。"""
    return str(data.get("status")) != "0" and "余额" in str(data)
