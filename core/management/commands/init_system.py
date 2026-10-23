from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from decimal import Decimal
from django.utils import timezone
from core.models import SystemSetting

from operations.models import ColdStore, Shelf, Crop, BoxType, Trader, TraderPackingRate

User = get_user_model()


class Command(BaseCommand):
    help = (
        'تهيئة النظام بحسابَي الإدارة (ammar و salah) — كلاهما مالك نظام بصلاحيات كاملة '
        'ومتطابقة — مع إعدادات الشركة، بدون أي بيانات تجريبية. '
        'استخدم --demo لإضافة بيانات تجريبية للعرض/الاختبار.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--demo',
            action='store_true',
            help='إنشاء بيانات تجريبية (برادات، أصناف بضاعة، أنواع صناديق، تاجر افتراضي). '
                 'لا تستخدمها في الإنتاج — الافتراضي هو نظام نظيف.',
        )

        parser.add_argument(
            '--keep-extra-users',
            action='store_true',
            help='الاحتفاظ بالمستخدمين الإضافيين وعدم حذفهم.',
        )

        parser.add_argument(
            '--only-if-no-users',
            action='store_true',
            help='تهيئة حسابات الإدارة فقط إذا لم يوجد أي مستخدم.',
        )



    def handle(self, *args, **options):
        self.stdout.write(self.style.NOTICE('بدء تهيئة النظام...'))

        # 1. إعدادات النظام (إعداد شركة، وليست بيانات تجريبية)
        #    الأسعار والتكاليف لا تُفرض هنا: تبقى على قيمها الافتراضية في النموذج
        #    ويمكن تعديلها لاحقاً من صفحة إعدادات النظام.
        settings_obj = SystemSetting.get_settings()
        settings_obj.company_name_en = 'Royal Fruit'
        settings_obj.company_name_ar = 'رويال فروت'
        settings_obj.brand_color = '#1B4D3E'
        settings_obj.save()
        self.stdout.write(self.style.SUCCESS('تم تحديث إعدادات الشركة: Royal Fruit (أخضر غامق).'))

        # 2) حسابا الإدارة: ammar و salah
        #    كلاهما بصلاحية "مالك النظام" وبصلاحيات كاملة متطابقة تماماً.
        ADMIN_ACCOUNTS = [
            ('ammar', 'ammar123'),
            ('salah', 'salah123'),
        ]
        admin_pks = []
        for username, password in ADMIN_ACCOUNTS:
            user, created = User.objects.get_or_create(
                username=username,
                defaults={
                    'email': f'{username}@royalfruit.com',
                    'role': User.Role.OWNER,
                    'is_staff': True,
                    'is_superuser': True,
                    'is_active': True,
                },
            )
            # توحيد الصلاحيات: كلاهما مالك كامل الصلاحية بغض النظر عن حالته السابقة.
            user.role = User.Role.OWNER
            user.is_staff = True
            user.is_superuser = True
            user.is_active = True
            if created:
                user.set_password(password)
            user.save()
            admin_pks.append(user.pk)
            if created:
                self.stdout.write(self.style.SUCCESS(f'تم إنشاء حساب الإدارة: {username} / {password}'))
            else:
                self.stdout.write(self.style.NOTICE(f'حساب الإدارة موجود مسبقاً وتم توحيد صلاحياته: {username}'))

        # 3) يبقى النظام بحسابَي الإدارة فقط (ammar و salah) بصلاحيات متطابقة وكاملة.
        extra = User.objects.exclude(pk__in=admin_pks)
        extra_count = extra.count()
        if extra_count:
            extra.delete()
            self.stdout.write(self.style.WARNING(
                f'تم حذف {extra_count} حساب مستخدم إضافي — النظام يبقى بحسابَي الإدارة فقط.'
            ))
        self.stdout.write(self.style.SUCCESS('حسابا الإدارة (بصلاحيات كاملة متطابقة): ammar و salah'))

        # 4) تصنيفات المصاريف الافتراضية (إعداد جاهز للمحاسبة — يبقى قابلاً للتعديل/الحذف)
        from finance.models import ExpenseCategory
        DEFAULT_EXPENSE_CATEGORIES = [
            ('كهرباء وماء', 'UTIL'),
            ('إيجار', 'RENT'),
            ('محروقات', 'FUEL'),
            ('صيانة وإصلاح', 'MAINT'),
            ('نقل وشحن', 'TRANS'),
            ('مواد تعبئة وتغليف', 'PACK'),
            ('أجور عمالة', 'LABOR'),
            ('رسوم حكومية ورخص', 'GOV'),
            ('اتصالات وإنترنت', 'TELCO'),
            ('مصاريف إدارية ومكتبية', 'ADMIN'),
            ('نظافة ومستلزمات', 'CLEAN'),
            ('مصاريف أخرى', 'OTHER'),
        ]
        created_cats = 0
        for cat_name, cat_code in DEFAULT_EXPENSE_CATEGORIES:
            _, was_created = ExpenseCategory.objects.get_or_create(
                name=cat_name, defaults={'code': cat_code}
            )
            if was_created:
                created_cats += 1
        if created_cats:
            self.stdout.write(self.style.SUCCESS(f'تم إنشاء {created_cats} تصنيف مصاريف افتراضي.'))
        else:
            self.stdout.write(self.style.NOTICE('تصنيفات المصاريف الافتراضية موجودة مسبقاً.'))

        # ============================================================
        # البيانات التجريبية — تُنشأ فقط عند تمرير --demo
        # (لا تُنشأ تلقائياً حتى لا تظهر أسماء/بيانات وهمية في نظام نظيف)
        # ============================================================
        if not options.get('demo'):
            self.stdout.write(self.style.WARNING(
                'تم تخطي البيانات التجريبية (الافتراضي: نظام نظيف). '
                'استخدم --demo إن أردت بيانات تجريبية للعرض.'
            ))
            self.stdout.write(self.style.SUCCESS('اكتملت تهيئة النظام بنجاح! (بدون بيانات تجريبية)'))
            return

        self.stdout.write(self.style.NOTICE('وضع البيانات التجريبية (--demo) مُفعّل...'))

        # برادات وأسياخ
        cs1, _ = ColdStore.objects.get_or_create(
            code='CS-01',
            defaults={'name': 'البراد الأول', 'capacity_pallets': 50, 'current_temperature': Decimal('3.0')}
        )
        cs2, _ = ColdStore.objects.get_or_create(
            code='CS-02',
            defaults={'name': 'البراد الثاني', 'capacity_pallets': 50, 'current_temperature': Decimal('4.0')}
        )
        for store in (cs1, cs2):
            # المعتمد: 4 أسياخ لكل براد
            for n in range(1, 5):
                Shelf.objects.get_or_create(
                    cold_store=store,
                    shelf_number=str(n),
                    defaults={'capacity_pallets': 25},
                )
        self.stdout.write(self.style.SUCCESS('تم إنشاء البرادات (الأول والثاني) مع 4 أسياخ لكل براد.'))

        # أصناف البضاعة
        for c in ['نكتارين', 'مشمش', 'خوخ', 'دراق', 'عنب']:
            Crop.objects.get_or_create(name=c)
        self.stdout.write(self.style.SUCCESS('تم إنشاء أنواع البضاعة التجريبية.'))

        # أنواع الصناديق
        boxes_data = [
            {'name': 'عكاز أزرق 8KG', 'color_or_type': 'أزرق', 'tare_weight': Decimal('0.800'), 'discount_price': Decimal('1.500')},
            {'name': 'عكاز أخضر', 'color_or_type': 'أخضر', 'tare_weight': Decimal('0.800'), 'discount_price': Decimal('1.500')},
            {'name': 'عكاز نهدي', 'color_or_type': 'نهدي', 'tare_weight': Decimal('0.800'), 'discount_price': Decimal('1.500')},
            {'name': 'عكاز أصفر', 'color_or_type': 'أصفر', 'tare_weight': Decimal('0.800'), 'discount_price': Decimal('1.500')},
            {'name': 'تسنيم ربع حقل', 'color_or_type': 'تسنيم', 'tare_weight': Decimal('0.750'), 'discount_price': Decimal('1.250')},
        ]
        for b in boxes_data:
            BoxType.objects.get_or_create(
                name=b['name'],
                defaults={
                    'color_or_type': b['color_or_type'],
                    'tare_weight': b['tare_weight'],
                    'discount_price': b['discount_price'],
                    'company_stock': 1000
                }
            )
        self.stdout.write(self.style.SUCCESS('تم إنشاء أنواع الصناديق التجريبية.'))

        # تاجر افتراضي
        trader, created = Trader.objects.get_or_create(
            code='T001',
            defaults={
                'name': 'ابو معتز القيسي',
                'phone': '0790000001',
                'notes': 'تاجر تجريبي - مورد نكتارين ومشمش'
            }
        )
        if created:
            # أسعار التوضيب التجريبية تُؤخذ من السعر الافتراضي في إعدادات النظام لكل طريقة
            settings_obj = SystemSetting.get_settings()
            TraderPackingRate.objects.get_or_create(
                trader=trader,
                method=TraderPackingRate.Method.MACHINE,
                defaults={'rate_per_kg': settings_obj.default_packing_rate_per_kg, 'effective_date': timezone.localdate()}
            )
            TraderPackingRate.objects.get_or_create(
                trader=trader,
                method=TraderPackingRate.Method.MANUAL,
                defaults={'rate_per_kg': settings_obj.default_manual_packing_rate_per_kg, 'effective_date': timezone.localdate()}
            )
            self.stdout.write(self.style.SUCCESS('تم إنشاء التاجر التجريبي: ابو معتز القيسي مع أسعار التوضيب.'))

        self.stdout.write(self.style.SUCCESS('اكتملت تهيئة النظام بنجاح! (مع بيانات تجريبية)'))
