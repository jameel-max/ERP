#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# سكربت البناء للنشر على Render.
# يثبت المتطلبات، ويجمع الملفات الساكنة، ويطبق ترحيلات قاعدة البيانات،
# ثم يهيئ حسابات الإدارة فقط إذا كانت قاعدة البيانات بلا مستخدمين.
#
# Render Build Command:
# ./build.sh
# ---------------------------------------------------------------------------

set -o errexit
set -o pipefail
set -o nounset

echo "==> تثبيت المتطلبات"
pip install --upgrade pip
pip install -r requirements.txt

echo "==> جمع الملفات الساكنة"
python manage.py collectstatic --no-input

echo "==> تطبيق ترحيلات قاعدة البيانات"
python manage.py migrate --no-input

echo "==> تهيئة حسابات الإدارة عند عدم وجود مستخدمين فقط"
python manage.py init_system --keep-extra-users --only-if-no-users

echo "==> فحص عدم وجود ترحيلات ناقصة"
python manage.py makemigrations --check --dry-run

echo "==> فحص سلامة Django"
python manage.py check --deploy || python manage.py check

echo "==> اكتمل البناء بنجاح"
