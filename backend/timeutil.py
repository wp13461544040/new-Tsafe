"""统一时间基准：北京时间（UTC+8）。

背景：原先全项目用 `datetime.utcnow()` 存库，`to_dict()` 又用 `isoformat()`
输出**不带时区后缀**的裸字符串（如 `2026-09-23T04:10:00`）。
前端 dayjs / `new Date()` 会把这种裸字符串当**本地时间**解析，
结果页面上所有时间都比真实北京时间慢 8 小时。

修复方式：后端所有"当前时间"统一取北京时间的 naive datetime。
这样存库、比较、序列化、前端展示全链路一致，无需前端改动。
"""

from datetime import datetime, timedelta, timezone

# UTC+8，不依赖系统 tzdata（slim 镜像默认没装时区库）
BEIJING_TZ = timezone(timedelta(hours=8))


def now() -> datetime:
    """当前北京时间（naive，无 tzinfo，便于直接存入 SQLite DateTime 列）。"""
    return datetime.now(BEIJING_TZ).replace(tzinfo=None)


def now_aware() -> datetime:
    """当前北京时间（带 tzinfo），需要明确时区语义时使用。"""
    return datetime.now(BEIJING_TZ)
