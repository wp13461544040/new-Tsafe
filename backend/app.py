"""Flask API 主文件"""
import sys
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from flask import Flask, jsonify
from flask_cors import CORS
from flask_jwt_extended import JWTManager
from sqlalchemy import text, inspect
from backend.models import db, User, SystemConfig
from backend import config


def run_migrations():
    """轻量迁移：为已存在的 SQLite 表补充新增列

    db.create_all() 只会创建缺失的表，不会给已有表添加列，
    因此这里手动检测并 ALTER TABLE。
    """
    inspector = inspect(db.engine)
    tables = set(inspector.get_table_names())

    # 表名 -> {列名: DDL}
    migrations = {
        "card_keys": {
            "quota": "INTEGER NOT NULL DEFAULT 1",
            "extracted_count": "INTEGER NOT NULL DEFAULT 0",
            "expires_at": "DATETIME",
        },
        "mail_configs": {
            "moemail_base": "VARCHAR(256)",
            "moemail_api_key": "VARCHAR(256)",
            "moemail_domain": "VARCHAR(100)",
            "moemail_expiry_ms": "INTEGER DEFAULT 86400000",
            "moemail_poll_interval": "FLOAT DEFAULT 3.0",
        },
    }

    added_all = {}
    for table, columns in migrations.items():
        # 表不存在时由 create_all 负责，跳过
        if table not in tables:
            continue

        existing = {col["name"] for col in inspector.get_columns(table)}
        added = []
        for column, ddl in columns.items():
            if column not in existing:
                db.session.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
                added.append(column)
        if added:
            added_all[table] = added

    if "card_keys" in added_all:
        # 旧数据迁移：已绑定 api_key 的老卡密视为额度1且已用完
        db.session.execute(text(
            "UPDATE card_keys SET extracted_count = 1, quota = 1 "
            "WHERE status = 'used' AND extracted_count = 0"
        ))

    if added_all:
        db.session.commit()
        for table, cols in added_all.items():
            print(f"✅ 数据库迁移完成，{table} 新增列: {', '.join(cols)}")


def create_app():
    """创建 Flask 应用"""
    app = Flask(__name__)
    app.config.from_object(config)
    
    # 确保必要的目录存在
    config.DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)
    
    # 初始化扩展
    db.init_app(app)
    CORS(app, origins=config.CORS_ORIGINS, supports_credentials=True)
    jwt = JWTManager(app)
    
    # JWT 错误处理
    @jwt.expired_token_loader
    def expired_token_callback(jwt_header, jwt_payload):
        return jsonify({"error": "Token 已过期"}), 401
    
    @jwt.invalid_token_loader
    def invalid_token_callback(error):
        return jsonify({"error": "无效的 Token"}), 422
    
    @jwt.unauthorized_loader
    def unauthorized_callback(error):
        return jsonify({"error": "缺少 Authorization Header"}), 401
    
    # 注册蓝图
    from backend.api import auth, mail, task, card, stats, system, account, public
    
    app.register_blueprint(auth.bp, url_prefix="/api/auth")
    app.register_blueprint(mail.bp, url_prefix="/api/mail")
    app.register_blueprint(task.bp, url_prefix="/api/task")
    app.register_blueprint(card.bp, url_prefix="/api/card")
    app.register_blueprint(stats.bp, url_prefix="/api/stats")
    app.register_blueprint(system.bp, url_prefix="/api/system")
    app.register_blueprint(account.bp, url_prefix="/api/account")
    # 公开接口：无需登录，供卡密持有者自助提取
    app.register_blueprint(public.bp, url_prefix="/api/public")
    
    # 错误处理
    @app.errorhandler(404)
    def not_found(error):
        return jsonify({"error": "Not found"}), 404
    
    @app.errorhandler(500)
    def internal_error(error):
        db.session.rollback()
        return jsonify({"error": "Internal server error"}), 500
    
    @app.errorhandler(Exception)
    def unhandled_exception(error):
        """兜底处理未捕获异常。
        
        🔴 关键是**把异常类型和消息带回前端**。以前一律回
        「Internal server error」，调试时必须去翻服务端日志才知道发生了什么 ——
        而生产环境下用户根本看不到日志，只能反馈"创建失败"，
        排查就得靠猜（2026-09-22 的 count 类型错误就是这么被藏起来的）。
        
        仍然**不返回 traceback**（那会泄露文件路径与代码结构），
        只给类型名 + 消息，足够定位又不过度暴露。
        """
        db.session.rollback()
        app.logger.exception("未处理异常")
        return jsonify({
            "error": f"服务端异常：{type(error).__name__}: {error}"
        }), 500
    
    # 健康检查
    @app.route("/api/health")
    def health():
        return jsonify({"status": "ok"})
    
    # 初始化数据库
    with app.app_context():
        db.create_all()
        run_migrations()
        
        # 创建默认管理员
        if not User.query.filter_by(username="admin").first():
            admin = User(username="admin", role="admin")
            admin.set_password("admin123")
            db.session.add(admin)
            db.session.commit()
            print("✅ 默认管理员已创建: admin / admin123")
        
        # 初始化系统配置
        if not SystemConfig.query.filter_by(key="announcement").first():
            SystemConfig.set_value(
                "announcement",
                "欢迎使用 TypeSafe API Key 提取系统",
                "首页公告内容"
            )
        
        if not SystemConfig.query.filter_by(key="extract_notice").first():
            SystemConfig.set_value(
                "extract_notice",
                "请妥善保管提取到的账号信息，卡密额度用尽后不可再次提取。",
                "对外提取页面提示文案"
            )
    
    # 任务跑在进程内线程里 ⇒ 进程重启后数据库里的 running 状态是假的。
    # 不清理的话页面上会永远显示"运行中"，且取消按钮点不动（线程早没了）。
    from backend.executor import recover_stale_tasks
    n = recover_stale_tasks(app)
    if n:
        print(f"⚠️  已将 {n} 个残留的 running 任务标记为 failed（服务重启导致中断）")
    
    return app


if __name__ == "__main__":
    app = create_app()
    print("🚀 后端 API 启动中...")
    print(f"📍 访问地址: http://127.0.0.1:5000")
    print(f"👤 默认账号: admin / admin123")
    app.run(host="0.0.0.0", port=5000, debug=config.DEBUG)
