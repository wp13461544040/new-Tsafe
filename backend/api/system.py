"""系统管理 API"""
import os
from datetime import timedelta
from backend.timeutil import now
from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from sqlalchemy import func
from backend.models import db, User, CardKey, RegisterTask, MailConfig, OperationLog, SystemConfig
# 注册器侧的配置模块 —— 站点配置的**真源**是它的模块级变量（跑批时读的就是那些）。
# 这里用别名，避免与 `backend.config` 混淆。
from src import config as site_config

bp = Blueprint("system", __name__)


def get_current_user_id():
    """获取当前用户ID（转为整数）"""
    return int(get_jwt_identity())


def check_admin():
    """当前用户是否管理员。

    本文件里这段判断原本逐个端点内联了 8 次 —— 抽成函数是为了新增端点时
    不会漏掉（漏掉就是一个无鉴权的管理接口，而且不会有任何报错）。
    """
    user = User.query.get(get_current_user_id())
    return user is not None and user.role == "admin"


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
    cutoff_date = now() - timedelta(days=days)
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


#: 站点配置的字段说明（顺序即页面展示顺序）。
#: 字段名清单由 `src.config.SITE_CONFIG_KEYS` 提供，这里只补 UI 用的元信息。
SITE_FIELD_META = {
    "SITE_ORIGIN": {
        "label": "控制台地址",
        "placeholder": "https://console.example.com",
        "hint": "目标站点的根地址，不要带尾部斜杠（保存时会自动去掉）",
        "required": True,
    },
    "STYTCH_LOGIN_HOST": {
        "label": "登录服务地址",
        "placeholder": "https://login.example.com",
        "hint": "魔法链接的落地域。填错会导致链接提取不到，表现成「等不到邮件」",
        "required": True,
    },
    "SENDER_DOMAIN": {
        "label": "验证邮件发件域",
        "placeholder": "example.com",
        "hint": "信封发件人以它结尾的邮件才会被收信规则匹配。填错同样表现为等不到信",
        "required": True,
    },
    "VERIFY_API_URL": {
        "label": "Key 验收端点",
        "placeholder": "https://api.example.com/v1/xxx",
        "hint": "仅 tools/verify_keys.py 用，不影响注册跑批",
        "required": False,
    },
}


@bp.route("/site-config", methods=["GET"])
@jwt_required()
def get_site_config():
    """读站点配置。

    返回三层信息，让页面能说清"这个值从哪来"：
      · value    数据库里存的（页面填的）
      · env      .env 里的（命令行入口用的）
      · active   当前进程实际生效的
    """
    if not check_admin():
        return jsonify({"error": "无权限"}), 403

    active = site_config.current_site_config()
    fields = []

    for key in site_config.SITE_CONFIG_KEYS:
        meta = SITE_FIELD_META.get(key, {})
        fields.append({
            "key": key,
            "value": SystemConfig.get_value(f"site.{key}", "") or "",
            # 进程启动时从 .env 读到的原始值 —— 页面留空时用的就是它
            "env": os.getenv(key, ""),
            "active": active.get(key, ""),
            "label": meta.get("label", key),
            "placeholder": meta.get("placeholder", ""),
            "hint": meta.get("hint", ""),
            "required": meta.get("required", False),
        })

    return jsonify({"fields": fields})


@bp.route("/site-config", methods=["POST", "PUT"])
@jwt_required()
def set_site_config():
    """写站点配置并**立即注入当前进程**。

    🔴 写库之后必须调 `apply_site_config()`：跑批用的是 `src.config` 的模块级
    变量，只写库不注入的表现是"页面上改了、列表里也显示新值，但跑批还是旧域名"
    —— 而且不报错。
    """
    if not check_admin():
        return jsonify({"error": "无权限"}), 403

    data = request.get_json() or {}
    user_id = get_current_user_id()

    updated = {}
    for key in site_config.SITE_CONFIG_KEYS:
        if key not in data:
            continue

        val = str(data.get(key) or "").strip()

        # URL 类字段做个基本校验：填成 "console.example.com"（少了协议）
        # 会让所有请求直接失败，而错误信息只会是 MissingSchema，指不出是配置问题
        if key in ("SITE_ORIGIN", "STYTCH_LOGIN_HOST", "VERIFY_API_URL") and val:
            if not val.startswith(("http://", "https://")):
                return jsonify({
                    "error": f"{SITE_FIELD_META[key]['label']} 必须以 http:// 或 https:// 开头"
                }), 400
            val = val.rstrip("/")

        SystemConfig.set_value(f"site.{key}", val, SITE_FIELD_META.get(key, {}).get("label", key))
        updated[key] = val

    # 注入当前进程。空值不覆盖（`apply_site_config` 自己会跳过），
    # 所以页面上清空某项 = 回落到 .env，而不是把生效值清成空。
    active = site_config.apply_site_config(**updated)

    log = OperationLog(
        user_id=user_id,
        action="update_site_config",
        details=f"更新站点配置: {'、'.join(updated) or '（无变更）'}",
        ip_address=request.remote_addr,
    )
    db.session.add(log)
    db.session.commit()

    return jsonify({
        "message": "已保存并生效",
        "updated": list(updated),
        "active": active,
    })


def load_site_config_from_db():
    """把数据库里的站点配置注入进程。

    调用点有两处，缺一不可：
      · `app.py` 启动时 —— 让命令行/后台线程一开始就用页面配的值
      · `executor` 跑批前 —— 防止"改了配置但没重启"时用旧值
    """
    values = {}
    for key in site_config.SITE_CONFIG_KEYS:
        v = SystemConfig.get_value(f"site.{key}", "")
        if v:
            values[key] = v

    if values:
        site_config.apply_site_config(**values)
    return values


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
