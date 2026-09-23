"""卡密管理 API"""
from flask import Blueprint, request, jsonify, make_response
from flask_jwt_extended import jwt_required, get_jwt_identity
from backend.models import db, CardKey, User, OperationLog
from backend.config import CARD_KEY_PREFIX, CARD_KEY_LENGTH
import csv
from io import StringIO
from datetime import timedelta
from backend.timeutil import now

bp = Blueprint("card", __name__)


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
def list_cards():
    """获取卡密列表"""
    page = request.args.get("page", 1, type=int)
    page_size = request.args.get("page_size", 20, type=int)
    status = request.args.get("status")
    batch_id = request.args.get("batch_id")
    
    query = CardKey.query
    
    if status:
        query = query.filter_by(status=status)
    if batch_id:
        query = query.filter_by(batch_id=batch_id)
    
    pagination = query.order_by(CardKey.created_at.desc()).paginate(
        page=page, per_page=page_size, error_out=False
    )
    
    # 管理员可以看到 API Key
    include_api_key = check_admin()
    
    return jsonify({
        "items": [c.to_dict(include_api_key=include_api_key) for c in pagination.items],
        "total": pagination.total,
        "page": page,
        "page_size": page_size
    })


@bp.route("/generate", methods=["POST"])
@jwt_required()
def generate_cards():
    """批量生成卡密"""
    if not check_admin():
        return jsonify({"error": "权限不足"}), 403
    
    data = request.get_json()
    count = data.get("count", 0)
    quota = data.get("quota", 1)
    batch_id = data.get("batch_id", "")
    remarks = data.get("remarks", "")
    expires_days = data.get("expires_days")

    if count <= 0 or count > 1000:
        return jsonify({"error": "生成数量必须在 1-1000 之间"}), 400

    try:
        quota = int(quota)
    except (TypeError, ValueError):
        return jsonify({"error": "额度格式错误"}), 400

    if quota <= 0 or quota > 1000:
        return jsonify({"error": "单卡额度必须在 1-1000 之间"}), 400

    expires_at = None
    if expires_days:
        try:
            expires_at = now() + timedelta(days=int(expires_days))
        except (TypeError, ValueError):
            return jsonify({"error": "有效期天数格式错误"}), 400

    user_id = get_current_user_id()

    # 如果没有提供 batch_id，自动生成
    if not batch_id:
        batch_id = f"BATCH-{now().strftime('%Y%m%d%H%M%S')}"

    cards = []
    for _ in range(count):
        card_key = CardKey.generate_card_key(CARD_KEY_PREFIX, CARD_KEY_LENGTH)

        # 确保卡密唯一
        while CardKey.query.filter_by(card_key=card_key).first():
            card_key = CardKey.generate_card_key(CARD_KEY_PREFIX, CARD_KEY_LENGTH)

        card = CardKey(
            card_key=card_key,
            quota=quota,
            extracted_count=0,
            status="unused",
            batch_id=batch_id,
            expires_at=expires_at,
            created_by=user_id,
            remarks=remarks
        )
        cards.append(card)
        db.session.add(card)

    db.session.commit()

    # 记录日志
    log = OperationLog(
        user_id=user_id,
        action="generate_cards",
        resource_type="card",
        details=f"批量生成卡密: {count} 张，单卡额度 {quota}（批次: {batch_id}）",
        ip_address=request.remote_addr
    )
    db.session.add(log)
    db.session.commit()

    return jsonify({
        "count": len(cards),
        "batch_id": batch_id,
        "quota": quota,
        "cards": [c.to_dict(include_api_key=True) for c in cards]
    }), 201


@bp.route("/<int:card_id>", methods=["PUT"])
@jwt_required()
def update_card(card_id):
    """更新卡密（调整额度 / 启用禁用 / 备注 / 有效期）"""
    if not check_admin():
        return jsonify({"error": "权限不足"}), 403

    card = CardKey.query.get_or_404(card_id)
    data = request.get_json() or {}

    if "quota" in data:
        try:
            quota = int(data["quota"])
        except (TypeError, ValueError):
            return jsonify({"error": "额度格式错误"}), 400

        if quota < (card.extracted_count or 0):
            return jsonify({"error": f"额度不能小于已提取数量 {card.extracted_count}"}), 400

        card.quota = quota

    if "remarks" in data:
        card.remarks = data["remarks"]

    if "expires_days" in data:
        days = data["expires_days"]
        card.expires_at = (now() + timedelta(days=int(days))) if days else None

    if "status" in data:
        status = data["status"]
        if status not in ("unused", "partial", "used", "disabled"):
            return jsonify({"error": "状态值非法"}), 400
        card.status = status
    else:
        # 额度变更后重算状态
        if card.status != "disabled":
            if card.extracted_count <= 0:
                card.status = "unused"
            elif card.remaining > 0:
                card.status = "partial"
            else:
                card.status = "used"

    db.session.commit()

    user_id = get_current_user_id()
    log = OperationLog(
        user_id=user_id,
        action="update_card",
        resource_type="card",
        resource_id=card.id,
        details=f"更新卡密 {card.card_key}: 额度 {card.quota}, 状态 {card.status}",
        ip_address=request.remote_addr
    )
    db.session.add(log)
    db.session.commit()

    return jsonify(card.to_dict(include_api_key=True))


@bp.route("/bind", methods=["POST"])
@jwt_required()
def bind_card():
    """绑定卡密到 API Key"""
    if not check_admin():
        return jsonify({"error": "权限不足"}), 403
    
    data = request.get_json()
    card_key = data.get("card_key", "").strip()
    api_key = data.get("api_key", "").strip()
    email = data.get("email", "").strip()
    api_key_id = data.get("api_key_id", "").strip()
    
    if not card_key:
        return jsonify({"error": "卡密不能为空"}), 400
    
    card = CardKey.query.filter_by(card_key=card_key).first()
    
    if not card:
        return jsonify({"error": "卡密不存在"}), 404
    
    if card.status == "used":
        return jsonify({"error": "该卡密已被使用"}), 400
    
    card.api_key = api_key
    card.email = email
    card.api_key_id = api_key_id
    card.status = "used"
    card.used_at = now()
    
    db.session.commit()
    
    # 记录日志
    user_id = get_current_user_id()
    log = OperationLog(
        user_id=user_id,
        action="bind_card",
        resource_type="card",
        resource_id=card.id,
        details=f"绑定卡密: {card_key}",
        ip_address=request.remote_addr
    )
    db.session.add(log)
    db.session.commit()
    
    return jsonify(card.to_dict(include_api_key=True))


@bp.route("/export", methods=["GET"])
@jwt_required()
def export_cards():
    """导出卡密"""
    status = request.args.get("status")
    batch_id = request.args.get("batch_id")
    format_type = request.args.get("format", "csv")  # csv, txt, json
    
    query = CardKey.query
    
    if status:
        query = query.filter_by(status=status)
    if batch_id:
        query = query.filter_by(batch_id=batch_id)
    
    cards = query.all()
    
    # 管理员可以导出 API Key
    include_api_key = check_admin()
    
    if format_type == "json":
        return jsonify([c.to_dict(include_api_key=include_api_key) for c in cards])
    
    elif format_type == "txt":
        output = StringIO()
        for card in cards:
            output.write(f"{card.card_key}\t{card.quota}\n")

        response = make_response(output.getvalue())
        response.headers["Content-Type"] = "text/plain; charset=utf-8"
        response.headers["Content-Disposition"] = f"attachment; filename=cards_{now().strftime('%Y%m%d%H%M%S')}.txt"
        return response

    else:  # csv
        output = StringIO()
        writer = csv.writer(output)

        writer.writerow(["卡密", "额度", "已提取", "剩余", "状态", "批次", "有效期", "创建时间", "首次使用时间"])
        for card in cards:
            writer.writerow([
                card.card_key,
                card.quota,
                card.extracted_count,
                card.remaining,
                card.status,
                card.batch_id or "",
                card.expires_at.isoformat() if card.expires_at else "",
                card.created_at.isoformat() if card.created_at else "",
                card.used_at.isoformat() if card.used_at else ""
            ])
        
        response = make_response(output.getvalue())
        response.headers["Content-Type"] = "text/csv; charset=utf-8-sig"
        response.headers["Content-Disposition"] = f"attachment; filename=cards_{now().strftime('%Y%m%d%H%M%S')}.csv"
        return response


@bp.route("/batches", methods=["GET"])
@jwt_required()
def list_batches():
    """获取批次列表（返回字符串数组用于筛选）"""
    batches = db.session.query(
        CardKey.batch_id
    ).filter(
        CardKey.batch_id.isnot(None)
    ).distinct().all()
    
    # 返回纯字符串数组
    return jsonify([b[0] for b in batches])


@bp.route("/<int:card_id>", methods=["DELETE"])
@jwt_required()
def delete_card(card_id):
    """删除卡密"""
    if not check_admin():
        return jsonify({"error": "权限不足"}), 403
    
    card = CardKey.query.get_or_404(card_id)
    
    if card.status == "used":
        return jsonify({"error": "已使用的卡密无法删除"}), 400
    
    card_key = card.card_key
    db.session.delete(card)
    db.session.commit()
    
    # 记录日志
    user_id = get_current_user_id()
    log = OperationLog(
        user_id=user_id,
        action="delete_card",
        resource_type="card",
        resource_id=card_id,
        details=f"删除卡密: {card_key}",
        ip_address=request.remote_addr
    )
    db.session.add(log)
    db.session.commit()
    
    return jsonify({"message": "删除成功"})


@bp.route("/import", methods=["POST"])
@jwt_required()
def import_cards():
    """批量导入卡密（仅卡密 + 额度，账号请用 /api/account/import）

    支持格式：
    1. JSON: {"cards": [{"card_key": "xxx", "quota": 5}, ...]}
    2. CSV:  card_key,quota
    3. TXT:  card_key<TAB>quota
    """
    if not check_admin():
        return jsonify({"error": "权限不足"}), 403
    
    user_id = get_current_user_id()
    
    # 检查请求内容类型
    content_type = request.content_type
    
    success_count = 0
    failed_count = 0
    skipped_count = 0
    errors = []
    
    rows = []

    try:
        if content_type and "application/json" in content_type:
            data = request.get_json()
            rows = data if isinstance(data, list) else data.get("cards", [])

        elif content_type and ("text/csv" in content_type or "text/plain" in content_type):
            text_data = request.data.decode("utf-8")
            lines = text_data.strip().split("\n")

            delimiter = "\t" if "\t" in lines[0] else ","

            start_idx = 1 if "card_key" in lines[0].lower() else 0

            for i, line in enumerate(lines[start_idx:], start=start_idx + 1):
                line = line.strip()
                if not line:
                    continue

                parts = [p.strip() for p in line.split(delimiter)]
                rows.append({
                    "card_key": parts[0],
                    "quota": parts[1] if len(parts) > 1 else 1,
                })

        else:
            return jsonify({"error": "不支持的内容类型，请使用 JSON、CSV 或 TXT 格式"}), 400

        batch_id = f"IMPORT-{now().strftime('%Y%m%d%H%M%S')}"

        for idx, row in enumerate(rows, 1):
            card_key = str(row.get("card_key", "")).strip()

            if not card_key:
                errors.append(f"第 {idx} 条: 卡密不能为空")
                failed_count += 1
                continue

            try:
                quota = int(row.get("quota") or 1)
            except (TypeError, ValueError):
                errors.append(f"第 {idx} 条: 额度格式错误")
                failed_count += 1
                continue

            if quota <= 0:
                errors.append(f"第 {idx} 条: 额度必须大于 0")
                failed_count += 1
                continue

            existing = CardKey.query.filter_by(card_key=card_key).first()

            if existing:
                errors.append(f"第 {idx} 条: 卡密 {card_key} 已存在")
                skipped_count += 1
                continue

            card = CardKey(
                card_key=card_key,
                quota=quota,
                extracted_count=0,
                status="unused",
                batch_id=batch_id,
                created_by=user_id,
                remarks=f"批量导入于 {now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            db.session.add(card)
            success_count += 1

        db.session.commit()

        log = OperationLog(
            user_id=user_id,
            action="import_cards",
            resource_type="card",
            details=f"批量导入卡密: 成功 {success_count}, 失败 {failed_count}, 跳过 {skipped_count}",
            ip_address=request.remote_addr
        )
        db.session.add(log)
        db.session.commit()

        return jsonify({
            "message": "导入完成",
            "batch_id": batch_id,
            "success_count": success_count,
            "failed_count": failed_count,
            "skipped_count": skipped_count,
            "total": len(rows),
            "errors": errors[:10]
        }), 201

    except Exception as e:
        db.session.rollback()
        return jsonify({
            "error": f"导入失败: {str(e)}",
            "success_count": success_count,
            "failed_count": failed_count
        }), 500
