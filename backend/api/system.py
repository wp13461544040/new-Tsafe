"""系统管理 API"""
from datetime import datetime, timedelta
from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from sqlalchemy import func
from backend.models import db, User, CardKey, RegisterTask, MailConfig, OperationLog, SystemConfig

bp = Blueprint("system", __name__)


def get_current_user_id():
    """获取当前用户ID（转为整数）"""
    return int(get_jwt_identity())


@bp.route("/users", methods=["GET"])
@jwt_required()
def get_users():
    """获取用户列表"""
    user_id = get_current_user_id()
    current_user = User.query.get(user_id)
    
    if not current_user or current_user.role != "admin":
        return jsonify({"error": "无权限"}), 403
    
    users = User.query.all()
    return jsonify([user.to_dict() for user in users])


@bp.route("/users", methods=["POST"])
@jwt_required()
def create_user():
    """创建用户"""
    user_id = get_current_user_id()
    current_user = User.query.get(user_id)
    
    if not current_user or current_user.role != "admin":
        return jsonify({"error": "无权限"}), 403
    
    data = request.get_json()
    username = data.get("username", "").strip()
    password = data.get("password", "")
    role = data.get("role", "user")
    
    if not username or not password:
        return jsonify({"error": "用户名和密码不能为空"}), 400
    
    if User.query.filter_by(username=username).first():
        return jsonify({"error": "用户名已存在"}), 400
    
    user = User(username=username, role=role)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    
    # 记录日志
    log = OperationLog(
        user_id=user_id,
        action="create_user",
        details=f"创建用户: {username}",
        ip_address=request.remote_addr
    )
    db.session.add(log)
    db.session.commit()
    
    return jsonify(user.to_dict()), 201


@bp.route("/users/<int:uid>", methods=["PUT"])
@jwt_required()
def update_user(uid):
    """更新用户"""
    user_id = get_current_user_id()
    current_user = User.query.get(user_id)
    
    if not current_user or current_user.role != "admin":
        return jsonify({"error": "无权限"}), 403
    
    user = User.query.get(uid)
    if not user:
        return jsonify({"error": "用户不存在"}), 404
    
    data = request.get_json()
    
    if "password" in data and data["password"]:
        user.set_password(data["password"])
    
    if "role" in data:
        user.role = data["role"]
    
    if "is_active" in data:
        user.is_active = data["is_active"]
    
    db.session.commit()
    
    # 记录日志
    log = OperationLog(
        user_id=user_id,
        action="update_user",
        details=f"更新用户: {user.username}",
        ip_address=request.remote_addr
    )
    db.session.add(log)
    db.session.commit()
    
    return jsonify(user.to_dict())


@bp.route("/users/<int:uid>", methods=["DELETE"])
@jwt_required()
def delete_user(uid):
    """删除用户"""
    user_id = get_current_user_id()
    current_user = User.query.get(user_id)
    
    if not current_user or current_user.role != "admin":
        return jsonify({"error": "无权限"}), 403
    
    if uid == user_id:
        return jsonify({"error": "不能删除自己"}), 400
    
    user = User.query.get(uid)
    if not user:
        return jsonify({"error": "用户不存在"}), 404
    
    username = user.username
    db.session.delete(user)
    db.session.commit()
    
    # 记录日志
    log = OperationLog(
        user_id=user_id,
        action="delete_user",
        details=f"删除用户: {username}",
        ip_address=request.remote_addr
    )
    db.session.add(log)
    db.session.commit()
    
    return jsonify({"message": "用户已删除"})


@bp.route("/logs", methods=["GET"])
@jwt_required()
def get_logs():
    """获取操作日志"""
    user_id = get_current_user_id()
    current_user = User.query.get(user_id)
    
    if not current_user or current_user.role != "admin":
        return jsonify({"error": "无权限"}), 403
    
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 20, type=int)
    action = request.args.get("action")
    
    query = OperationLog.query
    
    if action:
        query = query.filter_by(action=action)
    
    pagination = query.order_by(OperationLog.created_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )
    
    return jsonify({
        "logs": [log.to_dict() for log in pagination.items],
        "total": pagination.total,
        "page": page,
        "per_page": per_page,
        "pages": pagination.pages
    })


@bp.route("/settings", methods=["GET"])
@jwt_required()
def get_settings():
    """获取系统设置"""
    user_id = get_current_user_id()
    current_user = User.query.get(user_id)
    
    if not current_user or current_user.role != "admin":
        return jsonify({"error": "无权限"}), 403
    
    # 返回一些系统统计信息
    stats = {
        "total_users": User.query.count(),
        "total_cards": CardKey.query.count(),
        "total_tasks": RegisterTask.query.count(),
        "total_mail_configs": MailConfig.query.count(),
        "active_tasks": RegisterTask.query.filter_by(status="running").count()
    }
    
    return jsonify(stats)


@bp.route("/cleanup", methods=["POST"])
@jwt_required()
def cleanup():
    """清理过期数据"""
    user_id = get_current_user_id()
    current_user = User.query.get(user_id)
    
    if not current_user or current_user.role != "admin":
        return jsonify({"error": "无权限"}), 403
    
    data = request.get_json()
    days = data.get("days", 30)
    
    # 删除指定天数前的操作日志
    cutoff_date = datetime.utcnow() - timedelta(days=days)
    deleted_logs = OperationLog.query.filter(
        OperationLog.created_at < cutoff_date
    ).delete()
    
    db.session.commit()
    
    # 记录日志
    log = OperationLog(
        user_id=user_id,
        action="cleanup",
        details=f"清理 {days} 天前的数据，删除 {deleted_logs} 条日志",
        ip_address=request.remote_addr
    )
    db.session.add(log)
    db.session.commit()
    
    return jsonify({
        "message": f"已清理 {deleted_logs} 条日志",
        "deleted_logs": deleted_logs
    })


@bp.route("/config", methods=["GET"])
def get_config():
    """获取系统配置（公开接口，无需认证）"""
    key = request.args.get("key")
    
    if key:
        # 获取单个配置
        value = SystemConfig.get_value(key, "")
        return jsonify({"key": key, "value": value})
    else:
        # 获取所有配置
        configs = SystemConfig.query.all()
        return jsonify([config.to_dict() for config in configs])


@bp.route("/config", methods=["POST", "PUT"])
@jwt_required()
def set_config():
    """设置系统配置"""
    user_id = get_current_user_id()
    current_user = User.query.get(user_id)
    
    if not current_user or current_user.role != "admin":
        return jsonify({"error": "无权限"}), 403
    
    data = request.get_json()
    key = data.get("key")
    value = data.get("value")
    description = data.get("description")
    
    if not key:
        return jsonify({"error": "key 不能为空"}), 400
    
    SystemConfig.set_value(key, value, description)
    
    # 记录日志
    log = OperationLog(
        user_id=user_id,
        action="update_config",
        details=f"更新配置: {key}",
        ip_address=request.remote_addr
    )
    db.session.add(log)
    db.session.commit()
    
    return jsonify({"message": "配置已更新", "key": key, "value": value})
