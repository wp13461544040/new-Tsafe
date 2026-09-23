"""统计数据 API"""
from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from backend.models import db, RegisterTask, CardKey, MailConfig, OperationLog
from sqlalchemy import func
from datetime import timedelta
from backend.timeutil import now

bp = Blueprint("stats", __name__)


def get_current_user_id():
    """获取当前用户ID（转为整数）"""
    return int(get_jwt_identity())


@bp.route("/overview", methods=["GET"])
@jwt_required()
def get_overview():
    """获取统计概览"""
    from flask import request as flask_req
    print(f"🔍 收到请求: {flask_req.headers.get('Authorization', 'NO AUTH HEADER')}")
    
    # 总任务数和成功率
    total_tasks = RegisterTask.query.count()
    completed_tasks = RegisterTask.query.filter_by(status="completed").count()
    running_tasks = RegisterTask.query.filter_by(status="running").count()
    
    # 总注册数
    total_success = db.session.query(func.sum(RegisterTask.success_count)).scalar() or 0
    total_failed = db.session.query(func.sum(RegisterTask.failed_count)).scalar() or 0
    
    # 卡密统计
    total_cards = CardKey.query.count()
    used_cards = CardKey.query.filter_by(status="used").count()
    unused_cards = total_cards - used_cards
    
    # 邮箱配置统计
    total_mail_configs = MailConfig.query.count()
    active_mail_configs = MailConfig.query.filter_by(is_active=True).count()
    
    # 最近7天的任务趋势
    seven_days_ago = now() - timedelta(days=7)
    recent_tasks = RegisterTask.query.filter(
        RegisterTask.created_at >= seven_days_ago
    ).all()
    
    daily_stats = {}
    for task in recent_tasks:
        date_key = task.created_at.strftime("%Y-%m-%d")
        if date_key not in daily_stats:
            daily_stats[date_key] = {"tasks": 0, "success": 0, "failed": 0}
        
        daily_stats[date_key]["tasks"] += 1
        daily_stats[date_key]["success"] += task.success_count
        daily_stats[date_key]["failed"] += task.failed_count
    
    # 填充缺失日期
    trend_data = []
    for i in range(7):
        date = now() - timedelta(days=6-i)
        date_key = date.strftime("%Y-%m-%d")
        trend_data.append({
            "date": date_key,
            "tasks": daily_stats.get(date_key, {}).get("tasks", 0),
            "success": daily_stats.get(date_key, {}).get("success", 0),
            "failed": daily_stats.get(date_key, {}).get("failed", 0)
        })
    
    return jsonify({
        "tasks": {
            "total": total_tasks,
            "completed": completed_tasks,
            "running": running_tasks,
            "pending": total_tasks - completed_tasks - running_tasks
        },
        "accounts": {
            "total_success": total_success,
            "total_failed": total_failed,
            "success_rate": round(total_success / (total_success + total_failed) * 100, 2) if (total_success + total_failed) > 0 else 0
        },
        "cards": {
            "total": total_cards,
            "used": used_cards,
            "unused": unused_cards,
            "usage_rate": round(used_cards / total_cards * 100, 2) if total_cards > 0 else 0
        },
        "mail_configs": {
            "total": total_mail_configs,
            "active": active_mail_configs
        },
        "trend": trend_data
    })


@bp.route("/tasks", methods=["GET"])
@jwt_required()
def get_task_stats():
    """获取任务统计"""
    # 按状态统计
    status_stats = db.session.query(
        RegisterTask.status,
        func.count(RegisterTask.id).label("count"),
        func.sum(RegisterTask.success_count).label("success"),
        func.sum(RegisterTask.failed_count).label("failed")
    ).group_by(RegisterTask.status).all()
    
    # 按邮箱配置统计
    mail_stats = db.session.query(
        MailConfig.name,
        func.count(RegisterTask.id).label("count"),
        func.sum(RegisterTask.success_count).label("success")
    ).join(
        RegisterTask, RegisterTask.mail_config_id == MailConfig.id
    ).group_by(
        MailConfig.id, MailConfig.name
    ).all()
    
    return jsonify({
        "by_status": [{
            "status": s.status,
            "count": s.count,
            "success": s.success or 0,
            "failed": s.failed or 0
        } for s in status_stats],
        "by_mail_config": [{
            "name": m.name,
            "count": m.count,
            "success": m.success or 0
        } for m in mail_stats]
    })


@bp.route("/recent-activities", methods=["GET"])
@jwt_required()
def get_recent_activities():
    """获取最近操作记录"""
    limit = request.args.get("limit", 20, type=int)
    
    logs = OperationLog.query.order_by(
        OperationLog.created_at.desc()
    ).limit(limit).all()
    
    return jsonify([log.to_dict() for log in logs])
