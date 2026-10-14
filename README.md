# Super Productivity - Django Backend & Sync Server

Bu backend Super Productivity loyihasi uchun maxsus ishlab chiqilgan Django server hisoblanadi.

## Imkoniyatlari
1. **WebDAV Sinxronizatsiyasi (RFC 4918):** Super Productivity ilovasining barcha vazifalari, loyihalari va vaqt hisoblarini to'liq sinxronlash (`PROPFIND`, `GET`, `PUT`, `DELETE`, `MKCOL`, `OPTIONS`).
2. **REST API:**
   - `GET /api/status/` - Server holati va statistikasi
   - `GET /api/tasks/` - Sinxronlangan vazifalarni JSON ko'rinishida olish
3. **Django Admin:** `http://127.0.0.1:8000/admin/` orqali sinxronlangan fayllar va foydalanuvchilarni boshqarish.
4. **Frontend Serving:** Pre-built Angular frontendni to'g'ridan-to'g'ri `http://127.0.0.1:8000/` orqali uzatish.

## Ishga tushirish

```bash
cd django_backend
source venv/bin/activate
python manage.py runserver 127.0.0.1:8000
```

## Kirish ma'lumotlari
- **Admin login:** `admin`
- **Admin parol:** `admin123`

## Super Productivity bilan ulash
Ilova ichida:
1. **Settings** (Sozlamalar) -> **Sync & Export** bo'limiga o'ting.
2. **Sync Provider** sifatida **WebDAV** ni tanlang.
3. Parametrlarni kiriting:
   - **Base URL:** `http://127.0.0.1:8000/webdav/`
   - **User name:** `admin`
   - **Password:** `admin123`
   - **Sync folder path:** `/`
4. **Test connection** tugmasini bosing -> Muvaffaqiyatli bog'lanadi!

## Telegram bot (ovozli task + Gemini)

Bot ovozli/matnli xabarni Gemini orqali vazifaga aylantiradi va to'g'ridan-to'g'ri
SP sync fayliga (`sync-data.json`) yozadi — mos projectga (topilmasa Inbox'ga).
Inline tugmalar: Bajarildi / O'chirish / Boshqa projectga ko'chirish.

### `.env` (django_backend/.env)
```
TELEGRAM_BOT_TOKEN=123456:ABC...        # @BotFather
TELEGRAM_WEBHOOK_SECRET=<maxfiy-satr>   # masalan: openssl rand -base64 24
# Gemini: bitta yoki bir nechta kalit (vergul bilan) — limit (429) da avtomatik almashtiriladi
GEMINI_API_KEYS=AIza_key1,AIza_key2     # yoki bitta: GEMINI_API_KEY=AIza...
# Modellar prioriteti (vergul bilan): avval barcha kalitlarda 1-model, keyin 2-model
GEMINI_MODELS=gemini-flash-latest,gemini-flash-lite-latest
PUBLIC_BASE_URL=https://<user>.pythonanywhere.com
SP_SYNC_USERNAME=admin                  # bot qaysi WebDAV akkauntga yozadi
SP_SYNC_FILE_PATH=/sync-data.json
SP_SYNC_ENCRYPTION_PASSWORD=            # agar SP'da sync encryption YOQILGAN bo'lsa
SP_BOT_CLIENT_ID=tgbot0                 # op-log vector-clock uchun barqaror id
```

**Rotation:** `GEMINI_API_KEYS` × `GEMINI_MODELS` dan nomzodlar ro'yxati tuziladi
(model-major: `k1·model1, k2·model1, k1·model2, k2·model2`). Bir juftlik 429 (limit)
bersa, u `retryDelay` / kunlik kvota muddatiga cooldown'ga qo'yilib (holat bazada —
`GeminiQuota`), keyingisiga o'tiladi. Hammasi band bo'lsagina foydalanuvchiga xato
qaytadi.

### O'rnatish (server konsolida, virtualenv ichida)
```
pip install -r requirements.txt
python manage.py set_telegram_webhook      # webhookni ro'yxatdan o'tkazadi
```
Webhook endpoint: `POST /telegram/webhook/<secret>/` (secret `.env`dan; Telegram
`X-Telegram-Bot-Api-Secret-Token` header orqali ham tekshiriladi).

> Eslatma: bot ishlashi uchun SP sync **v2** formatida bo'lishi kerak (standart).
> Sync encryption yoqilgan bo'lsa, `SP_SYNC_ENCRYPTION_PASSWORD` to'g'ri bo'lishi
> shart (bot faylni o'sha parol bilan ochib/qayta shifrlaydi).
