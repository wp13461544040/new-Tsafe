"""数据模型"""
from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
import secrets

db = SQLAlchemy()


class User(db.Model):
    """管理员用户"""
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="admin")  # admin, viewer
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    last_login = db.Column(db.DateTime)

    def set_password(self, password: str):
        """设置密码"""
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        """验证密码"""
        return check_password_hash(self.password_hash, password)

    def has_permission(self, action: str) -> bool:
        """检查权限"""
        if not self.is_active:
            return False
        if self.role == "admin":
            return True
        # viewer 只能查看
        return action in ["view", "export"]

    def to_dict(self):
        """转换为字典"""
        return {
            "id": self.id,
            "username": self.username,
            "role": self.role,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_login": self.last_login.isoformat() if self.last_login else None,
        }


class MailConfig(db.Model):
    """邮箱配置"""
    __tablename__ = "mail_configs"

    id = db.Column(db.Integer, primary_key=True)
    backend = db.Column(db.String(20), nullable=False)  # cf, remail, moemail
    name = db.Column(db.String(100), nullable=False)
    
    # CF Worker 配置
    tempmail_base = db.Column(db.String(256))
    tempmail_admin_key = db.Column(db.String(256))
    tempmail_domain = db.Column(db.String(100))
    
    # Remail 配置
    remail_base = db.Column(db.String(256))
    remail_api_key = db.Column(db.String(256))
    remail_project_id = db.Column(db.Integer)
    remail_email_suffix = db.Column(db.String(100))
    remail_service_mode = db.Column(db.String(20))
    
    # MoeMail 配置（自建服务，认证走 X-API-Key 而非 Bearer）
    moemail_base = db.Column(db.String(256))
    moemail_api_key = db.Column(db.String(256))
    # 留空则由客户端从 GET /api/config 取第一个可用域名
    moemail_domain = db.Column(db.String(100))
    # 毫秒。服务端只接受 0 / 3600000 / 86400000 / 604800000
    moemail_expiry_ms = db.Column(db.Integer, default=86400000)
    # 收信轮询间隔（秒）。MoeMail 多跑在 Workers 免费额度上，
    # 间隔太小会打出 Error 1102（Worker 超资源限制）
    moemail_poll_interval = db.Column(db.Float, default=3.0)
    
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self, include_sensitive=False):
        """转换为字典"""
        data = {
            "id": self.id,
            "backend": self.backend,
            "name": self.name,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
        
        if include_sensitive:
            if self.backend == "cf":
                data.update({
                    "tempmail_base": self.tempmail_base,
                    "tempmail_admin_key": self.tempmail_admin_key,
                    "tempmail_domain": self.tempmail_domain,
                })
            elif self.backend == "remail":
                data.update({
                    "remail_base": self.remail_base,
                    "remail_api_key": self.remail_api_key,
                    "remail_project_id": self.remail_project_id,
                    "remail_email_suffix": self.remail_email_suffix,
                    "remail_service_mode": self.remail_service_mode,
                })
            elif self.backend == "moemail":
                data.update({
                    "moemail_base": self.moemail_base,
                    "moemail_api_key": self.moemail_api_key,
                    "moemail_domain": self.moemail_domain,
                    "moemail_expiry_ms": self.moemail_expiry_ms,
                    "moemail_poll_interval": self.moemail_poll_interval,
                })
        
        return data


class RegisterTask(db.Model):
    """注册任务"""
    __tablename__ = "register_tasks"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    count = db.Column(db.Integer, nullable=False)
    concurrency = db.Column(db.Integer, default=2, nullable=False)
    mail_config_id = db.Column(db.Integer, db.ForeignKey("mail_configs.id"), nullable=False)
    
    status = db.Column(db.String(20), default="pending", nullable=False)
    progress = db.Column(db.Integer, default=0, nullable=False)
    success_count = db.Column(db.Integer, default=0, nullable=False)
    failed_count = db.Column(db.Integer, default=0, nullable=False)
    
    started_at = db.Column(db.DateTime)
    completed_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    
    error_message = db.Column(db.Text)
    
    mail_config = db.relationship("MailConfig", backref="tasks")
    creator = db.relationship("User", backref="tasks")

    def to_dict(self):
        """转换为字典"""
        return {
            "id": self.id,
            "name": self.name,
            "count": self.count,
            "concurrency": self.concurrency,
            "mail_config_id": self.mail_config_id,
            "mail_config_name": self.mail_config.name if self.mail_config else None,
            "status": self.status,
            "progress": self.progress,
            "success_count": self.success_count,
            "failed_count": self.failed_count,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "created_by": self.created_by,
            "creator_name": self.creator.username if self.creator else None,
            "error_message": self.error_message,
        }


class CardKey(db.Model):
    """卡密（带可提取账号额度）"""
    __tablename__ = "card_keys"

    id = db.Column(db.Integer, primary_key=True)
    card_key = db.Column(db.String(50), unique=True, nullable=False, index=True)

    # 额度控制
    quota = db.Column(db.Integer, default=1, nullable=False)          # 可提取账号总数
    extracted_count = db.Column(db.Integer, default=0, nullable=False)  # 已提取数量

    # 兼容旧数据（单账号绑定字段，新流程改用 Account 账号池）
    api_key = db.Column(db.String(256))
    email = db.Column(db.String(256))
    api_key_id = db.Column(db.String(100))

    # unused=未使用 partial=部分提取 used=已用完 disabled=已禁用
    status = db.Column(db.String(20), default="unused", nullable=False)
    batch_id = db.Column(db.String(50), index=True)

    expires_at = db.Column(db.DateTime)   # 过期时间（可选）
    used_at = db.Column(db.DateTime)      # 首次提取时间
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    remarks = db.Column(db.Text)

    creator = db.relationship("User", backref="card_keys")

    @staticmethod
    def generate_card_key(prefix: str = "TS", length: int = 16) -> str:
        """生成卡密"""
        random_part = secrets.token_urlsafe(length)[:length].upper().replace("-", "").replace("_", "")
        return f"{prefix}-{random_part}"

    @property
    def remaining(self) -> int:
        """剩余可提取数量"""
        return max(0, (self.quota or 0) - (self.extracted_count or 0))

    @property
    def is_expired(self) -> bool:
        """是否已过期"""
        return bool(self.expires_at and datetime.utcnow() > self.expires_at)

    def check_extractable(self):
        """校验是否可提取，返回 (ok, error_message)"""
        if self.status == "disabled":
            return False, "该卡密已被禁用"
        if self.is_expired:
            return False, "该卡密已过期"
        if self.remaining <= 0:
            return False, "该卡密额度已用完"
        return True, None

    def to_dict(self, include_api_key=False):
        """转换为字典"""
        data = {
            "id": self.id,
            "card_key": self.card_key,
            "quota": self.quota,
            "extracted_count": self.extracted_count,
            "remaining": self.remaining,
            "status": self.status,
            "batch_id": self.batch_id,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "used_at": self.used_at.isoformat() if self.used_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "created_by": self.created_by,
            "creator_name": self.creator.username if self.creator else None,
            "remarks": self.remarks,
        }

        if include_api_key:
            data.update({
                "api_key": self.api_key,
                "email": self.email,
                "api_key_id": self.api_key_id,
            })

        return data


class Account(db.Model):
    """账号池（注册成功的 API Key 账号）"""
    __tablename__ = "accounts"

    id = db.Column(db.Integer, primary_key=True)
    api_key = db.Column(db.String(256), nullable=False)
    email = db.Column(db.String(256), index=True)
    password = db.Column(db.String(256))
    api_key_id = db.Column(db.String(100))

    # available=可分配 assigned=已被卡密提取 invalid=失效
    status = db.Column(db.String(20), default="available", nullable=False, index=True)
    batch_id = db.Column(db.String(50), index=True)

    card_key_id = db.Column(db.Integer, db.ForeignKey("card_keys.id"), index=True)
    assigned_at = db.Column(db.DateTime)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"))

    remarks = db.Column(db.Text)

    card = db.relationship("CardKey", backref="accounts")

    def to_dict(self, include_secret=True):
        """转换为字典"""
        data = {
            "id": self.id,
            "email": self.email,
            "status": self.status,
            "batch_id": self.batch_id,
            "card_key_id": self.card_key_id,
            "card_key": self.card.card_key if self.card else None,
            "assigned_at": self.assigned_at.isoformat() if self.assigned_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "remarks": self.remarks,
        }

        if include_secret:
            data.update({
                "api_key": self.api_key,
                "password": self.password,
                "api_key_id": self.api_key_id,
            })

        return data


class SystemConfig(db.Model):
    """系统配置"""
    __tablename__ = "system_configs"

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), unique=True, nullable=False, index=True)
    value = db.Column(db.Text)
    description = db.Column(db.String(256))
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    @staticmethod
    def get_value(key: str, default=None):
        """获取配置值"""
        config = SystemConfig.query.filter_by(key=key).first()
        return config.value if config else default

    @staticmethod
    def set_value(key: str, value: str, description: str = None):
        """设置配置值"""
        config = SystemConfig.query.filter_by(key=key).first()
        if config:
            config.value = value
            if description:
                config.description = description
        else:
            config = SystemConfig(key=key, value=value, description=description)
            db.session.add(config)
        db.session.commit()

    def to_dict(self):
        """转换为字典"""
        return {
            "id": self.id,
            "key": self.key,
            "value": self.value,
            "description": self.description,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class OperationLog(db.Model):
    """操作日志"""
    __tablename__ = "operation_logs"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    action = db.Column(db.String(50), nullable=False)
    resource_type = db.Column(db.String(50))
    resource_id = db.Column(db.Integer)
    details = db.Column(db.Text)
    ip_address = db.Column(db.String(50))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    
    user = db.relationship("User", backref="operation_logs")

    def to_dict(self):
        """转换为字典"""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "username": self.user.username if self.user else None,
            "action": self.action,
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "details": self.details,
            "ip_address": self.ip_address,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
