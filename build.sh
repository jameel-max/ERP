#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# سكربت البناء للنشر على Render.
#
# السبب: كان النشر لا يشغّل الترحيلات (migrations) إطلاقاً، فظهر خطأ HTTP 500
# على /empty-boxes/ بسبب جدول غير موجود:
#   relation "boxes_farmemptyboxconsumption" does not exist
# هذا السكربت يضمن أن كل نشر يُطبّق الترحيلات قبل تشغيل التطبيق.

#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# سكربت البناء للنشر على Render.
# يثبت المتطلبات، ويجهز قاعدة البيانات، وينشئ حسابات الإدارة
# إذا كانت قاعدة البيانات لا تحتوي على أي مستخدمين.
#
# Render: اضبط Build Command على ./build.sh
# ---------------------------------------------------------------------------

set -o errexit
set -o pipefail
set -o nounset

echo "==> تثبيت المتطلبات"
pip install --upgrade pip
pip install -r requirements.txt

echo "==> جمع الملفات الساكنة"
python manage.py collectstatic --no-input

echo "==> تطبيق الترحيلات على قاعدة البيانات"
python manage.py migrate --no-input

echo "==> تهيئة حسابات الإدارة إذا كانت القاعدة بلا مستخدمين"
python manage.py init_system --keep-extra-users --only-if-no-users

echo "==> فحص عدم وجود ترحيلات ناقصة"
python manage.py makemigrations --check --dry-run

echo "==> فحص سلامة Django"
python manage.py check --deploy || python manage.py check

echo "==> تم البناء بنجاح"

