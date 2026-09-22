"""集中配置。

凭据一律从环境变量读取，默认值留空 —— 避免 `os.getenv(k, "真实值")` 这种泄漏点。
用 .env 加载（stdlib 实现，不引 python-dotenv）。
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """极简 .env 加载器：真实环境变量优先级更高。"""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


_load_dotenv(ROOT / ".env")

# ── CF Temp Email Worker ────────────────────────────────────────────────
# 🔴 三个都**不留真实默认值**。以前 `TEMPMAIL_BASE` 的默认值是真实的
#    `https://temp-email-worker.<子域>.workers.dev`、`TEMPMAIL_DOMAIN` 是真实域名，
#    于是"基础设施标识"被写进了仓库（Worker 子域 + 自有邮箱域名）。
#    这类东西单独看不是凭据，但合起来足以被针对性打击，且一旦公开就永久公开。
#    现在一律走 .env，缺失由 `validate()` 显式报出来（不是静默用默认值连上去）。
TEMPMAIL_BASE = os.getenv("TEMPMAIL_BASE", "")
TEMPMAIL_ADMIN_KEY = os.getenv("TEMPMAIL_ADMIN_KEY", "")
TEMPMAIL_DOMAIN = os.getenv("TEMPMAIL_DOMAIN", "")

# ── Cloudflare API（只有 tools/probes/* 诊断脚本用得到）──────────────────
# 同理：账号 id / D1 库 id / API Token 一个都不进仓库。
# `tools/probes/probe_worker_health.py` 与 `probe_email_routing.py` 会读这三个。
CF_API_TOKEN = os.getenv("CF_API_TOKEN", "")
CF_ACCOUNT_ID = os.getenv("CF_ACCOUNT_ID", "")
CF_D1_ID = os.getenv("CF_D1_ID", "")

# ── Remail 邮箱接口聚合（remail.aishop6.com）─────────────────────────────
# 与上面的 CF Worker **并列的第二个邮箱后端**：一个 API 切换多种邮箱
# （outlook / gmail / icloud / proto / 自有域名）。2026-09-21 接入。
#
# 🔴 与 CF Worker 的**根本差异**（设计 `src/remail.py` 时必须记住）：
#   下单 `POST /v1/open/orders` 用 Bearer API Key，返回 `deliveryEmail` +
#   `serviceToken`；而取件 `GET /v1/pickup` **不用 API Key**，只认
#   `email` + `token` 这一对 ⇒ token 必须按邮箱存起来，不能只存全局 key。
#
# 规格真源：`exports/remail_openapi.json`（从 `/openapi.json` 拉的 OpenAPI 3.0.3）。
REMAIL_BASE = os.getenv("REMAIL_BASE", "")
REMAIL_API_KEY = os.getenv("REMAIL_API_KEY", "")
# TypeSafe 在 Remail 上的项目 id（`GET /v1/open/projects` 里 name='TypeSafe' 那条）。
# 这不是凭据，是公开商品编号，可以留默认值。
REMAIL_PROJECT_ID = int(os.getenv("REMAIL_PROJECT_ID") or 155)
# 商品后缀（`emailSuffix`）。⚠️ **不是完整邮箱地址**，服务端明确不接受。
#
# 🔴 2026-09-21 实测：**`domain` 商品已无货**，下单直接
#    `HTTP 422 {"message":"Insufficient inventory."}`。
#    `GET /v1/open/projects/155` 当天各商品的有货情况：
#      microsoft      code 8.00   purchase 10.00   库存 4,539,067（24 个后缀）
#      gmail_variant  code 8.00   purchase 10.00   库存 1,000,534,521
#      proto          code 10.00  purchase 20.00   库存 5        ← 几乎等于没货
#      gmail          code **关闭**  purchase 500.00  库存 6
#      icloud         code **关闭**  purchase 10.00   库存 221,677
#      domain         code 0.01   purchase 0.02    库存 **0**   ← 曾经最便宜，现已无货
#    ⇒ 默认取 `outlook.com`（microsoft 商品下库存最大的后缀，4,008,800）。
#      刻意用**具体后缀**而不是加权随机的 `outlook`：注册场景要的是可控、主流，
#      而不是被随机分到 `outlook.com.gr` 这类冷门域名（对站点风控的影响未知）。
#    ⚠️ **成本量级变了**：8 积分/单，是原来 domain 的 **800 倍**。跑批前先看余额
#      （`--doctor --mail-backend remail` 会打出来），别按 0.01/单 的旧印象估预算。
#    ⚠️ 库存是**会变**的（`domain` 就从有货变成 0）⇒ 报"库存不足"时先去
#      `GET /v1/open/projects/155` 看当天哪个后缀有货，不要改代码猜。
REMAIL_EMAIL_SUFFIX = os.getenv("REMAIL_EMAIL_SUFFIX", "outlook.com")
# `code` = 短效接码（10 分钟窗口，更便宜）；`purchase` = 长效购买。
REMAIL_SERVICE_MODE = os.getenv("REMAIL_SERVICE_MODE", "code")

# ── MoeMail（自建临时邮箱服务）────────────────────────────────────────────
# 认证走 `X-API-Key` 头（**不是** Bearer）—— 这是与另两个后端的第一处差异。
#
# 🔴 收信端点按 **emailId** 索引（`GET /api/emails/{emailId}`），不是按地址。
#    所以客户端必须维护 `{email: emailId}` 映射，见 `MoeMailClient._resolve_id()`。
#
# 同 CF Worker：base 不留真实默认值，避免把自建服务地址写进仓库。
MOEMAIL_BASE = os.getenv("MOEMAIL_BASE", "")
MOEMAIL_API_KEY = os.getenv("MOEMAIL_API_KEY", "")
# 可用域名列表由 `GET /api/config` 给出 ⇒ 报"域名无效"时先去查那个接口，不要改代码猜。
MOEMAIL_DOMAIN = os.getenv("MOEMAIL_DOMAIN", "")
# 邮箱有效期（毫秒）。服务端只接受这几个枚举值：
#   3600000（1小时）/ 86400000（1天）/ 604800000（7天）/ 0（永久）
# 默认 1 天：注册流程分钟级就结束，但留足余量给补跑（`resume_pending.py`）。
MOEMAIL_EXPIRY_MS = int(os.getenv("MOEMAIL_EXPIRY_MS", "86400000"))

# ── 目标站点 ────────────────────────────────────────────────────────────
# 🔴 不留真实默认值 —— 同 `TEMPMAIL_BASE` 的理由：站点标识写进仓库等于公开
#    "这个工具在对谁做批量注册"。这类信息单独看不是凭据，但一旦公开就永久公开。
#
# 两个来源，**数据库优先**：
#   · Web 管理端 → 「系统设置」页写入数据库，跑批前由 `executor` 调
#     `apply_site_config()` 注入（页面改完立即生效，不用重启）
#   · 命令行入口 → 读 .env（`tools/run_e2e.py` 这类没有数据库的场景）
SITE_ORIGIN = os.getenv("SITE_ORIGIN", "").rstrip("/")
SITE_LOGIN = f"{SITE_ORIGIN}/login" if SITE_ORIGIN else ""
STYTCH_LOGIN_HOST = os.getenv("STYTCH_LOGIN_HOST", "").rstrip("/")
#: 邮件发件人域（用于 `mailrules` 匹配验证邮件）。
SENDER_DOMAIN = os.getenv("SENDER_DOMAIN", "")

#: 站点配置的字段名清单 —— 新增字段只改这里，API 与前端都从它派生。
SITE_CONFIG_KEYS = ("SITE_ORIGIN", "STYTCH_LOGIN_HOST", "SENDER_DOMAIN", "VERIFY_API_URL")

#: key 验收端点（`tools/verify_keys.py` 用）。同样可被页面覆盖。
VERIFY_API_URL = os.getenv("VERIFY_API_URL", "")


def apply_site_config(**values: str) -> dict[str, str]:
    """运行时覆盖站点配置，返回实际生效的值。

    🔴 为什么需要它：这些常量被 `typesafe.py` 以 `config.SITE_ORIGIN` 形式读取
    （属性访问 ⇒ 每次求值），所以改本模块的全局变量就能立即生效。

    ⚠️ 但有两处是 **import 时求值**的，改全局变量对它们无效，必须一起重建：
      · `mailrules` 的规则表（`sender_contains` 曾经在 import 时绑定域名）
      · `parsing.MAGIC_LINK_RE`（正则在 import 时用 STYTCH_LOGIN_HOST 编译）
    这两处已分别改成运行时读取 / 惰性编译，本函数负责通知它们失效。
    忘了这一步的表现是"页面上改了域名却不生效"，而且不报错。

    空值会被忽略（不覆盖）—— 页面上留空意为"沿用 .env"，不是"清成空"。
    """
    global SITE_ORIGIN, SITE_LOGIN, STYTCH_LOGIN_HOST, SENDER_DOMAIN, VERIFY_API_URL

    if v := str(values.get("SITE_ORIGIN") or "").strip().rstrip("/"):
        SITE_ORIGIN = v
        SITE_LOGIN = f"{v}/login"

    if v := str(values.get("STYTCH_LOGIN_HOST") or "").strip().rstrip("/"):
        STYTCH_LOGIN_HOST = v

    if v := str(values.get("SENDER_DOMAIN") or "").strip():
        SENDER_DOMAIN = v

    if v := str(values.get("VERIFY_API_URL") or "").strip():
        VERIFY_API_URL = v

    # 通知 import 时求值的那两处重建。延迟 import 避免循环依赖
    # （`parsing` / `mailrules` 都 import 了本模块）。
    from . import mailrules, parsing
    parsing.invalidate_magic_link_re()
    mailrules.invalidate_sender_cache()

    return current_site_config()


def current_site_config() -> dict[str, str]:
    """当前生效的站点配置。"""
    return {
        "SITE_ORIGIN": SITE_ORIGIN,
        "STYTCH_LOGIN_HOST": STYTCH_LOGIN_HOST,
        "SENDER_DOMAIN": SENDER_DOMAIN,
        "VERIFY_API_URL": VERIFY_API_URL,
    }

# ── Framer 表单（waitlist 申请）—— **已于 2026-09-21 整体移除** ─────────
#
# 这里曾有 `FRAMER_SITE_ID` / `FRAMER_FORM_ID` / `FRAMER_SUBMIT_URL` /
# `FRAMER_REFERER` 与一套 PoW 常量（`POW_SALT` / `POW_DIFFICULTY` / …）。
# 它们服务于"向 Framer 表单投递 waitlist 申请"这一步，而 TypeSafe
# **已取消邀请制**：现在 `/login` 直接发确认邮件，注册即登录。
#
# ⇒ 连同 `src/framer_waitlist.py` 一起删除。**不要凭印象加回来**：
#    判据是 `POST /login` 是否直接回 `x-action-redirect: /login?sent=true`，
#    以及邮件是否直接是 "Welcome to TypeSafe — confirm your email"。
#    两条 2026-09-21 实测均成立（见 docs/architecture.md §1）。

# ── 邮箱匹配规则 ────────────────────────────────────────────────────────
# 🔴 收件规则**不在本文件**，唯一真源是 `src/mailrules.py` 的 `RULES` 表。
#
# 这里曾经有一份副本（MAIL_FROM_WAITLIST / MAIL_FROM_STYTCH / SUBJ_*），
# 全项目零引用，但恰好是 README、mailrules docstring、docs/mail-filters.md
# 三处都在禁止的"散落 subject 子串"。留着它的实际危害是：
# 下一个人改文案时看到 `SUBJ_ACCOUNT_READY = "account is ready"` 会去改它，
# 而真正生效的是规则表 ⇒ 改了不生效，且查不出原因。
# 2026-09-20 审计后删除。要加规则请改 `src/mailrules.py`。

# ── 网络 ────────────────────────────────────────────────────────────────
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36"
)

# ── 输出 ────────────────────────────────────────────────────────────────
# 两类产物**分开放**（2026-09-20 起）：
#
#   exports/  运行产物 —— 台账（含全部尝试，含失败的）、日志、诊断残留
#   result/   **成功数据** —— 拿到 key 的账号 + 验收结果。这是**交付物**目录。
#
# 分开的理由：台账要留全部历史（失败的也留，便于复盘），
# 而交付物只该有成功的。以前两者混在 exports/ 里，取交付物时得自己筛。
EXPORT_DIR = ROOT / "exports"
LEDGER_PATH = EXPORT_DIR / "ledger.jsonl"

RESULT_DIR = ROOT / "result"
SUCCESS_LEDGER_PATH = RESULT_DIR / "success.jsonl"     # 成功账号（append-only，按 key 去重）
# 精简交付物：标准 JSON 数组，**只含关键字段**（email / api_key / api_key_id / created_at）。
#
# 🔴 与 success.jsonl 的分工：那份是**运行台账**，塞了 stages / timings / user.profile
#    整个对象（单行 2KB+），是给复盘用的；这份是**给下游灌数据**用的交付物 ——
#    管理端导入、别的系统对接都读它。两者由同一处写出（stage_create_key），不会分叉。
ACCOUNTS_JSON_PATH = RESULT_DIR / "accounts.json"
KEYS_TXT_PATH = RESULT_DIR / "keys.txt"                # email----api_key----api_key_id
KEYS_JSON_PATH = RESULT_DIR / "keys_verified.json"     # 机器可读的验收结果
# 纯 api_key，一行一个（`keys.txt` 的无元数据版：不带邮箱、不带 key_id）。
# 面向"直接把 key 灌进别的系统"的场景；与 keys.txt 由**同一次** verify 写出，故集合恒等。
APIKEYS_TXT_PATH = RESULT_DIR / "apikeys.txt"

# Remail 的**取件凭证台账**（JSONL，append-only）。
#
# 🔴 为什么必须落盘：`GET /v1/pickup` **不认 API Key**，只认下单时返回的
#    `email` + `serviceToken` 这一对。而 `serviceToken` 只在**下单那个进程的内存**里
#    ⇒ `resume_pending.py` / `relogin_pending.py` 这类**另起进程**的补跑入口
#    会拿着台账里的邮箱却取不了信，报"没有 serviceToken"。
#    CF Worker 那边没这个问题（全局 admin key 就够），所以这是 Remail **独有**的坑。
#
# ⚠️ 放 `result/` 下：它含 per-order 凭证，必须被 `.gitignore` 的 `result/` 覆盖。
# ⚠️ 它是 `result/` 下**唯一非交付物**的文件 —— 不要把它混进交付清单。
REMAIL_STATE_PATH = RESULT_DIR / "remail_orders.jsonl"


def validate(*, need_tempmail: bool = True) -> list[str]:
    """返回缺失的必需配置项。

    刻意不在 import 时抛错 —— 那样连 --help 都跑不起来。
    """
    missing: list[str] = []
    if need_tempmail:
        for key, val in (("TEMPMAIL_BASE", TEMPMAIL_BASE),
                         ("TEMPMAIL_ADMIN_KEY", TEMPMAIL_ADMIN_KEY),
                         ("TEMPMAIL_DOMAIN", TEMPMAIL_DOMAIN)):
            if not val:
                missing.append(key)

    # 目标站点三项与邮箱后端无关，任何后端都要有 —— 所以不受 need_tempmail 影响
    missing.extend(validate_site())
    return missing


def validate_site() -> list[str]:
    """目标站点三项。所有跑批路径都需要，与选哪个邮箱后端无关。"""
    return [k for k, v in (("SITE_ORIGIN", SITE_ORIGIN),
                           ("STYTCH_LOGIN_HOST", STYTCH_LOGIN_HOST),
                           ("SENDER_DOMAIN", SENDER_DOMAIN)) if not v]


def validate_cf() -> list[str]:
    """Cloudflare API 三项。只有诊断探针需要，主流程不需要 —— 所以单独一个函数。"""
    return [k for k, v in (("CF_API_TOKEN", CF_API_TOKEN),
                           ("CF_ACCOUNT_ID", CF_ACCOUNT_ID),
                           ("CF_D1_ID", CF_D1_ID)) if not v]


def validate_remail() -> list[str]:
    """Remail 后端两项。**只有选了 Remail 后端才需要** —— 所以不并进 `validate()`，
    否则用 CF Worker 跑批时会被无谓地拦下来。"""
    return [k for k, v in (("REMAIL_BASE", REMAIL_BASE),
                           ("REMAIL_API_KEY", REMAIL_API_KEY)) if not v]

def validate_moemail() -> list[str]:
    """MoeMail 后端必需项。同 `validate_remail()`：**只有选了该后端才校验**，
    否则用别的后端跑批会被无谓拦下。

    `MOEMAIL_DOMAIN` 不在必需列表里 —— 缺省时由 `GET /api/config` 取第一个可用域名
    （见 `MoeMailClient.pick_domain()`），比让用户猜一个填进 .env 更不容易错。
    """
    return [k for k, v in (("MOEMAIL_BASE", MOEMAIL_BASE),
                           ("MOEMAIL_API_KEY", MOEMAIL_API_KEY)) if not v]


def validate_for_backend(backend: str) -> list[str]:
    """按后端取必需配置的缺失项 —— 新增后端只改这里一处。

    以前各调用点自己写 `validate_remail() if backend == "remail" else validate()`，
    加第三个后端时就得去每个调用点补分支（漏一处的表现是：用 MoeMail 跑批
    被 CF Worker 的配置缺失拦下，报错信息指向完全无关的变量）。
    """
    name = (backend or "").strip().lower()
    if name == "remail":
        # 邮箱后端两项 + 站点三项（站点与后端无关，漏了同样跑不了）
        return validate_remail() + validate_site()
    if name == "moemail":
        return validate_moemail() + validate_site()
    return validate()
