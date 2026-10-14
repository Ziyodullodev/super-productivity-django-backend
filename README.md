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
