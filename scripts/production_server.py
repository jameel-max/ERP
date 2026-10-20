"""تشغيل نظام رويال فروت إنتاجياً عبر Waitress (WSGI) على Windows أو Linux.

الاستخدام:
    python scripts/production_server.py

المتغيرات (تُقرأ من البيئة أو من ملف .env في جذر المشروع):
    SERVER_HOST     العنوان (افتراضي 0.0.0.0)
    SERVER_PORT     المنفذ (افتراضي 8000)
    SERVER_THREADS  عدد الخيوط (افتراضي 8)

يُنصح بتشغيله خلف وكيل عكسي (nginx/IIS) مع HTTPS في الإنتاج.
"""
import os
import sys

# السماح بتشغيل السكربت من جذر المشروع مباشرة
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

from waitress import serve  # noqa: E402
from config.wsgi import application  # noqa: E402


def main():
    host = os.environ.get('SERVER_HOST', '0.0.0.0')
    port = int(os.environ.get('SERVER_PORT', '8000'))
    threads = int(os.environ.get('SERVER_THREADS', '8'))
    print(f'Royal Fruit ERP -> http://{host}:{port} (threads={threads})')
    serve(application, host=host, port=port, threads=threads)


if __name__ == '__main__':
    main()
