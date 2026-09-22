"""MoeMail 客户端（自建临时邮箱服务）。

与另两个后端的**根本差异**（都是接口形状决定的，不是风格选择）：

1. **认证头是 `X-API-Key`**，不是 `Authorization: Bearer`。
2. **收信按 `emailId` 索引**，不是按邮箱地址：`GET /api/emails/{emailId}`。
   ⇒ 客户端必须维护 `{email: emailId}` 映射。好在服务端有 `GET /api/emails`
   可以按地址**反查**（remail 的 `serviceToken` 就没这个能力，只能靠落盘），
   所以这里的映射丢了也能自愈，落盘只是为了省掉反查的分页开销。
3. **列表接口可能不带正文**。`GET /api/emails/{emailId}` 返回的是邮件列表，
   正文字段名不确定 ⇒ 列表里取不到正文时，按需回源 `GET /api/emails/{id}/{msgId}`。
   这是 `list_mails()` 里 `_ensure_body()` 存在的原因。
4. **域名必须来自 `GET /api/config`**。乱填一个会被服务端拒；缺省时取第一个可用域名。

公开接口与 `tempemail.TempMailClient` / `remail.RemailClient` **完全对齐**
（`create_mailbox` / `list_mails` / `wait_for_mail` / `scan_all` / `health` / `stats`），
差异全收在本模块内部 —— `stages` 层不需要知道用的是哪个后端。
"""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from datetime import datetime
from typing import Any, Callable

import requests

from . import config
from .tempemail import Mail, Stats


class MoeMailError(RuntimeError):
    pass


#: 服务端接受的 `expiryTime` 枚举（毫秒）。0 = 永久。
VALID_EXPIRY_MS = (0, 3600000, 86400000, 604800000)

#: 轮询间隔**下界**（秒）。
#:
#: 🔴 为什么需要它：`stages.MAIL_POLL_INTERVAL` 是 0.5 —— 那个值是给 CF Temp Email
#:    Worker 调优的（它只做一次 D1 查询，很轻）。MoeMail 通常跑在
#:    **Cloudflare Workers 免费额度**上，0.5s × N 并发会直接打爆 CPU 限额，
#:    表现是 `Error 1102 Worker exceeded resource limits`（2026-09-22 实测触发）。
#:    一旦 1102，**整个服务对所有请求都返回错误页**，等于把自己的邮箱服务打挂了。
#:
#: 实现方式与 Remail 一致：客户端把传入的 interval 抬到自己的下界
#: （`wait = max(interval, 下界)`），所以上层调小它对本后端是无害的 no-op。
DEFAULT_MIN_POLL_INTERVAL = 3.0

#: `_resolve_id` 反查时最多翻几页。
#:
#: 🔴 原来是 50 —— 那意味着一次反查最多 50 个请求打到 Worker 上，而反查在
#:    缓存未命中时**每个账号都会发生一次** ⇒ 10 个账号就是 500 个请求，
#:    这是打爆 Worker 的主因之一。正常路径根本不需要反查（建邮箱时就存了 id），
#:    反查只是 `resume_pending.py` 补跑时的兜底，翻几页够用了。
MAX_RESOLVE_PAGES = 5

_TAG_RE = re.compile(r"<[^>]+>")

#: Cloudflare 的错误页特征码 → 人话解释。
#: 这些错误返回的是**HTML 错误页**，不是 JSON ⇒ 不特判的话
#: `r.json()` 抛 JSONDecodeError，错误信息里完全看不出发生了什么。
_CF_ERRORS = {
    "1102": "Worker 超出资源限制（CPU/内存）—— 请求打太快了，"
            "降低任务并发数或调大轮询间隔",
    "1101": "Worker 脚本抛出异常 —— 服务端代码错误，看 Cloudflare 的 Workers Logs",
    "1015": "被 Cloudflare 限流（Rate Limited）—— 降低并发",
    "1027": "Worker 超出每日免费额度",
}


def _to_ms(v: Any) -> int:
    """把服务端的时间字段统一成**毫秒**时间戳。

    `Mail.received_at` 的既有约定是毫秒（CF Worker 就是毫秒）。这里要兼容三种形态：
    秒级 int、毫秒级 int、ISO-8601 字符串 —— 秒/毫秒混用会差 1000 倍，
    而且不报错，只是 `since_ms` 过滤永远筛不出邮件（remail.py 踩过同一个坑）。
    """
    if not v:
        return 0

    if isinstance(v, (int, float)):
        n = int(v)
        # 10 位是秒级（2001-09-09 之后到 2286 年之间），13 位是毫秒级
        return n * 1000 if n < 10_000_000_000 else n

    s = str(v).strip()
    if s.isdigit():
        n = int(s)
        return n * 1000 if n < 10_000_000_000 else n

    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return 0
    return int(dt.timestamp() * 1000)


def _split_domains(raw: str) -> list[str]:
    """把域名字符串切成列表。逗号 / 分号 / 空白 / 换行都当分隔符。

    顺手过滤掉不含点号的项：域名必然有点，切错时留下的碎片（单字母、空串）
    会污染校验列表，让"域名不在可用列表里"的报错更难看懂。
    """
    parts = re.split(r"[,;\s]+", raw or "")
    return [p.strip().lstrip("@") for p in parts if p.strip() and "." in p]


def _html_to_text(html: str) -> str:
    """HTML 正文降级成纯文本。

    只做够用的处理：解析登录码 / 链接的正则跑在纯文本上更稳（`mailrules.py`
    的规则是按纯文本写的）。不引 bs4 —— 多一个依赖换不到实质收益。
    """
    if not html:
        return ""
    text = re.sub(r"<br\s*/?>", "\n", html, flags=re.I)
    text = re.sub(r"</(p|div|tr|h[1-6])>", "\n", text, flags=re.I)
    text = _TAG_RE.sub(" ", text)
    text = (text.replace("&nbsp;", " ").replace("&amp;", "&")
                .replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"'))
    return re.sub(r"[ \t]{2,}", " ", text).strip()


class MoeMailClient:
    """MoeMail 后端。建邮箱免费，但有效期由 `expiryTime` 决定（过期后收不到信）。"""

    def __init__(self, base: str | None = None, api_key: str | None = None,
                 session: requests.Session | None = None,
                 domain: str | None = None, expiry_ms: int | None = None,
                 min_poll_interval: float | None = None):
        self.base = (base or config.MOEMAIL_BASE).rstrip("/")
        self.api_key = api_key if api_key is not None else config.MOEMAIL_API_KEY
        #: 默认域名与有效期。判 `is not None` 而非 `or` —— 见 `tempemail.py` 同处说明：
        #: 空串/0 是合法的显式值（域名空串=自动选，有效期 0=永久），
        #: 用 `or` 会把它们静默换成 .env 的值。
        self.domain = domain if domain is not None else config.MOEMAIL_DOMAIN
        self.expiry_ms = expiry_ms if expiry_ms is not None else config.MOEMAIL_EXPIRY_MS
        #: 轮询间隔下界。见 `DEFAULT_MIN_POLL_INTERVAL` 的说明 ——
        #: 上层传更小的值时以这里为准，避免把 Worker 打成 1102。
        self.min_poll_interval = (DEFAULT_MIN_POLL_INTERVAL if min_poll_interval is None
                                  else float(min_poll_interval))
        self.s = session or requests.Session()
        self.s.headers.update({
            "User-Agent": config.UA,
            "Accept": "application/json",
            # 🔴 `X-API-Key` 而非 Bearer。写错的表现是全局 401，
            #    很容易被误诊成"key 失效"，而实际 key 是好的。
            "X-API-Key": self.api_key,
        })
        self.stats = Stats()
        #: 进程内锁：`_ids` 是可变 dict，多线程同时写会坏掉。
        self._lock = threading.Lock()
        #: `{email: emailId}` —— 收信端点按 id 索引，必须存。
        #: 丢了不致命（`_resolve_id()` 会去服务端反查），只是多几次分页请求。
        self._ids: dict[str, str] = {}
        #: `GET /api/config` 的缓存 —— 每次建邮箱都拉一遍纯属浪费。
        self._config_cache: dict[str, Any] | None = None

    # ── 底层 ──────────────────────────────────────────────────────────
    def _request(self, method: str, path: str, *, retries: int = 4,
                 backoff: float = 0.8, **kw) -> requests.Response:
        """5xx 重试、4xx 立刻失败。

        与 `TempMailClient._request` 同策略：5xx 是"服务端现在读不出来"，
        重试有意义；4xx 是"请求本身不对"，重试只会浪费时间并掩盖问题。
        """
        last: Exception | None = None
        for attempt in range(retries):
            try:
                r = self.s.request(method, f"{self.base}{path}", timeout=30, **kw)
            except requests.RequestException as exc:
                last = exc
                if attempt < retries - 1:
                    time.sleep(backoff * (attempt + 1))
                    continue
                raise MoeMailError(f"网络失败 {path}: {exc}") from exc

            # Cloudflare 错误页可能带任意状态码（1102 实测是 500），
            # 所以先认它 —— 否则会被归进"5xx 重试"然后报个看不懂的结论。
            cf = self._detect_cf_error(r.text)

            if cf or 500 <= r.status_code < 600:
                self.stats.http_5xx += 1
                if attempt < retries - 1:
                    # 🔴 CF 1102/1015 用**更长的退避**：它们的成因是"请求太密"，
                    #    按原来的 0.8s 线性退避继续打只会让 Worker 一直起不来。
                    wait = backoff * (attempt + 1)
                    if cf:
                        wait = max(wait, 3.0 * (attempt + 1))
                    time.sleep(wait)
                    continue
                if cf:
                    raise MoeMailError(f"{path} {cf}")
                raise MoeMailError(
                    f"MoeMail 持续 5xx（{self.stats.http_5xx} 次，最近 HTTP {r.status_code}）"
                    f"—— 不是邮件没到，是读不出来"
                )
            if r.status_code == 429:
                self.stats.http_5xx += 1
                if attempt < retries - 1:
                    # 服务端给了 Retry-After 就听它的
                    ra = r.headers.get("Retry-After")
                    wait = float(ra) if (ra or "").replace(".", "").isdigit() else 5.0 * (attempt + 1)
                    time.sleep(min(wait, 30.0))
                    continue
                raise MoeMailError(f"{path} 被限流（HTTP 429）—— 降低任务并发数")
            if 400 <= r.status_code < 500:
                hint = ""
                if r.status_code in (401, 403):
                    hint = "（认证头必须是 X-API-Key，不是 Authorization: Bearer）"
                raise MoeMailError(f"HTTP {r.status_code} {path}{hint}: {r.text[:200]}")
            return r
        raise MoeMailError(f"重试耗尽: {path} ({last})")

    @staticmethod
    def _detect_cf_error(text: str) -> str | None:
        """从响应正文里认出 Cloudflare 错误页，返回人话解释。

        CF 的错误页是 HTML，里面有 `Error 1102` 这样的标记。
        不认出来的话调用方拿到的是 `JSONDecodeError: Expecting value: line 1 column 1`，
        完全指不出真正的问题（"服务被自己打挂了"）。
        """
        if not text or "<" not in text[:200]:
            return None
        for code, hint in _CF_ERRORS.items():
            if f"Error {code}" in text or f"error code: {code}" in text.lower():
                return f"Cloudflare Error {code}：{hint}"
        return None

    def _json(self, r: requests.Response, path: str) -> Any:
        """安全解析 JSON。非 JSON 响应转成可读错误，而不是抛 JSONDecodeError。"""
        try:
            return r.json()
        except ValueError:
            cf = self._detect_cf_error(r.text)
            if cf:
                raise MoeMailError(f"{path} {cf}") from None
            raise MoeMailError(
                f"{path} 返回的不是 JSON（HTTP {r.status_code}）："
                f"{r.text[:150]!r}"
            ) from None

    @staticmethod
    def _rows(body: Any, *keys: str) -> list[dict[str, Any]]:
        """从响应里挖出列表。

        字段名在不同版本间不一致（`emails` / `messages` / `data` / 裸数组都见过），
        所以按候选键依次试，而不是硬编码一个 —— 硬编码的表现是"拉到 0 封邮件"
        然后一路超时，看起来像"邮件没到"。
        """
        if isinstance(body, list):
            return [x for x in body if isinstance(x, dict)]
        if not isinstance(body, dict):
            return []
        for k in keys:
            v = body.get(k)
            if isinstance(v, list):
                return [x for x in v if isinstance(x, dict)]
        return []

    # ── 系统配置 ──────────────────────────────────────────────────────
    def get_config(self, *, refresh: bool = False) -> dict[str, Any]:
        """`GET /api/config` —— 含可用域名列表。结果缓存在实例上。"""
        if self._config_cache is not None and not refresh:
            return self._config_cache
        r = self._request("GET", "/api/config")
        self._config_cache = self._json(r, "/api/config") or {}
        return self._config_cache

    def domains(self) -> list[str]:
        """可用域名列表。

        服务端返回的形状有三种，都得处理：
          1. 逗号分隔**字符串** —— `"a.com,b.com"`（MoeMail 实际用的就是这个）
          2. 字符串数组       —— `["a.com", "b.com"]`
          3. 对象数组         —— `[{"domain": "a.com"}]`

        🔴 第 1 种是这里曾经踩过的坑：`for d in raw` 对字符串**不报错**，
           只是逐字符迭代 ⇒ 返回 `['a', '.', 'c', 'o', 'm', ',', ...]`，
           于是校验把正确的域名判成"不在可用列表里"，报错信息里列出一堆单字符。
           所以必须先判类型，不能假设它是可迭代的序列就直接遍历。
        """
        cfg = self.get_config()
        raw = cfg.get("domains") or cfg.get("emailDomains") or cfg.get("availableDomains") or []

        # 形态 1：整个字段就是一个分隔字符串
        if isinstance(raw, str):
            return _split_domains(raw)

        out: list[str] = []
        for d in raw:
            if isinstance(d, str):
                # 数组里的单项也可能是分隔字符串（见过 `["a.com,b.com"]`）
                out.extend(_split_domains(d))
            elif isinstance(d, dict):
                v = d.get("domain") or d.get("name") or d.get("value")
                if v:
                    out.extend(_split_domains(str(v)))
        return out

    def pick_domain(self, domain: str | None = None) -> str:
        """决定用哪个域名。

        优先级：显式参数 > `MOEMAIL_DOMAIN` > `GET /api/config` 的第一个。

        🔴 显式指定的域名会**校验是否在可用列表里**：服务端对无效域名的报错
        （`HTTP 400 域名无效`）不会告诉你有哪些是有效的，自己先查一遍能省一轮排查。
        """
        want = (domain or self.domain or "").strip()
        available = self.domains()

        if want:
            if available and want not in available:
                raise MoeMailError(
                    f"域名 {want!r} 不在服务端的可用列表里。"
                    f"可用域名：{'、'.join(available)}。"
                    f"请在邮箱配置里改成其中之一，或留空让系统自动选择"
                )
            return want

        if not available:
            raise MoeMailError(
                "GET /api/config 没返回可用域名 ⇒ 无法决定用哪个域名建邮箱。"
                "先手工调一次该接口确认响应结构"
            )
        return available[0]

    # ── 健康检查 ──────────────────────────────────────────────────────
    def health(self) -> dict[str, Any]:
        """MoeMail 没有 `/health`，用 `GET /api/config` 当体检端点 ——
        它同时验证了「服务可达」与「API Key 有效」两件事。"""
        cfg = self.get_config(refresh=True)
        return {"ok": True, "domains": self.domains(), "config": cfg}

    # ── 建邮箱 ────────────────────────────────────────────────────────
    def create_mailbox(self, domain: str | None = None, *,
                       name: str | None = None,
                       expiry_ms: int | None = None) -> str:
        """建一个临时邮箱，返回完整地址。

        `name` 缺省用随机串 —— 服务端按 `name@domain` 组地址，撞名会失败或复用到
        别人的邮箱（更糟：读到别人的信）。所以默认必须够随机，不能用"test"这类固定值。
        """
        # 🔴 参数校验必须在**任何网络请求之前**。`pick_domain()` 会拉
        #    `GET /api/config`，把校验放在它后面的话，填错 expiry 要先等一轮
        #    网络往返（服务不可达时还要等满 4 次重试退避）才报出来，
        #    而且报的是网络错误，完全掩盖了真正的原因。
        expiry = self.expiry_ms if expiry_ms is None else int(expiry_ms)
        if expiry not in VALID_EXPIRY_MS:
            raise MoeMailError(
                f"expiryTime={expiry} 不是服务端接受的枚举值"
                f"（可选：{'、'.join(str(v) for v in VALID_EXPIRY_MS)}）"
            )

        use_domain = self.pick_domain(domain)
        local = (name or "").strip() or f"ts{uuid.uuid4().hex[:12]}"

        r = self._request(
            "POST", "/api/emails/generate",
            headers={"Content-Type": "application/json"},
            data=json.dumps({"name": local, "expiryTime": expiry, "domain": use_domain}),
        )
        data = self._json(r, "/api/emails/generate") or {}

        # 响应结构按版本有差异，逐个候选取 —— 取不到就把整个 body 摆出来，
        # 不要返回空字符串让调用方在下一步才炸（那时已经看不到响应了）。
        email = str(data.get("email") or data.get("address")
                    or (data.get("data") or {}).get("email") or "").strip()
        email_id = str(data.get("id") or data.get("emailId")
                       or (data.get("data") or {}).get("id") or "").strip()

        if not email:
            # 有些版本只回 id，地址得自己拼
            if email_id:
                email = f"{local}@{use_domain}"
            else:
                raise MoeMailError(
                    f"建邮箱响应里既没有地址也没有 id: "
                    f"{json.dumps(data, ensure_ascii=False)[:300]}"
                )

        if email_id:
            with self._lock:
                self._ids[email] = email_id
        else:
            # 🔴 建完没拿到 id 就**当场**反查一次并缓存。
            #    不这么做的话每次 `list_mails` 都会走 `_resolve_id` 的分页扫描
            #    （轮询是每几秒一次）⇒ 请求量直接翻几十倍，把 Worker 打成 1102。
            #    这里失败不抛：邮箱已经建好了，反查可以留给后面重试。
            try:
                self._resolve_id(email)
            except MoeMailError:
                pass

        return email

    # ── emailId 解析 ──────────────────────────────────────────────────
    def _resolve_id(self, email: str) -> str:
        """拿邮箱地址换 `emailId`，缓存未命中则去服务端分页反查。

        🔴 反查是这个后端的**自愈能力**：`resume_pending.py` 这类另起进程的补跑
        入口拿不到内存里的映射，但只要地址还在服务端的邮箱列表里就能找回来。
        （remail 的 `serviceToken` 没有对应端点，所以那边必须落盘台账。）
        """
        email = (email or "").strip()
        if not email:
            raise MoeMailError("收信必须指定 email")

        with self._lock:
            cached = self._ids.get(email)
        if cached:
            return cached

        cursor: str | None = None
        scanned = 0
        # 🔴 页数上限是 `MAX_RESOLVE_PAGES`（5），不是几十页：反查在缓存未命中时
        #    **每个账号都会发生一次**，翻太多页会把请求量放大一个量级 ——
        #    这是打出 Error 1102 的主因之一。正常路径不该走到这里
        #    （`create_mailbox` 建完就存了 id），反查只是补跑时的兜底。
        for _ in range(MAX_RESOLVE_PAGES):
            params = {"cursor": cursor} if cursor else {}
            r = self._request("GET", "/api/emails", params=params)
            body = self._json(r, "/api/emails") or {}

            for row in self._rows(body, "emails", "data", "items"):
                addr = str(row.get("address") or row.get("email") or "").strip()
                rid = str(row.get("id") or row.get("emailId") or "").strip()
                scanned += 1
                if addr and rid:
                    with self._lock:
                        self._ids.setdefault(addr, rid)
                    if addr.lower() == email.lower():
                        return rid

            cursor = body.get("nextCursor") or body.get("next_cursor")
            if not cursor:
                break

        raise MoeMailError(
            f"找不到 {email} 的 emailId（已扫 {scanned} 个邮箱）。"
            f"两种可能：①该地址不是 MoeMail 建的（换后端跑批时最常见）；"
            f"②邮箱已过期被服务端清理（本客户端的有效期设置是 {self.expiry_ms} ms）"
        )

    # ── 收信 ──────────────────────────────────────────────────────────
    def _ensure_body(self, email_id: str, msg: dict[str, Any]) -> str:
        """取正文。列表里没带就回源拉单封。

        列表接口是否返回正文按版本而异 ⇒ 这里做一次兜底。
        回源失败**不抛异常**：正文取不到时上层的 `match()` 会判不通过，
        继续轮询比让整条流程炸掉更合理（可能只是这一封的问题）。
        """
        for k in ("content", "text", "body", "html", "textContent", "htmlContent"):
            v = msg.get(k)
            if v:
                return _html_to_text(str(v)) if "<" in str(v)[:200] else str(v)

        msg_id = str(msg.get("id") or msg.get("messageId") or "").strip()
        if not msg_id:
            return ""

        try:
            r = self._request("GET", f"/api/emails/{email_id}/{msg_id}", retries=2)
            detail = self._json(r, "单封邮件") or {}
            # 单封接口也可能把内容包在 message / data 下
            for holder in (detail, detail.get("message") or {}, detail.get("data") or {}):
                if not isinstance(holder, dict):
                    continue
                for k in ("content", "text", "body", "html", "textContent", "htmlContent"):
                    v = holder.get(k)
                    if v:
                        return _html_to_text(str(v)) if "<" in str(v)[:200] else str(v)
        except MoeMailError:
            return ""
        return ""

    @staticmethod
    def _body_from_row(msg: dict[str, Any]) -> str:
        """从列表行里**直接**取正文（不发请求）。取不到返回空串。"""
        for k in ("content", "text", "body", "html", "textContent", "htmlContent"):
            v = msg.get(k)
            if v:
                s = str(v)
                return _html_to_text(s) if "<" in s[:200] else s
        return ""

    def list_mails(self, email: str | None = None,
                   limit: int | None = None, *,
                   with_body: bool = False) -> list[Mail]:
        """收信。**必须给 email** —— MoeMail 没有"扫全部邮箱的信"的端点。

        🔴 `with_body` 默认 **False**，这是关键的请求量控制（2026-09-22 修）：

        之前这里对**每一封**邮件都调 `_ensure_body()`，而列表接口不返回正文时
        它会回源 `GET /api/emails/{id}/{msgId}` ⇒ 邮箱里有 N 封邮件，
        一次轮询就是 `1 + N` 个请求。轮询是每几秒一次、持续整个等信过程，
        于是 **2 并发也能把 Worker 打成 Error 1102** —— 放大器不是并发数，
        是"每轮对所有历史邮件都回源一次"。

        现在默认只用列表里自带的字段，正文由 `wait_for_mail` 对**匹配候选**
        按需回源（通常就是目标那一封）。

        `limit` 只截断结果条数（服务端靠 cursor 分页，这里只取第一页：
        注册场景要的是最新那封验证邮件，翻页纯属浪费）。
        """
        if not email:
            raise MoeMailError("MoeMail 收信必须指定 email（没有全窗口扫描端点）")

        email_id = self._resolve_id(email)

        self.stats.polls += 1
        r = self._request("GET", f"/api/emails/{email_id}")
        body = self._json(r, f"/api/emails/{email_id}") or {}
        rows = self._rows(body, "messages", "data", "items", "emails")

        out: list[Mail] = []
        for m in rows:
            text = self._body_from_row(m)
            if not text and with_body:
                # 显式要求带正文时才回源（`scan`/调试路径）
                text = self._ensure_body(email_id, m)

            out.append(Mail(
                id=str(m.get("id") or m.get("messageId") or ""),
                to=email,
                sender=str(m.get("from_address") or m.get("from")
                           or m.get("fromAddress") or m.get("sender") or ""),
                subject=str(m.get("subject") or m.get("title") or ""),
                body=text,
                received_at=_to_ms(m.get("received_at") or m.get("receivedAt")
                                   or m.get("created_at") or m.get("createdAt")
                                   or m.get("date")),
            ))

        out.sort(key=lambda x: x.received_at)
        return out[-limit:] if limit else out

    def fetch_body(self, email: str, message_id: str) -> str:
        """读单封邮件全文（列表里的正文不够用时）。"""
        email_id = self._resolve_id(email)
        r = self._request("GET", f"/api/emails/{email_id}/{message_id}")
        detail = self._json(r, "单封邮件") or {}

        for holder in (detail, detail.get("message") or {}, detail.get("data") or {}):
            if not isinstance(holder, dict):
                continue
            for k in ("content", "text", "body", "html", "textContent", "htmlContent"):
                v = holder.get(k)
                if v:
                    return _html_to_text(str(v)) if "<" in str(v)[:200] else str(v)
        return ""

    def wait_for_mail(self, email: str, match: Callable[[Mail], bool], *,
                      timeout: float = 300.0, interval: float = 0.5,
                      since_ms: int | None = None) -> Mail | None:
        """轮询等一封满足条件的邮件。

        `since_ms` 用于跳过历史邮件 —— 同一个地址可能已经收过旧邮件
        （MoeMail 的邮箱可以长期存活，比 CF Worker 的 100 行窗口更容易撞上）。

        🔴 `interval` 会被抬到 `self.min_poll_interval`：上层的
        `stages.MAIL_POLL_INTERVAL = 0.5` 是给 CF Temp Email Worker 调优的，
        对跑在 Workers 免费额度上的 MoeMail 太激进（实测打出 Error 1102，
        一旦触发整个服务对所有请求都返回错误页）。
        做法与 Remail 一致：`wait = max(传入值, 自己的下界)`。

        🔴 正文**按需回源**，不是每轮对所有邮件都回源：
        先用列表自带的轻量字段跑一次 `match()`，只有列表里没正文、
        且这封邮件通过了时间过滤时才去拉全文。这样一轮轮询通常只有
        **1 个请求**（列表），命中时才多 1 个（取正文）——
        之前是 `1 + 邮箱里的邮件数`，那才是打出 1102 的真因。
        """
        wait = max(float(interval), self.min_poll_interval)
        deadline = time.time() + timeout
        #: 已经回源过正文的 message id —— 同一封邮件不要反复拉全文。
        fetched: set[str] = set()

        while time.time() < deadline:
            email_id = self._resolve_id(email)

            for m in self.list_mails(email=email):
                # 时间过滤放最前面：历史邮件直接跳过，不为它花任何请求
                if since_ms is not None and m.received_at < since_ms:
                    continue

                if match(m):
                    return m

                # 列表里没带正文 ⇒ 可能因为正文缺失才没匹配上，回源一次再判。
                # 只对**通过时间过滤**的邮件做，且每封只做一次。
                if not m.body and m.id and m.id not in fetched:
                    fetched.add(m.id)
                    m.body = self._ensure_body(email_id, {"id": m.id})
                    if m.body and match(m):
                        return m

            time.sleep(wait)
        return None

    def scan_all(self, limit: int = 100) -> list[Mail]:
        """MoeMail **没有**跨邮箱的全窗口扫描端点 —— 这是与 CF Worker 的能力差。

        刻意**抛异常而不是返回空列表**：返回 `[]` 会让"监听获批邮件"这类调用
        安静地什么都等不到，看起来像"邮件没来"。
        """
        raise MoeMailError(
            "MoeMail 没有跨邮箱扫描端点（只能按 emailId 收信）⇒ "
            "'不知道收件人是谁'的场景请改用 CF Worker 后端"
        )
