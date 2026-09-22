"""账号池巡检验活执行器。

设计要点：

1. **线程池只做网络探测，落库统一在主线程**。
   SQLAlchemy 的 session 不是线程安全的 —— 在 worker 线程里碰 ORM 对象会拿到
   `Session is already flushing` / 对象莫名脱管这类错误，而且是间歇性的
   （并发低时不出现）。所以线程池收到的只是 `(id, api_key)` 两个裸值，
   返回 `(id, CheckResult)`，由主线程统一 `apply_check` 并提交。

2. **同一时刻只允许一轮巡检**。
   两轮并行会让同一个账号被检查两次、`fail_streak` 被累加两次 ⇒
   阈值 2 的配置实际变成阈值 1，账号被提前判死。

3. **分批提交**。几千个账号攒到最后一次提交，中途取消就全丢；
   而且长事务会把 SQLite 锁住，前端查询全部卡住。

4. **`unknown` 全军覆没时提前中止**。
   如果前 N 个账号全是 unknown，几乎肯定是验收端点挂了或没配，
   继续跑几千个只是浪费时间 —— 而且每个都要经历重试+退避，会跑很久。
"""
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

from backend.models import Account, OperationLog, db
from src import config as site_config
from src import keycheck

#: 进程内状态。巡检是全局单例（不像注册任务那样一个 id 一个线程），
#: 所以用一组模块级变量就够，不需要按 id 索引的字典。
_lock = threading.Lock()
_running = False
_stop_event: threading.Event | None = None
#: 上一轮（或当前轮）的统计快照，供 API 查询进度用。
_last_stats: dict = {}

#: 连续多少个 unknown 就认为端点有问题、提前中止。
#: 取 20：正常情况下不会连续 20 个都读不出来；而端点挂了时这 20 个
#: 已经足够确认（每个还带重试，再多纯属浪费）。
UNKNOWN_ABORT_STREAK = 20

#: 每多少条提交一次。50 是折中：太小则提交开销占比高，
#: 太大则取消响应慢、SQLite 锁持有时间长。
COMMIT_BATCH = 50


def is_checking() -> bool:
    with _lock:
        return _running


def request_stop() -> bool:
    """请求停止当前巡检。返回当时是否正在跑。

    ⚠️ 只是打标记：已经派给线程池的探测会跑完（每个最多几十秒），
    下一批开始前才生效。强杀线程会让部分结果落了库、部分没落，
    `fail_streak` 处于不一致状态。
    """
    with _lock:
        if not _running or _stop_event is None:
            return False
        _stop_event.set()
        return True


def last_stats() -> dict:
    with _lock:
        return dict(_last_stats)


def _pick_targets(*, limit: int, include_assigned: bool,
                  min_interval_hours: float) -> list[tuple[int, str]]:
    """挑出本轮要检查的账号，返回 (id, api_key) 列表。

    排序按 `last_checked_at` 升序 —— SQLite 里 NULL 排最前，
    正好让**从未检查过的**账号优先，不需要额外写 NULLS FIRST
    （那是 PostgreSQL 语法，SQLite 不认）。
    """
    q = Account.query

    # 默认只查还在池子里的与已失效的：
    #   · available —— 要确认还能不能发给用户（这是巡检的主要目的）
    #   · invalid   —— 要看能不能恢复（误判或临时故障过去了）
    #   · assigned  —— 已发给用户，检查只为统计，默认不占配额
    statuses = ["available", "invalid"]
    if include_assigned:
        statuses.append("assigned")
    q = q.filter(Account.status.in_(statuses))

    # 跳过刚检查过的：定时巡检每小时触发一次，但间隔设 24h 时
    # 不该每小时把同一批又检查一遍。
    if min_interval_hours > 0:
        cutoff = datetime.utcnow() - timedelta(hours=min_interval_hours)
        q = q.filter(
            db.or_(Account.last_checked_at.is_(None),
                   Account.last_checked_at < cutoff)
        )

    rows = (q.order_by(Account.last_checked_at.asc())
             .limit(limit)
             .with_entities(Account.id, Account.api_key)
             .all())
    return [(r[0], r[1]) for r in rows]


def run_check(app, *, limit: int = 200, concurrency: int = 5,
              dead_threshold: int = 2, include_assigned: bool = False,
              min_interval_hours: float = 0.0, timeout: float = 30.0,
              triggered_by: str = "manual", user_id: int | None = None) -> dict:
    """跑一轮巡检（**同步**，调用方负责决定是否放到线程里）。

    Args:
        limit: 本轮最多检查多少个
        concurrency: 并发探测数
        dead_threshold: 连续几次 dead 才标失效
        include_assigned: 是否也检查已被卡密提取的账号
        min_interval_hours: 跳过最近这么多小时内检查过的（0=不跳过）
        timeout: 单次探测超时
        triggered_by: manual / schedule，只用于日志
        user_id: 记操作日志用

    Returns:
        统计 dict。`aborted` 字段说明是否被提前中止及原因。
    """
    global _running, _stop_event, _last_stats

    with _lock:
        if _running:
            return {"ok": False, "error": "已有一轮巡检在跑，请等它结束"}
        _running = True
        _stop_event = threading.Event()
        stop = _stop_event

    started = time.time()
    stats = {
        "ok": True, "checked": 0, "alive": 0, "dead": 0, "unknown": 0,
        "newly_invalid": 0, "recovered": 0, "total": 0,
        "aborted": "", "triggered_by": triggered_by,
        "started_at": datetime.utcnow().isoformat(),
    }

    try:
        with app.app_context():
            api_url = (site_config.VERIFY_API_URL or "").strip()
            if not api_url:
                stats.update(ok=False, error=(
                    "未配置 Key 验收端点 —— 无法验活。"
                    "请到「系统设置 → 站点配置」填 VERIFY_API_URL"))
                return stats

            targets = _pick_targets(limit=limit,
                                    include_assigned=include_assigned,
                                    min_interval_hours=min_interval_hours)
            stats["total"] = len(targets)

            with _lock:
                _last_stats = dict(stats)

            if not targets:
                stats["aborted"] = "没有符合条件的账号（可能都在间隔内刚检查过）"
                return stats

            unknown_streak = 0
            pending = 0

            with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
                futures = {
                    pool.submit(keycheck.check_key, key,
                                api_url=api_url, timeout=timeout): aid
                    for aid, key in targets
                }

                for fut in as_completed(futures):
                    aid = futures[fut]

                    try:
                        res = fut.result()
                    except Exception as exc:  # noqa: BLE001
                        # 探测函数本身炸了（不该发生，但不能让整轮挂掉）⇒
                        # 当 unknown 处理：绝不能因为我们自己的 bug 把账号判死。
                        res = keycheck.CheckResult(
                            "unknown", error=f"探测异常 {type(exc).__name__}: {exc}")

                    acc = db.session.get(Account, aid)
                    if acc is None:
                        continue

                    newly_dead = acc.apply_check(
                        res.verdict,
                        error=(f"HTTP {res.status} " if res.status else "") + (res.error or ""),
                        dead_threshold=dead_threshold,
                    )

                    stats["checked"] += 1
                    stats[res.verdict] += 1
                    if newly_dead:
                        stats["newly_invalid"] += 1
                    # apply_check 里 alive 会把巡检判死的账号恢复成 available
                    if res.verdict == "alive" and acc.status == "available" \
                            and "[巡检已恢复]" in (acc.remarks or ""):
                        stats["recovered"] += 1

                    # 端点整体不可用的早停判断
                    unknown_streak = unknown_streak + 1 if res.verdict == "unknown" else 0

                    pending += 1
                    if pending >= COMMIT_BATCH:
                        db.session.commit()
                        pending = 0
                        with _lock:
                            _last_stats = dict(stats)

                    if unknown_streak >= UNKNOWN_ABORT_STREAK:
                        stats["aborted"] = (
                            f"连续 {unknown_streak} 个账号都读不出结果 —— "
                            f"几乎肯定是验收端点不可用，已提前中止（未判任何账号失效）")
                        break

                    if stop.is_set():
                        stats["aborted"] = f"用户中止（已检查 {stats['checked']}/{len(targets)}）"
                        break

                # 取消/中止时，把还没跑的 future 撤掉，别让线程池等它们跑完
                if stats["aborted"]:
                    for f in futures:
                        f.cancel()

            db.session.commit()

            log = OperationLog(
                user_id=user_id,
                action="check_accounts",
                resource_type="account",
                details=(f"巡检（{triggered_by}）：检查 {stats['checked']} 个，"
                         f"可用 {stats['alive']}，失效 {stats['dead']}，"
                         f"未知 {stats['unknown']}，新标失效 {stats['newly_invalid']}，"
                         f"恢复 {stats['recovered']}"
                         + (f"｜{stats['aborted']}" if stats["aborted"] else "")),
            )
            db.session.add(log)
            db.session.commit()

    except Exception as exc:  # noqa: BLE001
        try:
            db.session.rollback()
        except Exception:  # noqa: BLE001
            pass
        stats.update(ok=False, error=f"{type(exc).__name__}: {exc}")
    finally:
        stats["elapsed"] = round(time.time() - started, 1)
        stats["finished_at"] = datetime.utcnow().isoformat()
        with _lock:
            _running = False
            _stop_event = None
            _last_stats = dict(stats)

    return stats


def start_check(app, **kwargs) -> bool:
    """起后台线程跑一轮巡检。返回是否成功启动（已有一轮在跑则返回 False）。"""
    with _lock:
        if _running:
            return False

    threading.Thread(
        target=run_check, args=(app,), kwargs=kwargs,
        name="account-check", daemon=True,
    ).start()
    return True
