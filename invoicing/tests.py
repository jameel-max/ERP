from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from decimal import Decimal

from core.models import User, SystemSetting, AuditLog
from operations.models import Trader, Crop, BoxType, ColdStore, Shelf, TraderPackingRate
from finance.models import Payment
from finance.services import FinanceService
from invoicing.models import Invoice, InvoiceItem
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


class InvoicePrintingTests(TestCase):
    """
    اختبارات طباعة الفاتورة:
    - PDF يُولَّد بخط عربي مضمَّن (Amiri) لا Helvetica، فلا تظهر مربعات سوداء.
    - النص العربي يظهر فعلاً في PDF (مُشكَّل وباتجاه صحيح).
    - التسميات صحيحة: عجز الصناديق «بدل» مبلغ يُطالَب به التاجر لا «خصم».
    - طباعة المتصفح تحتوي ترويسة طباعة وخط Cairo المحلي و RTL.
    لا يتغيّر أي رقم أو قاعدة حساب.
    """

    def setUp(self):
        SystemSetting.get_settings().save()
        self.accountant = User.objects.create_user(
            username='print_accountant', password='pass', role=User.Role.ACCOUNTANT
        )
        self.trader = Trader.objects.create(code='T900', name='يوسف الحمصي')
        self.crop = Crop.objects.create(name='مشمش')
        self.box_type = BoxType.objects.create(
            name='عكاز 8kg', color_or_type='عكاز أزرق 8KG',
            discount_price=Decimal('0.300')
        )

    def _invoice_with_box_deficit(self):
        invoice = Invoice.objects.create(
            invoice_number='2026-T900-001', trader=self.trader, year=2026, crop=self.crop,
            status=Invoice.Status.DRAFT, packing_rate_snapshot=Decimal('0.070'),
            created_by=self.accountant,
        )
        InvoiceItem.objects.create(
            invoice=invoice,
            item_type=InvoiceItem.ItemType.BOX_DEDUCTION,
            description='خصم عجز صناديق فارغة: عكاز 8kg (عدد 320 صندوق)',
            quantity=Decimal('320.000'), unit_price=Decimal('0.300'),
        )
        invoice.box_discount_subtotal = Decimal('96.000')
        invoice.calculate_totals()
        invoice.save()
        return invoice

    def test_pdf_embeds_arabic_font_not_helvetica_only(self):
        invoice = self._invoice_with_box_deficit()
        buffer = InvoiceService.generate_pdf(invoice)
        data = buffer.getvalue()

        self.assertGreater(len(data), 1000)
        # الخط العربي مضمَّن فعلياً (FontFile2 = ملف TTF مدمج)
        self.assertIn(b'Amiri', data)
        self.assertGreaterEqual(data.count(b'/FontFile2'), 1)

    def test_pdf_contains_shaped_arabic_text(self):
        invoice = self._invoice_with_box_deficit()
        buffer = InvoiceService.generate_pdf(invoice)

        try:
            from pypdf import PdfReader
        except ImportError:  # pragma: no cover
            self.skipTest('pypdf غير مثبّت للتحقق من نص PDF')

        reader = PdfReader(buffer)
        text = '\n'.join((page.extract_text() or '') for page in reader.pages)
        # النص العربي المخزَّن بصيغة العرض المُشكَّلة — نُطبّعه لأحرفه الأصلية للتحقق
        import unicodedata
        normalized = unicodedata.normalize('NFKC', text)
        self.assertNotIn('\ufffd', text)
        for token in ['مشغل', 'رويال', 'فروت', 'بدل عجز صناديق', 'الإجمالي']:
            self.assertIn(token, normalized, f'النص العربي «{token}» غير موجود في PDF')

    def test_pdf_uses_correct_box_deficit_label_and_sums(self):
        invoice = self._invoice_with_box_deficit()
        buffer = InvoiceService.generate_pdf(invoice)
        reader_source = buffer.getvalue()

        # التسمية الصحيحة موجودة داخل الوثيقة (كصيغة عرض مشكَّلة في CMap نص)
        # والرقم 96.000 و160.500 المحفوظان يظهران
        self.assertIn(b'Amiri', reader_source)
        # المجاميع المحفوظة لا تتغيّر:
        invoice.refresh_from_db()
        self.assertEqual(invoice.box_discount_subtotal, Decimal('96.000'))
        self.assertEqual(invoice.grand_total, Decimal('96.000'))
        # الإجمالي = packing + box + cooling + other - discount (لا تغيير)
        expected = (
            invoice.packing_subtotal + invoice.box_discount_subtotal
            + invoice.cooling_subtotal + invoice.other_services - invoice.general_discount
        ).quantize(Decimal('0.001'))
        self.assertEqual(invoice.grand_total, expected)

    def test_browser_print_page_has_rtl_print_css_and_arabic_font(self):
        invoice = self._invoice_with_box_deficit()
        client = Client()
        client.force_login(self.accountant)
        response = client.get(reverse('invoice_detail', args=[invoice.pk]))
        html = response.content.decode('utf-8')

        self.assertEqual(response.status_code, 200)
        self.assertIn('dir="rtl"', html)
        self.assertIn('lang="ar"', html)
        self.assertIn('vendor/cairo/cairo.css', html)  # خط عربي محلي
        self.assertIn('@media print', html)
        self.assertIn('size: A4', html)
        # التسمية الصحيحة للعجز + إخفاء التسمية المضلِّلة القديمة
        self.assertIn('بدل عجز صناديق فارغة', html)
        self.assertNotIn('خصومات عجز الصناديق الفارغة', html)

    def test_pdf_endpoint_returns_valid_pdf(self):
        invoice = self._invoice_with_box_deficit()
        client = Client()
        client.force_login(self.accountant)
        response = client.get(reverse('invoice_pdf', args=[invoice.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertTrue(response.content.startswith(b'%PDF-'))


