"""认证 API"""
from datetime import datetime
from flask import Blueprint, request, jsonify
from flask_jwt_extended import (
    create_access_token,
    create_refresh_token,
    jwt_required,
    get_jwt_identity,
    get_jwt
)
from backend.models import db, User, OperationLog

bp = Blueprint("auth", __name__)


def get_current_user_id():
    """获取当前用户ID（转为整数）"""
    return int(get_jwt_identity())


@bp.route("/login", methods=["POST"])
def login():
    """登录"""
    data = request.get_json()
    username = data.get("username", "").strip()
    password = data.get("password", "")
    
    if not username or not password:
        return jsonify({"error": "用户名和密码不能为空"}), 400
    
    user = User.query.filter_by(username=username).first()
    
    if not user or not user.check_password(password):
        return jsonify({"error": "用户名或密码错误"}), 401
    
    if not user.is_active:
        return jsonify({"error": "账号已被禁用"}), 403
    
    # 更新最后登录时间
    user.last_login = datetime.utcnow()
    db.session.commit()
    
    # 生成 token (identity 必须是字符串)
    access_token = create_access_token(identity=str(user.id))
    refresh_token = create_refresh_token(identity=str(user.id))
    
    # 记录登录日志
    log = OperationLog(
        user_id=user.id,
        action="login",
        ip_address=request.remote_addr
    )
    db.session.add(log)
    db.session.commit()
    
    return jsonify({
        "access_token": access_token,
        "refresh_token": refresh_token,
        "user": user.to_dict()
    })


@bp.route("/refresh", methods=["POST"])
@jwt_required(refresh=True)
def refresh():
    """刷新 token"""
    user_id = get_current_user_id()
    access_token = create_access_token(identity=str(user_id))
    return jsonify({"access_token": access_token})


@bp.route("/me", methods=["GET"])
@jwt_required()
def get_current_user():
    """获取当前用户信息"""
    user_id = get_current_user_id()
    user = User.query.get(user_id)
    
    if not user:
        return jsonify({"error": "用户不存在"}), 404
    
    return jsonify(user.to_dict())


@bp.route("/change-password", methods=["POST"])
@jwt_required()
def change_password():
    """修改密码"""
    user_id = get_current_user_id()
    user = User.query.get(user_id)
    
    if not user:
        return jsonify({"error": "用户不存在"}), 404
    
    data = request.get_json()
    old_password = data.get("old_password", "")
    new_password = data.get("new_password", "")
    
    if not user.check_password(old_password):
        return jsonify({"error": "原密码错误"}), 400
    
    if len(new_password) < 6:
        return jsonify({"error": "新密码长度不能少于6位"}), 400
    
    user.set_password(new_password)
    db.session.commit()
    
    # 记录日志
    log = OperationLog(
        user_id=user.id,
        action="change_password",
        ip_address=request.remote_addr
    )
    db.session.add(log)
    db.session.commit()
    
    return jsonify({"message": "密码修改成功"})


@bp.route("/logout", methods=["POST"])
@jwt_required()
def logout():
    """登出"""
    user_id = get_current_user_id()
    
    # 记录登出日志
    log = OperationLog(
        user_id=user_id,
        action="logout",
        ip_address=request.remote_addr
    )
    db.session.add(log)
    db.session.commit()
    
    return jsonify({"message": "已退出登录"})
