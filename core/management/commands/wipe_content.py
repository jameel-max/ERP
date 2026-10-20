from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth import get_user_model

from core.wipe_service import wipe_database, CONFIRM_PHRASE

User = get_user_model()


class Command(BaseCommand):
    help = (
        'تفريغ محتوى قاعدة البيانات (حذف البيانات). لا رجعة فيه.\n'
        'النماذج:\n'
        '  --mode operations  (الافتراضي) يحذف كل الحركات ويُبقي البيانات المرجعية\n'
        '  --mode full        يحذف كل شيء ما عدا المستخدمين وإعدادات النظام\n'
        f'يتطلب كتابة عبارة التأكيد "{CONFIRM_PHRASE}" حرفيًا عبر --confirm.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--mode', choices=['operations', 'full'], default='operations',
            help='نموذج التفريغ: operations (حركات فقط) أو full (كل شيء).',
        )
        parser.add_argument(
            '--confirm', default='',
            help=f'عبارة التأكيد الحرفية: "{CONFIRM_PHRASE}"',
        )
        parser.add_argument(
            '--user', default='',
            help='اسم المستخدم الذي سيُسجَّل في سجل التدقيق (اختياري؛ الافتراضي: أول مالك نظام).',
        )

    def handle(self, *args, **options):
        confirm = (options.get('confirm') or '').strip()
        if confirm != CONFIRM_PHRASE:
            raise CommandError(
                f'عبارة التأكيد غير صحيحة. مرّر --confirm "{CONFIRM_PHRASE}" حرفيًا لتنفيذ التفريغ.'
            )

        mode = 'FULL' if options['mode'] == 'full' else 'OPERATIONS'

        user = None
        if options.get('user'):
            user = User.objects.filter(username=options['user']).first()
            if user is None:
                raise CommandError(f'لا يوجد مستخدم بالاسم: {options["user"]}')
        if user is None:
            user = User.objects.filter(role=User.Role.OWNER).first() or User.objects.filter(is_superuser=True).first()

        deleted, total = wipe_database(user, mode, confirm)

        self.stdout.write(self.style.WARNING(f'تم التفريغ — النموذج: {mode}'))
        for label, count in deleted:
            self.stdout.write(f'  - {label}: {count}')
        self.stdout.write(self.style.SUCCESS(f'إجمالي السجلات المحذوفة: {total}'))
