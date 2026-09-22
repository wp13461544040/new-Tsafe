#!/usr/bin/env python
"""诊断共享 temp-email-worker 的"入库停摆"。

背景（2026-09-20 09:07）：邮箱窗口里**最新一封是 06:12:02**，
距当时 175 分钟。跑批因此全部卡在"等 waitlist 确认邮件"。

这个脚本只做**只读**查询，回答三个问题：

  1. Worker 还在不在、版本详情对不对（`handlers` 必须含 `scheduled` —— 缺它就是
     09-17 那次 5.5 小时 100% `scriptThrewException` 的形态）
  2. **D1 里到底有没有 06:12 之后的行** —— 这是"入库停摆" vs "读取被过滤"的分水岭
  3. 最近一次部署是什么时候（改坏了要能指到具体时间）

用法：
    python tools/probes/probe_worker_health.py
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
# 2026-09-20 之前这里是 `os.environ.get("CF_API_TOKEN", "cfat_…真实令牌…")` ——
# 等于把一个有 Workers/D1 读权限的活令牌提交进了仓库。
# 默认值来自环境变量（可为空），真正的值由 `main()` 从命令行参数覆盖。
TOKEN = os.getenv("CF_API_TOKEN", "")
ACCOUNT_ID = os.getenv("CF_ACCOUNT_ID", "")
D1_ID = os.getenv("CF_D1_ID", "")

# Worker 名字不是凭据，留在代码里没问题。
SCRIPT = "temp-email-worker"

#: ⚠️ 不要在模块级拼成常量 —— `ACCOUNT_ID` 由 `main()` 从命令行参数覆盖，
#:    import 时求值的话命令行传的账号 id 会被静默忽略（同 mailrules 踩过的坑）。
def _base() -> str:
    return f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}"


def api(path: str, *, method: str = "GET", body: dict | None = None) -> dict:
    url = _base() + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {TOKEN}")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return {"success": False, "http": exc.code, "body": exc.read().decode()[:500]}


def d1_query(sql: str) -> dict:
    return api(f"/d1/database/{D1_ID}/query", method="POST", body={"sql": sql})


def main() -> int:
    global TOKEN, ACCOUNT_ID, D1_ID

    ap = argparse.ArgumentParser(
        description="Cloudflare Worker / D1 健康诊断（只读）",
        epilog="凭据只在命令行传，不落任何文件 —— 探针是一次性工具，"
               "没必要为它维护一份配置。",
    )
    ap.add_argument("--token", default=TOKEN,
                    help="Cloudflare API Token（需 Workers Scripts:Read + D1:Read）")
    ap.add_argument("--account-id", default=ACCOUNT_ID, help="账号 id（32 位十六进制）")
    ap.add_argument("--d1-id", default=D1_ID, help="D1 数据库 id")
    args = ap.parse_args()

    TOKEN, ACCOUNT_ID, D1_ID = args.token, args.account_id, args.d1_id

    if missing := [n for n, v in (("--token", TOKEN), ("--account-id", ACCOUNT_ID),
                                  ("--d1-id", D1_ID)) if not v]:
        print("✗ 缺少参数：" + ", ".join(missing))
        print("  用法：python tools/probes/probe_worker_health.py \\")
        print("          --token <API_TOKEN> --account-id <ACCOUNT_ID> --d1-id <D1_ID>")
        print("  （也可用同名环境变量 CF_API_TOKEN / CF_ACCOUNT_ID / CF_D1_ID）")
        return 1

    print("=" * 74)
    print("1) Worker 服务与版本详情")
    print("=" * 74)
    svc = api(f"/workers/services/{SCRIPT}")
    if svc.get("success"):
        r = svc["result"]
        print(f"  id={r.get('id')}  default_environment={r.get('default_environment', {}).get('name')}")
    else:
        print("  ✗ 查询失败:", svc.get("http"), svc.get("body", "")[:200])

    for label, path in [
        ("settings", f"/workers/scripts/{SCRIPT}/settings"),
        ("deployments", f"/workers/scripts/{SCRIPT}/deployments"),
    ]:
        out = api(path)
        print(f"\n  ── {label} ──")
        if not out.get("success"):
            print("   ✗", out.get("http"), str(out.get("body"))[:300])
            continue
        res = out["result"]
        if label == "settings":
            print("   bindings:", [b.get("name") for b in res.get("bindings", [])])
            print("   compatibility_date:", res.get("compatibility_date"))
            print("   placement:", res.get("placement"))
            print("   logpush:", res.get("logpush"), " tail_consumers:", res.get("tail_consumers"))
            # 09-17 那次的判据：handlers 里少了 scheduled 就只剩 HTTP 事件，
            # 定时清理与邮件事件都跑不起来
            hs = res.get("handlers") or []
            print("   handlers:", hs)
            if not hs:
                # ⚠️ 空数组 ≠ 缺 scheduled：这是"CF API 本次没返回该字段"，
                # 不是"Worker 没有 scheduled handler"。别把"没拿到"当成"没有"
                # （2026-09-21 实测：看到 `handlers: []` 一度误判为 09-17 事故形态，
                #  实际 D1 正在正常入库）。判据请以第 2 节的 D1 直查为准。
                print("   ⚠️ handlers 为空 —— CF API 本次未返回该字段，**不能**据此判断；"
                      "请看第 2 节 D1 直查")
            elif "scheduled" not in hs:
                print("   🔴 handlers 缺 scheduled —— 与 09-17 事故同形态")
        else:
            d = (res.get("deployments") or [{}])[0]
            print("   version_id:", d.get("version_id"))
            print("   created_on:", d.get("created_on"))
            print("   author:", (d.get("author") or {}).get("email"))
            for a in (d.get("annotations") or {}).items():
                print("   annotation:", a)

    print("\n" + "=" * 74)
    print("2) D1 直查：06:12 之后到底有没有行（分水岭）")
    print("=" * 74)
    for sql in [
        "SELECT COUNT(*) AS n, MAX(received_at) AS newest, MIN(received_at) AS oldest FROM emails",
        "SELECT COUNT(*) AS n_after FROM emails WHERE received_at > 1789855922000",
    ]:
        out = d1_query(sql)
        print(f"  SQL: {sql}")
        if out.get("success"):
            print("   ->", json.dumps(out["result"], ensure_ascii=False)[:400])
        else:
            print("   ✗", out.get("http"), str(out.get("body"))[:300])
        print()

    # 🔴 列名必须是 `to_address`，**不是** `recipient`。
    # `emails` 表的真实列（`PRAGMA table_info` 实测）：
    #   id / message_id / from_address / to_address / subject /
    #   extracted_json / received_at / raw_text / raw_html
    # 这里曾写 `recipient` ⇒ D1 回 `no such column: recipient`，
    # 而本探针第 3 节自己就写着「确认列名，避免把'没这列'当成'没数据'」——
    # 规则没用到自己的代码上（2026-09-21 修）。
    out = d1_query("SELECT received_at, to_address, subject FROM emails "
                   "ORDER BY received_at DESC LIMIT 5")
    print("  最新 5 行：")
    if out.get("success"):
        rows = (out["result"][0].get("results") if isinstance(out["result"], list)
                else out["result"].get("results")) or []
        for r in rows:
            print("   ", r.get("received_at"), r.get("to_address"), "|", str(r.get("subject"))[:60])
    else:
        print("   ✗", out.get("http"), str(out.get("body"))[:300])

    print("\n" + "=" * 74)
    print("3) 表结构（确认列名，避免把'没这列'当成'没数据'）")
    print("=" * 74)
    out = d1_query("SELECT name FROM sqlite_master WHERE type='table'")
    if out.get("success"):
        res = out["result"]
        rows = (res[0].get("results") if isinstance(res, list) else res.get("results")) or []
        print("  表:", [r.get("name") for r in rows])
    return 0


if __name__ == "__main__":
    sys.exit(main())
