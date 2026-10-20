"""
اختبارات إثبات العيوب المكتشفة أثناء التدقيق (Audit findings) — للتوثيق فقط.

تُثبت هذه الاختبارات وجود العيوب المؤكدة قبل الإصلاح، وتبقى بعد الإصلاح
كاختبارات مانعة للرجوع (regression). لا تعتمد على بيانات Neon.
"""
from decimal import Decimal

from django.test import TestCase, Client
from django.urls import reverse

from core.models import User
from operations.models import Trader, Crop, ColdStore, Shelf, BoxType, TruckReceipt, Pallet
from finance.models import TreasuryAccount, Expense, ExpenseCategory
from boxes.models import FarmBoxType


class PublicPalletScanLeakTests(TestCase):
    """BUG-01 / BUG-02: صفحات مسح QR للطبالي مكشوفة بلا تسجيل دخول."""

    def setUp(self):
        self.trader = Trader.objects.create(name='تاجر اختبار', phone='0790000000')
        self.crop = Crop.objects.create(name='بندورة اختبار')
        self.farm_box = FarmBoxType.objects.create(name='صندوق مزرعة اختبار')
        self.store = ColdStore.objects.create(name='براد اختبار', capacity_pallets=100)
        self.shelf = Shelf.objects.create(cold_store=self.store, shelf_number='1')
        self.truck = TruckReceipt.objects.create(
            lot_number='2026-TEST-001', trader=self.trader, crop=self.crop,
            box_type=self.farm_box, box_count=80, gross_weight=Decimal('1000'),
            tare_weight=Decimal('100'), pallet_count=1, cold_store=self.store,
            shelf=self.shelf, arrival_date='2026-01-01',
        )
        self.pallet = Pallet.objects.create(
            pallet_code='2026-TEST-001-P01', truck_receipt=self.truck,
            trader=self.trader, crop=self.crop, box_type=self.farm_box, box_count=80,
            estimated_net_weight=Decimal('900'), cold_store=self.store,
            shelf=self.shelf,
        )

    def test_pallet_qr_scan_requires_login(self):
        url = reverse('pallet_qr_scan', args=[self.pallet.pallet_code])
        resp = Client().get(url)
        # المتوقع: إعادة توجيه إلى صفحة الدخول (302). الفعلي قبل الإصلاح: 200 (تسريب).
        self.assertEqual(resp.status_code, 302, 'صفحة مسح QR للطبلية مكشوفة بدون تسجيل دخول')


class ExpenseApproveHttpGetTests(TestCase):
    """BUG-03: اعتماد المصروف يحدث عبر GET (لا يوجد حارس أسلوب)."""

    def setUp(self):
        self.manager = User.objects.create_user(
            username='mgr_audit', password='x', role=User.Role.MANAGER)
        self.cat = ExpenseCategory.objects.create(name='تصنيف اختبار')
        self.exp = Expense.objects.create(
            category=self.cat, amount=Decimal('10'), due_date='2026-01-01',
            status=Expense.Status.ACCRUED)

    def test_expense_approve_via_get_is_rejected(self):
        c = Client()
        c.force_login(self.manager)
        resp = c.get(reverse('expense_approve', args=[self.exp.pk]))
        self.exp.refresh_from_db()
        # المتوقع: رفض GET (405) وعدم تغيير الحالة. الفعلي قبل الإصلاح: 302 + اعتماد.
        self.assertEqual(resp.status_code, 405, 'اعتماد المصروف متاح عبر GET')
        self.assertFalse(self.exp.is_approved)
