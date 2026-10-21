from django.db import models
from django.conf import settings
from django.utils import timezone
from django.core.exceptions import ValidationError
from decimal import Decimal


class TraderBoxBalance(models.Model):
    """
    رصيد الصناديق الفارغة لكل تاجر لكل نوع صندوق:
    المستلم (صادر) - المرتجع (وارد) = الرصيد في عهدة التاجر
    الفرق يحسب بسعر الصندوق المعتمد وتنبيه عند النقص/العجز
    """
    trader = models.ForeignKey(
        'operations.Trader',
        on_delete=models.CASCADE,
        related_name='box_balances',
        verbose_name='التاجر'
    )
    box_type = models.ForeignKey(
        'operations.BoxType',
        on_delete=models.PROTECT,
        related_name='trader_balances',
        verbose_name='نوع الصندوق'
    )
    issued_count = models.IntegerField(default=0, verbose_name='الصادر للتاجر (المستلم)')
    returned_count = models.IntegerField(default=0, verbose_name='الوارد من التاجر (المرتجع)')
    net_balance = models.IntegerField(default=0, verbose_name='الرصيد القائم في العهدة (صادر - وارد)')
    total_financial_value = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=Decimal('0.000'),
        verbose_name='قيمة العجز المالي المحسوبة (دينار)'
    )
    last_updated = models.DateTimeField(auto_now=True, verbose_name='آخر تحديث')

    class Meta:
        verbose_name = 'رصيد صناديق التاجر'
        verbose_name_plural = 'أرصدة صناديق التجار'
        unique_together = ('trader', 'box_type')

    def __str__(self):
        return f"{self.trader.name} - {self.box_type.name}: {self.net_balance} صندوق"

    def recalculate(self):
        """
        إعادة احتساب الرصيد والقيمة المالية بسعر الصندوق المعتمد
        """
        self.net_balance = self.issued_count - self.returned_count
        price = self.box_type.discount_price if self.box_type else Decimal('0.000')
        if self.net_balance > 0:
            self.total_financial_value = (Decimal(self.net_balance) * price).quantize(Decimal('0.001'))
        else:
            self.total_financial_value = Decimal('0.000')


class TraderBoxTransaction(models.Model):
    """
    سجل حركات الصناديق:
    النوع، الكمية، الجهة، المصدر، الوجهة، السبب، المستخدم، التاريخ
    فصل الكمية الفعلية عن القيمة المالية، ومنع خصم الصندوق مرتين
    """
    class Type(models.TextChoices):
        ISSUE = 'ISSUE', 'صادر للتاجر (صرف للتوضيب/سحب)'
        RETURN = 'RETURN', 'وارد من التاجر (إرجاع للمشغل)'
        ADJUSTMENT = 'ADJUSTMENT', 'تسوية جردية معتمدة'

    trader = models.ForeignKey(
        'operations.Trader',
        on_delete=models.CASCADE,
        related_name='box_transactions',
        verbose_name='التاجر'
    )
    box_type = models.ForeignKey(
        'operations.BoxType',
        on_delete=models.PROTECT,
        related_name='transactions',
        verbose_name='نوع الصندوق'
    )
    transaction_type = models.CharField(
        max_length=20,
        choices=Type.choices,
        verbose_name='نوع الحركة'
    )
    quantity = models.PositiveIntegerField(verbose_name='الكمية')
    source = models.CharField(max_length=150, verbose_name='المصدر')
    destination = models.CharField(max_length=150, verbose_name='الوجهة')
    reason = models.CharField(max_length=255, verbose_name='السبب / البيان')
    related_invoice = models.ForeignKey(
        'invoicing.Invoice',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='box_transactions',
        verbose_name='الفاتورة المرتبطة'
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name='المستخدم المنفذ'
    )
    is_reversed = models.BooleanField(
        default=False,
        verbose_name='تم عكسها بقيد موثق'
    )
    reversal_reason = models.CharField(
        max_length=255,
        blank=True,
        verbose_name='سبب القيد العكسي'
    )
    timestamp = models.DateTimeField(auto_now_add=True, verbose_name='التاريخ والوقت')

    class Meta:
        verbose_name = 'حركة صناديق'
        verbose_name_plural = 'سجل حركات الصناديق'
        ordering = ['-timestamp']

    def __str__(self):
        return f"{self.get_transaction_type_display()} - {self.trader.name}: {self.quantity} {self.box_type.name}"


# ==================== القسم المستقل: الصناديق الواردة من المزرعة ====================
# هذا القسم منفصل تماماً عن مخزون صناديق ومواد التغليف التي تستخدمها الشركة في التوضيب
# (operations.BoxType). لا يمسّ company_stock ولا أرصدة التجار إطلاقاً.


class FarmBoxType(models.Model):
    """
    نوع الصندوق الوارد من المزرعة (الصناديق التي تُحمّل بها البضاعة الخام).
    قسم مستقل بذاته لأغراض الإحصاء والجرد الخاص بالوارد من المزارع.
    """
    name = models.CharField(max_length=100, verbose_name='اسم الصندوق الوارد')
    description = models.CharField(
        max_length=150, blank=True, verbose_name='الوصف / المقاس'
    )
    tare_weight = models.DecimalField(
        max_digits=6,
        decimal_places=3,
        default=Decimal('0.000'),
        verbose_name='الوزن الفارغ (كغ)'
    )
    is_active = models.BooleanField(default=True, verbose_name='نشط')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='تاريخ الإضافة')

    class Meta:
        verbose_name = 'نوع صندوق وارد من المزرعة'
        verbose_name_plural = 'أنواع الصناديق الواردة من المزرعة'
        ordering = ['name']

    def __str__(self):
        return self.name


class FarmBoxRecord(models.Model):
    """
    سجل صناديق المزرعة لكل سيارة (قسم مستقل):
    - عدد الصناديق الواردة مع السيارة (البضاعة الخام).
    - عدد الصناديق الفارغة الناتجة عن تفريغ البضاعة الخام.
    لا يُخلط هذا الرصيد مع مخزون صناديق الشركة.
    """
    trader = models.ForeignKey(
        'operations.Trader',
        on_delete=models.PROTECT,
        related_name='farm_box_records',
        verbose_name='التاجر'
    )
    truck_receipt = models.OneToOneField(
        'operations.TruckReceipt',
        on_delete=models.CASCADE,
        related_name='farm_box_record',
        verbose_name='السيارة / الدفعة'
    )
    incoming_count = models.PositiveIntegerField(
        default=0,
        verbose_name='عدد الصناديق الواردة من المزرعة'
    )
    empty_count = models.PositiveIntegerField(
        default=0,
        verbose_name='عدد الصناديق الفارغة الناتجة عن التفريغ'
    )
    notes = models.TextField(blank=True, verbose_name='ملاحظات')
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name='المستخدم الذي سجّل السجل'
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='تاريخ التسجيل')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='آخر تحديث')

    class Meta:
        verbose_name = 'سجل صناديق مزرعة'
        verbose_name_plural = 'سجلات صناديق المزرعة'
        ordering = ['-created_at']

    def __str__(self):
        return (
            f"{self.truck_receipt.lot_number} — {self.trader.name} — وارد {self.incoming_count} "
            f"/ فارغ ناتج {self.empty_count} ({self.truck_receipt.box_type.name})"
        )

    def clean(self):
        super().clean()
        if self.truck_receipt_id and self.trader_id:
            if self.truck_receipt.trader_id != self.trader_id:
                raise ValidationError({
                    'trader': 'التاجر يجب أن يطابق تاجر السيارة المحددة.'
                })

    def save(self, *args, **kwargs):
        # التاجر يُشتق دائماً من السيارة لضمان ألا تُنسب الصناديق لتاجر آخر
        if self.truck_receipt_id:
            self.trader_id = self.truck_receipt.trader_id
        super().save(*args, **kwargs)


# ==================== تسليم الفارغ: إرجاعه إلى المزرعة أو بيعه ====================


class EmptyBoxRecipient(models.Model):
    """
    الجهة المستلمة للصناديق الفارغة: مزرعة تُرجَع إليها، أو منظمة/جهة تُشترى منها.
    جهة موحّدة للطرفين لأنّهما في الأساس «جهة تستلم الفارغ»، ويُفرَّق بينهما بالنوع.
    """
    class Kind(models.TextChoices):
        FARM = 'FARM', 'مزرعة (إرجاع)'
        ORGANIZATION = 'ORGANIZATION', 'منظمة / جهة أخرى (بيع)'

    name = models.CharField(max_length=150, verbose_name='اسم الجهة')
    kind = models.CharField(
        max_length=20, choices=Kind.choices, default=Kind.FARM, verbose_name='نوع الجهة'
    )
    contact_person = models.CharField(max_length=150, blank=True, verbose_name='الشخص المسؤول')
    phone = models.CharField(max_length=50, blank=True, verbose_name='رقم الهاتف')
    address = models.CharField(max_length=255, blank=True, verbose_name='العنوان')
    is_active = models.BooleanField(default=True, verbose_name='نشطة')
    notes = models.TextField(blank=True, verbose_name='ملاحظات')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='تاريخ الإضافة')

    class Meta:
        verbose_name = 'جهة مستلمة للفارغ'
        verbose_name_plural = 'الجهات المستلمة للفارغ'
        ordering = ['kind', 'name']
        unique_together = ('name', 'kind')

    def __str__(self):
        return f"{self.name} ({self.get_kind_display()})"


class EmptyBoxDelivery(models.Model):
    """
    أمر تسليم صناديق فارغة لجهة (مزرعة أو منظمة):

    - الإرجاع إلى المزرعة: يخرج الفارغ من حوزة المشغل بلا مقابل مالي
      (وزن الصناديق الفارغة أصلاً محسوم من صافي الاستلام).
    - البيع لمنظمة/جهة: يخرج بمقابل مالي، ويُقيَّد المبلغ في الخزينة كإيراد
      عبر `EmptyBoxService` في حركة واحدة لا تقبل التجزئة.

    لا يُخلط هذا الرصيد مع `TraderBoxBalance` (عهدة التجار) ولا مع
    `BoxType.company_stock` (مخزون صناديق الشركة).
    """
    class Kind(models.TextChoices):
        RETURN = 'RETURN', 'إرجاع إلى المزرعة'
        SALE = 'SALE', 'بيع لمنظمة / جهة'

    class Status(models.TextChoices):
        DRAFT = 'DRAFT', 'مسودة'
        DELIVERED = 'DELIVERED', 'مُسلَّم'

    class PaymentMethod(models.TextChoices):
        CASH = 'CASH', 'نقدي (كاش)'
        BANK_TRANSFER = 'BANK_TRANSFER', 'تحويل بنكي'
        RECEIVABLE = 'RECEIVABLE', 'على الحساب (لم يُقبض بعد)'

    reference_number = models.CharField(
        max_length=60, unique=True, verbose_name='رقم أمر التسليم'
    )
    kind = models.CharField(max_length=20, choices=Kind.choices, verbose_name='نوع التسليم')
    recipient = models.ForeignKey(
        EmptyBoxRecipient,
        on_delete=models.PROTECT,
        related_name='deliveries',
        verbose_name='الجهة المستلمة',
    )
    box_type = models.ForeignKey(
        'boxes.FarmBoxType',
        on_delete=models.PROTECT,
        related_name='empty_deliveries',
        verbose_name='نوع الصندوق',
        help_text='من أنواع الصناديق الواردة من المزرعة التي يُجمَع فارغها بعد التفريغ.',
    )
    quantity = models.PositiveIntegerField(verbose_name='عدد الصناديق')
    unit_price = models.DecimalField(
        max_digits=10, decimal_places=3, default=Decimal('0.000'),
        verbose_name='سعر الصندوق الواحد (دينار)',
        help_text='للبيع فقط — للإرجاع إلى المزرعة اتركه صفراً.',
    )
    total_amount = models.DecimalField(
        max_digits=12, decimal_places=3, default=Decimal('0.000'),
        verbose_name='إجمالي المبلغ (دينار)',
    )
    delivery_date = models.DateField(verbose_name='تاريخ التسليم')
    driver_name = models.CharField(max_length=150, blank=True, verbose_name='اسم السائق / الناقل')
    vehicle_plate = models.CharField(max_length=50, blank=True, verbose_name='رقم السيارة')
    payment_method = models.CharField(
        max_length=20, choices=PaymentMethod.choices, blank=True,
        verbose_name='طريقة القبض (للبيع)',
    )
    treasury_account = models.ForeignKey(
        'finance.TreasuryAccount',
        on_delete=models.PROTECT,
        related_name='empty_box_deliveries',
        null=True, blank=True,
        verbose_name='الخزينة المستلمة للمبلغ',
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.DRAFT, verbose_name='الحالة'
    )
    revenue_posted_at = models.DateTimeField(
        null=True, blank=True,
        verbose_name='وقت قيد الإيراد في الخزينة',
        help_text='يُملأ مرة واحدة عند قيد مبلغ البيع، ويمنع تكرار القيد عند إعادة الاعتماد.',
    )
    notes = models.TextField(blank=True, verbose_name='ملاحظات')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='empty_box_deliveries', verbose_name='أنشأ الأمر',
    )
    delivered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='empty_box_deliveries_confirmed', verbose_name='اعتمد التسليم',
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='تاريخ الإنشاء')
    delivered_at = models.DateTimeField(null=True, blank=True, verbose_name='وقت التسليم الفعلي')

    class Meta:
        verbose_name = 'أمر تسليم فارغ'
        verbose_name_plural = 'أوامر تسليم الفارغ'
        ordering = ['-delivery_date', '-id']

    def __str__(self):
        return f"{self.reference_number} — {self.get_kind_display()} — {self.quantity} صندوق إلى {self.recipient.name}"

    @property
    def is_return(self):
        return self.kind == self.Kind.RETURN

    @property
    def is_sale(self):
        return self.kind == self.Kind.SALE

    @property
    def is_revenue_posted(self):
        """هل قُيِّد مبلغ البيع في الخزينة؟ (يمنع خصم/إضافة المبلغ مرتين)."""
        return self.revenue_posted_at is not None

    @property
    def expected_total(self):
        """الإجمالي المتوقع من الكمية × سعر الوحدة (يُثبَّت في total_amount عند الاعتماد)."""
        return (Decimal(self.quantity) * Decimal(self.unit_price or 0)).quantize(Decimal('0.001'))

    @property
    def payment_status_label(self):
        if not self.is_sale:
            return '—'
        if self.status != self.Status.DELIVERED:
            return 'غير مُسلَّم بعد'
        if self.payment_method == self.PaymentMethod.RECEIVABLE:
            return 'على الحساب (لم يُقبض)'
        return 'مقبوض'

    @classmethod
    def generate_reference(cls):
        """رقم تسلسلي سنوي: EBD-2026-0001."""
        year = timezone.now().year
        last = cls.objects.filter(reference_number__startswith=f'EBD-{year}-').order_by('-id').first()
        seq = 1
        if last:
            try:
                seq = int(last.reference_number.rsplit('-', 1)[1]) + 1
            except (IndexError, ValueError):
                seq = cls.objects.filter(reference_number__startswith=f'EBD-{year}-').count() + 1
        return f'EBD-{year}-{seq:04d}'

    def clean(self):
        super().clean()
        if self.kind == self.Kind.RETURN and self.unit_price:
            raise ValidationError({'unit_price': 'الإرجاع إلى المزرعة لا يحمل سعراً — اترك السعر صفراً.'})
        if self.kind == self.Kind.SALE and self.unit_price and self.unit_price <= 0:
            raise ValidationError({'unit_price': 'سعر البيع يجب أن يكون أكبر من صفر.'})

    def recalc_total(self):
        self.total_amount = self.expected_total
        return self.total_amount


class FarmEmptyBoxConsumption(models.Model):
    """
    استهلاك الصناديق الفارغة (الواردة من المزرعة) داخل التوضيب:

    عندما تُفرَّغ البضاعة الخام من صناديق المزرعة، ينتج فارغ (`FarmBoxRecord.empty_count`).
    عند التوضيب يعيد المشغّل استخدام هذا الفارغ، فيُسجَّل هنا العدد المستهلك لكل نوع.

    **مهم:** هذا السجل يُنقص الرصيد المتاح للتسليم (`EmptyBoxDelivery`) حتى لا يُخصم
    نفس الفارغ مرتين (مرة في التوضيب ومرة عند تسليم الفارغ للمزرعة/بيعه).

    المتاح الفعلي = Σ `FarmBoxRecord.empty_count` − Σ المستهلك في التوضيب − Σ المسلَّم.
    """
    farm_box_type = models.ForeignKey(
        FarmBoxType,
        on_delete=models.PROTECT,
        related_name='packing_consumptions',
        verbose_name='نوع الصندوق الفارغ (من المزرعة)',
    )
    quantity = models.PositiveIntegerField(verbose_name='عدد الصناديق الفارغة المستهلكة')
    trader = models.ForeignKey(
        'operations.Trader',
        on_delete=models.PROTECT,
        related_name='farm_empty_consumptions',
        verbose_name='التاجر',
    )
    packing_session = models.ForeignKey(
        'packing.PackingSession',
        on_delete=models.CASCADE,
        null=True, blank=True,
        related_name='farm_empty_consumptions',
        verbose_name='جلسة التوضيب',
    )
    packed_pallet = models.ForeignKey(
        'packing.PackedPallet',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='farm_empty_consumptions',
        verbose_name='الطبلية الموضبة',
    )
    notes = models.CharField(max_length=255, blank=True, verbose_name='ملاحظات')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='farm_empty_consumptions',
        verbose_name='المستخدم الذي سجّل الاستهلاك',
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='تاريخ التسجيل')

    class Meta:
        verbose_name = 'استهلاك فارغ مزرعة في التوضيب'
        verbose_name_plural = 'استهلاك الفارغ في التوضيب'
        ordering = ['-created_at', '-id']

    def __str__(self):
        return f"{self.farm_box_type.name}: {self.quantity} صندوق — {self.trader.name}"
