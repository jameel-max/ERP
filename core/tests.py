from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from decimal import Decimal
from unittest import mock

from core.models import User, AuditLog, Notification, SystemSetting
from operations.models import Trader, Crop, BoxType, ColdStore, Shelf, TruckReceipt, Pallet
from operations.services import ReceptionService
from packing.models import PackingSession, PackedPallet
from invoicing.models import Invoice
from finance.models import TreasuryAccount, ExpenseCategory, Expense, Check, Payment
from finance.services import FinanceService
from labor.models import Workshop
from labor.services import LaborService
from boxes.models import TraderBoxBalance, FarmBoxType


class Phase3CoreTests(TestCase):
    """
    اختبارات النظام العامة: الصلاحيات، الترابط الذري، وصحة الصفحات.
    (القاعدة 20: فشل خطوة لا يترك تحديثات جزئية)
    """

    def setUp(self):
        self.settings = SystemSetting.get_settings()
        self.settings.save()
        self.manager = User.objects.create_user(
            username='core_manager', password='pass', role=User.Role.MANAGER
        )
        self.accountant = User.objects.create_user(
            username='core_accountant', password='pass', role=User.Role.ACCOUNTANT
        )
        self.viewer = User.objects.create_user(
            username='core_viewer', password='pass', role=User.Role.VIEWER
        )

    def test_rule_20_failed_step_leaves_no_partial_updates(self):
        """
        القاعدة 20: فشل خطوة داخل عملية مترابطة لا يترك تحديثات جزئية.
        """
        cash = TreasuryAccount.objects.create(
            name='خزينة الذرية', account_type=TreasuryAccount.AccountType.CASH,
            opening_balance=Decimal('100.000'), current_balance=Decimal('100.000'),
        )
        bank = TreasuryAccount.objects.create(
            name='بنك الذرية', account_type=TreasuryAccount.AccountType.BANK,
            opening_balance=Decimal('0.000'), current_balance=Decimal('0.000'),
        )

        with mock.patch.object(AuditLog, 'log', side_effect=Exception('فشل متعمد في التوثيق')):
            with self.assertRaises(Exception):
                FinanceService.internal_transfer(
                    from_account=cash, to_account=bank,
                    amount=Decimal('40.000'), user=self.manager,
                )

        cash.refresh_from_db()
        bank.refresh_from_db()
        # يجب أن تتراجع كل التحديثات: لا خصم جزئي من الرصيد
        self.assertEqual(cash.current_balance, Decimal('100.000'))
        self.assertEqual(bank.current_balance, Decimal('0.000'))

    def test_rule_9_accountant_cannot_change_settings(self):
        """
        الصلاحيات: المحاسب لا يغيّر الإعدادات المركزية، والمدير يستطيع.
        """
        client = Client()
        client.force_login(self.accountant)
        self.assertEqual(client.get(reverse('system_settings')).status_code, 403)

        client.force_login(self.manager)
        self.assertEqual(client.get(reverse('system_settings')).status_code, 200)

    def _seed_full_data(self):
        """تجهيز بيانات كافية لفحص كل صفحات النظام."""
        self.trader = Trader.objects.create(code='T001', name='ابو معتز القيسي')
        self.crop = Crop.objects.create(name='نكتارين')
        self.box_type = BoxType.objects.create(
            name='عكاز أزرق 8KG', color_or_type='أزرق',
            tare_weight=Decimal('0.800'), discount_price=Decimal('1.500'),
            company_stock=1000,
        )
        # نوع الصناديق الواردة من المزرعة (يُستخدم في استلام السيارة)
        self.farm_box_type = FarmBoxType.objects.create(
            name='صندوق مزرعة أزرق', description='أزرق',
            tare_weight=Decimal('0.800'),
        )
        self.cold_store = ColdStore.objects.create(
            name='البراد الأول', code='CS-01', capacity_pallets=50,
            current_temperature=Decimal('3.0'),
        )
        self.shelf = Shelf.objects.create(
            cold_store=self.cold_store, shelf_number='1', capacity_pallets=25
        )
        self.truck = ReceptionService.process_truck_reception({
            'trader': self.trader.id,
            'crop': self.crop.id,
            'box_type': self.farm_box_type.id,
            'box_count': 20,
            'gross_weight': Decimal('1000.000'),
            'tare_weight': Decimal('500.000'),
            'pallet_count': 2,
            'cold_store': self.cold_store.id,
            'shelf': self.shelf.id,
            'temperature': Decimal('3.0'),
        }, user=self.accountant)
        self.pallet = self.truck.pallets.first()

        self.session = PackingSession.objects.create(
            session_code='PS-SMOKE-001', trader=self.trader, crop=self.crop,
            packing_method=PackingSession.Method.MACHINE,
            output_packed_kg=Decimal('100.000'),
            status=PackingSession.Status.COMPLETED,
            created_by=self.accountant,
        )
        self.packed = PackedPallet.objects.create(
            packed_pallet_code=f'{self.pallet.pallet_code}-XL',
            original_pallet=self.pallet, session=self.session, trader=self.trader,
            crop=self.crop, size_name='XL', box_type=self.box_type,
            box_count=8, net_weight_kg=Decimal('90.000'),
        )
        self.invoice = Invoice.objects.create(
            invoice_number='2026-T001-001', trader=self.trader, year=2026,
            crop=self.crop, status=Invoice.Status.DRAFT,
            packing_rate_snapshot=Decimal('0.080'), created_by=self.accountant,
        )
        self.invoice.calculate_totals()
        self.invoice.save()

        self.cash = TreasuryAccount.objects.create(
            name='الصندوق النقدي', account_type=TreasuryAccount.AccountType.CASH,
            opening_balance=Decimal('0.000'), current_balance=Decimal('0.000'),
        )
        self.category = ExpenseCategory.objects.create(name='كهرباء وماء')
        self.expense = Expense.objects.create(
            category=self.category, amount=Decimal('10.000'),
            due_date=timezone.now().date(), status=Expense.Status.ACCRUED,
            description='مصروف تجريبي', created_by=self.accountant,
        )
        self.check = Check.objects.create(
            check_number='CHK-1', trader=self.trader, bank_name='البنك العربي',
            amount=Decimal('25.000'), due_date=timezone.now().date(),
            status=Check.Status.REGISTERED, created_by=self.accountant,
        )
        self.workshop = Workshop.objects.create(name='ورشة أم السعيد')

    def test_all_pages_smoke_as_manager(self):
        """
        فحص شامل: كل صفحات النظام تفتح بنجاح بصلاحية المدير دون أخطاء 500.
        """
        self._seed_full_data()
        client = Client()
        client.force_login(self.manager)

        pages = [
            reverse('home'),
            reverse('notifications'),
            reverse('audit_logs'),
            reverse('system_settings'),
            reverse('trader_list'),
            reverse('trader_detail', args=[self.trader.pk]),
            reverse('truck_list'),
            reverse('truck_detail', args=[self.truck.pk]),
            reverse('pallet_detail', args=[self.pallet.pk]),
            reverse('pallet_print_label', args=[self.pallet.pk]),
            reverse('pallet_qr_scan', args=[self.pallet.pallet_code]),
            reverse('truck_print_all_labels', args=[self.truck.pk]),
            reverse('packed_pallet_print_label', args=[self.packed.pk]),
            reverse('cold_store_list'),
            reverse('cold_store_temperature_log'),
            reverse('box_type_list'),
            reverse('boxes_dashboard'),
            reverse('trader_boxes_statement', args=[self.trader.pk]),
            reverse('packing_list'),
            reverse('packing_detail', args=[self.session.pk]),
            reverse('packed_pallets_list'),
            reverse('delivery_notes_list'),
            reverse('workshop_list'),
            reverse('workshop_detail', args=[self.workshop.pk]),
            reverse('labor_cost_report'),
            reverse('treasury_dashboard'),
            reverse('payment_list'),
            reverse('expense_list'),
            reverse('check_list'),
            reverse('invoice_list'),
            reverse('invoice_detail', args=[self.invoice.pk]),
            reverse('analytics_dashboard'),
            reverse('manager_daily_report'),
            reverse('export_manager_daily_excel'),
            reverse('manager_daily_pdf'),
        ]

        failures = []
        for url in pages:
            response = client.get(url)
            if response.status_code >= 400:
                failures.append(f"{url} -> {response.status_code}")
        self.assertEqual(failures, [], f"صفحات فشلت: {failures}")


class OperatingPricesComeFromSettingsTests(TestCase):
    """
    لا يُفترض أي سعر أو تكلفة تشغيلية داخل الشيفرة:
    كل الأسعار تُقرأ من صفحة إعدادات النظام وتُحفظ فيها.
    """

    def setUp(self):
        self.settings = SystemSetting.get_settings()
        self.manager = User.objects.create_user(
            username='prices_manager', password='pass', role=User.Role.MANAGER
        )
        self.accountant = User.objects.create_user(
            username='prices_accountant', password='pass', role=User.Role.ACCOUNTANT
        )
        self.trader = Trader.objects.create(code='T-PR', name='تاجر الأسعار')
        self.crop = Crop.objects.create(name='أسعار مشمش')
        self.box_type = BoxType.objects.create(
            name='صندوق أسعار', color_or_type='أزرق',
            tare_weight=Decimal('0.800'), discount_price=Decimal('1.500'),
        )
        # دفعة السيارة ترتبط بنوع صندوق مزرعة (FarmBoxType)
        self.farm_box_type = FarmBoxType.objects.create(
            name='صندوق مزرعة أسعار', description='بلاستيك', tare_weight=Decimal('0.800'),
        )

    def test_settings_page_saves_operating_prices(self):
        """صفحة إعدادات النظام تحفظ سعر الكيلو المجمد وساعات العمل وسعر الصرف."""
        client = Client()
        client.force_login(self.manager)
        response = client.post(reverse('system_settings'), {
            'company_name_en': 'Royal Fruit',
            'company_name_ar': 'رويال فروت',
            'brand_color': '#1B4D3E',
            'currency_symbol': 'دينار',
            'rounding_policy': 'ROUND_HALF_UP',
            'currency_decimals': '3',
            'weight_decimals': '3',
            'empty_crate_weight': '0.800',
            'empty_pallet_weight': '10.000',
            'worker_hourly_rate': '1.000',
            'worker_transport_daily': '1.000',
            'packed_cooling_daily_rate': '0.000',
            'default_packing_rate_per_kg': '0.055',
            'default_manual_packing_rate_per_kg': '0.045',
            'default_work_hours': '9.00',
            'default_exchange_rate': '0.710000',
            'critical_fields': self.settings.critical_fields,
            'non_critical_fields': self.settings.non_critical_fields,
        })
        self.assertEqual(response.status_code, 302)

        SystemSetting.invalidate_settings_cache()
        saved = SystemSetting.get_settings()
        self.assertEqual(saved.default_packing_rate_per_kg, Decimal('0.055'))
        self.assertEqual(saved.default_manual_packing_rate_per_kg, Decimal('0.045'))
        self.assertEqual(saved.default_work_hours, Decimal('9.00'))
        self.assertEqual(saved.default_exchange_rate, Decimal('0.710000'))

    def test_invoice_without_trader_rate_uses_settings_price_and_freezes_it(self):
        """تاجر بلا سعر توضيب: تُستخدم قيمة الإعدادات وتُجمَّد في الفاتورة."""
        SystemSetting.objects.filter(pk=self.settings.pk).update(
            default_packing_rate_per_kg=Decimal('0.055')
        )
        SystemSetting.invalidate_settings_cache()

        client = Client()
        client.force_login(self.accountant)
        response = client.post(reverse('invoice_create'), {
            'trader': self.trader.pk,
            'year': timezone.now().year,
            'crop': self.crop.pk,
            'packing_method': 'MACHINE',
            'notes': '',
        })
        self.assertEqual(response.status_code, 302)
        invoice = Invoice.objects.filter(trader=self.trader).latest('id')
        self.assertEqual(invoice.packing_rate_snapshot, Decimal('0.055'))

    def test_manual_invoice_without_trader_rate_uses_manual_settings_price(self):
        """
        توضيب يدوي بلا سعر تاجر: يُستخدم السعر الافتراضي للتوضيب اليدوي (مستقل عن سعر المكينة).
        """
        SystemSetting.objects.filter(pk=self.settings.pk).update(
            default_packing_rate_per_kg=Decimal('0.055'),
            default_manual_packing_rate_per_kg=Decimal('0.045'),
        )
        SystemSetting.invalidate_settings_cache()

        client = Client()
        client.force_login(self.accountant)
        response = client.post(reverse('invoice_create'), {
            'trader': self.trader.pk,
            'year': timezone.now().year,
            'crop': self.crop.pk,
            'packing_method': 'MANUAL',
            'notes': '',
        })
        self.assertEqual(response.status_code, 302)
        invoice = Invoice.objects.filter(trader=self.trader).latest('id')
        self.assertEqual(invoice.packing_method, 'MANUAL')
        self.assertEqual(invoice.packing_rate_snapshot, Decimal('0.045'))

    def test_machine_invoice_without_trader_rate_uses_machine_settings_price(self):
        """سعر المكينة الافتراضي مستقل عن اليدوي عند غياب سعر التاجر."""
        SystemSetting.objects.filter(pk=self.settings.pk).update(
            default_packing_rate_per_kg=Decimal('0.055'),
            default_manual_packing_rate_per_kg=Decimal('0.045'),
        )
        SystemSetting.invalidate_settings_cache()

        client = Client()
        client.force_login(self.accountant)
        response = client.post(reverse('invoice_create'), {
            'trader': self.trader.pk,
            'year': timezone.now().year,
            'crop': self.crop.pk,
            'packing_method': 'MACHINE',
            'notes': '',
        })
        self.assertEqual(response.status_code, 302)
        invoice = Invoice.objects.filter(trader=self.trader).latest('id')
        self.assertEqual(invoice.packing_rate_snapshot, Decimal('0.055'))

    def test_invoice_uses_trader_rate_when_present(self):
        """إذا وُجد سعر توضيب ساري للتاجر فهو المقدَّم على سعر الإعدادات."""
        from operations.models import TraderPackingRate

        SystemSetting.objects.filter(pk=self.settings.pk).update(
            default_packing_rate_per_kg=Decimal('0.055')
        )
        SystemSetting.invalidate_settings_cache()
        TraderPackingRate.objects.create(
            trader=self.trader, method='MACHINE',
            rate_per_kg=Decimal('0.130'), effective_date=timezone.localdate(),
        )

        client = Client()
        client.force_login(self.accountant)
        client.post(reverse('invoice_create'), {
            'trader': self.trader.pk,
            'year': timezone.now().year,
            'crop': self.crop.pk,
            'packing_method': 'MACHINE',
            'notes': '',
        })
        invoice = Invoice.objects.filter(trader=self.trader).latest('id')
        self.assertEqual(invoice.packing_rate_snapshot, Decimal('0.130'))

    def test_workshop_defaults_fall_back_to_settings(self):
        """ورشة بلا سعر ساعة/بدل مواصلات: تُقرأ من إعدادات النظام لا من الشيفرة."""
        SystemSetting.objects.filter(pk=self.settings.pk).update(
            worker_hourly_rate=Decimal('2.500'), worker_transport_daily=Decimal('1.750')
        )
        SystemSetting.invalidate_settings_cache()

        # ورشة بلا سعر ساعة/بدل مواصلات محددين (صفر = يرجع للإعدادات)
        workshop = Workshop.objects.create(
            name='ورشة الأسعار', default_hourly_rate=Decimal('0.000'),
            default_transport_daily=Decimal('0.000'),
        )
        log = LaborService.record_or_update_daily_log(
            workshop=workshop, work_date=timezone.localdate(),
            worker_count=10, work_hours=Decimal('8.00'), user=self.accountant,
        )
        self.assertEqual(log.hourly_rate, Decimal('2.500'))
        self.assertEqual(log.transport_allowance_daily, Decimal('1.750'))
        # 10 × 8 × 2.500 + 10 × 1.750 = 200 + 17.5 = 217.500
        self.assertEqual(log.total_cost, Decimal('217.500'))

    def test_truck_weights_use_settings_for_empty_pallet(self):
        """وزن الطبلية الفارغة يُقرأ من إعدادات النظام عند حساب صافي السيارة."""
        SystemSetting.objects.filter(pk=self.settings.pk).update(
            empty_pallet_weight=Decimal('12.000')
        )
        SystemSetting.invalidate_settings_cache()

        truck = TruckReceipt.objects.create(
            lot_number='2026-PR-1', trader=self.trader, crop=self.crop, box_type=self.farm_box_type,
            box_count=8, gross_weight=Decimal('2000.000'), tare_weight=Decimal('1000.000'),
            pallet_count=2, on_pallets=True, weight_deduction_applied=True,
            cold_store=ColdStore.objects.create(code='CS-PR', name='براد الأسعار', capacity_pallets=10),
        )
        truck.calculate_weights()  # بلا تمرير وزن → من الإعدادات
        self.assertEqual(truck.pallets_tare_weight, Decimal('24.000'))  # 2 × 12


class OwnerRoleAndWipeTests(TestCase):
    """
    اختبارات مالك النظام (شخص واحد مسؤول عن كل الإجراءات والموافقات)
    وخدمة تفريغ قاعدة البيانات.
    """

    def setUp(self):
        self.settings = SystemSetting.get_settings()
        self.settings.save()
        self.owner = User.objects.create_user(
            username='core_owner', password='pass', role=User.Role.OWNER
        )
        self.manager = User.objects.create_user(
            username='owner_t_manager', password='pass', role=User.Role.MANAGER
        )
        self.accountant = User.objects.create_user(
            username='owner_t_accountant', password='pass', role=User.Role.ACCOUNTANT
        )

    def test_owner_has_all_permissions(self):
        """المالك يمتلك صلاحيات المدير والمحاسب معاً."""
        self.assertTrue(self.owner.is_owner)
        self.assertTrue(self.owner.is_manager)
        self.assertTrue(self.owner.is_accountant)
        # المدير ليس مالكاً
        self.assertFalse(self.manager.is_owner)
        self.assertTrue(self.manager.is_manager)

    def test_only_owner_can_open_wipe_page(self):
        """صفحة تفريغ قاعدة البيانات للمالك فقط — أي شخص آخر يُمنع (403)."""
        client = Client()

        client.force_login(self.owner)
        self.assertEqual(client.get(reverse('database_wipe')).status_code, 200)

        # المدير يُمنع لأنها صلاحية المالك حصراً
        client.force_login(self.manager)
        self.assertEqual(client.get(reverse('database_wipe')).status_code, 403)

        client.force_login(self.accountant)
        self.assertEqual(client.get(reverse('database_wipe')).status_code, 403)

    def test_wipe_requires_exact_confirmation_phrase(self):
        """التفريغ لا ينفَّذ إلا بعبارة التأكيد الحرفية."""
        Trader.objects.create(code='W1', name='تاجر للتفريغ')
        from core.wipe_service import wipe_database, CONFIRM_PHRASE

        with self.assertRaises(ValueError):
            wipe_database(self.owner, 'OPERATIONS', 'عبارة خاطئة')

        # لم يُحذف أي شيء
        self.assertTrue(Trader.objects.filter(code='W1').exists())

        # العبارة الصحيحة تنفّذ الحذف دون رفع خطأ
        trader_keep = Trader.objects.create(code='W2', name='تاجر يبقى')
        _, total = wipe_database(self.owner, 'OPERATIONS', CONFIRM_PHRASE)
        self.assertGreaterEqual(total, 0)
        # في نموذج OPERATIONS تبقى البيانات المرجعية (كل التجار بما فيهم W1)
        self.assertTrue(Trader.objects.filter(code='W2').exists())
        self.assertTrue(Trader.objects.filter(code='W1').exists())

    def test_wipe_operations_keeps_reference_and_resets_balances(self):
        """نموذج الحركات يحذف المعاملات ويصفّر الأرصدة ويُبقي المرجعيات."""
        cash = TreasuryAccount.objects.create(
            name='خزينة التفريغ', account_type=TreasuryAccount.AccountType.CASH,
            opening_balance=Decimal('50.000'), current_balance=Decimal('50.000'),
        )
        BoxType.objects.create(name='صندوق تفريغ', color_or_type='أزرق', company_stock=500)
        FinanceService.record_payment(
            trader=Trader.objects.create(code='W3', name='تاجر دفع'),
            amount=Decimal('25.000'),
            payment_method=Payment.Method.CASH, treasury_account=cash, user=self.owner,
        )
        cash.refresh_from_db()
        self.assertEqual(cash.current_balance, Decimal('75.000'))

        from core.wipe_service import wipe_database, CONFIRM_PHRASE
        wipe_database(self.owner, 'OPERATIONS', CONFIRM_PHRASE)

        cash.refresh_from_db()
        # الرصيد الحالي يعود للرصيد الافتتاحي
        self.assertEqual(cash.current_balance, Decimal('50.000'))
        # مرجعيات باقية
        self.assertTrue(Trader.objects.filter(code='W3').exists())
        self.assertTrue(BoxType.objects.filter(name='صندوق تفريغ').exists())
        # الحركات حُذفت
        self.assertEqual(Payment.objects.filter(treasury_account=cash).count(), 0)

    def test_wipe_is_audited(self):
        """كل تفريغ يُسجَّل في سجل التدقيق."""
        from core.wipe_service import wipe_database, CONFIRM_PHRASE
        wipe_database(self.owner, 'OPERATIONS', CONFIRM_PHRASE)
        log = AuditLog.objects.filter(action='WIPE_DATABASE').first()
        self.assertIsNotNone(log)
        self.assertEqual(log.user, self.owner)
        self.assertEqual(log.details.get('mode'), 'OPERATIONS')

    def test_full_wipe_keeps_users_and_settings_only(self):
        """النموذج الكامل يحذف حتى المرجعيات ويُبقي المستخدمين والإعدادات."""
        Trader.objects.create(code='W9', name='تاجر يُحذف')
        TreasuryAccount.objects.create(
            name='خزينة تُحذف', account_type=TreasuryAccount.AccountType.CASH,
        )
        from core.wipe_service import wipe_database, CONFIRM_PHRASE
        wipe_database(self.owner, 'FULL', CONFIRM_PHRASE)

        self.assertFalse(Trader.objects.filter(code='W9').exists())
        self.assertFalse(TreasuryAccount.objects.filter(name='خزينة تُحذف').exists())
        # المستخدمون والإعدادات باقية
        self.assertTrue(User.objects.filter(username='core_owner').exists())
        self.assertTrue(SystemSetting.objects.exists())


class InitSystemDefaultCategoriesTests(TestCase):
    """
    init_system يُنشئ تصنيفات مصاريف افتراضية جاهزة (قابلة للتعديل والحذف لاحقاً).
    """

    def test_init_system_creates_default_expense_categories(self):
        from django.core.management import call_command
        from io import StringIO
        call_command('init_system', stdout=StringIO())
        self.assertTrue(ExpenseCategory.objects.filter(name='كهرباء وماء').exists())
        self.assertTrue(ExpenseCategory.objects.filter(name='إيجار').exists())
        self.assertTrue(ExpenseCategory.objects.filter(name='محروقات').exists())
        self.assertGreaterEqual(ExpenseCategory.objects.count(), 10)

    def test_init_system_is_idempotent_for_categories(self):
        from django.core.management import call_command
        from io import StringIO
        call_command('init_system', stdout=StringIO())
        count_first = ExpenseCategory.objects.count()
        call_command('init_system', stdout=StringIO())
        self.assertEqual(ExpenseCategory.objects.count(), count_first)
