from django.db import models
from django.utils import timezone
from django.conf import settings
from decimal import Decimal, ROUND_HALF_UP


class Invoice(models.Model):
    """
    الفاتورة: لكل تاجر + سنة + نوع بضاعة
    الترقيم: سنة-رمز التاجر-رقم، مثال: 2026-T012-004
    """
    class Status(models.TextChoices):
        DRAFT = 'DRAFT', 'مسودة'
        APPROVED = 'APPROVED', 'معتمدة'
        PAID = 'PAID', 'مسددة'
        CANCELLED = 'CANCELLED', 'ملغاة'

    class PackingMethod(models.TextChoices):
        MACHINE = 'MACHINE', 'مكينة'
        MANUAL = 'MANUAL', 'يدوي'

    invoice_number = models.CharField(
        max_length=60,
        unique=True,
        verbose_name='رقم الفاتورة'
    )
    trader = models.ForeignKey(
        'operations.Trader',
        on_delete=models.PROTECT,
        related_name='invoices',
        verbose_name='التاجر'
    )
    year = models.PositiveIntegerField(
        default=timezone.now().year,
        verbose_name='السنة المالية'
    )
    crop = models.ForeignKey(
        'operations.Crop',
        on_delete=models.PROTECT,
        related_name='invoices',
        verbose_name='نوع البضاعة'
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
        verbose_name='حالة الفاتورة'
    )
    packing_method = models.CharField(
        max_length=20,
        choices=PackingMethod.choices,
        default=PackingMethod.MACHINE,
        verbose_name='طريقة التوضيب المعتمدة'
    )
    packing_rate_snapshot = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        default=Decimal('0.000'),
        verbose_name='سعر الكيلو الموضب المجمد (دينار)'
    )
    total_packed_kg = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=Decimal('0.000'),
        verbose_name='إجمالي الكيلو الموضب فعلاً (كغ)'
    )
    packing_subtotal = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=Decimal('0.000'),
        verbose_name='مجموع أجور التوضيب (دينار)'
    )
    box_discount_subtotal = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=Decimal('0.000'),
        verbose_name='خصم الصناديق (دينار)'
    )
    cooling_subtotal = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=Decimal('0.000'),
        verbose_name='أجرة التبريد اليومية للطبالي الموضبة (دينار)'
    )
    other_services = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=Decimal('0.000'),
        verbose_name='خدمات إضافية (دينار)'
    )
    general_discount = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=Decimal('0.000'),
        verbose_name='خصم عام (دينار)'
    )
    grand_total = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=Decimal('0.000'),
        verbose_name='إجمالي الفاتورة النهائي (دينار)'
    )
    paid_amount = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=Decimal('0.000'),
        verbose_name='المبلغ المسدد (دينار)'
    )
    remaining_balance = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=Decimal('0.000'),
        verbose_name='المبلغ المتبقي (دينار)'
    )
    trucks = models.ManyToManyField(
        'operations.TruckReceipt',
        blank=True,
        related_name='invoices',
        verbose_name='السيارات المرتبطة'
    )
    notes = models.TextField(blank=True, verbose_name='ملاحظات الفاتورة')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_invoices',
        verbose_name='المحرر'
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='approved_invoices',
        verbose_name='المعتمد'
    )
    approved_at = models.DateTimeField(null=True, blank=True, verbose_name='تاريخ الاعتماد')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='تاريخ الإنشاء')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='تاريخ التحديث')

    class Meta:
        verbose_name = 'فاتورة'
        verbose_name_plural = 'الفواتير'
        ordering = ['-year', '-created_at']

    def __str__(self):
        return f"{self.invoice_number} - {self.trader.name} ({self.crop.name} {self.year})"

    @classmethod
    def generate_invoice_number(cls, trader, year):
        prefix = f"{year}-{trader.code}-"
        last_inv = cls.objects.filter(invoice_number__startswith=prefix).order_by('-invoice_number').first()
        if last_inv:
            try:
                seq = int(last_inv.invoice_number.split('-')[-1]) + 1
            except (IndexError, ValueError):
                seq = cls.objects.filter(invoice_number__startswith=prefix).count() + 1
        else:
            seq = 1
        return f"{year}-{trader.code}-{seq:03d}"

    def calculate_totals(self):
        """
        حساب الإجمالي بالدقة العشرية ROUND_HALF_UP و 3 خانات للدينار الأردني
        """
        three_places = Decimal('0.001')
        self.packing_subtotal = (self.total_packed_kg * self.packing_rate_snapshot).quantize(three_places, rounding=ROUND_HALF_UP)
        total = self.packing_subtotal + self.box_discount_subtotal + self.cooling_subtotal + self.other_services - self.general_discount
        self.grand_total = max(Decimal('0.000'), total).quantize(three_places, rounding=ROUND_HALF_UP)
        self.remaining_balance = max(Decimal('0.000'), self.grand_total - self.paid_amount).quantize(three_places, rounding=ROUND_HALF_UP)


class InvoiceItem(models.Model):
    """
    بنود الفاتورة التفصيلية:
    تجميد السعر والكمية والوحدة وطريقة الحساب داخل كل بند
    """
    class ItemType(models.TextChoices):
        PACKING = 'PACKING', 'أجور توضيب'
        BOX_DEDUCTION = 'BOX_DEDUCTION', 'خصم عجز صناديق'
        COOLING = 'COOLING', 'أجرة تبريد يومية'
        SERVICE = 'SERVICE', 'خدمات إضافية'
        DISCOUNT = 'DISCOUNT', 'خصم'

    invoice = models.ForeignKey(
        Invoice,
        on_delete=models.CASCADE,
        related_name='items',
        verbose_name='الفاتورة'
    )
    item_type = models.CharField(
        max_length=20,
        choices=ItemType.choices,
        default=ItemType.PACKING,
        verbose_name='نوع البند'
    )
    description = models.CharField(
        max_length=255,
        verbose_name='بيان البند'
    )
    quantity = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=Decimal('1.000'),
        verbose_name='الكمية / الوزن (كغ أو عدد)'
    )
    unit_price = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        verbose_name='سعر الوحدة المجمد (دينار)'
    )
    total_price = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=Decimal('0.000'),
        verbose_name='الإجمالي (دينار)'
    )

    class Meta:
        verbose_name = 'بند فاتورة'
        verbose_name_plural = 'بنود الفاتورة'

    def save(self, *args, **kwargs):
        three_places = Decimal('0.001')
        self.total_price = (self.quantity * self.unit_price).quantize(three_places, rounding=ROUND_HALF_UP)
        super().save(*args, **kwargs)
