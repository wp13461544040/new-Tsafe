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

#: 配置在 SystemConfig 里的键前缀。
CONFIG_PREFIX = "check."

#: 配置项定义：键 → (默认值, 类型, 说明)。
#: 前端表单、API 校验、调度器读取都从这里派生 —— 加配置项只改这一处。
CONFIG_SCHEMA: dict[str, tuple] = {
    # 🔴 默认**关闭**：巡检会对验收端点发真实请求，几千个账号就是几千次调用。
    #    默认开启等于用户什么都没做就开始消耗配额，必须让他显式打开。
    "enabled": (False, bool, "是否启用定时巡检"),
    "interval_hours": (6.0, float, "每隔多少小时跑一轮"),
    "limit": (200, int, "单轮最多检查多少个账号"),
    "concurrency": (5, int, "并发探测数"),
    "dead_threshold": (2, int, "连续几次判定失效才标记为 invalid"),
    "include_assigned": (False, bool, "是否也检查已被卡密提取的账号"),
    # 🔴 默认 probe（零额度）。inference 每次约消耗 317 tokens，
    #    1600 账号 × 每 6 小时一轮 ≈ 6500 次推理/天，纯属白烧额度。
    "mode": ("probe", str, "probe=零额度只验认证 / inference=真实推理最准但消耗额度"),
    "timeout": (30.0, float, "单次探测超时（秒）"),
    # 跳过最近这么多小时内检查过的。默认 0 = 跟随 interval_hours
    # （避免"间隔 6 小时但每轮都把同一批又检查一遍"）。
    "min_interval_hours": (0.0, float, "跳过最近N小时内已检查的，0=跟随巡检间隔"),
}

#: 调度器轮询间隔（秒）。
#: 🔴 不直接 `wait(interval_hours * 3600)`：那样用户把间隔从 24 小时改成 1 小时，
#:    得等满 24 小时才生效。改为每分钟醒一次重算，配置变更最迟 60 秒生效。
SCHED_TICK = 60.0

#: 上次巡检**完成**时间存在 SystemConfig 里（不是内存）。
#: 🔴 存盘的理由：进程重启后如果从内存的"从未跑过"开始算，就会立刻触发一轮 ——
#:    频繁重启（改配置、更新镜像）会变成反复打验收端点。
LAST_RUN_KEY = CONFIG_PREFIX + "last_run_at"

#: 枚举型配置的合法取值。
#: 🔴 必须白名单：`mode` 拼错成 "infrence" 时，`str()` 会老实存进去，
#:    keycheck 那边 `mode != "inference"` 就静默走 probe ——
#:    用户以为开了真实推理校验，其实没有，而且**完全没有报错**。
ENUM_CHOICES: dict[str, tuple[str, ...]] = {
    "mode": ("probe", "inference"),
}

_sched_thread: threading.Thread | None = None
_sched_stop: threading.Event | None = None


def load_config() -> dict:
    """从 SystemConfig 读巡检配置，缺失项用默认值补齐。"""
    from backend.models import SystemConfig

    out = {}
    for key, (default, typ, _desc) in CONFIG_SCHEMA.items():
        raw = SystemConfig.get_value(CONFIG_PREFIX + key, None)
        if raw is None or raw == "":
            out[key] = default
            continue
        try:
            if typ is bool:
                out[key] = str(raw).strip().lower() in ("1", "true", "yes", "on")
            else:
                out[key] = typ(raw)
        except (TypeError, ValueError):
            # 脏数据不该让调度器崩 —— 退回默认值继续跑
            out[key] = default

        choices = ENUM_CHOICES.get(key)
        if choices and out[key] not in choices:
            out[key] = default
    return out


def save_config(values: dict) -> dict:
    """写巡检配置。只接受 schema 里的键，返回落库后的完整配置。

    Raises:
        ValueError: 枚举型配置传了非法值。这里**故意抛**而不是静默退回默认 ——
            用户在页面上选了 inference 却因拼写落回 probe，他不会知道，
            结果是"以为在做严格校验，其实一直是宽松模式"。
    """
    from backend.models import SystemConfig

    for key, (_default, typ, desc) in CONFIG_SCHEMA.items():
        if key not in values:
            continue
        v = values[key]
        if typ is bool:
            v = "1" if (v is True or str(v).strip().lower() in ("1", "true", "yes", "on")) else "0"
        else:
            v = str(typ(v))

        choices = ENUM_CHOICES.get(key)
        if choices and v not in choices:
            raise ValueError(f"{key} 只能是 {' / '.join(choices)}，收到 {v!r}")

        SystemConfig.set_value(CONFIG_PREFIX + key, v, desc)

    return load_config()


def _get_last_run() -> datetime | None:
    from backend.models import SystemConfig

    raw = SystemConfig.get_value(LAST_RUN_KEY, "")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _set_last_run(when: datetime) -> None:
    from backend.models import SystemConfig

    SystemConfig.set_value(LAST_RUN_KEY, when.isoformat(), "上次巡检完成时间")


def next_run_at(cfg: dict | None = None) -> datetime | None:
    """下次预计巡检时间。未启用时返回 None。"""
    cfg = cfg or load_config()
    if not cfg["enabled"]:
        return None
    last = _get_last_run()
    if last is None:
        return datetime.utcnow()
    return last + timedelta(hours=max(0.1, cfg["interval_hours"]))


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
              mode: str = "probe",
              triggered_by: str = "manual", user_id: int | None = None) -> dict:
    """跑一轮巡检（**同步**，调用方负责决定是否放到线程里）。

    Args:
        limit: 本轮最多检查多少个
        concurrency: 并发探测数
        dead_threshold: 连续几次 dead 才标失效
        include_assigned: 是否也检查已被卡密提取的账号
        min_interval_hours: 跳过最近这么多小时内检查过的（0=不跳过）
        timeout: 单次探测超时
        mode: probe（零额度，默认）/ inference（真实推理，消耗 token 额度）
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
        "aborted": "", "triggered_by": triggered_by, "mode": mode,
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
                    pool.submit(keycheck.check_key, key, api_url=api_url,
                                mode=mode, timeout=timeout): aid
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


# ── 定时调度 ──────────────────────────────────────────────────────────
def _scheduler_loop(app, stop: threading.Event) -> None:
    """调度主循环。

    每 `SCHED_TICK` 秒醒一次，做三件事：读配置、判断是否该跑、跑。
    这样配置改动最迟 60 秒生效，不需要重启服务。
    """
    # 启动后先等一个 tick：给应用初始化留时间，也避免"容器刚起来就打端点"。
    if stop.wait(SCHED_TICK):
        return

    while not stop.is_set():
        try:
            with app.app_context():
                cfg = load_config()

                if not cfg["enabled"]:
                    # 未启用时仍然保持线程存活并轮询 —— 用户在页面上打开开关后
                    # 最迟 60 秒就会生效，不需要重启服务。
                    stop.wait(SCHED_TICK)
                    continue

                last = _get_last_run()
                interval = max(0.1, cfg["interval_hours"])
                due = last is None or (datetime.utcnow() - last) >= timedelta(hours=interval)

                if not due:
                    stop.wait(SCHED_TICK)
                    continue

                if is_checking():
                    # 手动巡检正在跑 ⇒ 这轮跳过，等下一个 tick。
                    # 不排队等待：巡检本身可能跑很久，攒着只会让后面连着跑好几轮。
                    stop.wait(SCHED_TICK)
                    continue

                # min_interval 缺省跟随巡检间隔：避免每轮都把同一批账号又检查一遍
                min_gap = cfg["min_interval_hours"] or interval

                stats = run_check(
                    app,
                    limit=cfg["limit"],
                    concurrency=cfg["concurrency"],
                    dead_threshold=cfg["dead_threshold"],
                    include_assigned=cfg["include_assigned"],
                    min_interval_hours=min_gap,
                    timeout=cfg["timeout"],
                    mode=cfg["mode"],
                    triggered_by="schedule",
                )

                # 🔴 无论成功与否都记完成时间：失败了也别马上重试，
                #    否则端点挂着时会每分钟打一次。
                with app.app_context():
                    _set_last_run(datetime.utcnow())

                if stats.get("checked"):
                    app.logger.info(
                        "定时巡检完成：检查 %s，可用 %s，失效 %s，未知 %s，新标失效 %s",
                        stats["checked"], stats["alive"], stats["dead"],
                        stats["unknown"], stats["newly_invalid"])

        except Exception:  # noqa: BLE001
            # 调度线程**绝不能**因为一次异常退出 —— 那样定时巡检会静默停止，
            # 而页面上开关还是"已启用"，没人会发现。
            try:
                app.logger.exception("巡检调度异常（已忽略，继续下一轮）")
            except Exception:  # noqa: BLE001
                pass
            stop.wait(SCHED_TICK)


def start_scheduler(app) -> bool:
    """启动定时巡检调度线程。返回是否新启动了线程。

    幂等：重复调用不会起第二个线程（两个调度器会让同一批账号被检查两次）。
    """
    global _sched_thread, _sched_stop

    with _lock:
        if _sched_thread is not None and _sched_thread.is_alive():
            return False

        _sched_stop = threading.Event()
        _sched_thread = threading.Thread(
            target=_scheduler_loop, args=(app, _sched_stop),
            name="account-check-scheduler", daemon=True,
        )
        _sched_thread.start()
        return True


def stop_scheduler() -> None:
    """停调度线程（测试用；生产靠 daemon=True 随进程退出）。"""
    global _sched_thread, _sched_stop

    with _lock:
        if _sched_stop is not None:
            _sched_stop.set()
        thread, _sched_thread, _sched_stop = _sched_thread, None, None

    if thread is not None:
        thread.join(timeout=5)


def scheduler_alive() -> bool:
    with _lock:
        return _sched_thread is not None and _sched_thread.is_alive()
