"""后端 API 配置"""
import os
from pathlib import Path
from datetime import timedelta

# 项目根目录
BASE_DIR = Path(__file__).resolve().parent.parent

# Flask 配置
SECRET_KEY = os.getenv("API_SECRET_KEY", "dev-secret-key-change-in-production-12345678")
DEBUG = os.getenv("API_DEBUG", "False").lower() == "true"

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

# 分页配置
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100

# 卡密配置
CARD_KEY_PREFIX = "TS"
CARD_KEY_LENGTH = 16
