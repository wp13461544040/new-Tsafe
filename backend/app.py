"""Flask API 主文件"""
import os
import secrets
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
        "register_tasks": {
            # 任务执行日志快照。见 backend/tasklog.py
            "log_text": "TEXT",
        },
        "accounts": {
            # 巡检验活。见 backend/models.py 的 Account.apply_check()
            "last_checked_at": "DATETIME",
            "check_status": "VARCHAR(20)",
            "check_error": "TEXT",
            "fail_streak": "INTEGER NOT NULL DEFAULT 0",
            "checked_count": "INTEGER NOT NULL DEFAULT 0",
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

    # 🔴 SQLite **不支持**用 ALTER TABLE 去掉 NOT NULL 约束 ⇒ 只能重建表。
    #    这里要放开 `operation_logs.user_id`：系统自动触发的操作（定时巡检、
    #    任务完成回写）没有归属用户，非空约束会让写日志抛 IntegrityError，
    #    而那发生在后台线程里，只表现为"日志里缺记录"，极难定位。
    if "operation_logs" in tables:
        uid_col = next((c for c in inspector.get_columns("operation_logs")
                        if c["name"] == "user_id"), None)
        if uid_col is not None and not uid_col.get("nullable", True):
            db.session.execute(text("""
                CREATE TABLE operation_logs_new (
                    id INTEGER NOT NULL PRIMARY KEY,
                    user_id INTEGER,
                    action VARCHAR(50) NOT NULL,
                    resource_type VARCHAR(50),
                    resource_id INTEGER,
                    details TEXT,
                    ip_address VARCHAR(50),
                    created_at DATETIME NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users (id)
                )
            """))
            db.session.execute(text(
                "INSERT INTO operation_logs_new "
                "SELECT id, user_id, action, resource_type, resource_id, "
                "       details, ip_address, created_at FROM operation_logs"))
            db.session.execute(text("DROP TABLE operation_logs"))
            db.session.execute(text(
                "ALTER TABLE operation_logs_new RENAME TO operation_logs"))
            db.session.commit()
            print("[OK] 数据库迁移完成，operation_logs.user_id 已放开非空约束")

    # 🔴 `ALTER TABLE ADD COLUMN` **不会**建索引 —— 模型里写的 `index=True`
    #    只在 `create_all()` 建新表时生效。已存在的表补了列却没索引，
    #    表现是"账号多了以后巡检查询变慢"，而且不报错、很难联想到索引缺失。
    #    这里显式补，`IF NOT EXISTS` 保证可重复执行。
    for idx_name, table, column in (
        ("ix_accounts_last_checked_at", "accounts", "last_checked_at"),
        ("ix_accounts_check_status", "accounts", "check_status"),
    ):
        if table not in tables:
            continue
        cols = {c["name"] for c in inspector.get_columns(table)} | set(
            added_all.get(table, []))
        if column in cols:
            db.session.execute(text(
                f"CREATE INDEX IF NOT EXISTS {idx_name} ON {table} ({column})"))

    if added_all:
        db.session.commit()
        for table, cols in added_all.items():
            print(f"[OK] 数据库迁移完成，{table} 新增列: {', '.join(cols)}")
    else:
        # 没加列也可能补了索引 ⇒ 仍要提交
        db.session.commit()


def create_app():
    """创建 Flask 应用"""
    app = Flask(__name__)
    app.config.from_object(config)
    
    # 确保必要的目录存在。
    # 用 config.sqlite_dir() 而不是 DATABASE_PATH.parent —— 后者在设了
    # DATABASE_URL 时会建错目录（详见 config.sqlite_dir 的注释）。
    db_dir = config.sqlite_dir()
    if db_dir is not None:
        db_dir.mkdir(parents=True, exist_ok=True)
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
        
        # 创建初始管理员。密码来自 ADMIN_PASSWORD，未配置则随机生成。
        if not User.query.filter_by(username=config.ADMIN_USERNAME).first():
            password = config.ADMIN_PASSWORD or secrets.token_urlsafe(12)

            admin = User(username=config.ADMIN_USERNAME, role="admin")
            admin.set_password(password)
            db.session.add(admin)
            db.session.commit()

            if config.ADMIN_PASSWORD:
                print(f"[OK] 初始管理员已创建：{config.ADMIN_USERNAME}（密码取自 ADMIN_PASSWORD）")
            else:
                # 🔴 随机密码**只在这里打印一次**，不落盘、不在页面显示。
                #    错过就只能删库重建或手工改密码 —— 所以打得醒目些。
                #
                # ⚠️ 这几行**不能带 emoji**：Windows 控制台默认 GBK，
                #    emoji 会抛 UnicodeEncodeError 让首次启动直接崩
                #    （而这正是最不该崩的一次 —— 崩了就拿不到密码）。
                print("=" * 64)
                print("[ 初始管理员已创建，密码随机生成，请立即保存 ]")
                print(f"    用户名: {config.ADMIN_USERNAME}")
                print(f"    密  码: {password}")
                print("  此密码不会再次显示。可在 .env 里设 ADMIN_PASSWORD 固定它。")
                print("=" * 64)
        
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
    
    # 把页面上配的站点配置注入注册器模块。
    # 🔴 必须在这里做一次：跑批读的是 `src.config` 的模块级变量，
    #    它们启动时只从 .env 取值 ⇒ 不注入的话页面配的值要等到下次改配置才生效。
    with app.app_context():
        from backend.api.system import load_site_config_from_db
        loaded = load_site_config_from_db()
        if loaded:
            print(f"[OK] 已从数据库加载站点配置: {'、'.join(loaded)}")

    # 任务跑在进程内线程里 ⇒ 进程重启后数据库里的 running 状态是假的。
    # 不清理的话页面上会永远显示"运行中"，且取消按钮点不动（线程早没了）。
    from backend.executor import recover_stale_tasks
    n = recover_stale_tasks(app)
    if n:
        print(f"[WARN] 已将 {n} 个残留的 running 任务标记为 failed（服务重启导致中断）")

    # 账号池定时巡检。默认关闭，由「系统设置 → 账号巡检」里的开关控制；
    # 调度线程常驻并每分钟读一次配置，所以开关改完不用重启。
    #
    # 🔴 Flask 的 debug reloader 会起**两个**进程（父进程只负责监视文件变化），
    #    两边都启调度器就会让同一批账号被检查两次 ⇒ fail_streak 双倍累加，
    #    阈值 2 的配置实际变成阈值 1。只在真正的工作进程里启。
    #    （gunicorn 是 -w 1 单 worker，没这个问题。）
    if not config.DEBUG or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        from backend.checker import start_scheduler
        if start_scheduler(app):
            print("[OK] 账号巡检调度器已启动（是否执行取决于页面上的开关）")
    
    return app


if __name__ == "__main__":
    app = create_app()

    if config.SECRET_KEY_IS_EPHEMERAL:
        print("[WARN] 密钥无法落盘（目录不可写），本次使用内存密钥 ——")
        print(f"    重启后所有登录态会失效。检查 {config.SECRET_KEY_FILE.parent} 是否可写。")

    print("后端 API 启动中...")
    print("访问地址: http://127.0.0.1:5000")
    app.run(host="0.0.0.0", port=5000, debug=config.DEBUG)
