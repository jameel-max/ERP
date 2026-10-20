from django.core.management.base import BaseCommand
from django.conf import settings
from django.db import connection


class Command(BaseCommand):
    help = (
        'يعرض قاعدة البيانات المستخدمة فعلياً وهل الموقع متصل بـ Neon/PostgreSQL، '
        'ويختبر الاتصال. استخدمه للتأكد أن النظام يخزّن البيانات فعلاً في Neon.'
    )

    def handle(self, *args, **options):
        cfg = settings.DATABASES['default']
        engine = cfg['ENGINE']
        is_postgres = 'postgresql' in engine

        self.stdout.write('')
        self.stdout.write(self.style.NOTICE('=== حالة قاعدة البيانات ==='))
        self.stdout.write(f"المحرّك (ENGINE): {engine}")
        self.stdout.write(f"اسم قاعدة البيانات: {cfg.get('NAME')}")
        self.stdout.write(f"المضيف (HOST): {cfg.get('HOST') or '(محلي/ملف)'}")
        self.stdout.write(f"المنفذ (PORT): {cfg.get('PORT') or '-'}")
        self.stdout.write(f"اسم المستخدم: {cfg.get('USER') or '-'}")
        self.stdout.write(f"خيارات الاتصال: {cfg.get('OPTIONS') or {}}")

        if is_postgres:
            host = cfg.get('HOST') or ''
            if 'neon.tech' in host:
                self.stdout.write(self.style.SUCCESS('>> الموقع مضبوط على Neon (console.neon.tech).'))
            else:
                self.stdout.write(self.style.WARNING('>> الموقع مضبوط على PostgreSQL لكن ليس Neon.'))
        else:
            self.stdout.write(self.style.WARNING(
                '>> الموقع يعمل حالياً على SQLite (ملف محلي) وليس Neon.\n'
                '   لإكمال الربط بـ Neon: ضع DATABASE_URL في ملف .env ثم نفّذ migrate.'
            ))

        # اختبار الاتصال الفعلي
        self.stdout.write('')
        self.stdout.write('جارٍ اختبار الاتصال...')
        try:
            with connection.cursor() as cur:
                cur.execute('SELECT 1')
                cur.fetchone()
            vendor = connection.vendor
            self.stdout.write(self.style.SUCCESS(f'الاتصال ناجح ✔ (vendor={vendor})'))
        except Exception as exc:
            self.stdout.write(self.style.ERROR(f'فشل الاتصال ✘: {exc}'))
            self.stdout.write(self.style.WARNING(
                'الخطوة المطلوبة: تأكد من صحة DATABASE_URL في .env ومن إنشاء قاعدة البيانات على Neon.'
            ))
            raise SystemExit(1)
