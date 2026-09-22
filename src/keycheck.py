"""API Key 验活 —— 供交付验收（`tools/verify_keys.py`）与账号池巡检共用。

🔴 **三态**，不是二态。这是整个模块存在的理由：

    alive    拿到 200 且响应结构正确 ⇒ key 可用
    dead     拿到 HTTP 响应且站点**明确拒绝**（401/403）⇒ key 真的失效
    unknown  **没拿到 HTTP 响应**（SSL/DNS/连接被拒），或拿到 5xx
             ⇒ 这一刻读不出来，key 很可能是好的

判据是「**有没有拿到 HTTP 响应**」，不是「成功还是失败」。

为什么必须分三态（2026-09-22 第 3 批验收实测）：
    `verify_keys.py` 报 1 把不可用，错误是 `SSLError`、`elapsed=0.096s`
    —— 连接**立即失败**，不是超时；手工复验**第 1 次就 200**，那把 key 完全可用。
    当时的二态实现把它归进"不可用"，于是交付清单静默少一行，
    而屏幕上只显示「可用 880 / 不可用 1」，看起来像"有一把坏了"，没人会去追。

对**定时巡检**来说这个区分更要紧：巡检是自动跑的、没人盯着，
把 `unknown` 当 `dead` 会在一次网络抖动里把整池账号标成失效 ——
那是不可逆的误判（账号已经发给用户了）。所以巡检侧的规则是：
只有连续多次拿到 `dead` 才真正标失效，`unknown` 不累加计数。
"""

from __future__ import annotations

import time
from typing import Any, Literal

import requests

from . import config

#: 探测请求体。用一个**最小**的真实调用 —— 只验"这把 key 能不能换来正常响应"，
#: 不做任何有副作用的操作。
PROBE_BODY: dict[str, Any] = {
    "state": "The build has been failing on CI for three days and the release is tomorrow.",
    "model": "jev-latest",
    "questions": {
        "is_urgent": {
            "type": "noul",
            "instructions": "Does this message convey urgency or time-sensitivity?",
        },
    },
}

Verdict = Literal["alive", "dead", "unknown"]

#: 站点**明确拒绝**的状态码 ⇒ 可以判死。
#: 刻意不含 429（限流是"现在不行"，不是"key 失效"）与 5xx（服务端问题）。
DEAD_STATUSES = frozenset({401, 403})


class CheckResult:
    """一次验活的结果。

    刻意做成类而不是裸 dict：调用方要读 `verdict` 做分支判断，
    用 dict 的话拼错键名会静默拿到 None 然后走错分支。
    """

    __slots__ = ("verdict", "status", "error", "elapsed", "attempts", "detail")

    def __init__(self, verdict: Verdict, *, status: int = 0, error: str = "",
                 elapsed: float = 0.0, attempts: int = 1,
                 detail: dict[str, Any] | None = None):
        self.verdict: Verdict = verdict
        #: HTTP 状态码。**0 表示压根没拿到响应**（网络层失败）——
        #: 这是区分 dead 与 unknown 的关键信号，不要省掉。
        self.status = status
        self.error = error
        self.elapsed = elapsed
        self.attempts = attempts
        self.detail = detail or {}

    @property
    def ok(self) -> bool:
        """兼容旧调用点（`verify_keys.py` 的 `v["ok"]`）。"""
        return self.verdict == "alive"

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "ok": self.ok,
            "status": self.status,
            "error": self.error,
            "elapsed": round(self.elapsed, 3),
            "attempts": self.attempts,
            **self.detail,
        }

    def __repr__(self) -> str:
        return (f"CheckResult({self.verdict}, status={self.status}, "
                f"elapsed={self.elapsed:.2f}s)")


def check_key(key: str, *, api_url: str = "", timeout: float = 60.0,
              retries: int = 2, backoff: float = 1.0) -> CheckResult:
    """验一把 key。

    Args:
        key: 待验的 api_key
        api_url: 验收端点。留空则用 `config.VERIFY_API_URL`
                 （站点配置页可改，所以这里**每次调用都重新读**，
                  不能在模块级绑定 —— 那样页面改完不生效）
        timeout: 单次请求超时
        retries: **只对网络层失败重试**。拿到 HTTP 响应就是确定性结论，
                 401 重试一万次还是 401，只会浪费时间。
        backoff: 线性退避基数（1s / 2s / …）

    Returns:
        CheckResult。`api_url` 未配置时返回 `unknown` 而**不是抛异常** ——
        巡检是批量跑的，一个配置问题不该让整轮崩掉；调用方看到
        `error` 里的提示自己决定要不要继续。
    """
    url = (api_url or config.VERIFY_API_URL or "").strip()
    if not url:
        return CheckResult(
            "unknown",
            error="未配置验收端点（系统设置 → 站点配置 → Key 验收端点）",
        )

    if not key:
        return CheckResult("unknown", error="api_key 为空")

    t0 = time.time()
    last: CheckResult | None = None

    for attempt in range(retries + 1):
        try:
            r = requests.post(
                url,
                headers={"Authorization": f"Bearer {key}",
                         "Content-Type": "application/json"},
                json=PROBE_BODY,
                timeout=timeout,
            )
        except requests.RequestException as exc:
            # 网络层失败 ⇒ unknown，可重试
            last = CheckResult(
                "unknown", status=0,
                error=f"{type(exc).__name__}: {exc}"[:300],
                elapsed=time.time() - t0, attempts=attempt + 1,
            )
            if attempt < retries:
                time.sleep(backoff * (attempt + 1))
                continue
            return last

        elapsed = time.time() - t0

        try:
            body = r.json()
        except ValueError:
            body = {"raw": r.text[:200]}

        # 成功：200 且响应结构对得上。
        # 只看状态码不够 —— 有些网关会用 200 包装错误页。
        if r.status_code == 200 and isinstance(body, dict) and "answers" in body:
            return CheckResult(
                "alive", status=200, elapsed=elapsed, attempts=attempt + 1,
                detail={
                    "model": body.get("model", ""),
                    "usage": body.get("usage", {}),
                },
            )

        err = ""
        if isinstance(body, dict):
            err = str(body.get("error") or body.get("code") or "")
        err = (err or str(body))[:200]

        # 5xx：服务端的问题，不是 key 的问题 ⇒ unknown，且值得重试
        if 500 <= r.status_code < 600:
            last = CheckResult("unknown", status=r.status_code, error=err,
                               elapsed=elapsed, attempts=attempt + 1)
            if attempt < retries:
                time.sleep(backoff * (attempt + 1))
                continue
            return last

        # 站点明确拒绝 ⇒ dead
        if r.status_code in DEAD_STATUSES:
            return CheckResult("dead", status=r.status_code, error=err,
                               elapsed=elapsed, attempts=attempt + 1)

        # 其余（429 限流、400 请求格式问题等）⇒ unknown。
        # 🔴 刻意不判死：429 是"现在不行"，400 可能是探测请求体与站点新版本不匹配
        #    —— 那是**我们的**问题，把用户的 key 标成失效属于误伤。
        return CheckResult("unknown", status=r.status_code, error=err,
                           elapsed=elapsed, attempts=attempt + 1)

    return last or CheckResult("unknown", error="重试耗尽")
