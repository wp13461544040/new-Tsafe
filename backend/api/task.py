"""注册任务 API"""
from backend.timeutil import now

from flask import Blueprint, request, jsonify, current_app
from flask_jwt_extended import jwt_required, get_jwt_identity
from sqlalchemy import func

from backend.executor import is_running as is_task_running, request_cancel, start_task
from backend.tasklog import (
    drop as drop_task_log,
    has as has_task_log,
    read as read_task_log,
)
from backend.mailfactory import MailConfigError, build_mail_client
from backend.models import db, RegisterTask, MailConfig, User, OperationLog
from src import config as site_config

bp = Blueprint("task", __name__)


def get_current_user_id():
    """获取当前用户ID（转为整数）"""
    return int(get_jwt_identity())


def check_admin():
    """检查管理员权限"""
    user_id = get_current_user_id()
    user = User.query.get(user_id)
    return user and user.role == "admin"


@bp.route("/list", methods=["GET"])
@jwt_required()
def list_tasks():
    """获取任务列表"""
    page = request.args.get("page", 1, type=int)
    page_size = request.args.get("page_size", 20, type=int)
    status = request.args.get("status")
    
    query = RegisterTask.query
    
    if status:
        query = query.filter_by(status=status)
    
    pagination = query.order_by(RegisterTask.created_at.desc()).paginate(
        page=page, per_page=page_size, error_out=False
    )
    
    return jsonify({
        "items": [t.to_dict() for t in pagination.items],
        "total": pagination.total,
        "page": page,
        "page_size": page_size
    })


@bp.route("/<int:task_id>", methods=["GET"])
@jwt_required()
def get_task(task_id):
    """获取任务详情"""
    task = RegisterTask.query.get_or_404(task_id)
    return jsonify(task.to_dict())


@bp.route("/<int:task_id>/logs", methods=["GET"])
@jwt_required()
def get_task_logs(task_id):
    """任务执行日志（增量轮询）。

    协议：前端带上次拿到的最大 `after=<seq>`，这里只回 `seq > after` 的行。
    全量重传会让轮询响应随任务变长而线性膨胀（跑 500 个账号时每 1.5 秒传几十 KB）。

    两个数据源，优先内存：
      · `live=true` —— 来自进程内缓冲，任务正在跑/刚跑完，支持增量。
      · `live=false` —— 缓冲没了（进程重启过、或历史任务被淘汰），
        回退到数据库里的整块快照 `snapshot`，只在 `after=0` 时给一次。
    """
    task = RegisterTask.query.get_or_404(task_id)
    after = request.args.get("after", 0, type=int)

    base = {
        "task_id": task.id,
        "status": task.status,
        "progress": task.progress,
        "count": task.count,
        "success_count": task.success_count,
        "failed_count": task.failed_count,
        "error_message": task.error_message,
        # 线程还在不在。与 status 分开给：status 已是终态但线程仍在收尾时
        # 前端应该继续轮询（否则会漏掉最后几行）。
        "running": is_task_running(task.id),
    }

    lines, latest = read_task_log(task.id, after)
    if latest or has_task_log(task.id):
        return jsonify({
            **base,
            "live": True,
            "lines": lines,
            "next": max(latest, after),
            # 前端据此提示"更早的日志已被截断"（环形缓冲挤掉了开头）
            "truncated": bool(lines and after and lines[0]["seq"] > after + 1),
        })

    return jsonify({
        **base,
        "live": False,
        "lines": [],
        "next": 0,
        "truncated": False,
        "snapshot": (task.log_text or "") if after == 0 else None,
    })


@bp.route("/create", methods=["POST"])
@jwt_required()
def create_task():
    """创建注册任务"""
    if not check_admin():
        return jsonify({"error": "权限不足"}), 403
    
    data = request.get_json() or {}
    name = str(data.get("name") or "").strip()
    mail_config_id = data.get("mail_config_id")
    
    if not name:
        return jsonify({"error": "任务名称不能为空"}), 400
    
    # 🔴 必须显式转 int：HTML 的 <input type="number"> 交上来的是**字符串**，
    #    直接 `count <= 0` 会抛 TypeError 变成 500「Internal server error」，
    #    用户完全看不出是哪个字段不对（2026-09-22 实际踩到）。
    # ⚠️ 用 `is None` 取默认值，**不能**写 `data.get("concurrency") or 2`：
    #    0 是 falsy，`or` 会把用户显式传的 0 静默换成 2 —— 校验形同虚设，
    #    而且不报错（实测 concurrency=0 直接建成了并发 2 的任务）。
    raw_count = data.get("count")
    raw_conc = data.get("concurrency")
    try:
        count = int(0 if raw_count is None else raw_count)
        concurrency = int(2 if raw_conc is None else raw_conc)
    except (TypeError, ValueError):
        return jsonify({"error": "数量与并发数必须是整数"}), 400
    
    if count <= 0 or count > 1000:
        return jsonify({"error": "数量必须在 1-1000 之间"}), 400
    
    if concurrency <= 0 or concurrency > 20:
        return jsonify({"error": "并发数必须在 1-20 之间"}), 400
    
    if not mail_config_id:
        return jsonify({"error": "请选择邮箱配置"}), 400
    
    # 检查邮箱配置是否存在
    mail_config = MailConfig.query.get(mail_config_id)
    if not mail_config:
        return jsonify({"error": "邮箱配置不存在"}), 404
    
    if not mail_config.is_active:
        return jsonify({"error": "该邮箱配置已被禁用"}), 400
    
    user_id = get_current_user_id()
    
    task = RegisterTask(
        name=name,
        count=count,
        concurrency=concurrency,
        mail_config_id=mail_config_id,
        created_by=user_id
    )
    
    db.session.add(task)
    db.session.commit()
    
    # 记录日志
    log = OperationLog(
        user_id=user_id,
        action="create_task",
        resource_type="task",
        resource_id=task.id,
        details=f"创建注册任务: {name} (数量: {count})",
        ip_address=request.remote_addr
    )
    db.session.add(log)
    db.session.commit()
    
    # 🔴 两项配置都在**起线程之前**校验：不完整就别浪费一次线程启动，
    #    更重要的是错误能当场回给用户，而不是让任务静默变成 failed
    #    然后用户去列表里翻 error_message。
    from backend.api.system import load_site_config_from_db
    load_site_config_from_db()

    if missing_site := site_config.validate_site():
        msg = (f"站点配置不完整，缺少：{'、'.join(missing_site)}。"
               f"请到「系统设置 → 站点配置」补全")
        task.status = "failed"
        task.error_message = msg
        db.session.commit()
        return jsonify({"error": msg}), 400

    try:
        build_mail_client(mail_config)
    except MailConfigError as exc:
        task.status = "failed"
        task.error_message = str(exc)
        db.session.commit()
        return jsonify({"error": str(exc)}), 400
    
    # 起后台线程真正执行。用 `current_app._get_current_object()` 取真实 app 实例 ——
    # 直接传 `current_app` 代理对象到线程里会脱离请求上下文而失效。
    start_task(current_app._get_current_object(), task.id)
    
    return jsonify(task.to_dict()), 201


@bp.route("/<int:task_id>", methods=["DELETE"])
@jwt_required()
def delete_task(task_id):
    """删除任务记录。

    🔴 运行中/待执行的**一律拒绝**，不做"先取消再删"：取消只是打标记，
    执行线程还要跑完当前账号（十几秒）才停。这期间记录被删掉的话，
    线程回写进度时 `RegisterTask.query.get()` 拿到 None ⇒ 任务静默结束、
    已注册的账号却没人回写统计。要删就先取消，等它真的停下来。

    ⚠️ 只删任务记录本身。跑出来的账号在账号池里独立存在（不是外键关联），
    删任务不会动它们 —— 否则"清理任务列表"会连带毁掉交付物。
    """
    if not check_admin():
        return jsonify({"error": "权限不足"}), 403

    task = RegisterTask.query.get_or_404(task_id)

    if task.status in ("pending", "running") or is_task_running(task_id):
        return jsonify({
            "error": "运行中或待执行的任务不能删除，请先取消并等待其停止"
        }), 400

    name = task.name
    db.session.delete(task)
    db.session.commit()

    # 内存里的日志缓冲一起清掉，否则同 id 被复用时会看到上一个任务的日志
    drop_task_log(task_id)

    log = OperationLog(
        user_id=get_current_user_id(),
        action="delete_task",
        resource_type="task",
        resource_id=task_id,
        details=f"删除任务: {name}",
        ip_address=request.remote_addr,
    )
    db.session.add(log)
    db.session.commit()

    return jsonify({"message": "删除成功"})


@bp.route("/batch-delete", methods=["POST"])
@jwt_required()
def batch_delete_tasks():
    """批量删除任务。跳过运行中/待执行的，返回实际删除数与跳过数。

    ⚠️ 逐条 `session.delete()` 而不是 `query.delete()`：后者绕过 ORM，
    没法顺手清理内存日志缓冲，也拿不到被跳过的条目。任务量级是几百，不值得为此优化。
    """
    if not check_admin():
        return jsonify({"error": "权限不足"}), 403

    data = request.get_json() or {}
    ids = data.get("ids") or []

    if not ids:
        return jsonify({"error": "请选择要删除的任务"}), 400

    tasks = RegisterTask.query.filter(RegisterTask.id.in_(ids)).all()

    deleted, skipped = 0, 0
    for t in tasks:
        if t.status in ("pending", "running") or is_task_running(t.id):
            skipped += 1
            continue
        tid = t.id
        db.session.delete(t)
        deleted += 1
        drop_task_log(tid)

    db.session.commit()

    if deleted:
        db.session.add(OperationLog(
            user_id=get_current_user_id(),
            action="batch_delete_task",
            resource_type="task",
            details=f"批量删除任务 {deleted} 个" + (f"，跳过运行中 {skipped} 个" if skipped else ""),
            ip_address=request.remote_addr,
        ))
        db.session.commit()

    msg = f"已删除 {deleted} 个任务"
    if skipped:
        msg += f"，{skipped} 个运行中/待执行的已跳过"

    return jsonify({"message": msg, "deleted": deleted, "skipped": skipped})


@bp.route("/<int:task_id>/cancel", methods=["POST"])
@jwt_required()
def cancel_task(task_id):
    """取消任务"""
    if not check_admin():
        return jsonify({"error": "权限不足"}), 403
    
    task = RegisterTask.query.get_or_404(task_id)
    
    if task.status not in ["pending", "running"]:
        return jsonify({"error": "只能取消待执行或运行中的任务"}), 400
    
    # 通知执行线程停下。⚠️ 只是打标记：当前正在注册的账号会跑完
    # （可能还要十几秒），下一批开始前才生效 —— 强杀线程会让邮箱建了
    # 却没记台账，那笔配额/钱白花。
    running = request_cancel(task_id)
    
    if running:
        # 状态由执行线程自己改成 cancelled（它知道跑到第几个了）。
        # 这里不抢着改，否则两边会互相覆盖。
        message = "已发送取消信号，当前账号完成后停止"
    else:
        # 线程不在（pending 还没起、或进程重启过）⇒ 这里直接落状态
        task.status = "cancelled"
        task.error_message = "任务已被取消"
        task.completed_at = now()
        db.session.commit()
        message = "任务已取消"
    
    # 记录日志
    user_id = get_current_user_id()
    log = OperationLog(
        user_id=user_id,
        action="cancel_task",
        resource_type="task",
        resource_id=task.id,
        details=f"取消任务: {task.name}",
        ip_address=request.remote_addr
    )
    db.session.add(log)
    db.session.commit()
    
    return jsonify({**task.to_dict(), "message": message})
