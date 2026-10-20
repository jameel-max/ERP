from django.test import TestCase, Client
from django.urls import reverse
from decimal import Decimal

from core.models import User, SystemSetting
from operations.models import Trader, Crop, BoxType, ColdStore, TruckReceipt
from boxes.models import FarmBoxType, FarmBoxRecord


class FarmBoxTests(TestCase):
    """
    القسم الثاني: الصناديق الواردة من المزرعة — قسم مستقل تماماً عن مخزون
    صناديق ومواد التغليف الخاصة بالشركة.
    """

    def setUp(self):
        self.settings = SystemSetting.get_settings()
        self.settings.save()
        self.accountant = User.objects.create_user(
            username='farm_accountant', password='pass', role=User.Role.ACCOUNTANT
        )
        self.client = Client()
        self.client.force_login(self.accountant)

        self.trader = Trader.objects.create(code='T900', name='تاجر المزرعة')
        self.crop = Crop.objects.create(name='نكتارين المزرعة')
        self.company_box = BoxType.objects.create(
            name='صندوق الشركة', color_or_type='أزرق',
            tare_weight=Decimal('0.800'),
            discount_price=Decimal('1.500'),
            company_stock=500,
        )
        self.cold_store = ColdStore.objects.create(
            name='براد المزرعة', code='CS-FARM', capacity_pallets=50,
        )
        self.farm_type = FarmBoxType.objects.create(
            name='صندوق مزرعة بلاستيك',
            description='مقاس كبير',
            tare_weight=Decimal('0.600'),
        )
        self.truck = TruckReceipt.objects.create(
            lot_number='2026-9001',
            trader=self.trader,
            crop=self.crop,
            box_type=self.farm_type,
            box_count=100,
            gross_weight=Decimal('1000.000'),
            tare_weight=Decimal('300.000'),
            pallet_count=4,
            cold_store=self.cold_store,
        )

    def test_record_create_does_not_touch_company_stock(self):
        resp = self.client.post(
            reverse('farm_box_record_create_for_truck', args=[self.truck.pk]),
            {
                'truck_receipt': self.truck.pk,
                'incoming_count': '100',
                'empty_count': '80',
                'notes': 'تفريغ كامل',
            },
        )
        self.assertEqual(resp.status_code, 302)
        record = FarmBoxRecord.objects.get(truck_receipt=self.truck)
        self.assertEqual(record.incoming_count, 100)
        self.assertEqual(record.empty_count, 80)
        self.assertEqual(record.recorded_by, self.accountant)
        # السجل مسجّل باسم تاجر السيارة
        self.assertEqual(record.trader, self.trader)

        # مخزون صناديق الشركة لم يتغير إطلاقاً
        self.company_box.refresh_from_db()
        self.assertEqual(self.company_box.company_stock, 500)

    def test_trader_is_always_derived_from_the_truck(self):
        """لا يمكن نسب صناديق سيارة إلى تاجر آخر حتى لو أُرسل خطأً."""
        other_trader = Trader.objects.create(code='T901', name='تاجر آخر')
        record = FarmBoxRecord(
            truck_receipt=self.truck,
            trader=other_trader,
            incoming_count=10,
            empty_count=8,
        )
        record.save()
        record.refresh_from_db()
        self.assertEqual(record.trader, self.trader)
        self.assertNotEqual(record.trader, other_trader)

    def test_dashboard_filter_by_trader(self):
        other_trader = Trader.objects.create(code='T902', name='تاجر ثانٍ')
        other_truck = TruckReceipt.objects.create(
            lot_number='2026-9002',
            trader=other_trader,
            crop=self.crop,
            box_type=self.farm_type,
            box_count=50,
            gross_weight=Decimal('600.000'),
            tare_weight=Decimal('200.000'),
            pallet_count=2,
            cold_store=self.cold_store,
        )
        record = FarmBoxRecord.objects.create(
            truck_receipt=self.truck,
            incoming_count=100,
            empty_count=80,
        )
        FarmBoxRecord.objects.create(
            truck_receipt=other_truck,
            incoming_count=50,
            empty_count=40,
        )

        resp = self.client.get(reverse('farm_boxes_dashboard'), {'trader': self.trader.pk})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['selected_trader'], self.trader)
        self.assertEqual(resp.context['total_incoming'], 100)
        self.assertEqual(resp.context['total_empty'], 80)
        self.assertEqual(list(resp.context['records']), [record])

    def test_trader_detail_shows_his_farm_boxes(self):
        FarmBoxRecord.objects.create(
            truck_receipt=self.truck,
            incoming_count=100,
            empty_count=80,
        )
        resp = self.client.get(reverse('trader_detail', args=[self.trader.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'صناديق المزرعة الخاصة بالتاجر')
        self.assertContains(resp, self.truck.lot_number)
        self.assertContains(resp, '100')
        self.assertContains(resp, '80')

    def test_dashboard_shows_totals(self):
        FarmBoxRecord.objects.create(
            truck_receipt=self.truck,
            incoming_count=100,
            empty_count=80,
        )
        resp = self.client.get(reverse('farm_boxes_dashboard'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'صندوق مزرعة بلاستيك')
        self.assertContains(resp, '100')
        self.assertContains(resp, '80')
        self.assertContains(resp, self.truck.lot_number)

    def test_farm_box_type_created(self):
        resp = self.client.post(reverse('farm_box_type_create'), {
            'name': 'صندوق ربع حقل',
            'description': 'للبيع',
            'tare_weight': '0.500',
            'is_active': 'on',
        })
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(FarmBoxType.objects.filter(name='صندوق ربع حقل').exists())

    def test_cannot_create_two_records_for_same_truck(self):
        FarmBoxRecord.objects.create(
            truck_receipt=self.truck,
            incoming_count=100,
            empty_count=80,
        )
        resp = self.client.get(
            reverse('farm_box_record_create_for_truck', args=[self.truck.pk])
        )
        # يُعاد التوجيه لتعديل السجل القائم بدل إنشاء سجل ثانٍ
        self.assertEqual(resp.status_code, 302)
        self.assertIn(
            reverse('farm_box_record_edit', args=[FarmBoxRecord.objects.get(truck_receipt=self.truck).pk]),
            resp.url,
        )
        self.assertEqual(FarmBoxRecord.objects.filter(truck_receipt=self.truck).count(), 1)

    def test_empty_count_defaults_to_incoming_when_left_blank(self):
        """إن تُرك «عدد الفارغ الناتج» فارغاً نجعله مساوياً لعدد الصناديق الواردة."""
        resp = self.client.post(
            reverse('farm_box_record_create_for_truck', args=[self.truck.pk]),
            {
                'truck_receipt': self.truck.pk,
                'incoming_count': '100',
                'empty_count': '',
                'notes': 'تفريغ كامل',
            },
        )
        self.assertEqual(resp.status_code, 302)
        record = FarmBoxRecord.objects.get(truck_receipt=self.truck)
        self.assertEqual(record.incoming_count, 100)
        self.assertEqual(record.empty_count, 100)

    def test_create_form_prefills_empty_same_as_incoming(self):
        resp = self.client.get(
            reverse('farm_box_record_create_for_truck', args=[self.truck.pk])
        )
        self.assertEqual(resp.status_code, 200)
        form = resp.context['form']
        self.assertEqual(form.initial['incoming_count'], self.truck.box_count)
        self.assertEqual(form.initial['empty_count'], self.truck.box_count)

    def test_dashboard_totals_equal_when_empty_matches_incoming(self):
        FarmBoxRecord.objects.create(
            truck_receipt=self.truck,
            incoming_count=100,
            empty_count=100,
        )
        resp = self.client.get(reverse('farm_boxes_dashboard'))
        self.assertEqual(resp.context['total_incoming'], resp.context['total_empty'])
        self.assertEqual(resp.context['total_incoming'], 100)
