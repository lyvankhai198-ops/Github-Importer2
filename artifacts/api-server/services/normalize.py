"""
normalize.py — Chuẩn hóa dữ liệu từ các API nguồn khác nhau.
"""
import html


def normalize_product_data(raw_item: dict) -> dict:
    """
    Map các tên field khác nhau từ API nguồn về chuẩn nội bộ.
    Hỗ trợ: Zampto Standard, Custom và các API tương tự.
    """
    # ID
    product_id = str(
        raw_item.get("product_id") or raw_item.get("id") or ""
    )

    # Tên
    name = (
        raw_item.get("name") or raw_item.get("title") or ""
    )

    # Mô tả
    description = (
        raw_item.get("description") or raw_item.get("desc") or
        raw_item.get("details") or raw_item.get("content") or
        raw_item.get("note") or ""
    )

    # Giá
    price = float(
        raw_item.get("price") or raw_item.get("unit_price") or
        raw_item.get("amount") or raw_item.get("cost") or 0
    )

    # Tồn kho
    stock = _safe_int(
        raw_item.get("stock") or raw_item.get("quantity") or
        raw_item.get("available") or raw_item.get("inventory") or 0
    )

    # Số lượng tối thiểu/tối đa
    min_qty = _safe_int(
        raw_item.get("min_quantity") or raw_item.get("min_qty") or
        raw_item.get("minimum") or 1
    ) or 1
    max_qty_raw = (
        raw_item.get("max_quantity") or raw_item.get("max_qty") or
        raw_item.get("maximum")
    )
    max_qty = _safe_int(max_qty_raw) if max_qty_raw else None

    # Trạng thái
    status = str(raw_item.get("status") or "active")

    # Ảnh
    image_url = str(
        raw_item.get("image") or raw_item.get("image_url") or
        raw_item.get("thumbnail") or raw_item.get("photo") or ""
    )

    # Bảo hành
    warranty = str(raw_item.get("warranty") or raw_item.get("guarantee") or "")

    # Thời hạn
    duration = str(
        raw_item.get("duration") or raw_item.get("period") or
        raw_item.get("validity") or raw_item.get("expire") or ""
    )

    return {
        "id": product_id,
        "name": name,
        "description": description,
        "price": price,
        "stock": stock,
        "min_quantity": min_qty,
        "max_quantity": max_qty,
        "status": status,
        "image_url": image_url,
        "warranty": warranty,
        "duration": duration,
    }


def normalize_delivery_items(response_json: dict) -> list:
    """
    Trích xuất danh sách tài khoản/sản phẩm giao từ phản hồi API mua hàng.
    Trả về list[dict] với các key: username, password, value, note.
    Tuyệt đối không trả về raw JSON.
    """
    if not response_json:
        return []

    # Tìm order object nếu có
    order_data = response_json.get("order", response_json)

    # Thử các key phổ biến theo thứ tự ưu tiên
    accounts = None
    for key in ["accounts", "items", "data", "result", "credentials", "account"]:
        val = order_data.get(key)
        if val is not None and val != "" and val != [] and val != {}:
            accounts = val
            break

    # Fallback: thử ở root response
    if accounts is None:
        for key in ["accounts", "items", "credentials", "data"]:
            val = response_json.get(key)
            if val is not None and val != "" and val != [] and val != {}:
                accounts = val
                break

    if accounts is None:
        return []

    items = []

    if isinstance(accounts, str):
        lines = [l.strip() for l in accounts.split("\n") if l.strip()]
        for line in lines:
            items.append(_parse_account_string(line))

    elif isinstance(accounts, list):
        for acc in accounts:
            if isinstance(acc, str):
                items.append(_parse_account_string(acc))
            elif isinstance(acc, dict):
                items.append(_parse_account_dict(acc))

    elif isinstance(accounts, dict):
        items.append(_parse_account_dict(accounts))

    return items


def format_delivery_message(order, items: list, product_name: str) -> tuple:
    """
    Tạo tin nhắn giao hàng đẹp cho bot (HTML parse_mode).
    Trả về (text, file_bytes_or_None).
    - ≤10 tài khoản: gửi text với <code> blocks.
    - >10 tài khoản: tạo nội dung file TXT.
    """
    header = (
        f"✅ <b>MUA HÀNG THÀNH CÔNG</b>\n\n"
        f"Mã đơn: <code>{order.order_code}</code>\n"
        f"Sản phẩm: {html.escape(product_name)}\n"
        f"Số lượng: {order.quantity}\n"
        f"Tổng tiền: {order.total_price:,.0f}đ\n\n"
        f"📦 <b>TÀI KHOẢN CỦA BẠN</b>\n"
    )

    lines = []
    for item in items:
        val = _item_display_value(item)
        lines.append(val)

    if len(items) <= 10:
        account_block = "\n".join(f"<code>{html.escape(l)}</code>" for l in lines)
        text = header + "\n" + account_block + "\n\nCảm ơn bạn đã mua hàng! 🙏"
        return text, None
    else:
        # Tạo file TXT
        file_content = f"Đơn hàng: {order.order_code}\nSản phẩm: {product_name}\n"
        file_content += "=" * 40 + "\n"
        file_content += "\n".join(lines)
        account_block = "\n".join(
            f"<code>{html.escape(lines[i])}</code>" for i in range(min(3, len(lines)))
        )
        text = (
            header + "\n" + account_block + "\n"
            f"<i>... và {len(lines) - 3} tài khoản nữa (xem file đính kèm)</i>\n\n"
            "Cảm ơn bạn đã mua hàng! 🙏"
        )
        return text, file_content.encode("utf-8")


def format_partial_delivery_message(order, items: list, product_name: str) -> str:
    delivered = len(items)
    missing = order.quantity - delivered
    external_code = order.external_order_code or order.external_order_id or "—"
    header = (
        f"⚠️ <b>GIAO HÀNG KHÔNG ĐỦ SỐ LƯỢNG</b>\n\n"
        f"Mã đơn: <code>{order.order_code}</code>\n"
        f"Mã đơn nguồn: <code>{external_code}</code>\n"
        f"Đặt: {order.quantity} | Nhận được: {delivered} | Thiếu: {missing}\n\n"
        f"📦 <b>TÀI KHOẢN ĐÃ NHẬN:</b>\n"
    )
    lines = [_item_display_value(item) for item in items]
    account_block = "\n".join(f"<code>{html.escape(l)}</code>" for l in lines)
    return (
        header + "\n" + account_block + "\n\n"
        "⏳ Nguồn đang xử lý phần còn lại. Admin sẽ liên hệ bạn sớm."
    )


# ── Helpers ──────────────────────────────────────────────────────────────────

def _item_display_value(item: dict) -> str:
    if item.get("value"):
        return item["value"]
    u = item.get("username", "")
    p = item.get("password", "")
    if u and p:
        return f"{u}|{p}"
    return u or p or str(item)


def _parse_account_string(s: str) -> dict:
    if "|" in s:
        parts = s.split("|", 1)
        return {
            "username": parts[0].strip(),
            "password": parts[1].strip(),
            "value": s,
            "note": "",
        }
    return {"username": "", "password": "", "value": s, "note": ""}


def _parse_account_dict(d: dict) -> dict:
    username = (
        d.get("email") or d.get("username") or
        d.get("account") or d.get("user") or ""
    )
    password = d.get("password") or d.get("pass") or d.get("pwd") or ""
    value = d.get("key") or d.get("code") or d.get("value") or ""
    note = d.get("note") or d.get("info") or ""
    if not value and username and password:
        value = f"{username}|{password}"
    elif not value and username:
        value = username
    return {
        "username": username,
        "password": password,
        "value": value,
        "note": note,
    }


def _safe_int(val) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return 0
