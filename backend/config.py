"""后端 API 配置"""
import os
import secrets
from pathlib import Path
from datetime import timedelta

# 项目根目录
BASE_DIR = Path(__file__).resolve().parent.parent

# ── Flask ───────────────────────────────────────────────────────────────
#: 密钥文件。JWT 用它签名 ⇒ 必须跨重启稳定，否则每次重启所有人被踢下线。
SECRET_KEY_FILE = BASE_DIR / "backend" / "data" / ".secret_key"


def _load_or_create_secret_key() -> str:
    """取密钥：文件里有就用，没有就生成一个存起来。

    🔴 为什么不写死默认值：JWT 用它签名，密钥公开 = 任何人都能伪造任意用户的
    token，管理端的认证形同虚设。以前默认值是
    `dev-secret-key-change-in-production-12345678` 写在仓库里 ——
    "change-in-production" 这种提示没人会照做。

    🔴 为什么不每次随机生成：那样进程一重启已签发的 token 全部失效，
    用户被无故踢到登录页，会被当成 bug。所以生成一次**落盘**。

    文件在 `backend/data/` 下（已被 .gitignore 覆盖），权限交给文件系统。
    环境变量 `API_SECRET_KEY` 仍可覆盖 —— 多实例部署时需要共享同一个密钥。
    """
    if env := os.getenv("API_SECRET_KEY"):
        return env

    try:
        if SECRET_KEY_FILE.is_file():
            if key := SECRET_KEY_FILE.read_text(encoding="utf-8").strip():
                return key

        key = secrets.token_urlsafe(48)
        SECRET_KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
        SECRET_KEY_FILE.write_text(key, encoding="utf-8")
        return key
    except OSError:
        # 文件系统不可写（只读容器等）⇒ 退回内存密钥，并让 app.py 提示一次。
        # 不静默：否则表现是"重启后莫名要重新登录"，查不到原因。
        return secrets.token_urlsafe(48)


SECRET_KEY = _load_or_create_secret_key()
#: 密钥是否只存在于内存里（= 重启会掉线）。`app.py` 启动时据此提示。
SECRET_KEY_IS_EPHEMERAL = not os.getenv("API_SECRET_KEY") and not SECRET_KEY_FILE.is_file()

DEBUG = os.getenv("API_DEBUG", "False").lower() == "true"

# ── 初始管理员 ──────────────────────────────────────────────────────────
# 🔴 不再硬编码 admin/admin123。那个密码曾经同时出现在三处：
#    数据库初始化、启动日志、以及**登录页面上的提示文字** ——
#    公开仓库 + 弱口令 + 页面明示，等于把后台交出去。
#
# 未配置密码时由 `app.py` 随机生成并**只打印一次**（不落盘、不在页面显示）。
# 之后改密码走页面（右上角头像 → 修改密码）。
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")

# 数据库配置
#
# 🔴 `DATABASE_URL` 必须可覆盖，原因有两个（后者是被真实事故推出来的）：
#   1. 部署：Docker 想把库挂到别的卷、或换成 PostgreSQL 时不用改代码。
#   2. **测试隔离**：路径写死时，任何 `create_app()` 都连到这个唯一的真实库。
#      2026-09-22 就因此出过事故 —— 一个本想跑在临时库上的验证脚本里有
#      `Account.query.delete()`，实际删的是开发库里的 accounts 表
#      （当时库里只有测试残留，没造成业务损失，但纯属运气）。
#      写死路径让"跑个测试"和"动生产数据"变成同一个操作，这是设计问题，
#      不是使用者不小心。
DATABASE_PATH = BASE_DIR / "backend" / "data" / "admin.db"
SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL", "") or f"sqlite:///{DATABASE_PATH}"
SQLALCHEMY_TRACK_MODIFICATIONS = False


def sqlite_dir() -> Path | None:
    """当前 URI 若是 SQLite，返回需要预先创建的目录；否则 None。

    启动时要 mkdir 的是**实际在用的**那个目录，不能固定用 `DATABASE_PATH.parent`：
    设了 `DATABASE_URL=sqlite:////var/lib/app/x.db` 时，前者会去建一个没人用的
    `backend/data/`，而真正需要的 `/var/lib/app/` 没建 ⇒ 启动报 "unable to open
    database file"，且错误信息完全不指向根因。
    """
    uri = SQLALCHEMY_DATABASE_URI
    if not uri.startswith("sqlite:"):
        return None  # PostgreSQL/MySQL 等无本地目录可建
    path = uri.split("///", 1)[-1] if "///" in uri else ""
    if not path or path == ":memory:":
        return None
    return Path(path).expanduser().resolve().parent

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
