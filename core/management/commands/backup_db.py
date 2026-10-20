import os
import shutil
from datetime import datetime
from django.core.management.base import BaseCommand
from django.conf import settings
from django.core.management import call_command


class Command(BaseCommand):
    help = 'أخذ نسخة احتياطية من قاعدة بيانات رويال فروت مع سجل كامل'

    def add_arguments(self, parser):
        parser.add_argument('--output-dir', type=str, default='backups', help='مجلد حفظ النسخ الاحتياطية')

    def handle(self, *args, **options):
        output_dir = os.path.join(settings.BASE_DIR, options['output_dir'])
        os.makedirs(output_dir, exist_ok=True)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

        # نسخ احتياطي لقاعدة البيانات
        db_settings = settings.DATABASES['default']
        engine = db_settings['ENGINE']

        if 'sqlite3' in engine:
            db_path = db_settings['NAME']
            backup_file = os.path.join(output_dir, f"royal_fruit_backup_{timestamp}.sqlite3")
            shutil.copy2(db_path, backup_file)
            self.stdout.write(self.style.SUCCESS(f"تم نسخ قاعدة بيانات SQLite بنجاح: {backup_file}"))
        else:
            backup_file = os.path.join(output_dir, f"royal_fruit_backup_{timestamp}.json")
            with open(backup_file, 'w', encoding='utf-8') as f:
                call_command('dumpdata', indent=2, stdout=f)
            self.stdout.write(self.style.SUCCESS(f"تم تصدير البيانات إلى: {backup_file}"))

        # ملف تعليمات الاستعادة
        readme_file = os.path.join(output_dir, "RESTORE_INSTRUCTIONS.md")
        with open(readme_file, 'w', encoding='utf-8') as f:
            f.write("""# تعليمات استعادة النسخة الاحتياطية - مشغل رويال فروت (Royal Fruit)

## 1. استعادة قاعدة بيانات SQLite:
- أوقف خادم الويب (Django/Gunicorn).
- انسخ ملف النسخة الاحتياطية المطلوب من مجلد `backups/` إلى المسار الرئيسي للمشروع وأعد تسميته إلى `db.sqlite3`.
- شغّل الأمر: `python manage.py migrate`

## 2. استعادة نسخة JSON (DumpData):
- شغّل الأمر:
  `python manage.py loaddata backups/royal_fruit_backup_YYYYMMDD_HHMMSS.json`

## 3. النسخ الاحتياطي التلقائي اليومي (Windows Task Scheduler):
- أنشئ مهمة مجدولة يومياً تشغّل الأمر:
  `python manage.py backup_db`
""")
        self.stdout.write(self.style.SUCCESS(f"تم حفظ تعليمات الاستعادة في: {readme_file}"))
