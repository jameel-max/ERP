#!/usr/bin/env python3
"""
مشغل سريع للتشغيل المحلي بعد التثبيت:
python manage.py migrate && python manage.py init_system && python manage.py runserver 127.0.0.1:8000
"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')


def run(cmd):
    print('>', ' '.join(cmd))
    subprocess.run(cmd, check=False)


if __name__ == '__main__':
    os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # جوّل المشروع
    run([sys.executable, 'manage.py', 'migrate', '--noinput'])
    run([sys.executable, 'manage.py', 'init_system'])
    run([sys.executable, 'manage.py', 'runserver', '127.0.0.1:8000'])
