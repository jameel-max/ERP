"""
قسم الاستيراد والتصدير (شحنات حقيقية).

يغطّي دورة الشحنة كاملةً:
1. تسجيل الشحنة: البضاعة، الكمية، السعر، المورد/العميل، المنشأ والوجهة.
2. متابعة الشحنة: الحالة والتواريخ (الطلب، المغادرة، الوصول المتوقع والفعلي).
3. التكاليف: قيمة البضاعة + النقل + الجمارك + التخليص + التأمين + أخرى.
4. الأوراق: الفاتورة التجارية، شهادة المنشأ، البيان الجمركي، قائمة التعبئة، بوليصة الشحن...
5. الدفعات: المدفوع للمورد / المقبوض من العميل والرصيد المتبقي.
6. مطابقة الكميات: المشحون مقابل الواصل والنقص والتلف.
7. الربح: قيمة البيع ناقص إجمالي التكلفة.

كل المبالغ تُحوَّل إلى الدينار الأردني (JOD) بسعر صرف يدوي — يعمل دون إنترنت.
"""

from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.db import models
from django.utils import timezone


THREE = Decimal('0.001')


def q3(value):
    """تقريب مبلغ/كمية إلى ثلاث خانات عشرية (فلس)."""
    if value is None:
        return Decimal('0.000')
    return Decimal(value).quantize(THREE, rounding=ROUND_HALF_UP)


class TradeShipment(models.Model):
    """شحنة استيراد أو تصدير واحدة."""

    class Direction(models.TextChoices):
        IMPORT = 'IMPORT', 'استيراد'
        EXPORT = 'EXPORT', 'تصدير'

    class Status(models.TextChoices):
        DRAFT = 'DRAFT', 'قيد التحضير'
        IN_TRANSIT = 'IN_TRANSIT', 'في الطريق'
        ARRIVED = 'ARRIVED', 'وصلت'
        CLEARED = 'CLEARED', 'تم التخليص'
        DELIVERED = 'DELIVERED', 'تم التسليم'
        CLOSED = 'CLOSED', 'مغلقة'
        CANCELLED = 'CANCELLED', 'ملغاة'

    class Currency(models.TextChoices):
        JOD = 'JOD', 'الدينار الأردني'
        USD = 'USD', 'الدولار الأمريكي'
        EUR = 'EUR', 'اليورو'
        GBP = 'GBP', 'الجنيه الإسترليني'
        AED = 'AED', 'الدرهم الإماراتي'
        SAR = 'SAR', 'الريال السعودي'
        EGP = 'EGP', 'الجنيه المصري'
        TRY = 'TRY', 'الليرة التركية'
        CNY = 'CNY', 'اليوان الصيني'
        OTHER = 'OTHER', 'أخرى'

    class QuantityUnit(models.TextChoices):
        BOX = 'BOX', 'صندوق'
        CARTON = 'CARTON', 'كرتونة'
        KG = 'KG', 'كيلوغرام'
        TON = 'TON', 'طن'
        PALLET = 'PALLET', 'طبلية'
        UNIT = 'UNIT', 'وحدة'

    reference_number = models.CharField(max_length=40, unique=True, verbose_name='رقم الشحنة')
    direction = models.CharField(
        max_length=10, choices=Direction.choices, default=Direction.IMPORT, verbose_name='النوع'
    )

    counterparty_name = models.CharField(max_length=200, verbose_name='المورد / العميل')
    counterparty_country = models.CharField(max_length=120, blank=True, verbose_name='بلد المورد / العميل')
    goods_description = models.CharField(max_length=255, verbose_name='البضاعة')
    crop = models.ForeignKey(
        'operations.Crop', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='trade_shipments', verbose_name='نوع البضاعة (اختياري)'
    )

    quantity = models.DecimalField(
        max_digits=14, decimal_places=3, default=Decimal('0.000'), verbose_name='الكمية'
    )
    quantity_unit = models.CharField(
        max_length=10, choices=QuantityUnit.choices, default=QuantityUnit.BOX, verbose_name='وحدة الكمية'
    )
    unit_price = models.DecimalField(
        max_digits=14, decimal_places=3, default=Decimal('0.000'), verbose_name='سعر الوحدة'
    )
    currency = models.CharField(
        max_length=10, choices=Currency.choices, default=Currency.JOD, verbose_name='العملة'
    )
    exchange_rate = models.DecimalField(
        max_digits=18, decimal_places=6, default=Decimal('1'),
        verbose_name='سعر الصرف مقابل الدينار (1 وحدة = ؟ دينار)'
    )
    sale_value = models.DecimalField(
        max_digits=16, decimal_places=3, default=Decimal('0.000'),
        verbose_name='قيمة البيع النهائية (دينار، اختياري)'
    )

    origin = models.CharField(max_length=200, blank=True, verbose_name='من أين (المنشأ)')
    destination = models.CharField(max_length=200, blank=True, verbose_name='إلى أين (الوجهة)')

    order_date = models.DateField(null=True, blank=True, verbose_name='تاريخ الطلب')
    departure_date = models.DateField(null=True, blank=True, verbose_name='تاريخ الشحن / المغادرة')
    eta_date = models.DateField(null=True, blank=True, verbose_name='الوصول المتوقع')
    arrival_date = models.DateField(null=True, blank=True, verbose_name='الوصول الفعلي')

    received_quantity = models.DecimalField(
        max_digits=14, decimal_places=3, default=Decimal('0.000'), verbose_name='الكمية الواصلة'
    )
    damaged_quantity = models.DecimalField(
        max_digits=14, decimal_places=3, default=Decimal('0.000'), verbose_name='الكمية التالفة'
    )

    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.DRAFT, verbose_name='الحالة'
    )
    notes = models.TextField(blank=True, verbose_name='ملاحظات')

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='trade_shipments', verbose_name='المحرر'
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='تاريخ الإنشاء')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='تاريخ التحديث')

    class Meta:
        verbose_name = 'شحنة استيراد/تصدير'
        verbose_name_plural = 'شحنات الاستيراد والتصدير'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.reference_number} — {self.goods_description}"

    @classmethod
    def generate_reference(cls, direction, year=None):
        year = year or timezone.now().year
        prefix = 'IMP' if direction == cls.Direction.IMPORT else 'EXP'
        base = f"{prefix}-{year}-"
        existing = cls.objects.filter(reference_number__startswith=base)
        last = existing.order_by('-reference_number').first()
        nxt = existing.count() + 1
        if last:
            try:
                nxt = int(last.reference_number.split('-')[-1]) + 1
            except (ValueError, IndexError):
                pass
        return f"{base}{nxt:04d}"

    # ---------------- القيم المحسوبة ----------------

    @property
    def goods_value_jod(self):
        """قيمة البضاعة = الكمية × سعر الوحدة × سعر الصرف."""
        return q3(Decimal(self.quantity) * Decimal(self.unit_price) * Decimal(self.exchange_rate))

    @property
    def extra_costs_jod(self):
        """مجموع التكاليف الإضافية (نقل/جمارك/تخليص...) بالدينار."""
        return q3(sum((c.amount_jod for c in self.costs.all()), Decimal('0')))

    @property
    def total_cost_jod(self):
        """
        إجمالي تكلفة الشحنة:
        - استيراد: قيمة البضاعة + التكاليف الإضافية.
        - تصدير: التكاليف الإضافية فقط (قيمة البضاعة هنا إيراد بيع لا تكلفة).
        """
        if self.direction == self.Direction.EXPORT:
            return self.extra_costs_jod
        return q3(self.goods_value_jod + self.extra_costs_jod)

    @property
    def revenue_jod(self):
        """
        الإيراد المعتمد لحساب الربح:
        - إن أُدخلت قيمة بيع صريحة تُستخدم.
        - وإلا فشحنة التصدير إيرادها قيمة البضاعة.
        - وشحنة الاستيراد بلا قيمة بيع = الربح غير محدد.
        """
        if self.sale_value and Decimal(self.sale_value) > 0:
            return q3(self.sale_value)
        if self.direction == self.Direction.EXPORT:
            return self.goods_value_jod
        return None

    @property
    def effective_sale_jod(self):
        """توافقية: تعادل revenue_jod."""
        return self.revenue_jod

    @property
    def profit_jod(self):
        revenue = self.revenue_jod
        if revenue is None:
            return None
        return q3(revenue - self.total_cost_jod)

    @property
    def profit_margin_pct(self):
        revenue = self.revenue_jod
        if not revenue:
            return None
        return (Decimal(self.profit_jod) / revenue * 100).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    @property
    def total_paid_jod(self):
        """المدفوع للمورد."""
        return q3(sum(
            (p.amount_jod for p in self.payments.all() if p.kind == ShipmentPayment.Kind.PAID),
            Decimal('0'),
        ))

    @property
    def total_received_jod(self):
        """المقبوض من العميل."""
        return q3(sum(
            (p.amount_jod for p in self.payments.all() if p.kind == ShipmentPayment.Kind.RECEIVED),
            Decimal('0'),
        ))

    @property
    def counterparty_balance_jod(self):
        """
        الرصيد مع الطرف الآخر:
        - استيراد: المتبقي لنا دفعه للمورد = قيمة البضاعة − المدفوع.
        - تصدير: المتبقي على العميل دفعه لنا = قيمة البضاعة − المقبوض.
        """
        if self.direction == self.Direction.EXPORT:
            return q3(self.goods_value_jod - self.total_received_jod)
        return q3(self.goods_value_jod - self.total_paid_jod)

    @property
    def shortage_quantity(self):
        """النقص = الكمية المشحونة − الكمية الواصلة."""
        return q3(Decimal(self.quantity) - Decimal(self.received_quantity))

    @property
    def has_shortage(self):
        return self.shortage_quantity > 0

    @property
    def costs_complete(self):
        return self.documents.filter(received=True).exists()


class ShipmentCost(models.Model):
    """تكلفة إضافية على الشحنة (غير قيمة البضاعة نفسها)."""

    class Kind(models.TextChoices):
        FREIGHT = 'FREIGHT', 'النقل / الشحن'
        CUSTOMS = 'CUSTOMS', 'الجمارك'
        CLEARANCE = 'CLEARANCE', 'التخليص'
        INSURANCE = 'INSURANCE', 'التأمين'
        STORAGE = 'STORAGE', 'التخزين / الميناء'
        OTHER = 'OTHER', 'أخرى'

    shipment = models.ForeignKey(
        TradeShipment, on_delete=models.CASCADE, related_name='costs', verbose_name='الشحنة'
    )
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.FREIGHT, verbose_name='نوع التكلفة')
    description = models.CharField(max_length=200, blank=True, verbose_name='البيان')
    amount = models.DecimalField(
        max_digits=14, decimal_places=3, default=Decimal('0.000'), verbose_name='المبلغ'
    )
    currency = models.CharField(
        max_length=10, choices=TradeShipment.Currency.choices,
        default=TradeShipment.Currency.JOD, verbose_name='العملة'
    )
    exchange_rate = models.DecimalField(
        max_digits=18, decimal_places=6, default=Decimal('1'), verbose_name='سعر الصرف'
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='وقت الإضافة')

    class Meta:
        verbose_name = 'تكلفة شحنة'
        verbose_name_plural = 'تكاليف الشحنات'
        ordering = ['id']

    def __str__(self):
        return f"{self.get_kind_display()} — {self.amount} {self.currency}"

    @property
    def amount_jod(self):
        return q3(Decimal(self.amount) * Decimal(self.exchange_rate))


class ShipmentPayment(models.Model):
    """دفعة على الشحنة (مدفوع للمورد أو مقبوض من العميل)."""

    class Kind(models.TextChoices):
        PAID = 'PAID', 'مدفوع للمورد'
        RECEIVED = 'RECEIVED', 'مقبوض من العميل'

    class Method(models.TextChoices):
        CASH = 'CASH', 'نقداً'
        BANK = 'BANK', 'تحويل بنكي'
        CHECK = 'CHECK', 'شيك'
        OTHER = 'OTHER', 'أخرى'

    shipment = models.ForeignKey(
        TradeShipment, on_delete=models.CASCADE, related_name='payments', verbose_name='الشحنة'
    )
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.PAID, verbose_name='الاتجاه')
    amount = models.DecimalField(
        max_digits=14, decimal_places=3, default=Decimal('0.000'), verbose_name='المبلغ'
    )
    currency = models.CharField(
        max_length=10, choices=TradeShipment.Currency.choices,
        default=TradeShipment.Currency.JOD, verbose_name='العملة'
    )
    exchange_rate = models.DecimalField(
        max_digits=18, decimal_places=6, default=Decimal('1'), verbose_name='سعر الصرف'
    )
    payment_date = models.DateField(default=timezone.now, verbose_name='تاريخ الدفعة')
    method = models.CharField(max_length=10, choices=Method.choices, default=Method.BANK, verbose_name='طريقة الدفع')
    reference = models.CharField(max_length=100, blank=True, verbose_name='رقم الحوالة / الشيك')
    notes = models.CharField(max_length=255, blank=True, verbose_name='ملاحظات')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='trade_payments', verbose_name='المحرر'
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='وقت التسجيل')

    class Meta:
        verbose_name = 'دفعة شحنة'
        verbose_name_plural = 'دفعات الشحنات'
        ordering = ['-payment_date', '-id']

    def __str__(self):
        return f"{self.get_kind_display()} — {self.amount} {self.currency}"

    @property
    def amount_jod(self):
        return q3(Decimal(self.amount) * Decimal(self.exchange_rate))


class ShipmentDocument(models.Model):
    """ورقة/مستند يخصّ الشحنة."""

    class Kind(models.TextChoices):
        COMMERCIAL_INVOICE = 'COMMERCIAL_INVOICE', 'فاتورة تجارية'
        CERTIFICATE_OF_ORIGIN = 'CERTIFICATE_OF_ORIGIN', 'شهادة منشأ'
        CUSTOMS_DECLARATION = 'CUSTOMS_DECLARATION', 'بيان جمركي'
        PACKING_LIST = 'PACKING_LIST', 'قائمة تعبئة'
        BILL_OF_LADING = 'BILL_OF_LADING', 'بوليصة شحن'
        PHYTOSANITARY = 'PHYTOSANITARY', 'شهادة صحية نباتية'
        OTHER = 'OTHER', 'أخرى'

    shipment = models.ForeignKey(
        TradeShipment, on_delete=models.CASCADE, related_name='documents', verbose_name='الشحنة'
    )
    kind = models.CharField(
        max_length=30, choices=Kind.choices, default=Kind.COMMERCIAL_INVOICE, verbose_name='نوع المستند'
    )
    number = models.CharField(max_length=120, blank=True, verbose_name='رقم المستند')
    issue_date = models.DateField(null=True, blank=True, verbose_name='تاريخ الإصدار')
    received = models.BooleanField(default=False, verbose_name='تم استلام الأصل')
    notes = models.CharField(max_length=255, blank=True, verbose_name='ملاحظات')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='وقت الإضافة')

    class Meta:
        verbose_name = 'مستند شحنة'
        verbose_name_plural = 'مستندات الشحنات'
        ordering = ['kind', 'id']

    def __str__(self):
        return f"{self.get_kind_display()} — {self.number or 'بلا رقم'}"

    @property
    def is_received(self):
        return self.received
