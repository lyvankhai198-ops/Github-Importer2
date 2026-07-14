# Tự host LibreTranslate cho dịch mô tả sản phẩm (VPS production)

Bot đã hỗ trợ sẵn LibreTranslate — chỉ cần chạy LibreTranslate trên VPS và khai
báo 1 biến môi trường, không cần sửa code (`services/translation_service.py`
tự động thử LibreTranslate trước, nếu không có/không phản hồi mới rơi xuống
các phương án khác).

## 1. Cài Docker (nếu VPS chưa có)

```bash
curl -fsSL https://get.docker.com | sh
```

## 2. Chạy LibreTranslate — chỉ tải 2 ngôn ngữ vi/en để nhẹ RAM/dung lượng

```bash
docker run -d \
  --name libretranslate \
  --restart unless-stopped \
  -p 5000:5000 \
  -e LT_LOAD_ONLY=en,vi \
  -e LT_DISABLE_WEB_UI=true \
  -v libretranslate_models:/home/libretranslate/.local \
  libretranslate/libretranslate
```

- Lần đầu chạy sẽ tự tải model dịch vi<->en (vài trăm MB), có thể mất vài phút.
- Kiểm tra đã sẵn sàng: `curl http://localhost:5000/languages` — phải trả JSON có "en" và "vi".
- Yêu cầu RAM còn trống tối thiểu ~1–2GB cho container này.

## 3. Khai báo biến môi trường cho bot

Thêm vào file env mà service `aicenter` (systemd) đang đọc (ví dụ
`/etc/aicenter.env` hoặc trong `EnvironmentFile=` của unit file):

```
LIBRETRANSLATE_URL=http://localhost:5000
```

(Không cần đặt `LIBRETRANSLATE_API_KEY` vì container chạy mặc định không yêu
cầu key. `TRANSLATION_PROVIDER` không cần đặt, mặc định là "auto" đã tự ưu
tiên LibreTranslate trước.)

## 4. Khởi động lại bot

```bash
systemctl restart aicenter
```

## 5. Kiểm tra

Vào 1 sản phẩm bất kỳ trên bot, gõ `/language` để chuyển English, xem mô tả
đã dịch đầy đủ (không còn sót tiếng Việt) chưa. Nếu LibreTranslate lỗi/không
phản hồi, bot sẽ tự rơi xuống bộ dịch từ điển đơn giản (không crash) — kiểm
tra `docker logs libretranslate` nếu vẫn thấy dịch kém.

## Dịch lại các sản phẩm đã có mô tả tiếng Anh cũ (dịch kém)

Các sản phẩm đã có `description_en` từ trước (dịch bằng bộ từ điển đơn giản)
sẽ KHÔNG tự dịch lại — hệ thống chỉ dịch khi mô tả gốc thay đổi hoặc chưa có
bản dịch. Để buộc dịch lại toàn bộ sau khi bật LibreTranslate, chạy trên VPS
(sau khi đã cấu hình `LIBRETRANSLATE_URL` và restart xong):

```bash
cd /path/to/artifacts/api-server
python3 -c "
from database import SessionLocal
from models import Product
from services.product_sync import sync_translations

db = SessionLocal()
products = db.query(Product).filter(
    Product.source_language == 'vi',
    Product.translation_status == 'translated',
    Product.description_en_locked != True,
).all()
print(f'Re-translating {len(products)} products...')
for p in products:
    sync_translations(p)
    db.commit()
    print('OK:', p.id, p.name)
db.close()
"
```

Script này bỏ qua các sản phẩm mà admin đã tự sửa tay `description_en`
(khoá `description_en_locked`).
