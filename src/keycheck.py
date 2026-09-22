"""API Key 验活 —— **零额度**探测，供交付验收与账号池巡检共用。

🟢 **不消耗账号额度**。这是硬约束，不是可选项。

原理：探测请求只发一个空 JSON body。目标站点**先认证、再校验参数**，于是

    key 有效 ⇒ 400/422（"缺少 state/questions"）—— 认证过了，没有推理发生
    key 失效 ⇒ 401/403                          —— 认证阶段就被拦下

两者状态码不同，足够判活；而因为压根没进推理阶段，**不产生 token 消耗**
（对照：带 questions 的真实推理请求每次约 317 tokens，1600 账号每 6 小时
一轮就是约 6500 次推理/天）。

🔴 **三态**，不是二态。这是整个模块存在的理由：

    alive    认证通过（没被 401/403 拦下，且响应里没有额度耗尽迹象）
    dead     站点**明确拒绝**：认证失败（401/403）或额度/欠费（402、配额关键词）
    unknown  **没拿到 HTTP 响应**（SSL/DNS/连接被拒），或拿到 5xx / 限流
             ⇒ 这一刻读不出来，key 很可能是好的

判据的核心是「**有没有拿到 HTTP 响应**」，不是「成功还是失败」。

为什么必须分三态（2026-09-22 第 3 批验收实测）：
    验收脚本报 1 把不可用，错误是 `SSLError`、`elapsed=0.096s`
    —— 连接**立即失败**，不是超时；手工复验**第 1 次就 200**，那把 key 完全可用。
    当时的二态实现把它归进"不可用"，于是交付清单静默少一行，
    而屏幕上只显示「可用 880 / 不可用 1」，看起来像"有一把坏了"，没人会去追。

对**定时巡检**来说这个区分更要紧：巡检是自动跑的、没人盯着，
把 `unknown` 当 `dead` 会在一次网络抖动里把整池账号标成失效 ——
那是不可逆的误判（账号已经发给用户了）。所以巡检侧的规则是：
只有连续多次拿到 `dead` 才真正标失效，`unknown` 不累加计数。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
零额度探测的两个已知局限（**必须知道**，否则会对结果产生错误预期）：

1. **它验的是"认证通过"，不是"真能跑出答案"。**
   如果站点存在「key 有效、但模型不可用/权限不足」这类状态，探测判 alive，
   实际调用仍会失败。已尽力缓解 —— 见下面的额度耗尽识别。

2. **它假设服务端认证早于参数校验。**
   若某版本反过来（先校验参数），失效 key 也会拿到 400 而被判 alive。
   方向是"偏保守"（不会把好 key 判死，只会把死 key 当活），
   但换端点或站点升级后应当复核：拿一把**已知失效**的 key 跑一次，
   期望得到 `dead`；若得到 `alive`，说明这个假设不再成立。
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

from __future__ import annotations

import time
from typing import Any, Literal

import requests

from . import config

#: 探测请求体：空 JSON。
#:
#: 🔴 **不要往里加字段**。任何让请求通过参数校验的字段（state / questions /
#:    model）都会让服务端进入推理阶段并计费，那正是这个模块要避免的事。
#:    需要"确认 key 真能跑出答案"时，请人工单独调一次，不要改这里 ——
#:    这个常量被定时巡检按账号数量成千上万次地使用。
PROBE_BODY: dict[str, Any] = {}

Verdict = Literal["alive", "dead", "unknown"]

#: 站点**明确拒绝**的状态码 ⇒ 判死。
#:   401/403 认证失败
#:   402     Payment Required（额度耗尽 / 欠费）—— 账号池的语义里这同样是"不可用"
#: 刻意不含 429（限流是"现在不行"）与 5xx（服务端问题）。
DEAD_STATUSES = frozenset({401, 402, 403})

#: 限流迹象。**必须先于额度关键词判断** ——
#: "rate limit exceeded" 同时含 "exceeded"，若先判额度会把限流误当欠费判死。
RATE_LIMIT_HINTS = (
    "rate limit", "ratelimit", "too many request", "slow down", "try again later",
)

#: 额度/欠费迹象。零额度探测本身验不出"还有没有钱"，
#: 但站点在认证之后往往会直接回一个带这些字样的错误 —— 能识别就识别，
#: 因为额度耗尽的账号发给用户等于废号，比认证失败更难被发现。
QUOTA_HINTS = (
    "insufficient", "quota", "out of credit", "no credit", "payment required",
    "billing", "balance", "subscription", "expired", "suspend", "disabled",
)


def _hit(text: str, hints: tuple[str, ...]) -> str:
    """返回命中的第一个关键词（用于写进 error 方便排查），没命中返回 ""。"""
    low = text.lower()
    for h in hints:
        if h in low:
            return h
    return ""


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
    """验一把 key。**不消耗账号额度。**

    Args:
        key: 待验的 api_key
        api_url: 验收端点。留空则用 `config.VERIFY_API_URL`
                 （站点配置页可改，所以这里**每次调用都重新读**，
                  不能在模块级绑定 —— 那样页面改完不生效）
        timeout: 单次请求超时
        retries: **只对网络层失败与 5xx 重试**。拿到确定性响应就不重试，
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

        err = ""
        if isinstance(body, dict):
            err = str(body.get("error") or body.get("message")
                      or body.get("code") or "")
        err = (err or str(body))[:200]

        # 5xx：服务端的问题，不是 key 的问题 ⇒ unknown，且值得重试
        if 500 <= r.status_code < 600:
            last = CheckResult("unknown", status=r.status_code, error=err,
                               elapsed=elapsed, attempts=attempt + 1)
            if attempt < retries:
                time.sleep(backoff * (attempt + 1))
                continue
            return last

        # 站点明确拒绝（认证失败 / 欠费）⇒ dead
        if r.status_code in DEAD_STATUSES:
            return CheckResult("dead", status=r.status_code, error=err,
                               elapsed=elapsed, attempts=attempt + 1)

        # 限流优先于额度判断：两者都可能用 429，
        # 且 "rate limit exceeded" 会同时命中额度关键词 "exceeded"。
        rate_hit = _hit(err, RATE_LIMIT_HINTS)
        if rate_hit or r.status_code == 429:
            return CheckResult(
                "unknown", status=r.status_code,
                error=err or f"限流（{rate_hit}）",
                elapsed=elapsed, attempts=attempt + 1,
                detail={"throttled": True},
            )

        # 认证过了，但响应在说额度/订阅有问题 ⇒ 对账号池而言同样不可用。
        # 这类号最坑：认证正常，发给用户后一调用就失败。
        quota_hit = _hit(err, QUOTA_HINTS)
        if quota_hit:
            return CheckResult(
                "dead", status=r.status_code,
                error=f"额度或订阅异常（命中 {quota_hit!r}）: {err}"[:300],
                elapsed=elapsed, attempts=attempt + 1,
                detail={"quota_issue": True},
            )

        # 🟢 零额度判据的核心：**没被拒绝、也没有额度问题，就说明这把 key 有效**。
        #    400/422（缺参数，这是预期结果）、404（路径变了）、200 —— 都算认证通过。
        #    业务层因为"参数不全"报错与 key 的有效性无关。
        return CheckResult(
            "alive", status=r.status_code, error="",
            elapsed=elapsed, attempts=attempt + 1,
            detail={"probe_status": r.status_code, "probe_note": err},
        )

    return last or CheckResult("unknown", error="重试耗尽")
