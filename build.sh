#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# سكربت البناء للنشر على Render.
#
# السبب: كان النشر لا يشغّل الترحيلات (migrations) إطلاقاً، فظهر خطأ HTTP 500
# على /empty-boxes/ بسبب جدول غير موجود:
#   relation "boxes_farmemptyboxconsumption" does not exist
# هذا السكربت يضمن أن كل نشر يُطبّق الترحيلات قبل تشغيل التطبيق.
#
# Render: اضبط Build Command على  ./build.sh
# ---------------------------------------------------------------------------
set -o errexit   # التوقف عند أي خطأ
set -o pipefail
set -o nounset   # رفض المتغيرات غير المعرّفة

echo "==> تثبيت المتطلبات"
pip install --upgrade pip
pip install -r requirements.txt

echo "==> جمع الملفات الساكنة"
python manage.py collectstatic --no-input

echo "==> تطبيق الترحيلات على قاعدة البيانات المُهيّأة"
python manage.py migrate --no-input

echo "==> فحص عدم وجود ترحيلات ناقصة (يجب أن يكون «No changes detected»)"
python manage.py makemigrations --check --dry-run

echo "==> فحص سلامة Django"
python manage.py check --deploy || python manage.py check

echo "==> تم البناء بنجاح"
