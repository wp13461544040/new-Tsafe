"""公开 API（对外开放，无需登录认证）

用于卡密持有者自助提取账号，不暴露任何管理功能。
"""
from backend.timeutil import now
from flask import Blueprint, request, jsonify
from backend.models import db, CardKey, Account, SystemConfig

bp = Blueprint("public", __name__)

# 单次提取上限，防止异常请求耗尽账号池
MAX_EXTRACT_PER_REQUEST = 100


@bp.route("/announcement", methods=["GET"])
def announcement():
    """获取对外公告"""
    return jsonify({
        "announcement": SystemConfig.get_value("announcement", ""),
        "extract_notice": SystemConfig.get_value("extract_notice", ""),
    })


@bp.route("/card/query", methods=["POST"])
def query_card():
    """查询卡密信息（不消耗额度，用于提取前预览）"""
    data = request.get_json(silent=True) or {}
    card_key = (data.get("card_key") or "").strip()

    if not card_key:
        return jsonify({"error": "请输入卡密"}), 400

    card = CardKey.query.filter_by(card_key=card_key).first()
    if not card:
        return jsonify({"error": "卡密不存在"}), 404

    ok, err = card.check_extractable()

    return jsonify({
        "card_key": card.card_key,
        "quota": card.quota,
        "extracted_count": card.extracted_count,
        "remaining": card.remaining,
        "status": card.status,
        "expires_at": card.expires_at.isoformat() if card.expires_at else None,
        "extractable": ok,
        "reason": err,
    })


@bp.route("/extract", methods=["POST"])
def extract():
    """使用卡密提取账号

    请求: {"card_key": "TS-XXXX", "count": 5}
    count 可选，缺省按剩余额度全部提取
    """
    data = request.get_json(silent=True) or {}
    card_key = (data.get("card_key") or "").strip()
    count = data.get("count")

    if not card_key:
        return jsonify({"error": "请输入卡密"}), 400

    card = CardKey.query.filter_by(card_key=card_key).first()
    if not card:
        return jsonify({"error": "卡密不存在或已失效"}), 404

    ok, err = card.check_extractable()
    if not ok:
        return jsonify({"error": err}), 400

    # 确定本次提取数量
    remaining = card.remaining
    if count is None:
        take = remaining
    else:
        try:
            take = int(count)
        except (TypeError, ValueError):
            return jsonify({"error": "提取数量格式错误"}), 400

        if take <= 0:
            return jsonify({"error": "提取数量必须大于 0"}), 400
        if take > remaining:
            return jsonify({"error": f"超出剩余额度，当前剩余 {remaining} 个"}), 400

    take = min(take, MAX_EXTRACT_PER_REQUEST)

    # 取出可用账号（SQLite 不支持 SKIP LOCKED，依赖提交时的唯一性再校验）
    accounts = (
        Account.query
        .filter_by(status="available", card_key_id=None)
        .order_by(Account.id.asc())
        .limit(take)
        .all()
    )

    if not accounts:
        return jsonify({"error": "账号池已空，请联系管理员补充"}), 409

    if len(accounts) < take:
        return jsonify({
            "error": f"账号池库存不足，当前仅剩 {len(accounts)} 个，请联系管理员",
            "available": len(accounts),
        }), 409

    assign_time = now()

    for acc in accounts:
        acc.status = "assigned"
        acc.card_key_id = card.id
        acc.assigned_at = assign_time

    card.extracted_count = (card.extracted_count or 0) + len(accounts)
    if card.used_at is None:
        card.used_at = assign_time
    card.status = "used" if card.remaining <= 0 else "partial"

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"提取失败，请重试: {e}"}), 500

    return jsonify({
        "message": f"成功提取 {len(accounts)} 个账号",
        "card_key": card.card_key,
        "extracted": len(accounts),
        "quota": card.quota,
        "extracted_count": card.extracted_count,
        "remaining": card.remaining,
        "accounts": [
            {
                "email": acc.email,
                "password": acc.password,
                "api_key": acc.api_key,
                "api_key_id": acc.api_key_id,
            }
            for acc in accounts
        ],
    })


@bp.route("/history", methods=["POST"])
def history():
    """查询某个卡密已提取的账号（凭卡密重新获取，防止用户丢失结果）"""
    data = request.get_json(silent=True) or {}
    card_key = (data.get("card_key") or "").strip()

    if not card_key:
        return jsonify({"error": "请输入卡密"}), 400

    card = CardKey.query.filter_by(card_key=card_key).first()
    if not card:
        return jsonify({"error": "卡密不存在"}), 404

    accounts = Account.query.filter_by(card_key_id=card.id).order_by(Account.assigned_at.asc()).all()

    return jsonify({
        "card_key": card.card_key,
        "quota": card.quota,
        "extracted_count": card.extracted_count,
        "remaining": card.remaining,
        "accounts": [
            {
                "email": acc.email,
                "password": acc.password,
                "api_key": acc.api_key,
                "api_key_id": acc.api_key_id,
                "assigned_at": acc.assigned_at.isoformat() if acc.assigned_at else None,
            }
            for acc in accounts
        ],
    })
