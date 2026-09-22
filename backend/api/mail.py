"""邮箱配置 API"""
from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from backend.models import db, MailConfig, User, OperationLog
# 🔴 从注册器模块复用枚举，不在这里另写一份字面量 ——
#    两处定义迟早漂移，表现是「页面能存进去、跑批时被服务端拒」。
from src.moemail import VALID_EXPIRY_MS
from backend.mailfactory import MailConfigError, check_mail_config

bp = Blueprint("mail", __name__)


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
def list_configs():
    """获取邮箱配置列表"""
    page = request.args.get("page", 1, type=int)
    page_size = request.args.get("page_size", 20, type=int)
    
    pagination = MailConfig.query.order_by(MailConfig.created_at.desc()).paginate(
        page=page, per_page=page_size, error_out=False
    )
    
    return jsonify({
        "items": [c.to_dict() for c in pagination.items],
        "total": pagination.total,
        "page": page,
        "page_size": page_size
    })


@bp.route("/<int:config_id>", methods=["GET"])
@jwt_required()
def get_config(config_id):
    """获取邮箱配置详情"""
    config = MailConfig.query.get_or_404(config_id)
    
    # 管理员可以看到敏感信息
    include_sensitive = check_admin()
    
    return jsonify(config.to_dict(include_sensitive=include_sensitive))


@bp.route("/add", methods=["POST"])
@jwt_required()
def add_config():
    """添加邮箱配置"""
    if not check_admin():
        return jsonify({"error": "权限不足"}), 403
    
    data = request.get_json()
    backend = data.get("backend")
    name = data.get("name", "").strip()
    
    if not name:
        return jsonify({"error": "配置名称不能为空"}), 400
    
    config = MailConfig(backend=backend, name=name)
    
    # CF Worker 配置
    if backend == "cf":
        config.tempmail_base = data.get("tempmail_base", "").strip()
        config.tempmail_admin_key = data.get("tempmail_admin_key", "").strip()
        config.tempmail_domain = data.get("tempmail_domain", "").strip()
        
        if not all([config.tempmail_base, config.tempmail_admin_key, config.tempmail_domain]):
            return jsonify({"error": "CF Worker 配置项不完整"}), 400
    
    # Remail 配置
    elif backend == "remail":
        config.remail_base = data.get("remail_base", "").strip()
        config.remail_api_key = data.get("remail_api_key", "").strip()
        config.remail_project_id = data.get("remail_project_id")
        config.remail_email_suffix = data.get("remail_email_suffix", "").strip()
        config.remail_service_mode = data.get("remail_service_mode", "code")
        
        if not all([config.remail_base, config.remail_api_key]):
            return jsonify({"error": "Remail 配置项不完整"}), 400
    
    # MoeMail 配置
    elif backend == "moemail":
        config.moemail_base = data.get("moemail_base", "").strip().rstrip("/")
        config.moemail_api_key = data.get("moemail_api_key", "").strip()
        # 域名留空是合法的：客户端会从 GET /api/config 取第一个可用域名
        config.moemail_domain = data.get("moemail_domain", "").strip()
        config.moemail_expiry_ms = data.get("moemail_expiry_ms") or 86400000
        config.moemail_poll_interval = float(data.get("moemail_poll_interval") or 3.0)
        
        if config.moemail_poll_interval < 1.0:
            return jsonify({
                "error": "轮询间隔不能小于 1 秒 —— MoeMail 多跑在 Workers 免费额度上，"
                         "太密的请求会触发 Cloudflare Error 1102（Worker 超资源限制），"
                         "届时整个邮箱服务都不可用"
            }), 400
        
        if not all([config.moemail_base, config.moemail_api_key]):
            return jsonify({"error": "MoeMail 配置项不完整（服务地址与 API Key 必填）"}), 400
        
        if config.moemail_expiry_ms not in VALID_EXPIRY_MS:
            return jsonify({
                "error": f"邮箱有效期必须是这几个值之一：{'、'.join(str(v) for v in VALID_EXPIRY_MS)}"
            }), 400
    
    else:
        return jsonify({"error": f"未知邮箱后端 {backend!r}（可选：cf、remail、moemail）"}), 400
    
    db.session.add(config)
    db.session.commit()
    
    # 记录日志
    user_id = get_current_user_id()
    log = OperationLog(
        user_id=user_id,
        action="add_mail_config",
        resource_type="mail_config",
        resource_id=config.id,
        details=f"添加邮箱配置: {name} ({backend})",
        ip_address=request.remote_addr
    )
    db.session.add(log)
    db.session.commit()
    
    return jsonify(config.to_dict(include_sensitive=True)), 201


@bp.route("/<int:config_id>", methods=["PUT"])
@jwt_required()
def update_config(config_id):
    """更新邮箱配置"""
    if not check_admin():
        return jsonify({"error": "权限不足"}), 403
    
    config = MailConfig.query.get_or_404(config_id)
    data = request.get_json()
    
    config.name = data.get("name", config.name).strip()
    
    if config.backend == "cf":
        config.tempmail_base = data.get("tempmail_base", config.tempmail_base).strip()
        config.tempmail_admin_key = data.get("tempmail_admin_key", config.tempmail_admin_key).strip()
        config.tempmail_domain = data.get("tempmail_domain", config.tempmail_domain).strip()
    elif config.backend == "remail":
        config.remail_base = data.get("remail_base", config.remail_base).strip()
        config.remail_api_key = data.get("remail_api_key", config.remail_api_key).strip()
        config.remail_project_id = data.get("remail_project_id", config.remail_project_id)
        config.remail_email_suffix = data.get("remail_email_suffix", config.remail_email_suffix).strip()
        config.remail_service_mode = data.get("remail_service_mode", config.remail_service_mode)
    elif config.backend == "moemail":
        config.moemail_base = (data.get("moemail_base") or config.moemail_base or "").strip().rstrip("/")
        config.moemail_api_key = (data.get("moemail_api_key") or config.moemail_api_key or "").strip()
        config.moemail_domain = (data.get("moemail_domain") if "moemail_domain" in data
                                 else config.moemail_domain or "").strip()
        expiry = data.get("moemail_expiry_ms", config.moemail_expiry_ms) or 86400000
        
        if expiry not in VALID_EXPIRY_MS:
            return jsonify({
                "error": f"邮箱有效期必须是这几个值之一：{'、'.join(str(v) for v in VALID_EXPIRY_MS)}"
            }), 400
        config.moemail_expiry_ms = expiry
        
        interval = float(data.get("moemail_poll_interval") or config.moemail_poll_interval or 3.0)
        if interval < 1.0:
            return jsonify({"error": "轮询间隔不能小于 1 秒（会触发 Cloudflare Error 1102）"}), 400
        config.moemail_poll_interval = interval
    
    db.session.commit()
    
    # 记录日志
    user_id = get_current_user_id()
    log = OperationLog(
        user_id=user_id,
        action="update_mail_config",
        resource_type="mail_config",
        resource_id=config.id,
        details=f"更新邮箱配置: {config.name}",
        ip_address=request.remote_addr
    )
    db.session.add(log)
    db.session.commit()
    
    return jsonify(config.to_dict(include_sensitive=True))


@bp.route("/<int:config_id>/test", methods=["POST"])
@jwt_required()
def test_config(config_id):
    """测试邮箱配置的连通性。
    
    只做只读检查（各后端的 `health()` 都刻意不建邮箱 / 不下单）——
    体检不该悄悄花钱，也不该在账号池里留垃圾邮箱。
    """
    if not check_admin():
        return jsonify({"error": "权限不足"}), 403
    
    config = MailConfig.query.get_or_404(config_id)
    
    try:
        result = check_mail_config(config)
    except MailConfigError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    
    return jsonify(result)


@bp.route("/<int:config_id>/toggle", methods=["POST"])
@jwt_required()
def toggle_config(config_id):
    """启用/禁用配置"""
    if not check_admin():
        return jsonify({"error": "权限不足"}), 403
    
    config = MailConfig.query.get_or_404(config_id)
    config.is_active = not config.is_active
    db.session.commit()
    
    # 记录日志
    user_id = get_current_user_id()
    log = OperationLog(
        user_id=user_id,
        action="toggle_mail_config",
        resource_type="mail_config",
        resource_id=config.id,
        details=f"{'启用' if config.is_active else '禁用'}邮箱配置: {config.name}",
        ip_address=request.remote_addr
    )
    db.session.add(log)
    db.session.commit()
    
    return jsonify({"is_active": config.is_active})


@bp.route("/<int:config_id>", methods=["DELETE"])
@jwt_required()
def delete_config(config_id):
    """删除配置"""
    if not check_admin():
        return jsonify({"error": "权限不足"}), 403
    
    config = MailConfig.query.get_or_404(config_id)
    
    # 检查是否有关联任务
    if config.tasks:
        return jsonify({"error": "该配置有关联任务，无法删除"}), 400
    
    name = config.name
    db.session.delete(config)
    db.session.commit()
    
    # 记录日志
    user_id = get_current_user_id()
    log = OperationLog(
        user_id=user_id,
        action="delete_mail_config",
        resource_type="mail_config",
        resource_id=config_id,
        details=f"删除邮箱配置: {name}",
        ip_address=request.remote_addr
    )
    db.session.add(log)
    db.session.commit()
    
    return jsonify({"message": "删除成功"})
