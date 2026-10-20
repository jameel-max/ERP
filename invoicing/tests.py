from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from decimal import Decimal

from core.models import User, SystemSetting, AuditLog
from operations.models import Trader, Crop, BoxType, ColdStore, Shelf, TraderPackingRate
from finance.models import Payment
from finance.services import FinanceService
from invoicing.models import Invoice
from invoicing.services import InvoiceService


class Phase1InvoiceTests(TestCase):
    """
    اختبارات الفواتير: القاعدة 1 (اعتماد الفاتورة لا ينشئ إيراداً مكرراً)
    ودعم قاعدة 10 (تجميد السعر وعدم تأثر الفواتير التاريخية).
    """

    def setUp(self):
        SystemSetting.get_settings().save()
        self.manager = User.objects.create_user(
            username='inv_manager', password='pass', role=User.Role.MANAGER
        )
        self.accountant = User.objects.create_user(
            username='inv_accountant', password='pass', role=User.Role.ACCOUNTANT
        )
        self.trader = Trader.objects.create(code='T001', name='ابو معتز القيسي')
        self.crop = Crop.objects.create(name='نكتارين')

    def _draft_invoice(self, total='200.000', number='2026-T001-001'):
        invoice = Invoice.objects.create(
            invoice_number=number, trader=self.trader, year=2026, crop=self.crop,
            status=Invoice.Status.DRAFT, packing_rate_snapshot=Decimal('0.000'),
            grand_total=Decimal(total), remaining_balance=Decimal(total),
            created_by=self.accountant,
        )
        return invoice

    def test_rule_1_approving_invoice_does_not_duplicate_revenue(self):
        """
        القاعدة 1: اعتماد الفاتورة لا ينشئ إيراداً مكرراً، وإعادة الاعتماد لا تغيّر شيئاً.
        """
        invoice = self._draft_invoice(total='200.000')
        client = Client()
        client.force_login(self.manager)

        self.assertEqual(FinanceService.calculate_financial_kpis()['earned_revenue'], Decimal('0.000'))

        # الاعتماد الأول
        client.get(reverse('invoice_approve', args=[invoice.pk]))
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.Status.APPROVED)
        self.assertEqual(FinanceService.calculate_financial_kpis()['earned_revenue'], Decimal('200.000'))
        self.assertEqual(Payment.objects.count(), 0)

        # إعادة الاعتماد لا تُنشئ إيراداً مكرراً
        client.get(reverse('invoice_approve', args=[invoice.pk]))
        self.assertEqual(Invoice.objects.filter(status=Invoice.Status.APPROVED).count(), 1)
        self.assertEqual(FinanceService.calculate_financial_kpis()['earned_revenue'], Decimal('200.000'))

    def test_approving_invoice_requires_manager_role(self):
        """
        اعتماد الفاتورة بصلاحية المدير فقط (حماية من الاعتماد غير المخوّل).
        """
        invoice = self._draft_invoice(total='50.000')
        client = Client()
        client.force_login(self.accountant)
        response = client.get(reverse('invoice_approve', args=[invoice.pk]))
        self.assertEqual(response.status_code, 403)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.Status.DRAFT)
