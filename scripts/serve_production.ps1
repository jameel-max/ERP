# ============================================================
# تشغيل نظام رويال فروت إنتاجياً على Windows
# ============================================================
# قبل التشغيل:
#   1) انسخ ملف deploy/env.example إلى .env في جذر المشروع وعدّل القيم
#   2) فعّل البيئة الافتراضية:  .\.venv\Scripts\Activate.ps1
#   3) ثبّت التبعيات:           pip install -r requirements.txt
# ============================================================

$ErrorActionPreference = "Stop"

if (-not (Test-Path ".env")) {
    Write-Host "تنبيه: لا يوجد ملف .env - سيتم استخدام القيم الافتراضية (للتجربة فقط)." -ForegroundColor Yellow
}

Write-Host "1/4 تطبيق الهجرات..." -ForegroundColor Cyan
python manage.py migrate --noinput

Write-Host "2/4 تجميع الملفات الساكنة..." -ForegroundColor Cyan
python manage.py collectstatic --noinput

Write-Host "3/4 فحص إعدادات الإنتاج..." -ForegroundColor Cyan
python manage.py check --deploy

Write-Host "4/4 تشغيل الخادم الإنتاجي..." -ForegroundColor Cyan
python scripts/production_server.py
