from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from decimal import Decimal
import io
import shutil
import tempfile

from PIL import Image

from core.models import Attachment

from .models import TradeShipment, ShipmentCost, ShipmentPayment, ShipmentDocument


class TradeModuleTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(
            username='owner_trade', password='x', role='OWNER'
        )
        self.client.force_login(self.owner)

    def _shipment(self, **kwargs):
        defaults = dict(
            reference_number='IMP-2026-0001',
            direction=TradeShipment.Direction.IMPORT,
            counterparty_name='مورد لبنان',
            goods_description='1000 صندوق تفاح',
            quantity=Decimal('1000'),
            unit_price=Decimal('2'),
            currency=TradeShipment.Currency.USD,
            exchange_rate=Decimal('0.710000'),
        )
        defaults.update(kwargs)
        return TradeShipment.objects.create(**defaults)

    def test_reference_generation(self):
        ref = TradeShipment.generate_reference(TradeShipment.Direction.IMPORT, 2026)
        self.assertEqual(ref, 'IMP-2026-0001')
        self._shipment(reference_number=ref)
        ref2 = TradeShipment.generate_reference(TradeShipment.Direction.IMPORT, 2026)
        self.assertEqual(ref2, 'IMP-2026-0002')

    def test_cost_and_profit_calculation(self):
        s = self._shipment()  # 1000 * 2 * 0.71 = 1420.000 JOD
        self.assertEqual(s.goods_value_jod, Decimal('1420.000'))
        ShipmentCost.objects.create(shipment=s, kind=ShipmentCost.Kind.FREIGHT, amount=Decimal('100'))
        ShipmentCost.objects.create(shipment=s, kind=ShipmentCost.Kind.CUSTOMS, amount=Decimal('50'))
        self.assertEqual(s.extra_costs_jod, Decimal('150.000'))
        self.assertEqual(s.total_cost_jod, Decimal('1570.000'))
        # استيراد بلا قيمة بيع → الربح غير محدد
        self.assertIsNone(s.profit_jod)
        s.sale_value = Decimal('2000')
        s.save()
        self.assertEqual(s.profit_jod, Decimal('430.000'))

    def test_export_profit_uses_goods_value(self):
        s = self._shipment(direction=TradeShipment.Direction.EXPORT, unit_price=Decimal('3'))
        # 1000 * 3 * 0.71 = 2130 إيراد، والتكاليف الإضافية هنا صفر
        self.assertEqual(s.revenue_jod, Decimal('2130.000'))
        self.assertEqual(s.total_cost_jod, Decimal('0.000'))
        self.assertEqual(s.profit_jod, Decimal('2130.000'))
        ShipmentCost.objects.create(shipment=s, kind=ShipmentCost.Kind.FREIGHT, amount=Decimal('130'))
        self.assertEqual(s.total_cost_jod, Decimal('130.000'))
        self.assertEqual(s.profit_jod, Decimal('2000.000'))

    def test_payment_reduces_counterparty_balance(self):
        s = self._shipment()
        ShipmentPayment.objects.create(shipment=s, kind=ShipmentPayment.Kind.PAID, amount=Decimal('1200'))
        self.assertEqual(s.total_paid_jod, Decimal('1200.000'))
        self.assertEqual(s.counterparty_balance_jod, Decimal('220.000'))

    def test_shortage_calculation(self):
        s = self._shipment(received_quantity=Decimal('980'))
        self.assertEqual(s.shortage_quantity, Decimal('20.000'))
        self.assertTrue(s.has_shortage)

    def test_list_page_renders(self):
        self._shipment()
        response = self.client.get(reverse('trade_shipment_list'))
        self.assertEqual(response.status_code, 200)

    def test_create_shipment_view(self):
        response = self.client.post(reverse('trade_shipment_create'), {
            'direction': 'IMPORT',
            'counterparty_name': 'مورد تركيا',
            'counterparty_country': 'تركيا',
            'goods_description': '500 صندوق عنب',
            'quantity': '500',
            'quantity_unit': 'BOX',
            'unit_price': '1.5',
            'currency': 'USD',
            'exchange_rate': '0.71',
            'sale_value': '0',
            'origin': 'تركيا',
            'destination': 'الأردن',
            'received_quantity': '0',
            'damaged_quantity': '0',
            'status': 'DRAFT',
            'notes': '',
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(TradeShipment.objects.count(), 1)

    def test_document_add_view(self):
        s = self._shipment()
        response = self.client.post(reverse('trade_document_create', args=[s.pk]), {
            'kind': 'CERTIFICATE_OF_ORIGIN',
            'number': 'CO-123',
            'issue_date': '',
            'notes': '',
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(ShipmentDocument.objects.count(), 1)


class TradeShipmentImageFormTests(TestCase):
    """اختبار إرفاق عدة صور مباشرةً من نموذج تسجيل الشحنة."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._media = tempfile.mkdtemp(prefix='erp-trade-img-')

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._media, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.media_override = override_settings(MEDIA_ROOT=self._media)
        self.media_override.enable()
        self.addCleanup(self.media_override.disable)

        User = get_user_model()
        self.accountant = User.objects.create_user(
            username='trade_img_accountant', password='x', role='ACCOUNTANT'
        )
        self.client.force_login(self.accountant)

    def _image(self, name, color):
        buffer = io.BytesIO()
        Image.new('RGB', (30, 30), color).save(buffer, format='JPEG')
        return SimpleUploadedFile(name, buffer.getvalue(), content_type='image/jpeg')

    def _payload(self):
        return {
            'direction': 'IMPORT',
            'counterparty_name': 'Lebanon Supplier',
            'counterparty_country': 'Lebanon',
            'goods_description': '500 boxes apples',
            'quantity': '500',
            'quantity_unit': 'BOX',
            'unit_price': '2',
            'currency': 'USD',
            'exchange_rate': '0.71',
            'sale_value': '0',
            'origin': 'Lebanon',
            'destination': 'Jordan',
            'received_quantity': '0',
            'damaged_quantity': '0',
            'status': 'DRAFT',
            'notes': '',
            'attachment_caption': 'goods photos',
            'images': [self._image('one.jpg', (255, 0, 0)), self._image('two.jpg', (0, 0, 255))],
        }

    def test_create_shipment_with_multiple_images(self):
        response = self.client.post(reverse('trade_shipment_create'), self._payload())
        self.assertEqual(response.status_code, 302)
        shipment = TradeShipment.objects.get()
        self.assertEqual(Attachment.objects.filter(object_id=shipment.pk).count(), 2)
        self.assertTrue(shipment.reference_number.startswith('IMP-'))

    def test_create_shipment_without_images_still_works(self):
        payload = self._payload()
        payload.pop('images')
        response = self.client.post(reverse('trade_shipment_create'), payload)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(TradeShipment.objects.count(), 1)
        self.assertEqual(Attachment.objects.count(), 0)
