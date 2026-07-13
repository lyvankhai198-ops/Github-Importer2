"""
normalize.py — Chuẩn hóa dữ liệu từ các API nguồn khác nhau.
"""
import html
import re


def format_vnd(value) -> str:
    """
    Format a number as Vietnamese-style VND: integer, dot as thousands
    separator (e.g. 5000 -> "5.000"). Never includes decimals or a comma.
    Caller appends the "đ" suffix.
    """
    try:
        return f"{float(value or 0):,.0f}".replace(",", ".")
    except (TypeError, ValueError):
        return "0"


def format_usdt(value) -> str:
    """
    Format a number as a USDT amount with 2 decimals (e.g. 2.0800 -> "2.08").
    Caller appends the "USDT" suffix.
    """
    try:
        return f"{float(value or 0):.2f}"
    except (TypeError, ValueError):
        return "0.00"


def compute_price_usdt(sale_price_vnd, rate: float) -> float:
    """
    Convert a VND retail price to USDT using the given VND-per-USDT rate.
    Rounded to 2 decimals for display. Returns 0.0 on invalid input.
    """
    try:
        rate = float(rate or 0)
        if rate <= 0:
            return 0.0
        return round(float(sale_price_vnd or 0) / rate, 2)
    except (TypeError, ValueError):
        return 0.0


# ── Vietnamese shorthand → English translation table ────────────────────────
# Used to auto-translate warranty/duration/name shorthand codes commonly typed
# by admins (e.g. "BHF", "BH 30D", "Ngày") wherever product text is rendered
# to English-language shoppers. Order matters: longer/more specific patterns
# (BH <N> D/M/Y) must be matched before the bare "BHF"/"KBH" codes.
_WARRANTY_PATTERNS = [
    (re.compile(r"\bBH\s*(\d+)\s*D\b", re.IGNORECASE), lambda m: f"{m.group(1)}-Day Warranty"),
    (re.compile(r"\bBH\s*(\d+)\s*M\b", re.IGNORECASE), lambda m: f"{m.group(1)}-Month Warranty"),
    (re.compile(r"\bBH\s*(\d+)\s*Y\b", re.IGNORECASE), lambda m: f"{m.group(1)}-Year Warranty"),
    (re.compile(r"\bBHF\b", re.IGNORECASE), "Full Warranty"),
    (re.compile(r"\bKBH\b", re.IGNORECASE), "No Warranty"),
    (re.compile(r"\bAdd\s*Fam\b", re.IGNORECASE), "Add Family"),
    (re.compile(r"\bSlot\b", re.IGNORECASE), "Shared Slot"),
    (re.compile(r"\bKey\b", re.IGNORECASE), "License Key"),
    (re.compile(r"\bAPI\b", re.IGNORECASE), "API"),
    (re.compile(r"\bTeam\b", re.IGNORECASE), "Team"),
    (re.compile(r"\bRandom\b", re.IGNORECASE), "Random"),
    (re.compile(r"\bCredit\b", re.IGNORECASE), "Credit"),
    (re.compile(r"\bNg[aà]y\b", re.IGNORECASE), "Days"),
    (re.compile(r"\bTh[aá]ng\b", re.IGNORECASE), "Months"),
    (re.compile(r"\bN[aă]m\b", re.IGNORECASE), "Years"),
]


def translate_shorthand_to_en(text: str) -> str:
    """
    Apply the fixed Vietnamese-shorthand → English translation table to a
    warranty/duration/name string (e.g. "BHF" -> "Full Warranty",
    "BH 30D" -> "30-Day Warranty", "Ngày" -> "Days"). Safe to call on text
    that already has no matches — it is returned unchanged.
    """
    if not text:
        return text
    result = text
    for pattern, repl in _WARRANTY_PATTERNS:
        result = pattern.sub(repl, result)
    return result


# ── Phrase-level Vietnamese → English dictionary for full descriptions ──────
# Longer/more specific phrases must be listed (and therefore matched) before
# the shorter phrases they contain, e.g. "không đổi mail" before "đổi mail".
_DESCRIPTION_PHRASE_PATTERNS = [
    (re.compile(r"tk\s*\|\s*mk", re.IGNORECASE), "username|password"),
    (re.compile(r"kh[oô]ng\s+đổi\s+mail", re.IGNORECASE), "do not change the email"),
    (re.compile(r"đổi\s+mail", re.IGNORECASE), "change the email"),
    (re.compile(r"kh[oô]ng\s+bật\s+2fa", re.IGNORECASE), "do not enable 2FA"),
    (re.compile(r"bật\s+2fa", re.IGNORECASE), "enable 2FA"),
    (re.compile(r"kh[oô]ng\s+link\s+ho[aặ]c\s+gỡ", re.IGNORECASE), "do not link or remove the account from"),
    (re.compile(r"link\s+ho[aặ]c\s+gỡ", re.IGNORECASE), "link or remove the account from"),
    (re.compile(r"bảo\s+h[aà]nh\s+full", re.IGNORECASE), "full warranty"),
    (re.compile(r"kh[oô]ng\s+bảo\s+h[aà]nh", re.IGNORECASE), "no warranty"),
    (re.compile(r"bảo\s+h[aà]nh", re.IGNORECASE), "warranty"),
    (re.compile(r"hạn\s+sử\s+dụng", re.IGNORECASE), "duration"),
    (re.compile(r"tài\s+khoản", re.IGNORECASE), "account"),
    (re.compile(r"đăng\s+nhập", re.IGNORECASE), "log in"),
    (re.compile(r"thiết\s+bị", re.IGNORECASE), "device"),
    (re.compile(r"định\s+dạng", re.IGNORECASE), "format"),
    (re.compile(r"gói", re.IGNORECASE), "package"),
    (re.compile(r"vui\s+l[oò]ng\s+đọc\s+kỹ\s+m[oô]\s+tả\s+trước\s+khi\s+mua", re.IGNORECASE),
     "please read the description carefully before buying"),
    (re.compile(r"m[oô]\s+tả", re.IGNORECASE), "description"),
]


def translate_product_name_to_en(name: str) -> str:
    """
    Auto-generate an English product name from a Vietnamese one using the
    fixed warranty/duration shorthand table (BHF -> Full Warranty,
    3 Tháng -> 3 Months, etc). Used to fill Product.name_en when the admin
    hasn't supplied one — never called once name_en_locked is set.
    """
    return translate_shorthand_to_en(name or "")


def normalize_and_translate_description(description: str) -> str:
    """
    Auto-generate an English product description from a Vietnamese one:
    applies the phrase-level dictionary (tk|mk -> username|password, bảo
    hành -> warranty, ...) followed by the warranty/duration shorthand
    table. Used to fill Product.description_en when the admin hasn't
    supplied one — never called once description_en_locked is set.
    """
    if not description:
        return description
    result = description
    for pattern, repl in _DESCRIPTION_PHRASE_PATTERNS:
        result = pattern.sub(repl, result)
    result = translate_shorthand_to_en(result)
    return result


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


def format_delivery_message(order, items: list, product_name: str, lang: str = "vi") -> tuple:
    """
    Tạo tin nhắn giao hàng đẹp cho bot (HTML parse_mode).
    Trả về (text, file_bytes_or_None).
    - ≤10 tài khoản: gửi text với <code> blocks.
    - >10 tài khoản: tạo nội dung file TXT.
    """
    if lang == "en":
        product = getattr(order, "product", None)
        if product is not None:
            total_str = f"{format_usdt(product.price_usdt * order.quantity)} USDT"
        else:
            total_str = f"{format_vnd(order.total_price)} VND"
        header = (
            f"✅ <b>PURCHASE SUCCESSFUL</b>\n\n"
            f"Order: <code>{order.order_code}</code>\n"
            f"Product: {html.escape(product_name)}\n"
            f"Quantity: {order.quantity}\n"
            f"Total: {total_str}\n\n"
            f"📦 <b>YOUR ACCOUNTS</b>\n"
        )
        thanks = "Thank you for your purchase! 🙏"
        more_suffix = "more account(s) (see attached file)"
        file_order_label = "Order"
        file_product_label = "Product"
    else:
        header = (
            f"✅ <b>MUA HÀNG THÀNH CÔNG</b>\n\n"
            f"Mã đơn: <code>{order.order_code}</code>\n"
            f"Sản phẩm: {html.escape(product_name)}\n"
            f"Số lượng: {order.quantity}\n"
            f"Tổng tiền: {format_vnd(order.total_price)}đ\n\n"
            f"📦 <b>TÀI KHOẢN CỦA BẠN</b>\n"
        )
        thanks = "Cảm ơn bạn đã mua hàng! 🙏"
        more_suffix = "tài khoản nữa (xem file đính kèm)"
        file_order_label = "Đơn hàng"
        file_product_label = "Sản phẩm"

    lines = []
    for item in items:
        val = _item_display_value(item)
        lines.append(val)

    if len(items) <= 10:
        account_block = "\n".join(f"<code>{html.escape(l)}</code>" for l in lines)
        text = header + "\n" + account_block + "\n\n" + thanks
        return text, None
    else:
        # Tạo file TXT
        file_content = f"{file_order_label}: {order.order_code}\n{file_product_label}: {product_name}\n"
        file_content += "=" * 40 + "\n"
        file_content += "\n".join(lines)
        account_block = "\n".join(
            f"<code>{html.escape(lines[i])}</code>" for i in range(min(3, len(lines)))
        )
        text = (
            header + "\n" + account_block + "\n"
            f"<i>... {'and ' if lang == 'en' else 'và '}{len(lines) - 3} {more_suffix}</i>\n\n"
            + thanks
        )
        return text, file_content.encode("utf-8")


def format_partial_delivery_message(order, items: list, product_name: str, lang: str = "vi") -> str:
    delivered = len(items)
    missing = order.quantity - delivered
    external_code = order.external_order_code or order.external_order_id or "—"
    if lang == "en":
        header = (
            f"⚠️ <b>INCOMPLETE DELIVERY</b>\n\n"
            f"Order: <code>{order.order_code}</code>\n"
            f"Source order: <code>{external_code}</code>\n"
            f"Ordered: {order.quantity} | Received: {delivered} | Missing: {missing}\n\n"
            f"📦 <b>ACCOUNTS RECEIVED:</b>\n"
        )
        footer = "⏳ The source is processing the remainder. Admin will contact you soon."
    else:
        header = (
            f"⚠️ <b>GIAO HÀNG KHÔNG ĐỦ SỐ LƯỢNG</b>\n\n"
            f"Mã đơn: <code>{order.order_code}</code>\n"
            f"Mã đơn nguồn: <code>{external_code}</code>\n"
            f"Đặt: {order.quantity} | Nhận được: {delivered} | Thiếu: {missing}\n\n"
            f"📦 <b>TÀI KHOẢN ĐÃ NHẬN:</b>\n"
        )
        footer = "⏳ Nguồn đang xử lý phần còn lại. Admin sẽ liên hệ bạn sớm."
    lines = [_item_display_value(item) for item in items]
    account_block = "\n".join(f"<code>{html.escape(l)}</code>" for l in lines)
    return header + "\n" + account_block + "\n\n" + footer


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
