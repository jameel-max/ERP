#!/usr/bin/env bash
# ============================================================
# تشغيل نظام رويال فروت إنتاجياً على Linux/macOS
# ============================================================
# قبل التشغيل:
#   1) cp deploy/env.example .env   ثم عدّل القيم
#   2) source .venv/bin/activate    ثم  pip install -r requirements.txt
# ============================================================
set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -f ".env" ]; then
  echo "تنبيه: لا يوجد ملف .env - سيتم استخدام القيم الافتراضية (للتجربة فقط)."
fi

echo "1/4 تطبيق الهجرات..."
python manage.py migrate --noinput

echo "2/4 تجميع الملفات الساكنة..."
python manage.py collectstatic --noinput

echo "3/4 فحص إعدادات الإنتاج..."
python manage.py check --deploy

echo "4/4 تشغيل الخادم الإنتاجي..."
exec python scripts/production_server.py
