from django.test import TestCase, Client
from django.urls import reverse
from django.core.exceptions import ValidationError
from decimal import Decimal
from datetime import timedelta
from django.utils import timezone

from core.models import User, SystemSetting, Notification
from operations.models import (
    Trader, Crop, BoxType, ColdStore, Shelf, TruckReceipt, Pallet, TraderPackingRate,
    PalletMovementLog,
)
from operations.services import ReceptionService
from boxes.models import TraderBoxBalance, TraderBoxTransaction, FarmBoxType
from boxes.services import BoxService
from packing.models import (
    PackingSession, PackedPallet, PackedPalletCoolingLog, DeliveryNote,
    PackingRawConsumption, PackingLeftoverSettlement,
)
from packing.services import PackingService
from invoicing.models import Invoice, InvoiceItem
from invoicing.services import InvoiceService


class Phase2ComprehensiveTests(TestCase):
    def setUp(self):
        self.settings = SystemSetting.get_settings()
        self.settings.company_name_en = 'Royal Fruit'
        self.settings.weight_tolerance_pct = Decimal('1.00')  # 1% حد السماح
        self.settings.save()

        self.manager = User.objects.create_user(
            username='phase2_manager', password='pass', role=User.Role.MANAGER
        )
        self.accountant = User.objects.create_user(
            username='phase2_accountant', password='pass', role=User.Role.ACCOUNTANT
        )

        self.trader1 = Trader.objects.create(code='T001', name='ابو معتز القيسي')
        self.trader2 = Trader.objects.create(code='T002', name='احمد خليل')

        self.crop = Crop.objects.create(name='نكتارين')
        self.box_blue = BoxType.objects.create(
            name='عكاز أزرق 8KG',
            color_or_type='أزرق',
            tare_weight=Decimal('0.800'),
            discount_price=Decimal('1.500'),
            company_stock=1000
        )
        self.box_green = BoxType.objects.create(
            name='عكاز أخضر',
            color_or_type='أخضر',
            tare_weight=Decimal('0.800'),
            discount_price=Decimal('1.250'),
            company_stock=1000
        )
        # نوع الصناديق الواردة من المزرعة (يُستخدم في استلام السيارة فقط)
        self.farm_box_blue = FarmBoxType.objects.create(
            name='صندوق مزرعة أزرق',
            description='أزرق',
            tare_weight=Decimal('0.800'),
        )

        self.cold_store = ColdStore.objects.create(name='البراد الأول', code='CS-01', capacity_pallets=50)
        self.shelf = Shelf.objects.create(cold_store=self.cold_store, shelf_number='1', capacity_pallets=25)

        # استقبال سيارة لبدء توضيب طباليها
        reception_data = {
            'trader': self.trader1.id,
            'crop': self.crop.id,
            'box_type': self.farm_box_blue.id,
            'box_count': 100,
            'gross_weight': Decimal('2000.000'),
            'tare_weight': Decimal('1000.000'),
            'pallet_count': 2,
            'cold_store': self.cold_store.id,
            'shelf': self.shelf.id,
        }
        self.truck = ReceptionService.process_truck_reception(reception_data, user=self.accountant)
        self.pallet1 = self.truck.pallets.first()
        self.pallet2 = self.truck.pallets.last()

    def test_rule_29_trader_box_balance_isolation(self):
        """
        القاعدة 29: رصيد صناديق كل تاجر = المستلم − المرتجع، دون خلط بين التجار
        """
        # ابو معتز يستلم 100 صندوق أزرق و 50 أخضر
        BoxService.record_box_movement(
            trader=self.trader1,
            box_type=self.box_blue,
            transaction_type=TraderBoxTransaction.Type.ISSUE,
            quantity=100,
            source='مستودع المشغل',
            destination='مزرعة ابو معتز',
            reason='صرف صناديق جني',
            user=self.accountant
        )
        BoxService.record_box_movement(
            trader=self.trader1,
            box_type=self.box_green,
            transaction_type=TraderBoxTransaction.Type.ISSUE,
            quantity=50,
            source='مستودع المشغل',
            destination='مزرعة ابو معتز',
            reason='صرف صناديق جني',
            user=self.accountant
        )

        # التاجر الثاني (احمد خليل) يستلم 80 صندوق أزرق
        BoxService.record_box_movement(
            trader=self.trader2,
            box_type=self.box_blue,
            transaction_type=TraderBoxTransaction.Type.ISSUE,
            quantity=80,
            source='مستودع المشغل',
            destination='مزرعة احمد',
            reason='صرف صناديق جني',
            user=self.accountant
        )

        # ابو معتز يرجع 30 صندوق أزرق
        BoxService.record_box_movement(
            trader=self.trader1,
            box_type=self.box_blue,
            transaction_type=TraderBoxTransaction.Type.RETURN,
            quantity=30,
            source='مزرعة ابو معتز',
            destination='مستودع المشغل',
            reason='إرجاع صناديق فارغة',
            user=self.accountant
        )

        # فحص رصيد ابو معتز: 100 - 30 = 70 أزرق، و 50 أخضر
        bal1_blue = TraderBoxBalance.objects.get(trader=self.trader1, box_type=self.box_blue)
        self.assertEqual(bal1_blue.issued_count, 100)
        self.assertEqual(bal1_blue.returned_count, 30)
        self.assertEqual(bal1_blue.net_balance, 70)
        # القيمة المالية = 70 × 1.500 = 105.000 د.أ
        self.assertEqual(bal1_blue.total_financial_value, Decimal('105.000'))

        bal1_green = TraderBoxBalance.objects.get(trader=self.trader1, box_type=self.box_green)
        self.assertEqual(bal1_green.net_balance, 50)
        # 50 × 1.250 = 62.500 د.أ
        self.assertEqual(bal1_green.total_financial_value, Decimal('62.500'))

        # فحص رصيد التاجر الثاني احمد خليل دون خلط
        bal2_blue = TraderBoxBalance.objects.get(trader=self.trader2, box_type=self.box_blue)
        self.assertEqual(bal2_blue.net_balance, 80)
        self.assertEqual(bal2_blue.total_financial_value, Decimal('120.000'))

    def test_rule_18_weight_diff_not_automatically_waste_and_randeman(self):
        """
        القاعدة 18: اختلاف الوزن لا يُصنَّف هالكاً تلقائياً، والتمييز بين الداخل والموضب والهالك والفرق غير المفسر
        القاعدة 14: غياب الإنتاج لا يسبب قسمة على صفر
        """
        # بدء جلسة توضيب للطبلية 1 (وزنها الصافي الداخل = 450 كغ مثلاً)
        session = PackingService.start_packing_session(
            trader=self.trader1,
            crop=self.crop,
            packing_method=PackingSession.Method.MACHINE,
            source_pallets=[self.pallet1],
            workshop_name='خط 1',
            user=self.accountant
        )
        self.assertEqual(session.source_pallets.count(), 1)
        self.pallet1.refresh_from_db()
        self.assertEqual(self.pallet1.status, Pallet.Status.OUT_FOR_PACKING)

        input_weight = session.input_weight_kg

        # نواتج: ناتج موضب 400 كغ صافي، وهالك مصنف 20 كغ
        # الوزن المُدخل (440 كغ) هو وزن الطبلية كما تُوزن (بضاعة + صناديق 50 × 0.800 = 40 كغ)،
        # والنظام يخصم وزن الصناديق تلقائياً فيصبح الصافي 400 كغ.
        # الفرق غير المفسر = input_weight - 420 كغ (ليس هالكاً تلقائياً)
        outputs_data = [{
            'size_name': 'XL',
            'box_type': self.box_blue,
            'box_count': 50,
            'net_weight_kg': Decimal('440.000'),
            'pallet_count': 1
        }]
        wastes_data = [{
            'waste_type': 'فرز صغير غير مطابق',
            'weight_kg': Decimal('20.000'),
            'notes': 'عيوب حقلية'
        }]

        PackingService.record_outputs_and_finalize(session, outputs_data, wastes_data, user=self.accountant)

        session.refresh_from_db()
        self.assertEqual(session.output_packed_kg, Decimal('400.000'))
        self.assertEqual(session.waste_weight_kg, Decimal('20.000'))
        expected_diff = max(Decimal('0.000'), input_weight - Decimal('420.000'))
        self.assertEqual(session.unexplained_diff_kg, expected_diff)

        # الرندمان = (400 / input_weight) * 100
        expected_randeman = ((Decimal('400.000') / input_weight) * Decimal('100.00')).quantize(Decimal('0.01'))
        self.assertEqual(session.randeman_pct, expected_randeman)

        # التحقق من أن الطبلية الأصلية أصبحت موضبة
        self.pallet1.refresh_from_db()
        self.assertEqual(self.pallet1.status, Pallet.Status.PACKED)

    def test_rule_26_packed_pallet_follows_original_and_delivery_note(self):
        """
        القاعدة 26: الطبلية الموضبة تحمل رقم الأصل (مثال: 2026-0001-P01-XL)
        وحالة الطبلية: في البراد -> خرجت للتوضيب -> موضبة -> مسلّمة
        """
        session = PackingService.start_packing_session(
            trader=self.trader1,
            crop=self.crop,
            packing_method=PackingSession.Method.MACHINE,
            source_pallets=[self.pallet1],
            user=self.accountant
        )
        outputs_data = [{
            'size_name': 'XL',
            'box_type': self.box_blue,
            'box_count': 50,
            'net_weight_kg': session.input_weight_kg,
            'pallet_count': 1
        }]
        PackingService.record_outputs_and_finalize(session, outputs_data, user=self.accountant)

        packed_pallet = session.packed_pallets.first()
        expected_code = f"{self.pallet1.pallet_code}-XL"
        self.assertEqual(packed_pallet.packed_pallet_code, expected_code)
        self.assertEqual(packed_pallet.status, PackedPallet.Status.PACKED)

        # QR الموضب يحتوي على الأصل
        qr_data = packed_pallet.get_qr_data()
        self.assertEqual(qr_data['original_pallet'], self.pallet1.pallet_code)
        self.assertEqual(qr_data['trader'], self.trader1.name)

        # تسليم الطبلية الموضبة عبر بيان تسليم
        delivery_note = PackingService.create_delivery_note(
            trader=self.trader1,
            packed_pallets=[packed_pallet],
            driver_name='محمود',
            truck_plate='12-34567',
            receiver_name='التاجر نفسه',
            notes='تسليم كامل الدفعة الموضبة',
            user=self.accountant
        )

        self.assertIsNotNone(delivery_note.pk)
        packed_pallet.refresh_from_db()
        self.assertEqual(packed_pallet.status, PackedPallet.Status.DELIVERED)

    def test_rule_3_box_discount_no_double_counting_and_invoice_sync(self):
        """
        القاعدة 3: خصم الصناديق لا يُحتسب مرتين
        ربط الفاتورة بالكميات الموضبة فعلاً، وخصم عجز الصناديق، وتوليد PDF
        """
        # 1. تسجيل سعر توضيب للتاجر
        rate = TraderPackingRate.objects.create(
            trader=self.trader1,
            method=TraderPackingRate.Method.MACHINE,
            rate_per_kg=Decimal('0.080'),
            effective_date=timezone.now().date()
        )

        # 2. إنشاء الفاتورة
        invoice = Invoice.objects.create(
            invoice_number='2026-T001-002',
            trader=self.trader1,
            year=timezone.now().year,
            crop=self.crop,
            packing_method=Invoice.PackingMethod.MACHINE,
            packing_rate_snapshot=rate.rate_per_kg,
            created_by=self.accountant
        )

        # 3. توضيب فعلي 800 كغ صافي
        # الوزن المُدخل 864 كغ = 800 صافي + 80 صندوق × 0.800 كغ وزن فارغ،
        # والنظام يخصم وزن الصناديق تلقائياً فلا تُحسب كبضاعة.
        session = PackingService.start_packing_session(
            trader=self.trader1,
            crop=self.crop,
            packing_method=PackingSession.Method.MACHINE,
            source_pallets=[self.pallet1],
            user=self.accountant
        )
        outputs_data = [{
            'size_name': 'Medium',
            'box_type': self.box_blue,
            'box_count': 80,
            'net_weight_kg': Decimal('864.000'),
            'pallet_count': 1
        }]
        PackingService.record_outputs_and_finalize(session, outputs_data, user=self.accountant)

        # 4. مزامنة الفاتورة
        InvoiceService.sync_invoice_with_operations(invoice, user=self.accountant)

        invoice.refresh_from_db()
        self.assertEqual(invoice.total_packed_kg, Decimal('800.000'))
        # أجور التوضيب = 800 × 0.080 = 64.000 د.أ
        self.assertEqual(invoice.packing_subtotal, Decimal('64.000'))

        # عجز الصناديق الناتج عن استهلاك 80 صندوق عكاز أزرق بسعر 1.500 د.أ = 120.000 د.أ
        self.assertEqual(invoice.box_discount_subtotal, Decimal('120.000'))

        # الإجمالي النهائي = 64.000 + 120.000 = 184.000 د.أ
        self.assertEqual(invoice.grand_total, Decimal('184.000'))

        # إعادة المزامنة مرة ثانية لا تكرر الخصم أو تضاعفه (منع الخصم المزدوج)
        InvoiceService.sync_invoice_with_operations(invoice, user=self.accountant)
        invoice.refresh_from_db()
        self.assertEqual(invoice.grand_total, Decimal('184.000'))

        # اختبار توليد الـ PDF
        pdf_buffer = InvoiceService.generate_pdf(invoice)
        self.assertGreater(len(pdf_buffer.getvalue()), 1000)

    def test_one_pallet_per_output_row_with_own_weight_and_qr(self):
        """
        كل سطر ناتج = طبلية موضبة واحدة بوزنها الخاص ورمز QR خاص بها،
        دون حقل (عدد الطبالي الموضبة)، وحتى لو تكرر المقاس تبقى الرموز فريدة.
        (عدد الأسطر لا يتجاوز عدد طبالي الاستلام المتاحة في الجلسة)
        """
        session = PackingService.start_packing_session(
            trader=self.trader1,
            crop=self.crop,
            packing_method=PackingSession.Method.MACHINE,
            source_pallets=[self.pallet1, self.pallet2],
            user=self.accountant,
        )
        outputs_data = [
            {'size_name': 'XL', 'box_type': self.box_blue, 'box_count': 10,
             'net_weight_kg': Decimal('20.345'), 'pallet_count': 1},
            {'size_name': 'XL', 'box_type': self.box_blue, 'box_count': 8,
             'net_weight_kg': Decimal('16.276'), 'pallet_count': 1},
        ]
        PackingService.record_outputs_and_finalize(session, outputs_data, user=self.accountant)

        packed = list(session.packed_pallets.order_by('id'))
        self.assertEqual(len(packed), 2)
        # وزن كل طبلية = الوزن المُدخل ناقص وزن صناديقها الفارغة (10 × 0.800 = 8 كغ، 8 × 0.800 = 6.4 كغ)
        self.assertEqual(packed[0].net_weight_kg, Decimal('12.345'))
        self.assertEqual(packed[1].net_weight_kg, Decimal('9.876'))
        self.assertEqual(packed[0].box_count, 10)
        self.assertEqual(packed[1].box_count, 8)
        # رموز الطبالي فريدة رغم تطابق المقاس
        self.assertEqual(len({p.packed_pallet_code for p in packed}), 2)

        # لكل طبلية ملصق QR قابل للطباعة
        client = Client()
        client.force_login(self.manager)
        resp = client.get(reverse('packed_pallet_print_label', args=[packed[0].pk]))
        self.assertEqual(resp.status_code, 200)

    def test_packing_finalize_view_works_without_pallet_count_field(self):
        """
        إرسال نموذج الإنهاء بدون حقل (عدد الطبالي الموضبة) ينجح ويولّد طبلية لكل سطر
        بوزنه الخاص ونوع صندوقه.
        """
        session = PackingService.start_packing_session(
            trader=self.trader1,
            crop=self.crop,
            packing_method=PackingSession.Method.MACHINE,
            source_pallets=[self.pallet1],
            user=self.accountant,
        )
        client = Client()
        client.force_login(self.accountant)
        resp = client.post(reverse('packing_finalize', args=[session.pk]), {
            'size_name[]': ['P1 Standard'],
            'box_type[]': [str(self.box_blue.id)],
            'box_count[]': ['20'],
            # 100 كغ = وزن الطبلية كما تُوزن (84 صافي + 20 صندوق × 0.800 كغ)
            'net_weight_kg[]': ['100.000'],
            'waste_type[]': ['تالف'],
            'waste_weight[]': ['0.000'],
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(session.packed_pallets.count(), 1)
        packed = session.packed_pallets.first()
        # يُخصم وزن الصناديق الفارغة (20 × 0.800 = 16 كغ) تلقائياً من الوزن المُدخل
        self.assertEqual(packed.net_weight_kg, Decimal('84.000'))
        self.assertEqual(packed.box_count, 20)

    def test_packing_finalize_view_records_farm_empty_consumption(self):
        """
        إدخال نواتج التوضيب مع صناديق فارغ (من المزرعة) في قسم البرارة/التالف المستقل
        يُخصم عددها من الرصيد المتاح، ويُسجَّل الاستهلاك مرتبطاً بجلسة التوضيب.
        """
        from boxes.models import FarmBoxRecord

        FarmBoxRecord.objects.create(
            trader=self.trader1, truck_receipt=self.truck,
            incoming_count=100, empty_count=100, recorded_by=self.accountant,
        )

        session = PackingService.start_packing_session(
            trader=self.trader1,
            crop=self.crop,
            packing_method=PackingSession.Method.MACHINE,
            source_pallets=[self.pallet1],
            user=self.accountant,
        )
        client = Client()
        client.force_login(self.accountant)
        resp = client.post(reverse('packing_finalize', args=[session.pk]), {
            'size_name[]': ['P1 Standard'],
            'box_type[]': [str(self.box_blue.id)],
            'box_count[]': ['20'],
            'net_weight_kg[]': ['100.000'],
            'waste_type[]': ['تالف'],
            'waste_weight[]': ['0.000'],
            # قسم مستقل للبرارة والتالف: بلا fe_output_idx
            'fe_type[]': [str(self.farm_box_blue.id)],
            'fe_qty[]': ['30'],
        })
        self.assertEqual(resp.status_code, 302)

        from boxes.services import EmptyBoxService
        self.assertEqual(
            EmptyBoxService.consumed_in_packing(self.farm_box_blue.pk), 30
        )
        available = EmptyBoxService.available_empty(self.farm_box_blue.pk)
        self.assertEqual(available[self.farm_box_blue.pk], 70)
        self.assertEqual(session.farm_empty_consumptions.count(), 1)
        # السجل مرتبط بالجلسة نفسها (قسم مستقل عن نواتج المقاسات)
        self.assertEqual(
            session.farm_empty_consumptions.first().packing_session_id, session.pk
        )

    # ==================== تعديل وحذف عملية التوضيب ====================

    def test_edit_session_updates_basics_and_returns_removed_pallets(self):
        """
        زر «تعديل عملية التوضيب»: يحدّث البيانات الأساسية، ويُرجع الطبالي المُزالة
        من الجلسة إلى البراد، ويعيد احتساب وزن الداخل.
        """
        session = PackingService.start_packing_session(
            trader=self.trader1,
            crop=self.crop,
            packing_method=PackingSession.Method.MACHINE,
            source_pallets=[self.pallet1, self.pallet2],
            user=self.accountant,
        )
        self.pallet1.refresh_from_db()
        self.assertEqual(self.pallet1.status, Pallet.Status.OUT_FOR_PACKING)

        client = Client()
        client.force_login(self.accountant)
        resp = client.post(reverse('packing_edit', args=[session.pk]), {
            'trader': self.trader2.pk,
            'crop': self.crop.pk,
            'packing_method': PackingSession.Method.MANUAL,
            'workshop_name': 'ورشة معدّلة',
            'source_pallets': [self.pallet1.pk],
            'notes': 'تعديل تجريبي',
        })
        self.assertEqual(resp.status_code, 302)

        session.refresh_from_db()
        self.assertEqual(session.trader, self.trader2)
        self.assertEqual(session.packing_method, PackingSession.Method.MANUAL)
        self.assertEqual(session.workshop_name, 'ورشة معدّلة')
        self.assertEqual(session.source_pallets.count(), 1)
        # الطبلية التي أُزيلت من الجلسة عادت إلى البراد
        self.pallet2.refresh_from_db()
        self.assertEqual(self.pallet2.status, Pallet.Status.IN_COOLING)
        # الطبلية الباقية ما زالت خارج البراد
        self.pallet1.refresh_from_db()
        self.assertEqual(self.pallet1.status, Pallet.Status.OUT_FOR_PACKING)
        # وزن الداخل أُعيد احتسابه على الطبلية الباقية فقط
        self.assertEqual(session.input_weight_kg, self.pallet1.estimated_net_weight)

    def test_delete_session_returns_pallets_and_releases_farm_empty(self):
        """
        حذف عملية توضيب: يُرجع كل الطبالي المصدر إلى البراد، ويحذف الطبالي الموضّبة
        الناتجة، ويحرّر رصيد صناديق الفارغ المستهلك.
        """
        from boxes.models import FarmBoxRecord
        from boxes.services import EmptyBoxService

        FarmBoxRecord.objects.create(
            trader=self.trader1, truck_receipt=self.truck,
            incoming_count=100, empty_count=100, recorded_by=self.accountant,
        )

        session = PackingService.start_packing_session(
            trader=self.trader1,
            crop=self.crop,
            packing_method=PackingSession.Method.MACHINE,
            source_pallets=[self.pallet1, self.pallet2],
            user=self.accountant,
        )
        PackingService.record_outputs_and_finalize(session, [{
            'size_name': 'XL',
            'box_type': self.box_blue,
            'box_count': 10,
            'net_weight_kg': Decimal('50.000'),
            'pallet_count': 1,
        }], farm_empty_items=[
            {'farm_box_type': self.farm_box_blue, 'quantity': 40},
        ], user=self.accountant)

        session_id = session.pk
        self.pallet1.refresh_from_db()
        self.assertEqual(self.pallet1.status, Pallet.Status.PACKED)
        self.assertEqual(EmptyBoxService.consumed_in_packing(self.farm_box_blue.pk), 40)

        session.delete()

        self.assertFalse(PackingSession.objects.filter(pk=session_id).exists())
        # كل الطبالي المصدر (المستهلكة والمرتجعة) عادت إلى البراد
        self.pallet1.refresh_from_db()
        self.pallet2.refresh_from_db()
        self.assertEqual(self.pallet1.status, Pallet.Status.IN_COOLING)
        self.assertEqual(self.pallet2.status, Pallet.Status.IN_COOLING)
        # الفارغ المستهلك حُرِّر
        self.assertEqual(EmptyBoxService.consumed_in_packing(self.farm_box_blue.pk), 0)

    def test_finalize_ajax_returns_json_success(self):
        """
        طبقة AJAX: نفس الحفظ الذرّي، لكن الاستجابة JSON مختصرة (نجاح + إعادة توجيه)
        بدل صفحة كاملة — الواجهة لا تتجمّد ولا تدّعي نجاحاً كاذباً.
        """
        session = PackingService.start_packing_session(
            trader=self.trader1, crop=self.crop,
            packing_method=PackingSession.Method.MACHINE,
            source_pallets=[self.pallet1], user=self.accountant,
        )
        client = Client()
        client.force_login(self.accountant)
        resp = client.post(reverse('packing_finalize', args=[session.pk]), {
            'size_name[]': ['P1 Standard'],
            'box_type[]': [str(self.box_blue.id)],
            'box_count[]': ['20'],
            'net_weight_kg[]': ['100.000'],
            'waste_type[]': ['تالف'],
            'waste_weight[]': ['0.000'],
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertTrue(payload['ok'])
        self.assertIn('redirect', payload)
        self.assertEqual(session.packed_pallets.count(), 1)

    def test_edit_requires_login_and_rejects_completed_session(self):
        """التعديل يتطلب تسجيل الدخول، ويُرفض على جلسة مكتملة."""
        session = PackingService.start_packing_session(
            trader=self.trader1, crop=self.crop,
            packing_method=PackingSession.Method.MACHINE,
            source_pallets=[self.pallet1], user=self.accountant,
        )
        # بلا تسجيل دخول → تحويل لصفحة الدخول
        anon = Client().get(reverse('packing_edit', args=[session.pk]))
        self.assertEqual(anon.status_code, 302)
        self.assertIn('/login/', anon.url)

        # جلسة مكتملة → رفض التعديل
        PackingService.record_outputs_and_finalize(session, [{
            'size_name': 'XL', 'box_type': self.box_blue, 'box_count': 10,
            'net_weight_kg': Decimal('50.000'), 'pallet_count': 1,
        }], user=self.accountant)
        client = Client()
        client.force_login(self.accountant)
        resp = client.get(reverse('packing_edit', args=[session.pk]))
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse('packing_detail', args=[session.pk]), resp.url)


    def _make_packed_pallet(self):
        session = PackingService.start_packing_session(
            trader=self.trader1,
            crop=self.crop,
            packing_method=PackingSession.Method.MACHINE,
            source_pallets=[self.pallet1],
            user=self.accountant,
        )
        PackingService.record_outputs_and_finalize(session, [{
            'size_name': 'XL',
            'box_type': self.box_blue,
            'box_count': 10,
            'net_weight_kg': self.pallet1.estimated_net_weight,
            'pallet_count': 1,
        }], user=self.accountant)
        return session.packed_pallets.first()

    # ==================== السحب والإفلات من خريطة البرادات ====================

    def test_packed_pallet_move_endpoint_updates_shelf(self):
        """نقل طبلية موضبة عبر سحبها وأفلاتها يستدعي الخدمة ويغيّر سيخها."""
        pp = self._make_packed_pallet()
        PackingService.send_packed_pallets_to_cooling(
            [pp], cold_store=self.cold_store, shelf=self.shelf, user=self.accountant
        )
        pp.refresh_from_db()
        self.assertEqual(pp.shelf, self.shelf)

        shelf2 = Shelf.objects.create(cold_store=self.cold_store, shelf_number='2', capacity_pallets=25)
        self.client.force_login(self.accountant)
        url = reverse('packed_pallet_move', args=[pp.pk])

        resp = self.client.post(url, {'shelf': shelf2.pk, 'cold_store': self.cold_store.pk})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data['ok'])
        self.assertTrue(data['moved'])
        pp.refresh_from_db()
        self.assertEqual(pp.shelf, shelf2)

        # نفس الموقع: نجاح بلا حركة (لا يقفل ويُفتح فترات تبريد بلا داعي)
        resp_same = self.client.post(url, {'shelf': shelf2.pk, 'cold_store': self.cold_store.pk})
        self.assertTrue(resp_same.json()['ok'])
        self.assertFalse(resp_same.json()['moved'])

        # سجل الحركة بنوع «نقل» مسجّل
        self.assertTrue(
            PackedPalletCoolingLog.objects.filter(packed_pallet=pp, movement_type='MOVE').exists()
        )

    def test_packed_pallet_move_requires_accountant(self):
        pp = self._make_packed_pallet()
        PackingService.send_packed_pallets_to_cooling(
            [pp], cold_store=self.cold_store, shelf=self.shelf, user=self.accountant
        )
        viewer = User.objects.create_user(
            username='drag_viewer', password='pass', role=User.Role.VIEWER
        )
        self.client.force_login(viewer)
        resp = self.client.post(
            reverse('packed_pallet_move', args=[pp.pk]),
            {'shelf': self.shelf.pk, 'cold_store': self.cold_store.pk},
        )
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(resp.json()['ok'])
        pp.refresh_from_db()
        self.assertEqual(pp.shelf, self.shelf)

    def test_packed_pallet_move_rejects_post_only_and_missing_target(self):
        pp = self._make_packed_pallet()
        self.client.force_login(self.accountant)
        url = reverse('packed_pallet_move', args=[pp.pk])

        self.assertEqual(self.client.get(url).status_code, 405)
        resp = self.client.post(url, {})
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.json()['ok'])

    def test_packed_pallet_returns_to_cold_store_with_daily_cooling(self):
        """
        الطبلية الموضبة تعود إلى براد المشغل، ويُحتسب عدد أيام التبريد وأجرتها اليومية،
        ثم تُخرَج فيتوقف الاحتساب مع بقاء الأيام المسجلة.
        """
        settings_obj = SystemSetting.get_settings()
        settings_obj.packed_cooling_daily_rate = Decimal('0.500')
        settings_obj.save()

        pp = self._make_packed_pallet()
        self.assertEqual(pp.status, PackedPallet.Status.PACKED)

        # إدخال الطبلية الموضبة إلى البراد
        PackingService.send_packed_pallets_to_cooling(
            [pp], cold_store=self.cold_store, shelf=self.shelf,
            user=self.accountant, notes='تبريد موضب جاهز للتصدير',
        )
        pp.refresh_from_db()
        self.assertEqual(pp.status, PackedPallet.Status.IN_COOLING)
        self.assertEqual(pp.cold_store, self.cold_store)
        self.assertEqual(pp.shelf, self.shelf)
        self.assertEqual(pp.cooling_logs.count(), 1)
        self.assertTrue(pp.location_display.startswith(self.cold_store.name))

        # محاكاة بقاء الطبلية 3 أيام في البراد
        open_log = pp.cooling_logs.filter(exited_at__isnull=True).first()
        PackedPalletCoolingLog.objects.filter(pk=open_log.pk).update(
            entered_at=timezone.now() - timedelta(days=3)
        )
        pp.refresh_from_db()
        self.assertEqual(pp.cooling_days_total, 3)
        # الأجرة = 3 أيام × 0.500 = 1.500 دينار
        self.assertEqual(pp.cooling_fee, Decimal('1.500'))

        # إخراج من البراد وإيقاف الاحتساب
        PackingService.checkout_packed_pallets_from_cooling([pp], user=self.accountant)
        pp.refresh_from_db()
        self.assertEqual(pp.status, PackedPallet.Status.PACKED)
        self.assertIsNone(pp.cold_store)
        self.assertIsNone(pp.shelf)
        self.assertEqual(pp.cooling_days_total, 3)
        self.assertEqual(pp.cooling_fee, Decimal('1.500'))

    def test_invoice_sync_includes_daily_cooling_service(self):
        """
        مزامنة الفاتورة تُدرج بند أجرة التبريد اليومية للطبالي الموضبة المرجعة للبراد.
        """
        settings_obj = SystemSetting.get_settings()
        settings_obj.packed_cooling_daily_rate = Decimal('0.500')
        settings_obj.save()

        pp = self._make_packed_pallet()
        PackingService.send_packed_pallets_to_cooling(
            [pp], cold_store=self.cold_store, shelf=self.shelf, user=self.accountant
        )
        open_log = pp.cooling_logs.filter(exited_at__isnull=True).first()
        PackedPalletCoolingLog.objects.filter(pk=open_log.pk).update(
            entered_at=timezone.now() - timedelta(days=4)
        )

        invoice = Invoice.objects.create(
            invoice_number='2026-T001-COOL1',
            trader=self.trader1,
            year=timezone.now().year,
            crop=self.crop,
            packing_method=Invoice.PackingMethod.MACHINE,
            packing_rate_snapshot=Decimal('0.000'),
            created_by=self.accountant,
        )
        InvoiceService.sync_invoice_with_operations(invoice, user=self.accountant)
        invoice.refresh_from_db()

        # أجرة التبريد = 4 أيام × 0.500 = 2.000 دينار
        self.assertEqual(invoice.cooling_subtotal, Decimal('2.000'))
        # الإجمالي = أجرة التبريد 2.000 + خصم عجز 10 صناديق × 1.500 = 15.000 => 17.000 دينار
        self.assertEqual(invoice.grand_total, Decimal('17.000'))
        cooling_items = invoice.items.filter(item_type=InvoiceItem.ItemType.COOLING)
        self.assertEqual(cooling_items.count(), 1)
        self.assertEqual(cooling_items.first().quantity, Decimal('4.000'))

    # ==================== فصل عدد الطبالي الخام عن عدد الطبالي الموضبة ====================

    def test_output_rows_may_exceed_available_pallets(self):
        """
        تصحيح المنطق: عدد طبالي الخام لا يساوي عدد طبالي المنتج الموضب.
        ٤ طبالي خام قد تنتج طبالي موضبة أكثر، والعكس ممكن أيضاً.
        فلا يُرفض الإدخال لمجرد تجاوز عدد الأسطر عدد الطبالي الخام.
        """
        session = PackingService.start_packing_session(
            trader=self.trader1,
            crop=self.crop,
            packing_method=PackingSession.Method.MACHINE,
            source_pallets=[self.pallet1, self.pallet2],
            user=self.accountant,
        )
        self.assertEqual(session.source_pallets.count(), 2)

        over_data = [
            {'size_name': 'XL', 'box_type': self.box_blue, 'box_count': 10, 'net_weight_kg': Decimal('50.000'), 'pallet_count': 1},
            {'size_name': 'L', 'box_type': self.box_blue, 'box_count': 10, 'net_weight_kg': Decimal('50.000'), 'pallet_count': 1},
            {'size_name': 'M', 'box_type': self.box_blue, 'box_count': 10, 'net_weight_kg': Decimal('50.000'), 'pallet_count': 1},
        ]
        PackingService.record_outputs_and_finalize(session, over_data, user=self.accountant)

        session.refresh_from_db()
        self.assertEqual(session.outputs.count(), 3)
        self.assertEqual(session.packed_pallets.count(), 3)

    def test_output_rows_equal_to_available_pallets_is_allowed(self):
        """
        الحد المسموح: عدد الأسطر = عدد الطبالي المتاحة (طبلية موضبة واحدة لكل طبلية استلام).
        """
        session = PackingService.start_packing_session(
            trader=self.trader1,
            crop=self.crop,
            packing_method=PackingSession.Method.MACHINE,
            source_pallets=[self.pallet1, self.pallet2],
            user=self.accountant,
        )
        rows = [
            {'size_name': 'XL', 'box_type': self.box_blue, 'box_count': 10, 'net_weight_kg': Decimal('450.000'), 'pallet_count': 1},
            {'size_name': 'L', 'box_type': self.box_green, 'box_count': 10, 'net_weight_kg': Decimal('450.000'), 'pallet_count': 1},
        ]
        PackingService.record_outputs_and_finalize(session, rows, user=self.accountant)

        session.refresh_from_db()
        self.assertEqual(session.outputs.count(), 2)
        self.assertEqual(session.packed_pallets.count(), 2)

    def test_packing_finalize_view_allows_rows_more_than_pallets(self):
        """
        تصحيح المنطق: عدد أسطر النواتج (الطبالي الموضبة) غير مرتبط بعدد الطبالي الخام.
        فإدخال ٣ مقاسات من طبالي خام اثنتين يجب أن يُحفظ ويُنتج ٣ طبالي موضبة.
        """
        session = PackingService.start_packing_session(
            trader=self.trader1,
            crop=self.crop,
            packing_method=PackingSession.Method.MACHINE,
            source_pallets=[self.pallet1, self.pallet2],
            user=self.accountant,
        )
        client = Client()
        client.force_login(self.accountant)
        resp = client.post(reverse('packing_finalize', args=[session.pk]), {
            'size_name[]': ['XL', 'L', 'M'],
            'box_type[]': [str(self.box_blue.id)] * 3,
            'box_count[]': ['10', '10', '10'],
            'net_weight_kg[]': ['50.000', '50.000', '50.000'],
            'waste_type[]': ['تالف'],
            'waste_weight[]': ['0.000'],
        })
        self.assertEqual(resp.status_code, 302)

        session.refresh_from_db()
        self.assertEqual(session.outputs.count(), 3)
        self.assertEqual(session.packed_pallets.count(), 3)

    def test_resubmitting_finalize_cannot_exceed_available_pallets(self):
        """
        إعادة الإرسال (مثلاً جلسة بانتظار اعتماد المدير) لا تُنتج طبالي موضبة أكثر من الطبالي المتاحة.
        """
        session = PackingService.start_packing_session(
            trader=self.trader1,
            crop=self.crop,
            packing_method=PackingSession.Method.MACHINE,
            source_pallets=[self.pallet1, self.pallet2],
            user=self.accountant,
        )
        first = [
            {'size_name': 'XL', 'box_type': self.box_blue, 'box_count': 10, 'net_weight_kg': Decimal('100.000'), 'pallet_count': 1},
            {'size_name': 'L', 'box_type': self.box_blue, 'box_count': 10, 'net_weight_kg': Decimal('100.000'), 'pallet_count': 1},
        ]
        PackingService.record_outputs_and_finalize(session, first, user=self.accountant)
        session.refresh_from_db()
        self.assertEqual(session.packed_pallets.count(), 2)

        with self.assertRaises(ValidationError):
            PackingService.record_outputs_and_finalize(session, [
                {'size_name': 'M', 'box_type': self.box_blue, 'box_count': 5, 'net_weight_kg': Decimal('50.000'), 'pallet_count': 1},
            ], user=self.accountant)

        session.refresh_from_db()
        self.assertEqual(session.packed_pallets.count(), 2)

    # ==================== رمز QR ملصق الطبلية الموضبة ====================

    def test_packed_pallet_label_shows_qr_only_and_qr_opens_scan_page(self):
        """
        ملصق الطبلية الموضبة يحتوي رمز QR فقط بلا معلومات مكتوبة،
        والرمز يُشفّر رابط صفحة الطبلية الموضبة التي تعرض بياناتها كاملة.
        """
        packed = self._make_packed_pallet()
        client = Client()
        client.force_login(self.accountant)
        resp = client.get(reverse('packed_pallet_print_label', args=[packed.pk]))
        self.assertEqual(resp.status_code, 200)

        expected_url = f"http://testserver{reverse('packed_pallet_qr_scan', args=[packed.packed_pallet_code])}"
        self.assertContains(resp, f'data-qr-url="{expected_url}"')
        self.assertNotContains(resp, 'data-val')

    def test_packed_pallet_qr_scan_page_shows_full_info_without_login(self):
        """
        صفحة الطبلية الموضبة تُفتح من الهاتف بلا تسجيل دخول وتعرض بياناتها كاملة
        مع الطبلية الأصلية والتاجر والموقع وفترات التبريد.
        """
        packed = self._make_packed_pallet()
        public_client = Client()
        resp = public_client.get(reverse('packed_pallet_qr_scan', args=[packed.packed_pallet_code]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, packed.packed_pallet_code)
        self.assertContains(resp, packed.original_pallet.pallet_code)
        self.assertContains(resp, self.trader1.name)
        self.assertContains(resp, packed.session.session_code)

    def test_packed_pallet_qr_uses_configured_scan_base_url(self):
        """
        عند ضبط (عنوان السيرفر لروابط مسح QR) يُشفَّر داخل رمز الملصق
        ليعمل المسح من هواتف الشبكة المحلية (نظام يعمل دون إنترنت).
        """
        self.settings.scan_base_url = 'http://192.168.1.50:8000'
        self.settings.save()
        packed = self._make_packed_pallet()
        client = Client()
        client.force_login(self.accountant)
        resp = client.get(reverse('packed_pallet_print_label', args=[packed.pk]))

        expected_url = f"http://192.168.1.50:8000{reverse('packed_pallet_qr_scan', args=[packed.packed_pallet_code])}"
        self.assertContains(resp, f'data-qr-url="{expected_url}"')


class PackagingItemsChargeTests(TestCase):
    """
    أصناف التغليف العادية في نواتج التوضيب:
    - يمكن إضافة أكثر من صنف لناتج واحد، وكل صنف من مجموعة تغليف مختلفة.
    - تُجمَّد أسعارها وقت الحفظ (لقطة) وتُخصم من التاجر.
    - تُرحَّل قيمتها إلى فاتورة التاجر بند «خدمات إضافية» مرة واحدة فقط.
    """

    def setUp(self):
        from operations.models import PackagingGroup
        from packing.models import (
            PackingOutputPackagingItem, PackingSessionPackagingCharge,
        )
        self.PackagingGroup = PackagingGroup
        self.PackingOutputPackagingItem = PackingOutputPackagingItem
        self.PackingSessionPackagingCharge = PackingSessionPackagingCharge

        self.settings = SystemSetting.get_settings()
        self.settings.weight_tolerance_pct = Decimal('100.00')  # لا نريد اعتماد مدير هنا
        self.settings.save()

        self.accountant = User.objects.create_user(
            username='pkg_acc', password='pass', role=User.Role.ACCOUNTANT
        )
        self.trader = Trader.objects.create(code='TP01', name='تاجر التغليف')
        self.crop = Crop.objects.create(name='خوخ')

        self.group_boxes = PackagingGroup.objects.create(name='الصناديق')
        self.group_paper = PackagingGroup.objects.create(name='ورق التغليف')
        self.group_stickers = PackagingGroup.objects.create(name='الستيكرات')

        # الصناديق الأساسية المستخدمة في التوضيب
        self.box = BoxType.objects.create(
            name='عكاز أساسي', color_or_type='أزرق', group=self.group_boxes,
            tare_weight=Decimal('0.800'), discount_price=Decimal('1.500'), company_stock=1000,
        )
        # أصناف تغليف عادية من مجموعات مختلفة
        self.paper = BoxType.objects.create(
            name='ورق تغليف', color_or_type='أبيض', group=self.group_paper,
            tare_weight=Decimal('0.001'), discount_price=Decimal('0.080'), company_stock=10000,
            is_box=False,
        )
        self.sticker = BoxType.objects.create(
            name='ستيكر بلد المنشأ', color_or_type='ملون', group=self.group_stickers,
            tare_weight=Decimal('0.001'), discount_price=Decimal('0.030'), company_stock=10000,
            is_box=False,
        )

        self.cold_store = ColdStore.objects.create(name='براد التغليف', code='CS-PK', capacity_pallets=20)
        self.shelf = Shelf.objects.create(cold_store=self.cold_store, shelf_number='1', capacity_pallets=10)

        self.farm_box = FarmBoxType.objects.create(name='صندوق مزرعة للتغليف', tare_weight=Decimal('0.800'))
        self.truck = ReceptionService.process_truck_reception({
            'trader': self.trader.id,
            'crop': self.crop.id,
            'box_type': self.farm_box.id,
            'box_count': 100,
            'gross_weight': Decimal('2000.000'),
            'tare_weight': Decimal('1000.000'),
            'pallet_count': 2,
            'cold_store': self.cold_store.id,
            'shelf': self.shelf.id,
        }, user=self.accountant)
        self.pallet = self.truck.pallets.first()

    def _start_session(self):
        return PackingService.start_packing_session(
            trader=self.trader, crop=self.crop,
            packing_method=PackingSession.Method.MACHINE,
            source_pallets=[self.pallet], user=self.accountant,
        )

    def test_multiple_packaging_items_from_different_groups_are_recorded(self):
        session = self._start_session()
        PackingService.record_outputs_and_finalize(session, [{
            'size_name': 'XL',
            'box_type': self.box,
            'box_count': 80,
            'net_weight_kg': self.pallet.estimated_net_weight,
            'pallet_count': 1,
            'packaging_items': [
                {'packaging_item': self.paper, 'quantity': Decimal('80')},
                {'packaging_item': self.sticker, 'quantity': Decimal('80')},
                {'packaging_item': self.box, 'quantity': Decimal('5')},
            ],
        }], user=self.accountant)

        output = session.outputs.first()
        self.assertEqual(output.packaging_items.count(), 3)
        groups = set(output.packaging_items.values_list('packaging_group_name', flat=True))
        self.assertEqual(groups, {'الصناديق', 'ورق التغليف', 'الستيكرات'})

    def test_prices_are_frozen_and_charge_computed(self):
        session = self._start_session()
        PackingService.record_outputs_and_finalize(session, [{
            'size_name': 'XL', 'box_type': self.box, 'box_count': 80,
            'net_weight_kg': self.pallet.estimated_net_weight, 'pallet_count': 1,
            'packaging_items': [
                {'packaging_item': self.paper, 'quantity': Decimal('80')},   # 80 × 0.080 = 6.400
                {'packaging_item': self.sticker, 'quantity': Decimal('80')},  # 80 × 0.030 = 2.400
            ],
        }], user=self.accountant)

        charge = self.PackingSessionPackagingCharge.objects.get(session=session)
        self.assertEqual(charge.trader, self.trader)
        self.assertEqual(charge.total_amount, Decimal('8.800'))
        self.assertFalse(charge.is_posted)

        # تغيير سعر الصنف لاحقاً لا يغيّر اللقطة المجمدة
        self.paper.discount_price = Decimal('9.999')
        self.paper.save()
        session.refresh_from_db()
        output = session.outputs.first()
        paper_line = output.packaging_items.get(packaging_item=self.paper)
        self.assertEqual(paper_line.unit_price_snapshot, Decimal('0.080'))
        self.assertEqual(paper_line.total_price, Decimal('6.400'))
        self.assertEqual(PackingService.packaging_charge_total_for_session(session), Decimal('8.800'))

    def test_charge_is_posted_to_trader_invoice_once(self):
        session = self._start_session()
        PackingService.record_outputs_and_finalize(session, [{
            'size_name': 'XL', 'box_type': self.box, 'box_count': 80,
            'net_weight_kg': self.pallet.estimated_net_weight, 'pallet_count': 1,
            'packaging_items': [
                {'packaging_item': self.paper, 'quantity': Decimal('100')},  # 8.000
            ],
        }], user=self.accountant)

        invoice = Invoice.objects.create(
            invoice_number='2026-TP01-001', trader=self.trader, year=2026, crop=self.crop,
            packing_method=Invoice.PackingMethod.MACHINE,
            packing_rate_snapshot=Decimal('0.070'), created_by=self.accountant,
        )
        InvoiceService.sync_invoice_with_operations(invoice, user=self.accountant)
        invoice.refresh_from_db()

        packaging_items = invoice.items.filter(item_type=InvoiceItem.ItemType.SERVICE)
        self.assertEqual(packaging_items.count(), 1)
        self.assertEqual(packaging_items.first().unit_price, Decimal('8.000'))
        self.assertEqual(invoice.other_services, Decimal('8.000'))
        # الإجمالي = أجور التوضيب + خصم عجز الصناديق + أصناف التغليف
        expected_total = (
            invoice.packing_subtotal + invoice.box_discount_subtotal + Decimal('8.000')
        ).quantize(Decimal('0.001'))
        self.assertEqual(invoice.grand_total, expected_total)

        charge = self.PackingSessionPackagingCharge.objects.get(session=session)
        self.assertTrue(charge.is_posted)
        self.assertEqual(charge.posted_invoice_id, invoice.pk)

        # مزامنة ثانية لا تُكرر البند (الترحيل مرة واحدة)
        invoice.status = Invoice.Status.DRAFT
        InvoiceService.sync_invoice_with_operations(invoice, user=self.accountant)
        invoice.refresh_from_db()
        self.assertEqual(invoice.items.filter(item_type=InvoiceItem.ItemType.SERVICE).count(), 1)
        self.assertEqual(invoice.other_services, Decimal('8.000'))

    def test_packaging_items_do_not_affect_packed_weight_or_randeman(self):
        session = self._start_session()
        # الوزن المُدخل = الوزن الصافي التقديري للطبلية + وزن الصناديق الفارغة (80 × 0.800)
        entered = self.pallet.estimated_net_weight + Decimal('64.000')
        PackingService.record_outputs_and_finalize(session, [{
            'size_name': 'XL', 'box_type': self.box, 'box_count': 80,
            'net_weight_kg': entered, 'pallet_count': 1,
            'packaging_items': [
                {'packaging_item': self.paper, 'quantity': Decimal('80')},
            ],
        }], user=self.accountant)
        session.refresh_from_db()
        # الوزن الموضب = الصافي بعد خصم وزن الصناديق فقط،
        # ولا تدخل أوزان أصناف التغليف (الورق) في الوزن ولا في الرندمان
        self.assertEqual(session.output_packed_kg, self.pallet.estimated_net_weight)

    def test_zero_quantity_packaging_items_are_ignored(self):
        session = self._start_session()
        PackingService.record_outputs_and_finalize(session, [{
            'size_name': 'XL', 'box_type': self.box, 'box_count': 80,
            'net_weight_kg': self.pallet.estimated_net_weight, 'pallet_count': 1,
            'packaging_items': [
                {'packaging_item': self.paper, 'quantity': Decimal('0')},
            ],
        }], user=self.accountant)
        self.assertFalse(self.PackingSessionPackagingCharge.objects.filter(session=session).exists())

    def test_box_tare_weight_is_deducted_from_packed_weight(self):
        """
        وزن الصناديق الفارغة يُخصم تلقائياً من الوزن المُدخل، فلا يُحاسَب
        التاجر على وزن الصناديق كأنه بضاعة موضبة، ولا يتضخم الرندمان.
        """
        session = PackingService.start_packing_session(
            trader=self.trader,
            crop=self.crop,
            packing_method=PackingSession.Method.MACHINE,
            source_pallets=[self.pallet],
            user=self.accountant,
        )
        input_weight = session.input_weight_kg

        # 30 صندوق × 0.800 كغ = 24 كغ وزن صناديق
        entered = input_weight  # نُدخل وزناً يساوي الداخل تماماً
        PackingService.record_outputs_and_finalize(session, [{
            'size_name': 'XL', 'box_type': self.box, 'box_count': 30,
            'net_weight_kg': entered, 'pallet_count': 1,
        }], user=self.accountant)

        session.refresh_from_db()
        expected_net = (entered - Decimal('24.000')).quantize(Decimal('0.001'))
        self.assertEqual(session.output_packed_kg, expected_net)
        self.assertEqual(session.packed_pallets.first().net_weight_kg, expected_net)
        # الرندمان محسوب على الصافي الحقيقي لا على الوزن المُدخل
        expected_randeman = ((expected_net / input_weight) * Decimal('100.00')).quantize(Decimal('0.01'))
        self.assertEqual(session.randeman_pct, expected_randeman)

    def test_entered_weight_lower_than_box_tare_is_rejected(self):
        """
        لو كان الوزن المُدخل أقل من وزن صناديقها الفارغة، تُرفض العملية
        برسالة واضحة بدل تسجيل وزن سالب.
        """
        from django.core.exceptions import ValidationError

        session = PackingService.start_packing_session(
            trader=self.trader,
            crop=self.crop,
            packing_method=PackingSession.Method.MACHINE,
            source_pallets=[self.pallet],
            user=self.accountant,
        )
        with self.assertRaises(ValidationError):
            PackingService.record_outputs_and_finalize(session, [{
                'size_name': 'XL', 'box_type': self.box, 'box_count': 100,
                'net_weight_kg': Decimal('50.000'),  # أقل من 100 × 0.800 = 80 كغ
                'pallet_count': 1,
            }], user=self.accountant)
        session.refresh_from_db()
        self.assertEqual(session.outputs.count(), 0)

    def test_packing_detail_page_shows_packaging_picker(self):
        session = self._start_session()
        self.client.force_login(self.accountant)
        resp = self.client.get(reverse('packing_detail', args=[session.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'pkgItemTemplate')
        self.assertContains(resp, 'أصناف التغليف المستخدمة في هذا المقاس')

    def test_packing_detail_has_no_prefilled_output_row(self):
        """
        لا يُعرض أي سطر ناتج جاهز عند فتح صفحة الإدخال، حتى لا يظهر مقاس
        أو عدد صناديق معبّأ مسبقاً. سطر الناتج يُبنى من قالب عند طلب المستخدم فقط.
        """
        session = self._start_session()
        self.client.force_login(self.accountant)
        resp = self.client.get(reverse('packing_detail', args=[session.pk]))
        self.assertEqual(resp.status_code, 200)
        # الحاوية فارغة، والسطر موجود كقالب فقط
        self.assertContains(resp, 'id="outputsContainer"></div>')
        self.assertContains(resp, 'id="outputRowTemplate"')
        # لا قيم معبّأة مسبقاً في سطر الناتج
        self.assertNotContains(resp, 'value="P1 Standard"')
        self.assertNotContains(resp, 'value="80"')

    def test_packing_detail_farm_empty_section_is_independent_of_outputs(self):
        """
        قسم فارغ المزرعة للبرارة والتالف قسم مستقل تماماً عن أسطر نواتج المقاسات.
        """
        session = self._start_session()
        self.client.force_login(self.accountant)
        resp = self.client.get(reverse('packing_detail', args=[session.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'id="farmEmptyContainer"')
        self.assertContains(resp, 'id="addFarmEmptyRow"')
        self.assertContains(resp, 'صناديق الفارغ (من المزرعة) المستهلكة في البرارة والتالف')
        # لم يعد مرتبطاً بسطر الناتج
        self.assertNotContains(resp, 'fe_output_idx[]')

    def test_farm_empty_consumption_is_session_level_not_per_output(self):
        """
        تسجيل استهلاك فارغ المزرعة لا يحتاج ربطه بسطر ناتج؛ يُسجَّل على الجلسة مباشرة.
        """
        from boxes.models import FarmBoxRecord
        from boxes.services import EmptyBoxService

        FarmBoxRecord.objects.create(
            trader=self.trader, truck_receipt=self.truck,
            incoming_count=50, empty_count=50, recorded_by=self.accountant,
        )
        session = self._start_session()
        PackingService.record_outputs_and_finalize(
            session,
            [{
                'size_name': 'XL', 'box_type': self.box, 'box_count': 80,
                'net_weight_kg': self.pallet.estimated_net_weight,
                'pallet_count': 1,
            }],
            farm_empty_items=[
                {'farm_box_type': self.farm_box, 'quantity': 12},
            ],
            user=self.accountant,
        )
        cons = session.farm_empty_consumptions.all()
        self.assertEqual(cons.count(), 1)
        self.assertEqual(cons.first().quantity, 12)
        self.assertEqual(EmptyBoxService.consumed_in_packing(self.farm_box.pk), 12)


# =============================================================================
# تصحيح منطق التوضيب:
#   فصل عدد طبالي الخام عن عدد طبالي المنتج الموضب
#   + دفتر الكميات الخام (مصدر كل كمية وموقع صرفها)
#   + سؤال المحاسب الإلزامي عند إنهاء العملية
# =============================================================================


class PackingQuantityLedgerTests(TestCase):
    """
    اختبارات منطق الكميات الجديد (تُثبت المتطلبات السبعة):

    1) ٤ طبالي خام يمكن أن تنتج طباليتين موضبتين دون افتراض طبلية موضبة لكل طبلية خام.
    2) إمكانية إكمال طبلية موضبة بصناديق من طبلية أخرى في البراد.
    3) بقاء الصناديق غير المستخدمة في رصيد الطبلية الأصلية.
    4) صحة أرصدة كل مصدر بعد سحب الصناديق.
    5) منع تجاوز الرصيد أو تكرار الصرف.
    6) منع إغلاق العملية مع وجود كميات غير محسومة.
    7) منع إدخال خام جديد إلى دفعة مغلقة.
    """

    def setUp(self):
        self.settings = SystemSetting.get_settings()
        self.settings.company_name_en = 'Royal Fruit'
        # حد سماح واسع: هذه الاختبارات تركز على منطق الكميات لا على فرق الوزن
        self.settings.weight_tolerance_pct = Decimal('100.00')
        self.settings.save()

        self.manager = User.objects.create_user(
            username='qty_manager', password='pass', role=User.Role.MANAGER
        )
        self.accountant = User.objects.create_user(
            username='qty_accountant', password='pass', role=User.Role.ACCOUNTANT
        )

        self.trader = Trader.objects.create(code='T100', name='تاجر الكميات')
        self.crop = Crop.objects.create(name='فواكه')
        self.box = BoxType.objects.create(
            name='صندوق تعبئة',
            color_or_type='أزرق',
            tare_weight=Decimal('0.800'),
            discount_price=Decimal('1.500'),
            company_stock=1000,
        )
        self.farm_box = FarmBoxType.objects.create(
            name='صندوق مزرعة',
            description='أزرق',
            tare_weight=Decimal('0.800'),
        )
        self.cold_store = ColdStore.objects.create(name='براد الكميات', code='CS-Q1', capacity_pallets=50)
        self.shelf = Shelf.objects.create(cold_store=self.cold_store, shelf_number='1', capacity_pallets=25)

        # سيارة واحدة بـ ٢٠٠ صندوق موزّعة على ٤ طبالي (٥٠ صندوقاً لكل طبلية)
        self.truck = ReceptionService.process_truck_reception({
            'trader': self.trader.id,
            'crop': self.crop.id,
            'box_type': self.farm_box.id,
            'box_count': 200,
            'gross_weight': Decimal('4000.000'),
            'tare_weight': Decimal('2000.000'),
            'pallet_count': 4,
            'cold_store': self.cold_store.id,
            'shelf': self.shelf.id,
        }, user=self.accountant)
        self.pallets = list(self.truck.pallets.order_by('id'))
        self.assertEqual(len(self.pallets), 4)
        for p in self.pallets:
            self.assertEqual(p.box_count, 50)
            self.assertEqual(p.remaining_box_count, 50)
            self.assertEqual(p.estimated_net_weight, Decimal('450.000'))

    # ---------------- أدوات مساعدة ----------------

    def _start_session(self, source_pallets):
        return PackingService.start_packing_session(
            trader=self.trader,
            crop=self.crop,
            packing_method=PackingSession.Method.MACHINE,
            source_pallets=list(source_pallets),
            user=self.accountant,
        )

    def _output(self, size_name='XL', boxes=10, weight='100.000'):
        return {
            'size_name': size_name,
            'box_type': self.box,
            'box_count': boxes,
            'net_weight_kg': Decimal(weight),
            'pallet_count': 1,
        }

    def _extra_cold_store_pallet(self):
        """طبلية خام ما زالت في البراد (لم تدخل أي جلسة توضيب)."""
        truck2 = ReceptionService.process_truck_reception({
            'trader': self.trader.id,
            'crop': self.crop.id,
            'box_type': self.farm_box.id,
            'box_count': 50,
            'gross_weight': Decimal('1000.000'),
            'tare_weight': Decimal('500.000'),
            'pallet_count': 1,
            'cold_store': self.cold_store.id,
            'shelf': self.shelf.id,
        }, user=self.accountant)
        return truck2.pallets.first()

    # ---------------- 1) فصل عدد الطبالي ----------------

    def test_four_raw_pallets_can_produce_two_packed_pallets(self):
        """
        ٤ طبالي خام تُنتج طباليتين موضبتين: لا افتراض بوجود طبلية موضبة مقابل كل طبلية خام،
        ولا استنتاج عدد الطبالي المستهلكة من الفرق بين العددين.
        """
        session = self._start_session(self.pallets)
        self.assertEqual(session.source_pallets.count(), 4)

        outputs = [self._output('XL'), self._output('L')]
        allocations = [
            [
                {'source_pallet': self.pallets[0].id, 'box_count': 40},
                {'source_pallet': self.pallets[1].id, 'box_count': 30},
            ],
            [
                {'source_pallet': self.pallets[2].id, 'box_count': 45},
                {'source_pallet': self.pallets[3].id, 'box_count': 20},
            ],
        ]
        PackingService.record_outputs_and_finalize(
            session, outputs, allocations=allocations, all_packed=False, user=self.accountant
        )
        session.refresh_from_db()

        # طبلتان موضبتان فقط من أربع طبالي خام
        self.assertEqual(session.packed_pallets.count(), 2)
        self.assertEqual(session.source_pallets.count(), 4)
        # كل طبلية موضبة لها مصدران فعليان مسجّلان
        for packed in session.packed_pallets.all():
            self.assertEqual(packed.raw_consumptions.count(), 2)
        # ليست الطبالي مستهلكة بالكامل → العملية تبقى مفتوحة
        self.assertEqual(session.status, PackingSession.Status.INCOMPLETE)
        self.assertFalse(session.all_packed_confirmed)
        # وزن الداخل محسوب من السحوبات الموثّقة (لا من عدد الطبالي)
        expected_input = (
            Decimal('450.000') * Decimal(40) / Decimal(50)
            + Decimal('450.000') * Decimal(30) / Decimal(50)
            + Decimal('450.000') * Decimal(45) / Decimal(50)
            + Decimal('450.000') * Decimal(20) / Decimal(50)
        ).quantize(Decimal('0.001'))
        self.assertEqual(session.input_weight_kg, expected_input)

    # ---------------- 2) إكمال طبلية موضبة من طبلية في البراد ----------------

    def test_packed_pallet_can_be_completed_from_another_pallet_in_cold_store(self):
        """
        أخذ صناديق ناقصة من طبلية خام موجودة في البراد لإكمال طبلية موضبة،
        مع تسجيل مصدر الكمية وموقعها وصرفها من رصيدها المتاح فقط.
        """
        session = self._start_session([self.pallets[0]])
        store_pallet = self._extra_cold_store_pallet()
        self.assertEqual(store_pallet.status, Pallet.Status.IN_COOLING)

        outputs = [self._output('XL')]
        allocations = [[
            {'source_pallet': self.pallets[0].id, 'box_count': 30},
            {'source_pallet': store_pallet.id, 'box_count': 20},
        ]]
        PackingService.record_outputs_and_finalize(
            session, outputs, allocations=allocations, all_packed=False, user=self.accountant
        )
        session.refresh_from_db()

        # الطبلية المسحوبة من البراد أصبحت ضمن طبالي مصدر العملية
        self.assertIn(store_pallet.id, list(session.source_pallets.values_list('id', flat=True)))
        store_pallet.refresh_from_db()
        self.assertEqual(store_pallet.consumed_box_count, 20)
        self.assertEqual(store_pallet.remaining_box_count, 30)
        self.assertEqual(store_pallet.status, Pallet.Status.OPEN_PARTIAL)

        # حركة صرف من موقع التخزين موثّقة
        movement = PalletMovementLog.objects.filter(
            pallet=store_pallet, movement_type='TO_PACKING'
        ).order_by('-id').first()
        self.assertIsNotNone(movement)
        self.assertEqual(movement.from_cold_store_id, self.cold_store.id)

        # سطر في دفتر الكميات الخام يربط المصدر بالطبلية الموضبة
        cons = session.raw_consumptions.filter(source_pallet=store_pallet).first()
        self.assertIsNotNone(cons)
        self.assertEqual(cons.box_count, 20)
        self.assertEqual(cons.packed_pallet_id, session.packed_pallets.first().id)
        self.assertEqual(cons.cold_store_id, self.cold_store.id)
        self.assertIsNotNone(cons.movement_id)

    # ---------------- 3) بقاء الصناديق غير المستخدمة ----------------

    def test_unused_boxes_remain_in_source_pallet_balance(self):
        """
        سحب جزء من طبلية خام لا يعني استهلاكها بالكامل:
        يبقى الباقي في رصيدها ولا تُعتبر مستهلكة.
        """
        session = self._start_session([self.pallets[0]])
        PackingService.record_outputs_and_finalize(
            session, [self._output()],
            allocations=[[{'source_pallet': self.pallets[0].id, 'box_count': 10}]],
            all_packed=False, user=self.accountant,
        )
        pallet = Pallet.objects.get(pk=self.pallets[0].id)
        self.assertEqual(pallet.consumed_box_count, 10)
        self.assertEqual(pallet.remaining_box_count, 40)
        self.assertEqual(pallet.status, Pallet.Status.OPEN_PARTIAL)
        self.assertNotEqual(pallet.status, Pallet.Status.PACKED)

        session.refresh_from_db()
        self.assertEqual(session.status, PackingSession.Status.INCOMPLETE)
        self.assertEqual(len(session.leftover_pallets), 1)
        self.assertEqual(session.leftover_box_count, 40)

    # ---------------- 4) صحة أرصدة كل مصدر ----------------

    def test_each_source_pallet_balance_is_correct_after_drawing(self):
        """
        صحة أرصدة كل مصدر بعد سحب الصناديق: المستهلَك + المتبقي = أصل الطبلية.
        """
        session = self._start_session(self.pallets)
        outputs = [self._output('XL'), self._output('L')]
        allocations = [
            [
                {'source_pallet': self.pallets[0].id, 'box_count': 40},
                {'source_pallet': self.pallets[1].id, 'box_count': 30},
            ],
            [
                {'source_pallet': self.pallets[2].id, 'box_count': 45},
                {'source_pallet': self.pallets[3].id, 'box_count': 20},
            ],
        ]
        PackingService.record_outputs_and_finalize(
            session, outputs, allocations=allocations, all_packed=False, user=self.accountant
        )
        for pallet, used in zip(self.pallets, [40, 30, 45, 20]):
            pallet.refresh_from_db()
            self.assertEqual(pallet.consumed_box_count, used)
            self.assertEqual(pallet.remaining_box_count, 50 - used)
            self.assertEqual(pallet.consumed_box_count + pallet.remaining_box_count, pallet.box_count)

        # مجموع ما سُحب = مجموع الاستهلاك في الدفتر
        self.assertEqual(sum(c.box_count for c in session.raw_consumptions.all()), 135)
        # ملخص الدفتر يغطي كل الطبالي الأربع
        self.assertEqual(session.raw_consumptions.count(), 4)
        self.assertEqual(
            set(c.source_pallet_id for c in session.raw_consumptions.all()),
            set(p.id for p in self.pallets),
        )

    # ---------------- 5) منع تجاوز الرصيد وتكرار الصرف ----------------

    def test_cannot_exceed_available_balance(self):
        session = self._start_session([self.pallets[0]])
        with self.assertRaises(ValidationError):
            PackingService.record_outputs_and_finalize(
                session, [self._output(boxes=5)],
                allocations=[[{'source_pallet': self.pallets[0].id, 'box_count': 60}]],
                all_packed=True, user=self.accountant,
            )
        session.refresh_from_db()
        self.assertEqual(session.packed_pallets.count(), 0)
        self.assertEqual(session.raw_consumptions.count(), 0)
        pallet = Pallet.objects.get(pk=self.pallets[0].id)
        self.assertEqual(pallet.remaining_box_count, 50)
        self.assertEqual(pallet.consumed_box_count, 0)

    def test_same_boxes_cannot_be_issued_twice(self):
        """نفس الصناديق لا تُصرف مرتين على نواتج مختلفة."""
        session = self._start_session([self.pallets[0]])
        with self.assertRaises(ValidationError):
            PackingService.record_outputs_and_finalize(
                session, [self._output('XL'), self._output('L')],
                allocations=[
                    [{'source_pallet': self.pallets[0].id, 'box_count': 30}],
                    [{'source_pallet': self.pallets[0].id, 'box_count': 30}],
                ],
                all_packed=True, user=self.accountant,
            )
        session.refresh_from_db()
        self.assertEqual(session.raw_consumptions.count(), 0)
        pallet = Pallet.objects.get(pk=self.pallets[0].id)
        self.assertEqual(pallet.consumed_box_count, 0)
        self.assertEqual(pallet.remaining_box_count, 50)

    def test_each_output_row_must_declare_its_raw_source(self):
        """كل سطر ناتج يجب أن يبيّن مصدر كميته الخام."""
        session = self._start_session([self.pallets[0]])
        with self.assertRaises(ValidationError):
            PackingService.record_outputs_and_finalize(
                session, [self._output()], allocations=[[]], all_packed=True, user=self.accountant,
            )

    def test_cannot_draw_from_pallet_of_another_trader_or_crop(self):
        """لا يُسمح بخلط أصناف أو تشغيلات غير متوافقة."""
        other_trader = Trader.objects.create(code='T200', name='تاجر آخر')
        other_truck = ReceptionService.process_truck_reception({
            'trader': other_trader.id,
            'crop': self.crop.id,
            'box_type': self.farm_box.id,
            'box_count': 50,
            'gross_weight': Decimal('1000.000'),
            'tare_weight': Decimal('500.000'),
            'pallet_count': 1,
            'cold_store': self.cold_store.id,
            'shelf': self.shelf.id,
        }, user=self.accountant)
        other_pallet = other_truck.pallets.first()

        session = self._start_session([self.pallets[0]])
        with self.assertRaises(ValidationError):
            PackingService.record_outputs_and_finalize(
                session, [self._output()],
                allocations=[[{'source_pallet': other_pallet.id, 'box_count': 10}]],
                all_packed=True, user=self.accountant,
            )

    # ---------------- 6) منع الإغلاق مع كميات غير محسومة ----------------

    def test_cannot_close_session_with_unresolved_leftovers(self):
        """
        لا يجوز الإغلاق ما دام في طبلية خام متبقٍ لم يُحدَّد مصيره،
        ولا قبل تسجيل تسوية معتمدة.
        """
        session = self._start_session([self.pallets[0]])

        # الإجابة «نعم» مع بقاء خام ⇒ رفض
        with self.assertRaises(ValidationError):
            PackingService.record_outputs_and_finalize(
                session, [self._output()],
                allocations=[[{'source_pallet': self.pallets[0].id, 'box_count': 10}]],
                all_packed=True, user=self.accountant,
            )
        session.refresh_from_db()
        self.assertEqual(session.status, PackingSession.Status.DRAFT)
        self.assertFalse(session.all_packed_confirmed)
        self.assertEqual(session.packed_pallets.count(), 0)

        # الإجابة «لا» ⇒ العملية تبقى مفتوحة (توضيب غير مكتمل)
        PackingService.record_outputs_and_finalize(
            session, [self._output()],
            allocations=[[{'source_pallet': self.pallets[0].id, 'box_count': 10}]],
            all_packed=False, user=self.accountant,
        )
        session.refresh_from_db()
        self.assertEqual(session.status, PackingSession.Status.INCOMPLETE)

        # لا يمكن الإغلاق قبل معالجة المتبقي
        with self.assertRaises(ValidationError):
            PackingService.close_session(session, user=self.accountant)

        # بعد تسجيل تسوية معتمدة يمكن الإغلاق
        PackingService.settle_leftovers(
            session,
            [{'pallet': self.pallets[0].id, 'resolution': 'WRITE_OFF', 'notes': 'تالف أثناء الفرز'}],
            user=self.accountant,
        )
        PackingService.close_session(session, user=self.accountant)
        session.refresh_from_db()
        self.assertEqual(session.status, PackingSession.Status.COMPLETED)
        self.assertTrue(session.all_packed_confirmed)
        self.assertIsNotNone(session.all_packed_confirmed_by)
        self.assertIsNotNone(session.all_packed_confirmed_at)
        self.assertEqual(session.leftover_settlements.count(), 1)

    def test_settling_leftovers_back_to_store_keeps_inventory(self):
        """إعادة الباقي للبراد تُبقيه مخزوناً ولا تُنشئ قيوداً مالية."""
        session = self._start_session([self.pallets[0]])
        PackingService.record_outputs_and_finalize(
            session, [self._output()],
            allocations=[[{'source_pallet': self.pallets[0].id, 'box_count': 10}]],
            all_packed=False, user=self.accountant,
        )
        PackingService.settle_leftovers(
            session,
            [{'pallet': self.pallets[0].id, 'resolution': 'RETURN_TO_STORE', 'notes': 'إعادة للمخزون'}],
            user=self.accountant,
        )
        pallet = Pallet.objects.get(pk=self.pallets[0].id)
        self.assertEqual(pallet.status, Pallet.Status.IN_COOLING)
        self.assertEqual(pallet.remaining_box_count, 40)
        settlement = session.leftover_settlements.first()
        self.assertEqual(settlement.resolution, PackingLeftoverSettlement.Resolution.RETURN_TO_STORE)
        self.assertEqual(settlement.box_count, 40)

    # ---------------- 7) منع إدخال خام جديد إلى دفعة مغلقة ----------------

    def test_cannot_add_raw_material_to_closed_session(self):
        session = self._start_session([self.pallets[0]])
        PackingService.record_outputs_and_finalize(
            session, [self._output()],
            allocations=[[{'source_pallet': self.pallets[0].id, 'box_count': 50}]],
            all_packed=True, user=self.accountant,
        )
        session.refresh_from_db()
        self.assertEqual(session.status, PackingSession.Status.COMPLETED)

        # لا يمكن إضافة طبالي خام جديدة
        with self.assertRaises(ValidationError):
            PackingService.add_raw_pallets_to_session(
                session, [self.pallets[1]], user=self.accountant
            )
        # ولا يمكن إدخال نواتج جديدة
        with self.assertRaises(ValidationError):
            PackingService.record_outputs_and_finalize(
                session, [self._output('M')],
                allocations=[[{'source_pallet': self.pallets[1].id, 'box_count': 10}]],
                all_packed=True, user=self.accountant,
            )
        session.refresh_from_db()
        self.assertEqual(session.source_pallets.count(), 1)
        self.assertEqual(session.packed_pallets.count(), 1)

    def test_can_add_raw_pallets_to_open_session(self):
        """العملية المفتوحة تقبل إضافة طبالي خام من البراد مع حركة إخراج موثّقة."""
        session = self._start_session([self.pallets[0]])
        added = PackingService.add_raw_pallets_to_session(
            session, [self.pallets[1]], user=self.accountant
        )
        self.assertEqual(len(added), 1)
        session.refresh_from_db()
        self.assertEqual(session.source_pallets.count(), 2)
        pallet = Pallet.objects.get(pk=self.pallets[1].id)
        self.assertEqual(pallet.status, Pallet.Status.OUT_FOR_PACKING)
        self.assertTrue(
            PalletMovementLog.objects.filter(pallet=pallet, movement_type='TO_PACKING').exists()
        )

    # ---------------- سؤال المحاسب الإلزامي في الواجهة ----------------

    def test_finalize_view_requires_accountant_answer(self):
        session = self._start_session([self.pallets[0]])
        client = Client()
        client.force_login(self.accountant)
        resp = client.post(reverse('packing_finalize', args=[session.pk]), {
            'size_name[]': ['XL'],
            'box_type[]': [str(self.box.id)],
            'box_count[]': ['10'],
            'net_weight_kg[]': ['100.000'],
            'alloc_output_idx[]': ['0'],
            'alloc_pallet[]': [str(self.pallets[0].id)],
            'alloc_boxes[]': ['10'],
            # بدون إجابة المحاسب
        })
        session.refresh_from_db()
        self.assertEqual(session.packed_pallets.count(), 0)
        stored = [str(m) for m in resp.wsgi_request._messages]
        self.assertTrue(any('سؤال المحاسب' in m for m in stored))

    def test_finalize_view_answer_no_keeps_session_open(self):
        session = self._start_session([self.pallets[0]])
        client = Client()
        client.force_login(self.accountant)
        resp = client.post(reverse('packing_finalize', args=[session.pk]), {
            'size_name[]': ['XL'],
            'box_type[]': [str(self.box.id)],
            'box_count[]': ['10'],
            'net_weight_kg[]': ['100.000'],
            'alloc_output_idx[]': ['0'],
            'alloc_pallet[]': [str(self.pallets[0].id)],
            'alloc_boxes[]': ['10'],
            'all_packed': 'no',
        })
        self.assertEqual(resp.status_code, 302)
        session.refresh_from_db()
        self.assertEqual(session.status, PackingSession.Status.INCOMPLETE)
        self.assertEqual(session.packed_pallets.count(), 1)
        self.assertEqual(session.raw_consumptions.count(), 1)
        self.assertEqual(session.raw_consumptions.first().box_count, 10)

    def test_finalize_view_answer_yes_rejects_leftovers(self):
        session = self._start_session([self.pallets[0]])
        client = Client()
        client.force_login(self.accountant)
        resp = client.post(reverse('packing_finalize', args=[session.pk]), {
            'size_name[]': ['XL'],
            'box_type[]': [str(self.box.id)],
            'box_count[]': ['10'],
            'net_weight_kg[]': ['100.000'],
            'alloc_output_idx[]': ['0'],
            'alloc_pallet[]': [str(self.pallets[0].id)],
            'alloc_boxes[]': ['10'],
            'all_packed': 'yes',
        })
        self.assertEqual(resp.status_code, 302)
        session.refresh_from_db()
        self.assertEqual(session.packed_pallets.count(), 0)
        stored = [str(m) for m in resp.wsgi_request._messages]
        self.assertTrue(any('كميات خام متبقية' in m for m in stored))

    # ---------------- واجهة المستخدم: عرض الصفحات الجديدة ----------------

    def test_detail_page_renders_ledger_leftovers_and_question(self):
        """صفحة التفاصيل تعرض دفتر الكميات الخام والمتبقي وسؤال المحاسب."""
        session = self._start_session([self.pallets[0]])
        PackingService.record_outputs_and_finalize(
            session, [self._output()],
            allocations=[[{'source_pallet': self.pallets[0].id, 'box_count': 10}]],
            all_packed=False, user=self.accountant,
        )
        client = Client()
        client.force_login(self.accountant)
        resp = client.get(reverse('packing_detail', args=[session.pk]))
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode('utf-8')
        # دفتر الكميات الخام
        self.assertIn('دفتر الكميات الخام', body)
        # سؤال المحاسب
        self.assertIn('هل تم توضيب جميع الكميات الخام', body)
        self.assertIn('all_packed', body)
        # عرض الطبلية المفتوحة ومتبقيها
        self.assertIn('كميات خام متبقية غير محسومة', body)

    def test_add_raw_and_settle_pages_render(self):
        session = self._start_session([self.pallets[0]])
        PackingService.record_outputs_and_finalize(
            session, [self._output()],
            allocations=[[{'source_pallet': self.pallets[0].id, 'box_count': 10}]],
            all_packed=False, user=self.accountant,
        )
        client = Client()
        client.force_login(self.accountant)

        resp = client.get(reverse('packing_add_raw', args=[session.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertIn('إضافة طبالي خام', resp.content.decode('utf-8'))

        resp = client.get(reverse('packing_settle_leftovers', args=[session.pk]))
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode('utf-8')
        self.assertIn('تسوية الكميات الخام المتبقية', body)
        self.assertIn('WRITE_OFF', body)

    # ---------------- دفتر التدقيق ----------------

    def test_audit_log_records_sources_and_confirmation(self):
        session = self._start_session([self.pallets[0]])
        PackingService.record_outputs_and_finalize(
            session, [self._output()],
            allocations=[[{'source_pallet': self.pallets[0].id, 'box_count': 10}]],
            all_packed=False, user=self.accountant,
        )
        from core.models import AuditLog
        actions = list(AuditLog.objects.filter(model_name='PackingSession').values_list('action', flat=True))
        self.assertIn('FINALIZE_PACKING_SESSION', actions)

    def test_reversing_session_restores_pallet_balances(self):
        """حذف عملية التوضيب يعيد أرصدة الطبالي كما كانت."""
        session = self._start_session([self.pallets[0]])
        PackingService.record_outputs_and_finalize(
            session, [self._output()],
            allocations=[[{'source_pallet': self.pallets[0].id, 'box_count': 10}]],
            all_packed=False, user=self.accountant,
        )
        pallet = Pallet.objects.get(pk=self.pallets[0].id)
        self.assertEqual(pallet.remaining_box_count, 40)

        session.delete()
        pallet.refresh_from_db()
        self.assertEqual(pallet.remaining_box_count, 50)
        self.assertEqual(pallet.consumed_box_count, 0)


