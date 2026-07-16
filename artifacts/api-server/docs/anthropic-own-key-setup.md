# Dùng API key Anthropic riêng cho dịch mô tả (VPS ít RAM)

Không cần chạy thêm dịch vụ nào trên VPS — chỉ là một cuộc gọi HTTP ra
ngoài mỗi khi có mô tả cần dịch, phù hợp với VPS RAM thấp.

## 1. Lấy API key

1. Vào https://console.anthropic.com → tạo API key (`sk-ant-...`).
2. Nạp credit vào tài khoản (chi phí dịch rất nhỏ).

## 2. Biến môi trường cần đặt trên VPS

```
AI_INTEGRATIONS_ANTHROPIC_BASE_URL=https://api.anthropic.com
AI_INTEGRATIONS_ANTHROPIC_API_KEY=sk-ant-xxxxxxxxxxxxxxxx
TRANSLATION_ANTHROPIC_MODEL=claude-3-5-haiku-20241022
```

**Quan trọng — `TRANSLATION_ANTHROPIC_MODEL` không được bỏ qua.** Mặc định
code dùng model id `claude-haiku-4-5`, đây là một *tên rút gọn* chỉ Replit
proxy hiểu được — API thật của Anthropic không nhận dạng tên này, gọi sẽ
báo lỗi 400 và bot tự âm thầm rơi về bộ dịch từ điển yếu (chính là lỗi bạn
đang gặp). Khi dùng key thật của Anthropic, luôn đặt biến này thành một
model id đầy đủ như trên.

## 3. Restart bot

```bash
systemctl restart aicenter
```

## 4. Chẩn đoán nhanh nếu vẫn không dịch được

Chạy trực tiếp trên VPS (đường dẫn đổi theo máy bạn) để xem lỗi thật:

```bash
cd /path/to/artifacts/api-server
python3 -c "
import logging
logging.basicConfig(level=logging.INFO)
from services.translation_service import translate_description_to_english
print('RESULT:', translate_description_to_english('Xin chào, đây là bản test dịch thử.'))
"
```

- Nếu in ra câu tiếng Anh → đã hoạt động, chỉ cần chạy lại script dịch lại
  sản phẩm cũ (xem `libretranslate-setup.md`, mục cuối).
- Nếu in ra `RESULT: None` và log phía trên có dòng `HTTP 400` /
  `HTTP 401` / `HTTP 404` kèm nội dung lỗi → gửi lại đúng dòng log đó, tôi
  sẽ chẩn đoán chính xác (401 = key sai, 404/400 model không tồn tại =
  quên đặt `TRANSLATION_ANTHROPIC_MODEL`, v.v).
