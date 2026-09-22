"""鎺㈤拡锛氭帴鍙?ToS **涔嬪悗**锛宱nboarding 杩樺墿鍝嚑姝ャ€?

涓婁竴杞帰閽堢殑鐩茬偣锛氬畠鍦?*鎺ュ彈 ToS 涔嬪墠**灏?GET 浜嗘墍鏈?/setup/* 椤甸潰锛?
浜庢槸鍥涗釜璺緞閮借繑鍥炲悓涓€寮?ToS 椤碉紙onboarding 闂ㄧ锛夛紝鐪嬩笉鍑?set-name 鐨勭湡瀹炲舰鎬併€?

鏈疆椤哄簭锛氱櫥褰?鈫?鍏堝彧鎻愪氦 ToS 鈫?鍐嶉€愪釜 GET 椤甸潰 鈫?鍐嶆彁浜?set-name 鈫?鍐?GET銆?
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _bootstrap import *  # noqa: F401,F403,E402

from src import config  # noqa: E402
from src.parsing import actions_from_html, extract_magic_link, visible_text  # noqa: E402
from src.tempemail import TempMailClient  # noqa: E402
from src.typesafe import MODE_LINK, TypeSafeClient  # noqa: E402

BASE = config.SITE_ORIGIN
mail = TempMailClient()
email = mail.create_mailbox()
print(f"=== 閭 {email} ===")

cl = TypeSafeClient()
since = int(time.time() * 1000) - 5_000
cl.send_login_email(email, mode=MODE_LINK)
m = mail.wait_for_mail(email, lambda x: "confirm your email" in x.subject.lower(),
                       timeout=180, interval=2.0, since_ms=since)
redirect = cl.exchange_magic_link(extract_magic_link(m.body))
res = cl.auth_callback(cl.token_from_redirect_url(redirect), "magic_links", email)
print(f"auth_callback HTTP {res.status} ok={res.ok}")


def dump_pages(tag: str) -> None:
    print(f"\n### 鍚勯〉闈㈠舰鎬侊紙{tag}锛?)
    for path in ("/setup/tos?returnTo=%2Fhook", "/setup/set-name?returnTo=%2Fhook",
                 "/setup/console-survey?returnTo=%2Fhook", "/hook", "/home"):
        r = cl.s.get(f"{BASE}{path}", timeout=30, allow_redirects=False)
        loc = r.headers.get("location", "")
        body = r.text if r.status_code == 200 else ""
        acts = actions_from_html(body) if body else {}
        aid = ""
        if acts:
            n = sorted(acts)[0]
            aid = f" id={acts[n]['id'][:20]}鈥?
        print(f"  {path:44s} HTTP {r.status_code} len={len(body):6d} "
              f"actions={sorted(acts)}{aid} loc={loc[:60]!r}")
        if body:
            print(f"        txt={visible_text(body)[:120]!r}")


dump_pages("鎺ュ彈 ToS 鍓?)

print("\n### 鍙彁浜?ToS")
r1 = cl.post_setup("/setup/tos?returnTo=%2Fhook", {
    "legalAcknowledged": "true", "returnTo": "/hook",
    "marketingOptIn": "true", "marketingOptedOutInitial": ""})
print(f"  tos -> ok={r1.ok} via={r1.data.get('via')} redirect={r1.data.get('redirect')!r} "
      f"err={r1.error!r}")
st = cl.onboarding_state()
print(f"  /api/me: needs_tos={st['needs_tos']} needs_name={st['needs_name']} "
      f"needs_survey={st['needs_survey']} human_name={st['profile'].get('human_name')!r}")

dump_pages("鎺ュ彈 ToS 鍚?)
