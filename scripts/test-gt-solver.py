# -*- coding: utf-8 -*-
"""自检：真实米游社验证码 → gt-solver 自定义接口 → 看能否解出 validate。

不需要登录：米游社的 misc/api/createVerification 只校验设备类请求头。
"""
from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from miyouqian import constants as c  # noqa: E402
from miyouqian.core import crypto  # noqa: E402

GT_SOLVER_URL = os.environ.get("GT_SOLVER_URL", "http://127.0.0.1:10721/custom")
TIMEOUT = float(os.environ.get("GT_SOLVER_TIMEOUT", "150"))


def create_verification() -> tuple[str, str]:
    headers = {
        "DS": crypto.ds(web=False),
        "x-rpc-client_type": "2",
        "x-rpc-app_version": c.BBS_VERSION,
        "x-rpc-sys_version": "12",
        "x-rpc-channel": "miyousheluodi",
        "x-rpc-device_id": "7B197916-2425-471A-B1B0-8AB4ABA69262",
        "x-rpc-device_name": "Xiaomi 13",
        "x-rpc-device_model": "2211133C",
        "x-rpc-h265_supported": "1",
        "User-Agent": c.DEFAULT_MOBILE_UA,
        "Referer": "https://app.mihoyo.com",
    }
    req = urllib.request.Request(c.BBS_CREATE_VERIFICATION_URL, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read().decode("utf-8"))
    if data.get("retcode") != 0:
        raise SystemExit(f"createVerification 失败: {data.get('retcode')} {data.get('message')}")
    d = data.get("data") or {}
    gt, challenge = str(d.get("gt") or ""), str(d.get("challenge") or "")
    if not gt or not challenge:
        raise SystemExit("createVerification 没返回 gt/challenge")
    return gt, challenge


def main() -> int:
    gt, challenge = create_verification()
    print(f"gt        = {gt}")
    print(f"challenge = {challenge}")
    url = f"{GT_SOLVER_URL}?gt={gt}&challenge={urllib.parse.quote(challenge)}"
    print(f"请求      = {GT_SOLVER_URL}")
    with urllib.request.urlopen(url, timeout=TIMEOUT) as r:
        raw = r.read().decode("utf-8", "replace")
    print(f"响应      = {raw[:400]}")
    try:
        body = json.loads(raw)
    except Exception:
        print("❌ 响应不是 JSON，不符合自定义接口规范")
        return 1
    # 规范要求：code 为整数；成功时 data.validate 非空；失败时 data 可为 null
    if not isinstance(body.get("code"), int):
        print("❌ 缺少整数 code 字段")
        return 1
    data = body.get("data") or {}
    if body["code"] == 0:
        if not data.get("validate"):
            print("❌ code=0 但没有 data.validate")
            return 1
        print(f"✅ 通过：解出 validate={data['validate'][:16]}..")
        return 0
    print(f"⚠️ 协议合规但本次未解出：code={body['code']} message={body.get('message')}")
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
