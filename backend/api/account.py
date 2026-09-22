"""账号池管理 API（管理端，需要登录）"""
import csv
import json
from io import StringIO
from datetime import datetime

from flask import Blueprint, request, jsonify, make_response
from flask_jwt_extended import jwt_required, get_jwt_identity

from backend.models import db, Account, CardKey, User, OperationLog

bp = Blueprint("account", __name__)


def get_current_user_id():
    """获取当前用户ID（转为整数）"""
    return int(get_jwt_identity())


def check_admin():
    """检查管理员权限"""
    user = User.query.get(get_current_user_id())
    return user and user.role == "admin"


def _extract_from_json_obj(obj):
    """从 JSON 对象中提取账号字段

    兼容注册器产出的 success.jsonl 结构，字段名做了多种别名兼容。
    """
    if not isinstance(obj, dict):
        return None

    api_key = obj.get("api_key") or obj.get("apiKey") or obj.get("key_value") or ""
    email = obj.get("email") or obj.get("key") or obj.get("mail") or ""
    password = obj.get("password") or obj.get("passwd") or obj.get("pwd") or ""
    api_key_id = obj.get("api_key_id") or obj.get("apiKeyId") or obj.get("key_id") or ""

    # email 字段可能是嵌套 user.profile.email
    if not email:
        profile = (obj.get("user") or {}).get("profile") or {}
        email = profile.get("email") or ""

    # api_key 以 apikey_ 开头才算有效，避免把 email 误当 key
    if not api_key:
        return None

    return {
        "api_key": str(api_key).strip(),
        "email": str(email).strip(),
        "password": str(password).strip(),
        "api_key_id": str(api_key_id).strip(),
        "status_hint": obj.get("status") or "",
    }


def parse_account_text(text):
    """解析账号文本，返回 (rows, skipped_lines)

    标准格式是 **JSON 数组**（注册器 result/accounts.json），只含关键字段：
        [{"email": "..", "api_key": "..", "api_key_id": ".."}, ...]

    同时兼容以下历史格式，便于旧数据一次性迁移：
    - JSONL      —— 每行一个 JSON 对象（result/success.jsonl）
    - 分隔符文本 —— 逗号 / Tab / ---- 分隔，字段按内容特征识别而非位置
    - 单列       —— 仅 api_key，每行一个（result/apikeys.txt）

    空行与 # 开头的注释行自动忽略。
    """
    text = (text or "").strip()
    if not text:
        return [], []

    rows = []
    skipped = []

    # 整体是 JSON 数组
    if text.startswith("["):
        try:
            for obj in json.loads(text):
                parsed = _extract_from_json_obj(obj)
                if parsed:
                    rows.append(parsed)
            return rows, skipped
        except json.JSONDecodeError:
            pass  # 解析失败则退回逐行处理

    for lineno, raw in enumerate(text.split("\n"), 1):
        line = raw.strip()

        # 跳过空行与注释
        if not line or line.startswith("#") or line.startswith("//"):
            continue

        # JSONL 行
        if line.startswith("{"):
            try:
                parsed = _extract_from_json_obj(json.loads(line))
                if parsed:
                    rows.append(parsed)
                else:
                    skipped.append(f"第 {lineno} 行: JSON 中未找到 api_key")
            except json.JSONDecodeError:
                skipped.append(f"第 {lineno} 行: JSON 格式错误")
            continue

        # 分隔符文本
        if "----" in line:
            parts = [p.strip() for p in line.split("----")]
        elif "\t" in line:
            parts = [p.strip() for p in line.split("\t")]
        elif "," in line:
            parts = [p.strip() for p in line.split(",")]
        else:
            parts = [line]

        parts = [p for p in parts if p]
        if not parts:
            continue

        # 跳过 CSV 表头
        if parts[0].lower() in ("email", "邮箱", "api_key", "apikey"):
            continue

        row = _identify_fields(parts)
        if row:
            rows.append(row)
        else:
            skipped.append(f"第 {lineno} 行: 未识别到 api_key")

    return rows, skipped


def _identify_fields(parts):
    """按内容特征识别字段，不依赖列顺序

    识别规则：
    - 含 @ 且有点号        → email
    - 以 apikey_ / sk- 开头 → api_key
    - 以 key_ 开头          → api_key_id
    - 其余非空             → password
    """
    email = api_key = api_key_id = password = ""
    leftovers = []

    for p in parts:
        low = p.lower()

        if "@" in p and "." in p.split("@")[-1]:
            email = email or p
        elif low.startswith("apikey_") or low.startswith("sk-"):
            api_key = api_key or p
        elif low.startswith("key_"):
            api_key_id = api_key_id or p
        else:
            leftovers.append(p)

    # 剩余字段按序补到 password；若还没识别出 api_key，则取第一个剩余项
    if not api_key and leftovers:
        api_key = leftovers.pop(0)

    if leftovers:
        password = leftovers[0]

    if not api_key:
        return None

    return {
        "api_key": api_key,
        "email": email,
        "password": password,
        "api_key_id": api_key_id,
        "status_hint": "",
    }


@bp.route("/list", methods=["GET"])
@jwt_required()
def list_accounts():
    """账号池列表"""
    page = request.args.get("page", 1, type=int)
    page_size = request.args.get("page_size", 20, type=int)
    status = request.args.get("status")
    batch_id = request.args.get("batch_id")
    keyword = request.args.get("keyword")

    query = Account.query

    if status:
        query = query.filter_by(status=status)
    if batch_id:
        query = query.filter_by(batch_id=batch_id)
    if keyword:
        query = query.filter(Account.email.like(f"%{keyword}%"))

    pagination = query.order_by(Account.created_at.desc()).paginate(
        page=page, per_page=page_size, error_out=False
    )

    include_secret = check_admin()

    return jsonify({
        "items": [a.to_dict(include_secret=include_secret) for a in pagination.items],
        "total": pagination.total,
        "page": page,
        "page_size": page_size,
    })


@bp.route("/stats", methods=["GET"])
@jwt_required()
def account_stats():
    """账号池库存统计"""
    return jsonify({
        "total": Account.query.count(),
        "available": Account.query.filter_by(status="available").count(),
        "assigned": Account.query.filter_by(status="assigned").count(),
        "invalid": Account.query.filter_by(status="invalid").count(),
    })


@bp.route("/import", methods=["POST"])
@jwt_required()
def import_accounts():
    """批量导入账号到账号池

    支持格式（自动识别）：
    1. JSONL —— 每行一个 JSON，兼容注册器 success.jsonl
    2. JSON  —— {"accounts": [...]} 或直接数组
    3. CSV   —— email,password,api_key,api_key_id
    4. TXT   —— Tab 或 ---- 分隔，或每行仅一个 api_key
    自动忽略空行和 # 注释行。
    """
    if not check_admin():
        return jsonify({"error": "权限不足"}), 403

    user_id = get_current_user_id()
    content_type = request.content_type or ""

    success_count = 0
    failed_count = 0
    skipped_count = 0
    errors = []
    rows = []

    # 是否只导入注册成功（status=keyed）的记录
    only_keyed = request.args.get("only_keyed", "1") != "0"

    try:
        if "application/json" in content_type:
            data = request.get_json()
            raw_rows = data if isinstance(data, list) else data.get("accounts", [])

            for obj in raw_rows:
                parsed = _extract_from_json_obj(obj)
                if parsed:
                    rows.append(parsed)
                else:
                    errors.append("跳过一条缺少 api_key 的记录")
                    skipped_count += 1

        elif "text/csv" in content_type or "text/plain" in content_type:
            text_data = request.data.decode("utf-8-sig")
            rows, parse_errors = parse_account_text(text_data)
            errors.extend(parse_errors)
            skipped_count += len(parse_errors)

        else:
            return jsonify({"error": "不支持的内容类型，请使用 JSON、CSV 或 TXT 格式"}), 400

        batch_id = request.args.get("batch_id") or f"POOL-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"

        for idx, row in enumerate(rows, 1):
            api_key = str(row.get("api_key") or "").strip()
            email = str(row.get("email") or "").strip()
            password = str(row.get("password") or "").strip()
            api_key_id = str(row.get("api_key_id") or "").strip()
            status_hint = str(row.get("status_hint") or "").strip()

            if not api_key:
                errors.append(f"第 {idx} 条: api_key 不能为空")
                failed_count += 1
                continue

            # JSONL 中带 status 字段时，默认只收注册成功的
            if only_keyed and status_hint and status_hint not in ("keyed", "ok", "success"):
                errors.append(f"第 {idx} 条: status={status_hint}，非成功记录已跳过")
                skipped_count += 1
                continue

            # 去重：相同 api_key 不重复入库
            if Account.query.filter_by(api_key=api_key).first():
                errors.append(f"第 {idx} 条: api_key 已存在，已跳过")
                skipped_count += 1
                continue

            account = Account(
                api_key=api_key,
                email=email,
                password=password,
                api_key_id=api_key_id,
                status="available",
                batch_id=batch_id,
                created_by=user_id,
                remarks=f"导入于 {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}",
            )
            db.session.add(account)
            success_count += 1

        db.session.commit()

        log = OperationLog(
            user_id=user_id,
            action="import_accounts",
            resource_type="account",
            details=f"导入账号池: 成功 {success_count}, 失败 {failed_count}, 跳过 {skipped_count}",
            ip_address=request.remote_addr,
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
            "errors": errors[:10],
        }), 201

    except Exception as e:
        db.session.rollback()
        return jsonify({
            "error": f"导入失败: {str(e)}",
            "success_count": success_count,
            "failed_count": failed_count,
        }), 500


@bp.route("/<int:account_id>", methods=["PUT"])
@jwt_required()
def update_account(account_id):
    """更新账号（标记失效 / 恢复可用 / 备注）"""
    if not check_admin():
        return jsonify({"error": "权限不足"}), 403

    account = Account.query.get_or_404(account_id)
    data = request.get_json() or {}

    if "status" in data:
        status = data["status"]
        if status not in ("available", "assigned", "invalid"):
            return jsonify({"error": "状态值非法"}), 400

        # 恢复为可用时解除卡密绑定
        if status == "available":
            account.card_key_id = None
            account.assigned_at = None

        account.status = status

    if "remarks" in data:
        account.remarks = data["remarks"]

    db.session.commit()
    return jsonify(account.to_dict())


@bp.route("/<int:account_id>", methods=["DELETE"])
@jwt_required()
def delete_account(account_id):
    """删除账号"""
    if not check_admin():
        return jsonify({"error": "权限不足"}), 403

    account = Account.query.get_or_404(account_id)

    if account.status == "assigned":
        return jsonify({"error": "已被卡密提取的账号不能删除，请先标记失效"}), 400

    db.session.delete(account)
    db.session.commit()

    log = OperationLog(
        user_id=get_current_user_id(),
        action="delete_account",
        resource_type="account",
        resource_id=account_id,
        details=f"删除账号: {account.email or account.api_key[:20]}",
        ip_address=request.remote_addr,
    )
    db.session.add(log)
    db.session.commit()

    return jsonify({"message": "删除成功"})


@bp.route("/batch-delete", methods=["POST"])
@jwt_required()
def batch_delete():
    """批量删除账号"""
    if not check_admin():
        return jsonify({"error": "权限不足"}), 403

    data = request.get_json() or {}
    ids = data.get("ids", [])

    if not ids:
        return jsonify({"error": "请选择要删除的账号"}), 400

    deleted = Account.query.filter(
        Account.id.in_(ids),
        Account.status != "assigned"
    ).delete(synchronize_session=False)

    db.session.commit()

    return jsonify({"message": f"已删除 {deleted} 个账号", "deleted": deleted})


@bp.route("/export", methods=["GET"])
@jwt_required()
def export_accounts():
    """导出账号"""
    if not check_admin():
        return jsonify({"error": "权限不足"}), 403

    status = request.args.get("status")
    batch_id = request.args.get("batch_id")
    format_type = request.args.get("format", "json")
    # 是否附带管理信息（状态 / 绑定卡密 / 批次）。默认只导关键字段，
    # 这样导出的文件能直接被 /account/import 原样吃回去（往返一致）。
    with_meta = request.args.get("with_meta") == "1"

    query = Account.query
    if status:
        query = query.filter_by(status=status)
    if batch_id:
        query = query.filter_by(batch_id=batch_id)

    accounts = query.all()

    if format_type == "json":
        rows = []
        for a in accounts:
            row = {
                "email": a.email or "",
                "api_key": a.api_key,
                "api_key_id": a.api_key_id or "",
                "created_at": a.created_at.timestamp() if a.created_at else None,
            }

            if a.password:
                row["password"] = a.password

            if with_meta:
                row.update({
                    "status": a.status,
                    "batch_id": a.batch_id or "",
                    "card_key": a.card.card_key if a.card else "",
                    "assigned_at": a.assigned_at.isoformat() if a.assigned_at else None,
                })

            rows.append(row)

        payload = json.dumps(rows, ensure_ascii=False, indent=2)
        response = make_response(payload)
        response.headers["Content-Type"] = "application/json; charset=utf-8"
        response.headers["Content-Disposition"] = \
            f"attachment; filename=accounts_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}.json"
        return response

    if format_type == "txt":
        output = StringIO()
        for a in accounts:
            output.write(f"{a.email or ''}\t{a.password or ''}\t{a.api_key}\t{a.api_key_id or ''}\n")

        response = make_response(output.getvalue())
        response.headers["Content-Type"] = "text/plain; charset=utf-8"
        response.headers["Content-Disposition"] = \
            f"attachment; filename=accounts_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}.txt"
        return response

    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(["邮箱", "密码", "API Key", "API Key ID", "状态", "批次", "绑定卡密", "分配时间", "创建时间"])
    for a in accounts:
        writer.writerow([
            a.email or "",
            a.password or "",
            a.api_key,
            a.api_key_id or "",
            a.status,
            a.batch_id or "",
            a.card.card_key if a.card else "",
            a.assigned_at.isoformat() if a.assigned_at else "",
            a.created_at.isoformat() if a.created_at else "",
        ])

    response = make_response(output.getvalue())
    response.headers["Content-Type"] = "text/csv; charset=utf-8-sig"
    response.headers["Content-Disposition"] = \
        f"attachment; filename=accounts_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}.csv"
    return response


@bp.route("/batches", methods=["GET"])
@jwt_required()
def list_batches():
    """账号池批次列表"""
    batches = db.session.query(Account.batch_id).filter(
        Account.batch_id.isnot(None)
    ).distinct().all()
    return jsonify([b[0] for b in batches])
