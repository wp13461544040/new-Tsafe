#!/usr/bin/env python
"""诊断共享 temp-email-worker 的"入库停摆"（第二步：定位是哪一层断的）。

`probe_worker_health.py` 已确认：D1 里最新一行是 06:12:02，之后**零入库**。
现在要分清是三层里的哪一层：

  A. **Cloudflare Email Routing**（域名收信规则）—— 关了/规则没了 ⇒ 邮件根本没进来
  B. **Worker 的 email handler** —— 版本详情里 handlers 缺 `email` ⇒ 邮件进来了但没人接
  C. **Worker 内部逻辑** —— handler 在但抛异常 ⇒ 表现为入库为 0

判据：
  - `email/routing` 的 `enabled` + `status`
  - catch-all 规则是否仍指向 worker
  - 部署版本的 `handlers` 列表

用法：
    python tools/probes/probe_email_routing.py
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _bootstrap import ROOT  # noqa: E402,F401

from src import config  # noqa: E402

# 🔴 凭据**只从命令行/环境变量取**，代码里不留默认值。
# 这里以前把账号 id 与一个**活令牌**当默认值写死在代码里。
# 真正的值由 `main()` 从命令行参数覆盖。
ACCOUNT_ID = os.getenv("CF_ACCOUNT_ID", "")
TOKEN = os.getenv("CF_API_TOKEN", "")
#: 探针自己的域名清单（`--domain` 可传多次）。空则问 Worker 的 /health。
DOMAINS: list[str] = []

SCRIPT = "temp-email-worker"


#: ⚠️ 运行时拼，不要做成模块级常量 —— `ACCOUNT_ID` 会被命令行参数覆盖。
def _base() -> str:
    return f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}"


def domains(worker_base: str = "") -> list[str]:
    """要查的域名列表。

    优先命令行传的 `--domain`；没传就问 Worker 自己的 `/health`
    —— 它返回的 `domains` 就是权威列表，不必在任何地方维护一份副本。
    """
    if DOMAINS:
        return DOMAINS

    base = (worker_base or config.TEMPMAIL_BASE or "").rstrip("/")
    if not base:
        return []
    try:
        req = urllib.request.Request(f"{base}/health")
        req.add_header("User-Agent", config.UA)
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode()).get("domains") or []
    except Exception as exc:  # noqa: BLE001
        print(f"  ⚠ 取域名列表失败（{exc}）—— 用 --domain 显式指定")
        return []


def api(path: str) -> dict:
    req = urllib.request.Request(_base() + path)
    req.add_header("Authorization", f"Bearer {TOKEN}")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return {"success": False, "http": exc.code, "body": exc.read().decode()[:400]}


def show(label: str, path: str, keys: list[str] | None = None) -> dict:
    out = api(path)
    print(f"\n── {label} ──")
    if not out.get("success"):
        print("   ✗", out.get("http"), str(out.get("body"))[:300])
        return out
    res = out["result"]
    if isinstance(res, list):
        print(f"   {len(res)} 条")
        for r in res[:12]:
            if keys:
                print("   ", {k: r.get(k) for k in keys})
            else:
                print("   ", json.dumps(r, ensure_ascii=False)[:220])
    else:
        print("   ", json.dumps(res, ensure_ascii=False)[:600])
    return out


def main() -> int:
    global ACCOUNT_ID, TOKEN, DOMAINS

    ap = argparse.ArgumentParser(
        description="Cloudflare Email Routing 收信链路诊断（只读）",
        epilog="凭据只在命令行传，不落任何文件 —— 探针是一次性工具。",
    )
    ap.add_argument("--token", default=TOKEN, help="Cloudflare API Token（需 Zone:Read）")
    ap.add_argument("--account-id", default=ACCOUNT_ID, help="账号 id")
    ap.add_argument("--domain", action="append", default=[],
                    help="要查的域名，可传多次。不传则问 Worker 的 /health")
    ap.add_argument("--worker-base", default="",
                    help="Worker 根地址，用于自动取域名列表")
    args = ap.parse_args()

    TOKEN, ACCOUNT_ID = args.token, args.account_id
    DOMAINS = args.domain

    if missing := [n for n, v in (("--token", TOKEN), ("--account-id", ACCOUNT_ID)) if not v]:
        print("✗ 缺少参数：" + ", ".join(missing))
        print("  用法：python tools/probes/probe_email_routing.py \\")
        print("          --token <API_TOKEN> --account-id <ACCOUNT_ID> [--domain a.com]")
        return 1

    doms = domains(args.worker_base)
    if not doms:
        print("✗ 拿不到域名列表：用 --domain 显式指定，"
              "或 --worker-base 指向 Worker 让它的 /health 自报。")
        return 1

    print("=" * 74)
    print("A) Email Routing：域名的收信开关（这一层关了 ⇒ 邮件压根进不来）")
    print("=" * 74)
    for d in doms:
        out = api(f"/zones?name={d}")
        if not out.get("success") or not out["result"]:
            print(f"  {d:<22} ✗ 拿不到 zone（不在本账号？）")
            continue
        zid = out["result"][0]["id"]
        r = api(f"/zones/{zid}/email/routing")
        res = r.get("result") or {}
        flag = "✓" if res.get("enabled") else "🔴 未启用"
        print(f"  {d:<22} {flag}  status={res.get('status')}  zone={zid}")

    print("\n" + "=" * 74)
    print("B) catch-all 规则是否仍指向 Worker")
    print("=" * 74)
    for d in doms:
        out = api(f"/zones?name={d}")
        if not out.get("success") or not out["result"]:
            continue
        zid = out["result"][0]["id"]
        r = api(f"/zones/{zid}/email/routing/rules/catch_all")
        if not r.get("success"):
            print(f"  {d:<22} ✗ {r.get('http')} {str(r.get('body'))[:160]}")
            continue
        res = r.get("result") or {}
        acts = res.get("actions") or []
        tgt = [a.get("value") for a in acts if a.get("type") == "worker"]
        print(f"  {d:<22} enabled={res.get('enabled')}  actions={[a.get('type') for a in acts]}"
              f"  worker={tgt}")

    print("\n" + "=" * 74)
    print("C) 部署版本的 handlers（缺 email ⇒ 邮件没人接；缺 scheduled ⇒ 定时清理没了）")
    print("=" * 74)
    out = api(f"/workers/scripts/{SCRIPT}/versions")
    if not out.get("success"):
        print("   ✗", out.get("http"), str(out.get("body"))[:300])
    else:
        vs = out["result"]["items"] if isinstance(out["result"], dict) else out["result"]
        for v in vs[:5]:
            hs = ((v.get("resources") or {}).get("script") or {}).get("handlers") or []
            meta = v.get("metadata") or {}
            print(f"   {v.get('id')}  created={v.get('created_on')}  handlers={hs}")
            print(f"      bindings={[b.get('name') for b in (meta.get('bindings') or [])]}")

    print("\n" + "=" * 74)
    print("D) 最近的部署（对齐「停摆时刻 06:12」）")
    print("=" * 74)
    show("deployments", f"/workers/scripts/{SCRIPT}/deployments")
    return 0


if __name__ == "__main__":
    sys.exit(main())
