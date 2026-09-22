"""后端 API 配置"""
import os
import secrets
from pathlib import Path
from datetime import timedelta

# 项目根目录
BASE_DIR = Path(__file__).resolve().parent.parent

# ── Flask ───────────────────────────────────────────────────────────────
# 🔴 不留固定默认值。JWT 就是用这个密钥签名的 ⇒ 密钥一旦公开，
#    任何人都能伪造任意用户的 token，等于整个管理端没有认证。
#    以前默认值是 `dev-secret-key-change-in-production-12345678`
#    写死在仓库里，"change-in-production" 这种提示没人会照做。
#
# 未配置时随机生成：进程重启后密钥变化 ⇒ 已签发的 token 全部失效（需重新登录）。
# 这是刻意的取舍 —— 用一个"会掉线"的提示逼出配置，比静默用公开密钥安全。
SECRET_KEY = os.getenv("API_SECRET_KEY") or secrets.token_urlsafe(48)
#: 是否用了临时密钥。`app.py` 启动时据此打印警告。
SECRET_KEY_IS_EPHEMERAL = not os.getenv("API_SECRET_KEY")

DEBUG = os.getenv("API_DEBUG", "False").lower() == "true"

# ── 初始管理员 ──────────────────────────────────────────────────────────
# 🔴 不再硬编码 admin/admin123。那个密码曾经同时出现在三处：
#    数据库初始化、启动日志、以及**登录页面上的提示文字** ——
#    公开仓库 + 弱口令 + 页面明示，等于把后台交出去。
#
# 未配置密码时由 `app.py` 随机生成并**只打印一次**（不落盘、不在页面显示）。
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")

# 数据库配置
DATABASE_PATH = BASE_DIR / "backend" / "data" / "admin.db"
SQLALCHEMY_DATABASE_URI = f"sqlite:///{DATABASE_PATH}"
SQLALCHEMY_TRACK_MODIFICATIONS = False

# JWT 配置 - 使用相同的密钥
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", SECRET_KEY)
JWT_ACCESS_TOKEN_EXPIRES = timedelta(hours=24)
JWT_REFRESH_TOKEN_EXPIRES = timedelta(days=7)
JWT_TOKEN_LOCATION = ["headers"]
JWT_HEADER_NAME = "Authorization"
JWT_HEADER_TYPE = "Bearer"

# CORS 配置
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")

# 上传配置
UPLOAD_FOLDER = BASE_DIR / "backend" / "uploads"
MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16MB

# 卡密配置
CARD_KEY_PREFIX = "TS"
CARD_KEY_LENGTH = 16
