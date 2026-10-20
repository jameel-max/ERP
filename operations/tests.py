from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from django.core.exceptions import ValidationError
from decimal import Decimal
from datetime import timedelta

from core.models import User, SystemSetting, Notification, AuditLog
from operations.models import Trader, Crop, ColdStore, Shelf, TruckReceipt, Pallet, PalletMovementLog, TraderPackingRate
from boxes.models import FarmBoxType
from operations.services import ReceptionService, StorageService
from invoicing.models import Invoice


class Phase1ComprehensiveTests(TestCase):
    def setUp(self):
        # 1. إعدادات النظام
        self.settings = SystemSetting.get_settings()
        self.settings.company_name_en = 'Royal Fruit'
        self.settings.company_name_ar = 'رويال فروت'
        self.settings.brand_color = '#1B4D3E'
        self.settings.empty_crate_weight = Decimal('0.800')
        self.settings.empty_pallet_weight = Decimal('10.000')
        self.settings.fifo_alert_days = 7
        self.settings.save()

        # 2. المستخدمون والصلاحيات
        self.manager = User.objects.create_user(
            username='manager_user', password='password123', role=User.Role.MANAGER
        )
        self.accountant = User.objects.create_user(
            username='accountant_user', password='password123', role=User.Role.ACCOUNTANT
        )
        self.viewer = User.objects.create_user(
            username='viewer_user', password='password123', role=User.Role.VIEWER
        )

        # 3. الكيانات الأساسية
        self.trader = Trader.objects.create(
            code='T001',
            name='ابو معتز القيسي',
            phone='0790000001'
        )
        self.crop = Crop.objects.create(name='نكتارين')
        # نوع الصناديق في الاستلام يُسحب من الصناديق الواردة من المزرعة
        self.box_type = FarmBoxType.objects.create(
            name='صندوق مزرعة أزرق 8KG',
            description='أزرق',
            tare_weight=Decimal('0.800'),
        )
        self.cold_store1 = ColdStore.objects.create(
            code='CS-01',
            name='البراد الأول',
            capacity_pallets=20,
            current_temperature=Decimal('3.0')
        )
        self.shelf1 = Shelf.objects.create(
            cold_store=self.cold_store1,
            shelf_number='1',
            capacity_pallets=10
        )
        self.shelf2 = Shelf.objects.create(
            cold_store=self.cold_store1,
            shelf_number='2',
            capacity_pallets=10
        )
        self.cold_store2 = ColdStore.objects.create(
            code='CS-02',
            name='البراد الثاني',
            capacity_pallets=20,
            current_temperature=Decimal('4.0')
        )
        self.shelf2_1 = Shelf.objects.create(
            cold_store=self.cold_store2,
            shelf_number='1',
            capacity_pallets=10
        )

    def test_rule_30_missing_critical_field_prevents_save(self):
        """
        القاعدة 30: حقل حرج ناقص يمنع حفظ السيارة
        الحقول الحرجة: التاجر، نوع البضاعة، نوع الصناديق وعددها، الوزن القائم والفارغ، عدد الطبالي، البراد
        """
        invalid_data = {
            'trader': self.trader.id,
            # crop ناقص
            'box_type': self.box_type.id,
            'box_count': 100,
            'gross_weight': Decimal('2000.000'),
            'tare_weight': Decimal('1000.000'),
            'pallet_count': 2,
            'cold_store': self.cold_store1.id,
        }
        with self.assertRaises(ValidationError):
            ReceptionService.process_truck_reception(data=invalid_data, user=self.accountant)

    def test_rule_30_missing_non_critical_field_creates_notification_and_saves(self):
        """
        القاعدة 30: حقل غير حرج ناقص يُنشئ إشعاراً ولا يمنع الحفظ
        الحقول غير الحرجة: اسم السائق، رقم السيارة، السيخ، درجة الحرارة، الملاحظات
        """
        valid_data = {
            'trader': self.trader.id,
            'crop': self.crop.id,
            'box_type': self.box_type.id,
            'box_count': 600,
            'gross_weight': Decimal('6000.000'),
            'tare_weight': Decimal('2000.000'),
            'pallet_count': 6,
            'cold_store': self.cold_store1.id,
            # حقول غير حرجة تركت فارغة: driver_name, truck_plate, shelf, temperature, notes
            'driver_name': '',
            'truck_plate': '',
            'shelf': None,
            'temperature': None,
            'notes': '',
        }
        initial_notif_count = Notification.objects.count()
        truck = ReceptionService.process_truck_reception(data=valid_data, user=self.accountant)

        self.assertIsNotNone(truck.pk)
        self.assertEqual(truck.lot_number, '2026-0001')
        self.assertEqual(truck.pallets.count(), 6)

        # التحقق من إنشاء إشعار في جرس الإشعارات برابط التعديل المباشر
        new_notifs = Notification.objects.filter(related_model='TruckReceipt', related_id=str(truck.id))
        self.assertEqual(new_notifs.count(), 1)
        notif = new_notifs.first()
        self.assertIn('حقول غير حرجة ناقصة', notif.title)
        self.assertFalse(notif.is_resolved)
        self.assertEqual(notif.link, reverse('truck_edit', args=[truck.id]))

    def test_rule_30_fields_configurable_from_settings(self):
        """
        البند خامساً: قائمة الحقول الحرجة وغير الحرجة قابلة للتعديل من الإعدادات.
        ترقية حقل (رقم السيارة) إلى حرج تمنع الحفظ، وتخفيض حقل (اسم المزرعة) إلى غير حرج يُنشئ إشعاراً.
        """
        s = SystemSetting.get_settings()
        s.critical_fields = ('trader,crop,box_type,box_count,gross_weight,'
                             'tare_weight,pallet_count,cold_store,truck_plate')
        s.non_critical_fields = 'farm_name'
        s.save()

        data = {
            'trader': self.trader.id,
            'crop': self.crop.id,
            'box_type': self.box_type.id,
            'box_count': 10,
            'gross_weight': Decimal('500.000'),
            'tare_weight': Decimal('100.000'),
            'pallet_count': 1,
            'cold_store': self.cold_store1.id,
            # رقم السيارة ناقص لكنه أصبح حرجاً الآن
        }
        with self.assertRaises(ValidationError):
            ReceptionService.process_truck_reception(data=data, user=self.accountant)

        # إضافة رقم السيارة -> يمر الحفظ لأن اسم المزرعة أصبح الحقل غير الحرج الوحيد الناقص
        data['truck_plate'] = '70-55555'
        truck = ReceptionService.process_truck_reception(data=data, user=self.accountant)
        notif = Notification.objects.filter(
            related_model='TruckReceipt', related_id=str(truck.id)
        ).first()
        self.assertIsNotNone(notif)
        self.assertIn('اسم المزرعة', notif.message)

    def test_rule_16_scale_calculation_and_no_double_deduction(self):
        """
        القاعدة 16: الوزن نفسه لا يُخصم مرتين
        الصافي الظاهر = القائم - الفارغ
        الصافي المحسوب = الصافي الظاهر - (الصناديق × وزن الصندوق) - (الطبالي × وزن الطبلية)
        """
        # مثال: 600 صندوق × 0.8 كغ = 480 كغ
        # 6 طبالي × 10 كغ = 60 كغ
        # إجمالي خصم العبوات = 540 كغ
        # قائم = 6000 كغ، فارغ = 2000 كغ -> ظاهر = 4000 كغ
        # صافي محسوب = 4000 - 540 = 3460 كغ
        data = {
            'trader': self.trader.id,
            'crop': self.crop.id,
            'box_type': self.box_type.id,
            'box_count': 600,
            'gross_weight': Decimal('6000.000'),
            'tare_weight': Decimal('2000.000'),
            'pallet_count': 6,
            'cold_store': self.cold_store1.id,
            'weight_deduction_applied': True,
        }
        truck = ReceptionService.process_truck_reception(data=data, user=self.accountant)

        self.assertEqual(truck.apparent_net_weight, Decimal('4000.000'))
        self.assertEqual(truck.crates_tare_weight, Decimal('480.000'))
        self.assertEqual(truck.pallets_tare_weight, Decimal('60.000'))
        self.assertEqual(truck.calculated_net_weight, Decimal('3460.000'))

        # إذا كانت قراءة الميزان صافية مسبقاً (weight_deduction_applied=False)
        data2 = data.copy()
        data2['weight_deduction_applied'] = False
        truck2 = ReceptionService.process_truck_reception(data=data2, user=self.accountant)
        self.assertEqual(truck2.calculated_net_weight, Decimal('4000.000'))

    def test_rule_17_and_27_pallet_unique_location_and_transfer_updates_both(self):
        """
        القاعدة 17: الطبلية لا تظهر في موقعين في الوقت نفسه
        القاعدة 27: نقل الطبلية بين الأسياخ والبرادين يحدّث الإشغال في الموقعين معاً
        """
        data = {
            'trader': self.trader.id,
            'crop': self.crop.id,
            'box_type': self.box_type.id,
            'box_count': 100,
            'gross_weight': Decimal('1500.000'),
            'tare_weight': Decimal('500.000'),
            'pallet_count': 2,
            'cold_store': self.cold_store1.id,
            'shelf': self.shelf1.id,
        }
        truck = ReceptionService.process_truck_reception(data=data, user=self.accountant)
        pallet1 = truck.pallets.first()

        # الموقع الابتدائي: البراد الأول - السيخ 1
        self.assertEqual(pallet1.cold_store, self.cold_store1)
        self.assertEqual(pallet1.shelf, self.shelf1)
        self.assertEqual(self.cold_store1.occupied_pallets, 2)
        self.assertEqual(self.shelf1.occupied_pallets, 2)
        self.assertEqual(self.cold_store2.occupied_pallets, 0)

        # تنفيذ النقل إلى البراد الثاني - السيخ 1
        StorageService.transfer_pallet(
            pallet=pallet1,
            to_cold_store=self.cold_store2,
            to_shelf=self.shelf2_1,
            user=self.accountant,
            reason='نقل إلى براد التخزين طويل الأمد'
        )

        pallet1.refresh_from_db()
        # الموقع تحدّث وموجود في موقع واحد فقط
        self.assertEqual(pallet1.cold_store, self.cold_store2)
        self.assertEqual(pallet1.shelf, self.shelf2_1)

        # تحديث الإشغال في الموقعين معاً
        self.assertEqual(self.cold_store1.occupied_pallets, 1)
        self.assertEqual(self.shelf1.occupied_pallets, 1)
        self.assertEqual(self.cold_store2.occupied_pallets, 1)
        self.assertEqual(self.shelf2_1.occupied_pallets, 1)

        # التحقق من تسجيل الحركة في PalletMovementLog
        last_move = PalletMovementLog.objects.filter(pallet=pallet1).order_by('-timestamp').first()
        self.assertEqual(last_move.from_cold_store, self.cold_store1)
        self.assertEqual(last_move.to_cold_store, self.cold_store2)
        self.assertEqual(last_move.movement_type, 'TRANSFER')

    def test_rule_25_and_26_qr_and_sscc_and_lot_unique_and_accurate(self):
        """
        القاعدة 25: كل QR فريد، ومسحه يعرض التاجر والسيارة والموقع الصحيحين
        القاعدة 26: رقم الدفعة والطبلية لا يتكرران
        """
        data = {
            'trader': self.trader.id,
            'crop': self.crop.id,
            'box_type': self.box_type.id,
            'box_count': 160,
            'gross_weight': Decimal('2000.000'),
            'tare_weight': Decimal('500.000'),
            'pallet_count': 2,
            'cold_store': self.cold_store1.id,
            'shelf': self.shelf1.id,
            'temperature': Decimal('3.5'),
            'truck_plate': '70-12345',
            'driver_name': 'سعيد ابو علي',
        }
        truck = ReceptionService.process_truck_reception(data=data, user=self.accountant)
        p1 = truck.pallets.get(pallet_code=f"{truck.lot_number}-P01")
        p2 = truck.pallets.get(pallet_code=f"{truck.lot_number}-P02")

        # التحقق من الفرادة وعدم التكرار
        self.assertNotEqual(p1.pallet_code, p2.pallet_code)
        self.assertNotEqual(p1.sscc_code, p2.sscc_code)
        self.assertTrue(p1.sscc_code.startswith('(00)'))

        # التحقق من بيانات QR
        qr_data = p1.get_qr_data()
        self.assertEqual(qr_data['pallet_code'], p1.pallet_code)
        self.assertEqual(qr_data['trader'], self.trader.name)
        self.assertEqual(qr_data['crop'], self.crop.name)
        self.assertEqual(qr_data['cold_store'], self.cold_store1.name)
        self.assertEqual(qr_data['shelf'], 'السيخ 1')
        self.assertEqual(qr_data['temperature'], '3.5°C')
        self.assertEqual(qr_data['truck_plate'], '70-12345')

    def test_rule_28_fifo_alert_appears_only_after_threshold(self):
        """
        القاعدة 28: تنبيه الأقدم أولاً يظهر عند تجاوز المدة المعتمدة فقط
        """
        data = {
            'trader': self.trader.id,
            'crop': self.crop.id,
            'box_type': self.box_type.id,
            'box_count': 80,
            'gross_weight': Decimal('1000.000'),
            'tare_weight': Decimal('300.000'),
            'pallet_count': 1,
            'cold_store': self.cold_store1.id,
        }
        truck = ReceptionService.process_truck_reception(data=data, user=self.accountant)
        pallet = truck.pallets.first()

        # طبلية دخلت اليوم (أيام البقاء = 0 < 7)
        self.assertEqual(pallet.days_in_cooling, 0)
        self.assertFalse(pallet.days_in_cooling >= self.settings.fifo_alert_days)

        # محاكاة مرور 10 أيام
        pallet.entered_cooling_at = timezone.now() - timedelta(days=10)
        pallet.save()
        self.assertEqual(pallet.days_in_cooling, 10)
        self.assertTrue(pallet.days_in_cooling >= self.settings.fifo_alert_days)

    def test_rule_10_and_15_price_change_does_not_affect_historic_invoices_and_decimal_precision(self):
        """
        القاعدة 10: تعديل سعر خدمة لا يغيّر الفواتير التاريخية
        القاعدة 15: المبالغ المالية تستخدم الحساب العشري الدقيق (Decimal و 3 خانات للدينار)
        """
        # سعر التوضيب وقت إنشاء الفاتورة 0.070 دينار/كغ
        rate = TraderPackingRate.objects.create(
            trader=self.trader,
            method=TraderPackingRate.Method.MACHINE,
            rate_per_kg=Decimal('0.070'),
            effective_date=timezone.now().date()
        )

        invoice = Invoice.objects.create(
            invoice_number='2026-T001-001',
            trader=self.trader,
            year=2026,
            crop=self.crop,
            packing_method=Invoice.PackingMethod.MACHINE,
            packing_rate_snapshot=rate.rate_per_kg,
            total_packed_kg=Decimal('1000.000'),
            created_by=self.accountant
        )
        invoice.calculate_totals()
        invoice.save()

        # 1000 كغ × 0.070 = 70.000 دينار
        self.assertEqual(invoice.packing_subtotal, Decimal('70.000'))
        self.assertEqual(invoice.grand_total, Decimal('70.000'))

        # تعديل السعر اللاحق إلى 0.120 دينار/كغ
        rate.rate_per_kg = Decimal('0.120')
        rate.save()

        # إعادة فتح الفاتورة القديمة والتأكد من ثبات السعر المجمد فيها
        historic_inv = Invoice.objects.get(id=invoice.id)
        self.assertEqual(historic_inv.packing_rate_snapshot, Decimal('0.070'))
        self.assertEqual(historic_inv.grand_total, Decimal('70.000'))

    def test_role_permissions_enforcement(self):
        """
        اختبار الصلاحيات: المحاسب يدخل السيارات، والمدير فقط يغير الإعدادات المركزية
        """
        client = Client()

        # تسجيل دخول المحاسب
        client.force_login(self.accountant)

        # المحاسب يحاول فتح صفحة إعدادات النظام (مخصصة للمدير)
        response = client.get(reverse('system_settings'))
        # يجب أن يرفض بـ 403 PermissionDenied
        self.assertEqual(response.status_code, 403)

        # تسجيل دخول المدير
        client.force_login(self.manager)
        response = client.get(reverse('system_settings'))
        self.assertEqual(response.status_code, 200)

    def test_audit_log_recorded_on_operations(self):
        """
        اختبار توثيق العمليات في سجل التدقيق (AuditLog)
        """
        initial_logs = AuditLog.objects.count()
        data = {
            'trader': self.trader.id,
            'crop': self.crop.id,
            'box_type': self.box_type.id,
            'box_count': 100,
            'gross_weight': Decimal('1500.000'),
            'tare_weight': Decimal('500.000'),
            'pallet_count': 1,
            'cold_store': self.cold_store1.id,
        }
        truck = ReceptionService.process_truck_reception(data=data, user=self.accountant)
        self.assertGreater(AuditLog.objects.count(), initial_logs)
        last_log = AuditLog.objects.latest('timestamp')
        self.assertEqual(last_log.action, 'CREATE_TRUCK_RECEIPT')
        self.assertEqual(last_log.user, self.accountant)

    # ==================== رمز QR الملصق: يفتح صفحة الطبلية عند المسح ====================

    def _receive_one_pallet(self):
        data = {
            'trader': self.trader.id,
            'crop': self.crop.id,
            'box_type': self.box_type.id,
            'box_count': 80,
            'gross_weight': Decimal('1000.000'),
            'tare_weight': Decimal('300.000'),
            'pallet_count': 1,
            'cold_store': self.cold_store1.id,
            'truck_plate': '70-12345',
            'driver_name': 'سعيد ابو علي',
        }
        truck = ReceptionService.process_truck_reception(data=data, user=self.accountant)
        return truck.pallets.first()

    def test_pallet_label_shows_qr_only_and_qr_opens_scan_page(self):
        """
        ملصق الطبلية يحتوي رمز QR فقط بلا أي معلومات مكتوبة،
        والرمز يُشفّر رابطاً يفتح صفحة بيانات الطبلية عند المسح.
        """
        pallet = self._receive_one_pallet()
        client = Client()
        client.force_login(self.accountant)
        resp = client.get(reverse('pallet_print_label', args=[pallet.pk]))
        self.assertEqual(resp.status_code, 200)

        expected_url = f"http://testserver{reverse('pallet_qr_scan', args=[pallet.pallet_code])}"
        self.assertContains(resp, f'data-qr-url="{expected_url}"')
        # لا معلومات مكتوبة على الملصق (الرمز فقط)
        self.assertNotContains(resp, 'data-val')
        self.assertNotContains(resp, 'sscc_code')

    def test_scan_base_url_setting_used_in_qr_link(self):
        """
        عند إدخال (عنوان السيرفر لروابط مسح QR) في الإعدادات يُشفَّر داخل الرمز
        بدل عنوان الصفحة الحالي، ليعمل المسح من هواتف الشبكة المحلية.
        """
        self.settings.scan_base_url = 'http://192.168.1.50:8000'
        self.settings.save()
        pallet = self._receive_one_pallet()
        client = Client()
        client.force_login(self.accountant)
        resp = client.get(reverse('pallet_print_label', args=[pallet.pk]))

        expected_url = f"http://192.168.1.50:8000{reverse('pallet_qr_scan', args=[pallet.pallet_code])}"
        self.assertContains(resp, f'data-qr-url="{expected_url}"')

    def test_pallet_qr_scan_page_reachable_without_login(self):
        """
        صفحة الطبلية التي تُفتح عند المسح متاحة من الهاتف بلا تسجيل دخول وتعرض بياناتها كاملة.
        """
        pallet = self._receive_one_pallet()
        public_client = Client()
        resp = public_client.get(reverse('pallet_qr_scan', args=[pallet.pallet_code]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, pallet.pallet_code)
        self.assertContains(resp, self.trader.name)
        self.assertContains(resp, self.crop.name)

    def _base_truck_data(self, **overrides):
        data = {
            'trader': self.trader.id,
            'crop': self.crop.id,
            'box_type': self.box_type.id,
            'box_count': 100,
            'gross_weight': Decimal('2000.000'),
            'tare_weight': Decimal('1000.000'),
            'pallet_count': 2,
            'cold_store': self.cold_store1.id,
        }
        data.update(overrides)
        return data

    def test_no_pallets_still_generates_pallets_but_skips_deduction(self):
        """
        بضاعة وصلت بكمات (بلا طبالي): لا يُخصم وزن الطبالي، لكن تُولَّد الطبالي
        للتخزين والتوضيب حسب المعيار (عدد الصناديق على الطبلية).
        """
        data = self._base_truck_data(on_pallets=False)
        data.pop('pallet_count', None)
        data['boxes_per_pallet'] = 50
        truck = ReceptionService.process_truck_reception(data=data, user=self.accountant)

        self.assertFalse(truck.on_pallets)
        self.assertEqual(truck.pallet_count, 2)          # 100 ÷ 50
        self.assertEqual(truck.pallets.count(), 2)
        self.assertEqual(truck.pallets_tare_weight, Decimal('0.000'))
        # الصافي المحسوب لا يخصم أي وزن طبالي: 1000 - (100*0.8) - 0
        self.assertEqual(truck.calculated_net_weight, Decimal('920.000'))

    def test_standard_80_boxes_per_pallet_computes_pallet_count(self):
        """المعيار 80 صندوقاً للطبلية: 600 صندوق ← 8 طبالي (7×80 + 40)."""
        data = self._base_truck_data(on_pallets=True, box_count=600,
                                     gross_weight=Decimal('2000.000'),
                                     tare_weight=Decimal('1000.000'))
        data.pop('pallet_count', None)
        data['boxes_per_pallet'] = 80
        truck = ReceptionService.process_truck_reception(data=data, user=self.accountant)

        self.assertTrue(truck.on_pallets)
        self.assertEqual(truck.boxes_per_pallet, 80)
        self.assertEqual(truck.pallet_count, 8)
        counts = sorted(p.box_count for p in truck.pallets.all())
        self.assertEqual(counts, [40, 80, 80, 80, 80, 80, 80, 80])
        self.assertEqual(truck.pallets_tare_weight, Decimal('80.000'))  # 8 × 10

    def test_more_than_80_boxes_per_pallet_is_configurable(self):
        """يمكن تغيير المعيار: 100 صندوق لكل طبلية ← 200 ÷ 100 = 2 طبالي."""
        data = self._base_truck_data(on_pallets=True, box_count=200)
        data.pop('pallet_count', None)
        data['boxes_per_pallet'] = 100
        truck = ReceptionService.process_truck_reception(data=data, user=self.accountant)

        self.assertEqual(truck.pallet_count, 2)
        self.assertEqual(truck.pallets.count(), 2)

    def test_on_pallets_applies_deduction_with_standard(self):
        """عند النزول على طبالي يُخصم وزنها من الصافي المحسوب."""
        data = self._base_truck_data(on_pallets=True)
        data.pop('pallet_count', None)
        data['boxes_per_pallet'] = 50
        truck = ReceptionService.process_truck_reception(data=data, user=self.accountant)

        self.assertEqual(truck.pallet_count, 2)
        self.assertEqual(truck.pallets_tare_weight, Decimal('20.000'))  # 2 × 10
        self.assertEqual(truck.calculated_net_weight, Decimal('900.000'))  # 1000 - 80 - 20

    def test_truck_form_computes_pallet_count_and_validates_boxes_per_pallet(self):
        """النموذج: «عدد الطبالي» يُحسب تلقائياً، و«عدد الصناديق على الطبلية» إلزامي > 0."""
        from operations.forms import TruckReceiptForm

        form = TruckReceiptForm(data={
            'lot_number': '2026-TEST-1',
            'arrival_date': '2026-10-19',
            'trader': self.trader.id,
            'crop': self.crop.id,
            'box_type': self.box_type.id,
            'box_count': 600,
            'boxes_per_pallet': 80,
            'gross_weight': '2000.000',
            'tare_weight': '1000.000',
            'cold_store': self.cold_store1.id,
            'on_pallets': 'on',
        })
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['pallet_count'], 8)

        # «عدد الصناديق على الطبلية» صفر = خطأ
        form2 = TruckReceiptForm(data={
            'lot_number': '2026-TEST-2',
            'arrival_date': '2026-10-19',
            'trader': self.trader.id,
            'crop': self.crop.id,
            'box_type': self.box_type.id,
            'box_count': 600,
            'boxes_per_pallet': 0,
            'gross_weight': '2000.000',
            'tare_weight': '1000.000',
            'cold_store': self.cold_store1.id,
            'on_pallets': 'on',
        })
        self.assertFalse(form2.is_valid())
        self.assertIn('boxes_per_pallet', form2.errors)


class DuplicateEntryGuardTests(TestCase):
    """
    القاعدة: إدخال بيانات مكررة (اسم/رمز فريد) يجب أن يُظهر خطأ نموذج واضحاً
    بالعربية بدل تعطّل الصفحة بخطأ 500 من قاعدة البيانات.
    """

    def setUp(self):
        self.manager = User.objects.create_user(
            username='dup_manager', password='password123', role=User.Role.MANAGER
        )
        self.cold_store = ColdStore.objects.create(
            code='CS-01',
            name='البراد الاول',
            capacity_pallets=10,
        )

    def test_duplicate_cold_store_name_shows_form_error_not_500(self):
        self.client.force_login(self.manager)
        resp = self.client.post(reverse('cold_store_create'), {
            'code': 'CS-01',
            'name': 'البراد الاول',
            'capacity_pallets': '55',
            'current_temperature': '3',
            'shelves_count': '5',
            'notes': '',
        })
        # لا يتعطل الطلب (لا 500) ويعود النموذج مع خطأ واضح
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'موجود مسبقاً')
        # لم يُنشأ براد مكرر
        self.assertEqual(ColdStore.objects.filter(name='البراد الاول').count(), 1)

    def test_save_form_transactionally_converts_integrity_error(self):
        from django.db import IntegrityError
        from core.utils import save_form_transactionally
        from operations.forms import ColdStoreForm

        form = ColdStoreForm(data={
            'code': 'CS-99',
            'name': 'براد جديد',
            'capacity_pallets': '10',
            'current_temperature': '2',
            'shelves_count': '4',
            'notes': '',
        })
        self.assertTrue(form.is_valid())

        def failing_save():
            # محاكاة خطأ قيد فريد صادر من قاعدة البيانات
            raise IntegrityError('duplicate key value violates unique constraint')

        ok, result = save_form_transactionally(form, failing_save, 'قيمة مكررة')
        self.assertFalse(ok)
        self.assertIsNone(result)
        self.assertIn('قيمة مكررة', form.errors['__all__'])


class ShelfSpilloverTests(TestCase):
    """
    القاعدة: عند امتلاء السيخ تنتقل الطبالي تلقائياً إلى السيخ التالي.
    - إن لم تُحدَّد سعة للسيخ تُشتق تلقائياً من سعة البراد ÷ عدد الأسياخ.
    - إن لم تُحدَّد سعة للبراد أيضاً يبقى السيخ غير محدود (بلا انتقال).
    """

    def setUp(self):
        self.settings = SystemSetting.get_settings()
        self.accountant = User.objects.create_user(
            username='spill_accountant', password='password123', role=User.Role.ACCOUNTANT
        )
        self.trader = Trader.objects.create(code='T900', name='تاجر التوزيع')
        self.crop = Crop.objects.create(name='تفاح')
        self.box_type = FarmBoxType.objects.create(
            name='صندوق توزيع', description='أزرق', tare_weight=Decimal('0.800')
        )
        # براد سعته 15 طبلية و3 أسياخ بلا سعة محددة → السعة المشتقة 5 لكل سيخ
        self.store = ColdStore.objects.create(code='CS-SP', name='براد التوزيع', capacity_pallets=15)
        self.shelf_a = Shelf.objects.create(cold_store=self.store, shelf_number='1')
        self.shelf_b = Shelf.objects.create(cold_store=self.store, shelf_number='2')
        self.shelf_c = Shelf.objects.create(cold_store=self.store, shelf_number='3')

    def _receive(self, pallet_count, shelf, box_count=None):
        box_count = box_count or pallet_count * 80
        return ReceptionService.process_truck_reception(data={
            'trader': self.trader.id,
            'crop': self.crop.id,
            'box_type': self.box_type.id,
            'box_count': box_count,
            'gross_weight': Decimal('5000.000'),
            'tare_weight': Decimal('1000.000'),
            'pallet_count': pallet_count,
            'cold_store': self.store.id,
            'shelf': shelf.id,
        }, user=self.accountant)

    def test_derived_capacity_from_store_total(self):
        self.assertEqual(self.shelf_a.effective_capacity, 5)
        self.assertEqual(self.shelf_b.effective_capacity, 5)

    def test_pallets_spill_to_next_shelf_when_full(self):
        # 8 طبالي بدءاً من السيخ 1 (سعة مشتقة 5) → 5 على السيخ 1 و3 على السيخ 2
        truck = self._receive(8, self.shelf_a)
        self.assertEqual(truck.pallets.filter(shelf=self.shelf_a).count(), 5)
        self.assertEqual(truck.pallets.filter(shelf=self.shelf_b).count(), 3)
        self.assertEqual(truck.pallets.filter(shelf=self.shelf_c).count(), 0)

    def test_full_shelf_is_skipped_and_next_used(self):
        # تعبئة السيخ 1 والسيخ 2 (10 طبالي)، ثم استلام جديد يبدأ من السيخ 1
        self._receive(10, self.shelf_a)
        self.assertEqual(self.shelf_a.occupied_pallets, 5)
        self.assertEqual(self.shelf_b.occupied_pallets, 5)

        truck = self._receive(4, self.shelf_a)
        # السيخان 1 و2 ممتلئان → كل الطبالي تذهب للسيخ 3
        self.assertEqual(truck.pallets.filter(shelf=self.shelf_a).count(), 0)
        self.assertEqual(truck.pallets.filter(shelf=self.shelf_c).count(), 4)

    def test_explicit_shelf_capacity_overrides_derived(self):
        self.shelf_a.capacity_pallets = 2
        self.shelf_a.save(update_fields=['capacity_pallets'])
        self.assertEqual(self.shelf_a.effective_capacity, 2)
        truck = self._receive(3, self.shelf_a)
        self.assertEqual(truck.pallets.filter(shelf=self.shelf_a).count(), 2)
        self.assertEqual(truck.pallets.filter(shelf=self.shelf_b).count(), 1)

    def test_unlimited_shelves_when_store_capacity_missing(self):
        store = ColdStore.objects.create(code='CS-UN', name='براد بلا سعة')
        shelf = Shelf.objects.create(cold_store=store, shelf_number='1')
        self.assertIsNone(shelf.effective_capacity)
        truck = ReceptionService.process_truck_reception(data={
            'trader': self.trader.id,
            'crop': self.crop.id,
            'box_type': self.box_type.id,
            'box_count': 640,
            'gross_weight': Decimal('5000.000'),
            'tare_weight': Decimal('1000.000'),
            'pallet_count': 8,
            'cold_store': store.id,
            'shelf': shelf.id,
        }, user=self.accountant)
        self.assertEqual(truck.pallets.filter(shelf=shelf).count(), 8)

    def test_insufficient_total_capacity_raises_clear_error(self):
        # إجمالي السعة المشتقة 15 طبلية → استلام 16 يرفض برسالة عربية واضحة
        with self.assertRaises(ValidationError) as ctx:
            self._receive(16, self.shelf_a, box_count=16 * 80)
        self.assertIn('لا توجد سعة كافية', str(ctx.exception))
        self.assertEqual(Pallet.objects.count(), 0)


class PalletMoveApiTests(TestCase):
    """
    اختبار نقطة نقل الطبلية عبر السحب والإفلات من خريطة البرادات (استجابة JSON):
    الخادم وحده يفحص الصلاحية والسعة والبراد والسيخ المستهدفين.
    """

    def setUp(self):
        self.accountant = User.objects.create_user(
            username='drag_accountant', password='password123', role=User.Role.ACCOUNTANT
        )
        self.viewer = User.objects.create_user(
            username='drag_viewer', password='password123', role=User.Role.VIEWER
        )
        self.trader = Trader.objects.create(code='T920', name='تاجر السحب')
        self.crop = Crop.objects.create(name='خوخ')
        self.box_type = FarmBoxType.objects.create(
            name='صندوق السحب', description='', tare_weight=Decimal('0.800')
        )
        self.store = ColdStore.objects.create(code='CS-DR', name='براد السحب', capacity_pallets=20)
        self.shelf1 = Shelf.objects.create(cold_store=self.store, shelf_number='1', capacity_pallets=1)
        self.shelf2 = Shelf.objects.create(cold_store=self.store, shelf_number='2', capacity_pallets=5)
        self.shelf3 = Shelf.objects.create(cold_store=self.store, shelf_number='3', capacity_pallets=5)

        self.truck = ReceptionService.process_truck_reception(data={
            'trader': self.trader.id,
            'crop': self.crop.id,
            'box_type': self.box_type.id,
            'box_count': 240,
            'gross_weight': Decimal('5000.000'),
            'tare_weight': Decimal('1000.000'),
            'pallet_count': 3,
            'cold_store': self.store.id,
            'shelf': self.shelf1.id,
        }, user=self.accountant)
        # السيخ 1 سعته 1 → طبلية واحدة فيه، والباقي انتقل إلى السيخ 2
        self.pallet_on_full = self.truck.pallets.get(shelf=self.shelf1)
        self.pallet_in_shelf2 = self.truck.pallets.filter(shelf=self.shelf2).first()
        self.other_pallet = self.truck.pallets.filter(shelf=self.shelf2).exclude(
            pk=self.pallet_in_shelf2.pk
        ).first()

    def _url(self, pallet):
        return reverse('pallet_move', args=[pallet.pk])

    def test_move_pallet_to_free_shelf(self):
        self.client.force_login(self.accountant)
        resp = self.client.post(self._url(self.other_pallet), {
            'shelf': self.shelf3.pk, 'cold_store': self.store.pk,
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data['ok'])
        self.assertTrue(data['moved'])
        self.other_pallet.refresh_from_db()
        self.assertEqual(self.other_pallet.shelf, self.shelf3)
        # السيخ الأصلي تحرّر
        self.assertEqual(self.shelf2.pallets.count(), 1)

    def test_move_to_full_shelf_returns_arabic_error(self):
        self.client.force_login(self.accountant)
        resp = self.client.post(self._url(self.other_pallet), {
            'shelf': self.shelf1.pk, 'cold_store': self.store.pk,
        })
        self.assertEqual(resp.status_code, 400)
        data = resp.json()
        self.assertFalse(data['ok'])
        self.assertIn('ممتلئ', data['message'])
        self.other_pallet.refresh_from_db()
        self.assertEqual(self.other_pallet.shelf, self.shelf2)

    def test_move_same_location_is_noop(self):
        self.client.force_login(self.accountant)
        resp = self.client.post(self._url(self.other_pallet), {
            'shelf': self.shelf2.pk, 'cold_store': self.store.pk,
        })
        self.assertTrue(resp.json()['ok'])
        self.assertFalse(resp.json()['moved'])
        self.assertEqual(PalletMovementLog.objects.filter(
            pallet=self.other_pallet, movement_type='TRANSFER'
        ).count(), 0)

    def test_move_requires_accountant(self):
        self.client.force_login(self.viewer)
        resp = self.client.post(self._url(self.other_pallet), {
            'shelf': self.shelf3.pk, 'cold_store': self.store.pk,
        })
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(resp.json()['ok'])
        self.other_pallet.refresh_from_db()
        self.assertEqual(self.other_pallet.shelf, self.shelf2)

    def test_move_rejects_get_and_missing_target(self):
        self.client.force_login(self.accountant)
        self.assertEqual(self.client.get(self._url(self.other_pallet)).status_code, 405)
        resp = self.client.post(self._url(self.other_pallet), {})
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.json()['ok'])

    def test_move_to_shelf_of_other_store_is_rejected(self):
        other_store = ColdStore.objects.create(code='CS-OT', name='آخر براد', capacity_pallets=10)
        other_shelf = Shelf.objects.create(cold_store=other_store, shelf_number='1', capacity_pallets=5)
        self.client.force_login(self.accountant)
        # إرسال سيخ ينتمي لبراد آخر مع تمرير براد مختلف → الخدمة ترفض
        resp = self.client.post(self._url(self.other_pallet), {
            'shelf': other_shelf.pk, 'cold_store': self.store.pk,
        })
        self.assertFalse(resp.json()['ok'])
        self.other_pallet.refresh_from_db()
        self.assertEqual(self.other_pallet.shelf, self.shelf2)

