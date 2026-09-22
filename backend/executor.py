"""注册任务执行器：把 `RegisterTask` 真正跑起来。

设计取舍：
- **用后台线程而不是 Celery / RQ**：这个管理端是单机自用工具，引消息队列要多跑
  一个 broker，运维成本换不到实质收益。代价是进程重启会丢正在跑的任务 ——
  所以启动时会把 `running` 状态的残留任务标成 `failed`（见 `recover_stale_tasks`），
  否则它们会永远停在 running，看起来像卡死。
- **邮箱客户端来自数据库配置**，不是 `.env`（见 `mailfactory.build_mail_client`）。
- **进度实时写库**：注册一个账号要十几秒，不落进度的话页面上只能看到
  "running" 然后突然变 completed，中途完全没有反馈。
"""
import threading
import traceback
from datetime import datetime

from backend.mailfactory import MailConfigError, build_mail_client
from backend.models import MailConfig, OperationLog, RegisterTask, db

#: `{task_id: threading.Event}` —— 取消信号。
#: 用 Event 而不是布尔标志：worker 在等邮件时会阻塞，Event 让它能被及时唤醒检查。
_cancel_flags: dict[int, threading.Event] = {}
_flags_lock = threading.Lock()


def request_cancel(task_id: int) -> bool:
    """请求取消任务。返回该任务是否正在运行。

    ⚠️ 只是**打标记**，不强杀线程：当前账号会跑完（可能还要十几秒），
    下一个开始前才检查。强杀会让邮箱建了却没记台账，那笔钱/配额白花。
    """
    with _flags_lock:
        ev = _cancel_flags.get(task_id)
    if ev is None:
        return False
    ev.set()
    return True


def is_running(task_id: int) -> bool:
    with _flags_lock:
        return task_id in _cancel_flags


def recover_stale_tasks(app) -> int:
    """把上次进程退出时残留的 `running` 任务标成 `failed`。

    🔴 必须做：任务跑在进程内线程里，进程一挂线程也没了，但数据库里的状态
    还是 `running` ⇒ 页面上永远显示"运行中"，而且因为 `_cancel_flags` 里没有它，
    连取消都点不动。看起来像"任务卡死"，实际是进程早就重启过了。
    """
    with app.app_context():
        stale = RegisterTask.query.filter_by(status="running").all()
        for t in stale:
            t.status = "failed"
            t.error_message = "服务重启导致任务中断（进程内线程随进程退出）"
            t.completed_at = datetime.utcnow()
        if stale:
            db.session.commit()
        return len(stale)


def _run_task(app, task_id: int):
    """在后台线程里跑一个任务。全程自己管 app_context 与异常。"""
    # 延迟 import：`src.runner` 会拉起整个注册链路的依赖，
    # 放在模块顶层会让 Flask 启动变慢，且 Web 端只有跑批时才需要它。
    from src.runner import Pipeline

    cancel = threading.Event()
    with _flags_lock:
        _cancel_flags[task_id] = cancel

    try:
        with app.app_context():
            task = RegisterTask.query.get(task_id)
            if task is None:
                return

            mc = MailConfig.query.get(task.mail_config_id)

            try:
                mail_client = build_mail_client(mc)
            except MailConfigError as exc:
                task.status = "failed"
                task.error_message = str(exc)
                task.completed_at = datetime.utcnow()
                db.session.commit()
                return

            task.status = "running"
            task.started_at = datetime.utcnow()
            task.progress = 0
            task.success_count = 0
            task.failed_count = 0
            task.error_message = None
            db.session.commit()

            total = task.count
            concurrency = max(1, task.concurrency or 1)
            backend = (mc.backend or "").strip().lower()

            # 🔴 `mail_factory` 必须传：并发时 `Pipeline._clone()` 用它给每个 worker
            #    造**同款**客户端。不传的话 clone 会走默认值新建一个读 .env 的客户端，
            #    表现是"一半账号建在页面配的服务上、一半建在 .env 配的服务上"，且不报错。
            pipeline = Pipeline(
                mail=mail_client,
                mail_factory=lambda: build_mail_client(
                    MailConfig.query.get(task.mail_config_id)
                ),
                backend=backend,
                domain=None,  # 用客户端实例自带的（来自页面配置）
                verbose=False,
            )

            done = success = failed = 0
            last_error = ""

            # 分批跑：每批 concurrency 个，批间写一次进度并检查取消。
            # 不用 run_batch 一次跑完 —— 那样中途没有任何进度反馈，
            # 页面上只能看到 running 然后突然结束。
            while done < total:
                if cancel.is_set():
                    task.status = "cancelled"
                    task.error_message = f"用户取消（已完成 {done}/{total}）"
                    task.completed_at = datetime.utcnow()
                    db.session.commit()
                    return

                batch = min(concurrency, total - done)
                try:
                    recs = pipeline.run_batch(
                        count=batch,
                        name=task.name,
                        concurrency=batch,
                    )
                except Exception as exc:  # noqa: BLE001
                    # 整批炸了（通常是邮箱服务不可用）⇒ 停下来报错，
                    # 不要继续白跑剩下的批次。
                    task.status = "failed"
                    task.error_message = f"{type(exc).__name__}: {exc}"
                    task.completed_at = datetime.utcnow()
                    db.session.commit()
                    app.logger.exception("任务 %s 批次异常", task_id)
                    return

                for rec in recs:
                    if getattr(rec, "status", "") == "keyed":
                        success += 1
                    else:
                        failed += 1
                        # 留一条失败原因。`run_batch` 不抛异常、而是把失败写进
                        # AccountRecord ⇒ 不主动摘出来的话页面上错误信息永远是空的，
                        # 用户只能看到"失败 N 个"却不知道为什么。
                        err = getattr(rec, "error", "") or ""
                        if err:
                            last_error = err

                done += batch

                # 每批写一次进度。`task` 可能已过期（线程里跨了 commit），
                # 重新查一次拿最新实例。
                task = RegisterTask.query.get(task_id)
                if task is None:
                    return
                task.progress = done
                task.success_count = success
                task.failed_count = failed
                db.session.commit()

            # 🔴 终态按**实际结果**判，不能跑完就无条件 completed：
            #    全部失败却显示"已完成"会让人以为成功了，而这正是最需要看见的情况
            #    （实测：邮箱服务不可达时 run_batch 不抛异常，只是每条记录都不是 keyed）。
            if success == 0 and total > 0:
                task.status = "failed"
                task.error_message = (
                    f"全部 {total} 个账号都失败了"
                    + (f"。最后一条错误：{last_error[:300]}" if last_error else "")
                )
            else:
                task.status = "completed"
                if failed:
                    task.error_message = (
                        f"{failed} 个失败"
                        + (f"，最后一条错误：{last_error[:300]}" if last_error else "")
                    )

            task.completed_at = datetime.utcnow()
            db.session.commit()

            log = OperationLog(
                user_id=task.created_by,
                action="task_finished",
                resource_type="task",
                resource_id=task.id,
                details=f"任务「{task.name}」{task.status}：成功 {success} / 失败 {failed}",
            )
            db.session.add(log)
            db.session.commit()

    except Exception:  # noqa: BLE001 - 线程里的异常必须自己兜住
        try:
            with app.app_context():
                t = RegisterTask.query.get(task_id)
                if t is not None and t.status == "running":
                    t.status = "failed"
                    t.error_message = f"执行器异常：{traceback.format_exc(limit=3)[:500]}"
                    t.completed_at = datetime.utcnow()
                    db.session.commit()
        except Exception:  # noqa: BLE001
            pass
    finally:
        with _flags_lock:
            _cancel_flags.pop(task_id, None)


def start_task(app, task_id: int) -> None:
    """起一个后台线程跑任务。立即返回，不阻塞请求。"""
    t = threading.Thread(
        target=_run_task,
        args=(app, task_id),
        name=f"task-{task_id}",
        daemon=True,
    )
    t.start()
