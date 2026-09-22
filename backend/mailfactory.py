"""把 Web 端的 `MailConfig` 记录变成注册器能用的邮箱客户端。

🔴 这是 Web 端与注册器之间的**唯一**桥：在它出现之前，页面上存的邮箱配置
   只是存了起来，跑批时 `make_mail_client()` 读的仍是 `.env` —— 表现是
   "在页面上改了配置却完全不生效"，而且不报错。

所有字段都**显式传入**，不让客户端回落到 `config.*`：
回落的表现是"页面上某一项留空，实际用了 .env 里的另一个值"，
对账时才发现邮箱建在了别的服务上。
"""
from src.moemail import MoeMailClient
from src.remail import RemailClient
from src.tempemail import TempMailClient


class MailConfigError(RuntimeError):
    """配置不完整或后端未知 —— 必须在跑批**之前**抛出来，不要等到建邮箱时才炸。"""


def build_mail_client(mc):
    """按 `MailConfig` 记录造客户端。

    Args:
        mc: `backend.models.MailConfig` 实例

    Returns:
        实现统一契约（`create_mailbox` / `list_mails` / `wait_for_mail` /
        `health` / `stats`）的客户端实例。

    Raises:
        MailConfigError: 后端未知或必填项缺失。
    """
    if mc is None:
        raise MailConfigError("没有选择邮箱配置")

    backend = (mc.backend or "").strip().lower()

    if backend == "cf":
        missing = [k for k, v in (
            ("服务地址", mc.tempmail_base),
            ("Admin Key", mc.tempmail_admin_key),
            ("域名", mc.tempmail_domain),
        ) if not v]
        if missing:
            raise MailConfigError(
                f"CF Worker 配置「{mc.name}」缺少：{'、'.join(missing)}"
            )
        return TempMailClient(
            base=mc.tempmail_base,
            admin_key=mc.tempmail_admin_key,
            domain=mc.tempmail_domain,
        )

    if backend == "remail":
        missing = [k for k, v in (
            ("服务地址", mc.remail_base),
            ("API Key", mc.remail_api_key),
        ) if not v]
        if missing:
            raise MailConfigError(
                f"Remail 配置「{mc.name}」缺少：{'、'.join(missing)}"
            )
        return RemailClient(
            base=mc.remail_base,
            api_key=mc.remail_api_key,
            # `project_id` 留空时传 None，让客户端用自己的默认值
            # （那是公开商品编号，不是凭据）
            project_id=mc.remail_project_id or None,
            email_suffix=mc.remail_email_suffix or None,
            service_mode=mc.remail_service_mode or None,
        )

    if backend == "moemail":
        missing = [k for k, v in (
            ("服务地址", mc.moemail_base),
            ("API Key", mc.moemail_api_key),
        ) if not v]
        if missing:
            raise MailConfigError(
                f"MoeMail 配置「{mc.name}」缺少：{'、'.join(missing)}"
            )
        return MoeMailClient(
            base=mc.moemail_base,
            api_key=mc.moemail_api_key,
            # 空串是**合法的显式值**（= 让客户端从 /api/config 自动选），
            # 所以这里传 `or ""` 而不是 `or None` —— 传 None 会让客户端
            # 回落到 .env 的 MOEMAIL_DOMAIN，那就不是"页面说了算"了。
            domain=mc.moemail_domain or "",
            expiry_ms=mc.moemail_expiry_ms if mc.moemail_expiry_ms is not None else 86400000,
            # 轮询间隔：留空/0 用客户端默认下界（3 秒）。
            # 调小会打出 Error 1102，所以这里不做"0 当成无限制"的解读。
            min_poll_interval=mc.moemail_poll_interval or None,
        )

    raise MailConfigError(
        f"未知邮箱后端 {mc.backend!r}（可选：cf、remail、moemail）"
    )


def check_mail_config(mc) -> dict:
    """连通性体检：造客户端并调一次 `health()`。

    页面上的「测试连接」按钮用它。放在创建任务**之前**做，
    比让用户跑一批然后看着全部失败要好。
    """
    client = build_mail_client(mc)
    try:
        info = client.health()
    except Exception as exc:  # noqa: BLE001 - 任何异常都要转成可读结论
        return {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
        }

    out = {"ok": True, "backend": mc.backend}

    # 各后端的 health() 返回结构不同，挑有用的摘出来
    if isinstance(info, dict):
        if "domains" in info:
            out["domains"] = info["domains"]
        for k in ("balance", "credits", "status"):
            if k in info:
                out[k] = info[k]

    return out
