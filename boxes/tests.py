from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from django.core.exceptions import ValidationError
from decimal import Decimal

from core.models import User, AuditLog, SystemSetting
from operations.models import Trader, BoxType
from boxes.models import TraderBoxBalance, TraderBoxTransaction
from boxes.services import BoxService, EmptyBoxService


class Phase3BoxesTests(TestCase):
    """
    اختبارات المرحلة الثالثة: جرد الصناديق
    (القاعدة 19: إلغاء عملية مخزنية يعكس أثرها بصورة موثقة، والقاعدة 21: عدم تكرار الحركة)
    """

    def setUp(self):
        self.settings = SystemSetting.get_settings()
        self.settings.save()
        self.manager = User.objects.create_user(
            username='box_manager', password='pass', role=User.Role.MANAGER
        )
        self.accountant = User.objects.create_user(
            username='box_accountant', password='pass', role=User.Role.ACCOUNTANT
        )
        self.trader = Trader.objects.create(code='T001', name='ابو معتز القيسي')
        self.box_type = BoxType.objects.create(
            name='عكاز أزرق 8KG', color_or_type='أزرق',
            tare_weight=Decimal('0.800'), discount_price=Decimal('1.500'),
            company_stock=1000,
        )

    def test_rule_19_reversing_warehouse_movement_is_documented(self):
        """
        القاعدة 19: إلغاء عملية مخزنية (حركة صناديق) يعكس أثرها بصورة موثقة،
        ويحدّث المخزون ورصيد التاجر دون حذف صامت.
        """
        trans = BoxService.record_box_movement(
            trader=self.trader, box_type=self.box_type,
            transaction_type=TraderBoxTransaction.Type.ISSUE,
            quantity=100, source='مستودع المشغل', destination='مزرعة التاجر',
            reason='صرف صناديق جني', user=self.accountant,
        )
        self.box_type.refresh_from_db()
        self.assertEqual(self.box_type.company_stock, 900)
        bal = TraderBoxBalance.objects.get(trader=self.trader, box_type=self.box_type)
        self.assertEqual(bal.net_balance, 100)

        # عكس الحركة بقيد تسوية موثق
        reversal = BoxService.reverse_movement(trans, reason='خطأ في الكمية', user=self.manager)

        self.box_type.refresh_from_db()
        bal.refresh_from_db()
        trans.refresh_from_db()

        self.assertEqual(self.box_type.company_stock, 1000)
        self.assertEqual(bal.net_balance, 0)
        self.assertTrue(trans.is_reversed)
        self.assertEqual(trans.reversal_reason, 'خطأ في الكمية')
        self.assertIsNotNone(reversal.pk)
        self.assertEqual(reversal.transaction_type, TraderBoxTransaction.Type.RETURN)

        log = AuditLog.objects.filter(action='BOX_REVERSAL', object_id=str(trans.pk)).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.details.get('reversed_transaction_id'), trans.id)
        self.assertEqual(log.details.get('quantity'), 100)

        # لا يمكن عكس الحركة مرتين
        with self.assertRaises(ValidationError):
            BoxService.reverse_movement(trans, reason='عكس مكرر', user=self.manager)

    def test_rule_21_duplicate_movement_not_created_on_reverse(self):
        """
        القاعدة 21: تكرار الإرسال لا يكرر حركة المخزون؛ العكس يُنشئ حركة تسوية واحدة فقط.
        """
        trans = BoxService.record_box_movement(
            trader=self.trader, box_type=self.box_type,
            transaction_type=TraderBoxTransaction.Type.RETURN,
            quantity=40, source='مزرعة التاجر', destination='مستودع المشغل',
            reason='إرجاع صناديق', user=self.accountant,
        )
        self.box_type.refresh_from_db()
        self.assertEqual(self.box_type.company_stock, 1040)

        BoxService.reverse_movement(trans, reason='إرجاع خاطئ', user=self.manager)
        self.assertEqual(TraderBoxTransaction.objects.count(), 2)  # الأصلية + قيد التسوية فقط
        self.box_type.refresh_from_db()
        self.assertEqual(self.box_type.company_stock, 1000)


class EmptyBoxDeliveryTests(TestCase):
    """
    قسم تسليم الفارغ: إرجاع الفارغ إلى المزرعة أو بيعه لمنظمة أخرى.

    القواعد المحقَّقة:
    - الإرجاع إلى المزرعة لا يحمل سعراً ولا يُنشئ أي قيد في الخزينة.
    - البيع يقيَّد إيراده في الخزينة مرة واحدة فقط عند الاعتماد (نقدي/بنكي).
    - «على الحساب» يبقي المبلغ غير مقبوض بلا قيد خزينة.
    - لا يمكن تسليم أكثر من الفارغ المتاح فعلاً (مسودة أو اعتماد).
    - الأمر المعتمد لا يُعاد اعتماده ولا يُعدَّل.
    """

    def setUp(self):
        from django.test import Client
        from operations.models import Crop, TruckReceipt, ColdStore
        from boxes.models import FarmBoxType, FarmBoxRecord, EmptyBoxRecipient, EmptyBoxDelivery
        from finance.models import TreasuryAccount

        self.TruckReceipt = TruckReceipt
        self.EmptyBoxDelivery = EmptyBoxDelivery
        self.EmptyBoxRecipient = EmptyBoxRecipient

        self.settings = SystemSetting.get_settings()
        self.settings.save()
        self.accountant = User.objects.create_user(
            username='eb_accountant', password='pass', role=User.Role.ACCOUNTANT
        )
        self.manager = User.objects.create_user(
            username='eb_manager', password='pass', role=User.Role.MANAGER
        )
        self.trader = Trader.objects.create(code='EB01', name='مزارع الوادي')
        self.crop = Crop.objects.create(name='طماطم')
        self.farm_box_type = FarmBoxType.objects.create(
            name='صندوق مزرعة بلاستيك', description='أزرق 8KG'
        )
        self.cold_store = ColdStore.objects.create(
            code='CS-EB1', name='براد تسليم الفارغ', capacity_pallets=20
        )
        truck = TruckReceipt.objects.create(
            lot_number='2026-9001', trader=self.trader, crop=self.crop,
            box_type=self.farm_box_type, box_count=300,
            gross_weight=Decimal('5000.000'), tare_weight=Decimal('2000.000'),
            cold_store=self.cold_store,
        )
        # 300 صندوق وارد ⇒ 300 فارغ ناتج عن التفريغ
        FarmBoxRecord.objects.create(
            trader=self.trader, truck_receipt=truck,
            incoming_count=300, empty_count=300, recorded_by=self.accountant,
        )
        self.treasury = TreasuryAccount.objects.create(
            name='خزينة تسليم الفارغ', account_type=TreasuryAccount.AccountType.CASH,
            opening_balance=Decimal('0.000'), current_balance=Decimal('0.000'),
        )
        self.farm_recipient = EmptyBoxRecipient.objects.create(
            name='مزرعة الوادي الأخضر', kind=EmptyBoxRecipient.Kind.FARM
        )
        self.org_recipient = EmptyBoxRecipient.objects.create(
            name='منظمة الإغاثة', kind=EmptyBoxRecipient.Kind.ORGANIZATION
        )

    def _data(self, **overrides):
        data = {
            'kind': self.EmptyBoxDelivery.Kind.RETURN,
            'recipient': self.farm_recipient,
            'box_type': self.farm_box_type,
            'quantity': 100,
            'unit_price': Decimal('0.000'),
            'delivery_date': timezone.localdate(),
            'payment_method': '',
            'treasury_account': None,
            'status': self.EmptyBoxDelivery.Status.DRAFT,
        }
        data.update(overrides)
        return data

    def test_available_empty_is_produced_minus_delivered(self):
        available = EmptyBoxService.available_empty(self.farm_box_type.pk)
        self.assertEqual(available[self.farm_box_type.pk], 300)

        EmptyBoxService.create_delivery(
            self._data(quantity=120, status=self.EmptyBoxDelivery.Status.DELIVERED),
            user=self.accountant,
        )
        available = EmptyBoxService.available_empty(self.farm_box_type.pk)
        self.assertEqual(available[self.farm_box_type.pk], 180)

    def test_return_to_farm_has_no_price_and_no_treasury_entry(self):
        delivery = EmptyBoxService.create_delivery(
            self._data(
                kind=self.EmptyBoxDelivery.Kind.RETURN, quantity=50,
                status=self.EmptyBoxDelivery.Status.DELIVERED,
            ),
            user=self.accountant,
        )
        self.assertTrue(delivery.is_return)
        self.assertEqual(delivery.unit_price, Decimal('0.000'))
        self.assertEqual(delivery.total_amount, Decimal('0.000'))
        self.assertFalse(delivery.is_revenue_posted)
        self.treasury.refresh_from_db()
        self.assertEqual(self.treasury.current_balance, Decimal('0.000'))

    def test_sale_posts_revenue_once_into_treasury(self):
        delivery = EmptyBoxService.create_delivery(
            self._data(
                kind=self.EmptyBoxDelivery.Kind.SALE, recipient=self.org_recipient,
                quantity=100, unit_price=Decimal('0.250'),
                payment_method=self.EmptyBoxDelivery.PaymentMethod.CASH,
                treasury_account=self.treasury,
                status=self.EmptyBoxDelivery.Status.DRAFT,
            ),
            user=self.accountant,
        )
        self.assertEqual(delivery.total_amount, Decimal('25.000'))
        self.treasury.refresh_from_db()
        self.assertEqual(self.treasury.current_balance, Decimal('0.000'))  # مسودة: بلا قيد

        EmptyBoxService.confirm_delivery(delivery, user=self.manager)
        delivery.refresh_from_db()
        self.treasury.refresh_from_db()
        self.assertEqual(delivery.status, self.EmptyBoxDelivery.Status.DELIVERED)
        self.assertTrue(delivery.is_revenue_posted)
        self.assertEqual(self.treasury.current_balance, Decimal('25.000'))  # 100 × 0.250

        # إعادة الاعتماد ممنوعة
        with self.assertRaises(ValidationError):
            EmptyBoxService.confirm_delivery(delivery, user=self.manager)
        self.treasury.refresh_from_db()
        self.assertEqual(self.treasury.current_balance, Decimal('25.000'))  # القيد مرة واحدة

    def test_sale_on_account_requires_no_treasury(self):
        delivery = EmptyBoxService.create_delivery(
            self._data(
                kind=self.EmptyBoxDelivery.Kind.SALE, recipient=self.org_recipient,
                quantity=60, unit_price=Decimal('0.300'),
                payment_method=self.EmptyBoxDelivery.PaymentMethod.RECEIVABLE,
                treasury_account=None,
                status=self.EmptyBoxDelivery.Status.DELIVERED,
            ),
            user=self.accountant,
        )
        self.assertEqual(delivery.total_amount, Decimal('18.000'))
        self.assertFalse(delivery.is_revenue_posted)
        self.treasury.refresh_from_db()
        self.assertEqual(self.treasury.current_balance, Decimal('0.000'))
        self.assertEqual(delivery.payment_status_label, 'على الحساب (لم يُقبض)')

    def test_cannot_deliver_more_than_available(self):
        with self.assertRaises(ValidationError):
            EmptyBoxService.create_delivery(
                self._data(quantity=301), user=self.accountant
            )
        # ولم يُنشأ أي سجل
        self.assertEqual(self.EmptyBoxDelivery.objects.count(), 0)

    def test_sale_requires_price_and_payment_method(self):
        with self.assertRaises(ValidationError):
            EmptyBoxService.create_delivery(
                self._data(
                    kind=self.EmptyBoxDelivery.Kind.SALE, recipient=self.org_recipient,
                    quantity=10, unit_price=Decimal('0.000'),
                    payment_method=self.EmptyBoxDelivery.PaymentMethod.CASH,
                    treasury_account=self.treasury,
                ),
                user=self.accountant,
            )

    def test_return_rejects_a_price(self):
        with self.assertRaises(ValidationError):
            EmptyBoxService.create_delivery(
                self._data(kind=self.EmptyBoxDelivery.Kind.RETURN, unit_price=Decimal('0.500')),
                user=self.accountant,
            )

    def test_update_draft_changes_quantity_and_total(self):
        delivery = EmptyBoxService.create_delivery(
            self._data(
                kind=self.EmptyBoxDelivery.Kind.SALE, recipient=self.org_recipient,
                quantity=10, unit_price=Decimal('0.200'),
                payment_method=self.EmptyBoxDelivery.PaymentMethod.CASH,
                treasury_account=self.treasury,
            ),
            user=self.accountant,
        )
        updated = EmptyBoxService.update_delivery(
            delivery, self._data(
                kind=self.EmptyBoxDelivery.Kind.SALE, recipient=self.org_recipient,
                quantity=20, unit_price=Decimal('0.200'),
                payment_method=self.EmptyBoxDelivery.PaymentMethod.CASH,
                treasury_account=self.treasury,
            ),
            user=self.accountant,
        )
        self.assertEqual(updated.pk, delivery.pk)
        self.assertEqual(updated.quantity, 20)
        self.assertEqual(updated.total_amount, Decimal('4.000'))

    def test_delivered_order_cannot_be_updated(self):
        delivery = EmptyBoxService.create_delivery(
            self._data(quantity=25, status=self.EmptyBoxDelivery.Status.DELIVERED),
            user=self.accountant,
        )
        with self.assertRaises(ValidationError):
            EmptyBoxService.update_delivery(delivery, self._data(quantity=10), user=self.accountant)

    def test_cash_sale_without_treasury_is_rejected_on_confirm(self):
        """بيع نقدي بلا خزينة محددة: لا يُقبل القيد — رسالة عربية واضحة بدل أثر مالي ناقص."""
        delivery = EmptyBoxService.create_delivery(
            self._data(
                kind=self.EmptyBoxDelivery.Kind.SALE, recipient=self.org_recipient,
                quantity=15, unit_price=Decimal('0.100'),
                payment_method=self.EmptyBoxDelivery.PaymentMethod.CASH,
                treasury_account=self.treasury,
            ),
            user=self.accountant,
        )
        # نحاكي أمراً وُجد بلا خزينة (كأن الخزينة حُذفت/فُرِّغت لاحقاً)
        self.EmptyBoxDelivery.objects.filter(pk=delivery.pk).update(treasury_account=None)
        delivery.refresh_from_db()
        with self.assertRaises(ValidationError):
            EmptyBoxService.confirm_delivery(delivery, user=self.accountant)
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, self.EmptyBoxDelivery.Status.DRAFT)
        self.treasury.refresh_from_db()
        self.assertEqual(self.treasury.current_balance, Decimal('0.000'))

    def test_sale_draft_without_payment_method_is_rejected_on_confirm(self):
        """مسودة بيع بلا طريقة قبض: الاعتماد يرفضها برسالة عربية واضحة."""
        delivery = EmptyBoxService.create_delivery(
            self._data(
                kind=self.EmptyBoxDelivery.Kind.SALE, recipient=self.org_recipient,
                quantity=15, unit_price=Decimal('0.100'),
                payment_method=self.EmptyBoxDelivery.PaymentMethod.RECEIVABLE,
                treasury_account=None,
            ),
            user=self.accountant,
        )
        self.EmptyBoxDelivery.objects.filter(pk=delivery.pk).update(payment_method='')
        delivery.refresh_from_db()
        with self.assertRaises(ValidationError):
            EmptyBoxService.confirm_delivery(delivery, user=self.accountant)


class EmptyBoxDeliveryViewTests(TestCase):
    """مسارات الويب لقسم تسليم الفارغ: الصفحات + الدخول المطلوب."""

    def setUp(self):
        from boxes.models import FarmBoxType
        self.settings = SystemSetting.get_settings()
        self.settings.save()
        self.accountant = User.objects.create_user(
            username='ebv_accountant', password='pass', role=User.Role.ACCOUNTANT
        )
        self.trader = Trader.objects.create(code='EBV1', name='مزارع ثانٍ')
        self.farm_box_type = FarmBoxType.objects.create(
            name='صندوق عرض 8KG', description='أخضر'
        )

    def test_list_page_renders_with_empty_state(self):
        client = self.client
        client.force_login(self.accountant)
        response = client.get(reverse('empty_box_delivery_list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'تسليم الفارغ')

    def test_pages_require_login(self):
        client = self.client
        response = client.get(reverse('empty_box_delivery_list'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response.url)

    def test_recipient_create_and_edit(self):
        from boxes.models import EmptyBoxRecipient
        client = self.client
        client.force_login(self.accountant)
        response = client.post(reverse('empty_box_recipient_create'), {
            'name': 'منظمة الفارغ', 'kind': 'ORGANIZATION',
            'contact_person': '', 'phone': '', 'address': '', 'is_active': 'on', 'notes': '',
        })
        self.assertEqual(response.status_code, 302)
        recipient = EmptyBoxRecipient.objects.get(name='منظمة الفارغ')
        response = client.post(reverse('empty_box_recipient_edit', args=[recipient.pk]), {
            'name': 'منظمة الفارغ المحدثة', 'kind': 'ORGANIZATION',
            'contact_person': 'أبو سامي', 'phone': '0790000000', 'address': 'عمان',
            'is_active': 'on', 'notes': '',
        })
        self.assertEqual(response.status_code, 302)
        recipient.refresh_from_db()
        self.assertEqual(recipient.name, 'منظمة الفارغ المحدثة')

    def test_duplicate_recipient_shows_form_error_not_500(self):
        from boxes.models import EmptyBoxRecipient
        client = self.client
        client.force_login(self.accountant)
        EmptyBoxRecipient.objects.create(name='جهة مكررة', kind='FARM')
        response = client.post(reverse('empty_box_recipient_create'), {
            'name': 'جهة مكررة', 'kind': 'FARM',
            'contact_person': '', 'phone': '', 'address': '', 'is_active': 'on', 'notes': '',
        })
        self.assertEqual(response.status_code, 200)  # خطأ في النموذج لا 500
        self.assertContains(response, 'مكرر')
