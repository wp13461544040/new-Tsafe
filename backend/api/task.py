"""注册任务 API"""
from datetime import datetime

from flask import Blueprint, request, jsonify, current_app
from flask_jwt_extended import jwt_required, get_jwt_identity
from sqlalchemy import func

from backend.executor import request_cancel, start_task
from backend.mailfactory import MailConfigError, build_mail_client
from backend.models import db, RegisterTask, MailConfig, User, OperationLog

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
    
    # 🔴 邮箱配置在**起线程之前**先校验一遍：配置不完整就别浪费一次线程启动，
    #    更重要的是错误能当场回给用户，而不是让任务静默变成 failed
    #    然后用户去列表里翻 error_message。
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
        task.completed_at = datetime.utcnow()
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
