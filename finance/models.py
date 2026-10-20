from django.db import models
from django.conf import settings
from django.utils import timezone
from decimal import Decimal


class TreasuryAccount(models.Model):
    """
    الخزينة والحسابات البنكية:
    النقدية منفصلة عن الحسابات البنكية، ورصيد الخزينة ينتج عن حركات موثقة.
    """
    class AccountType(models.TextChoices):
        CASH = 'CASH', 'صندوق نقدي (كاش)'
        BANK = 'BANK', 'حساب بنكي'

    name = models.CharField(max_length=150, unique=True, verbose_name='اسم الحساب / الخزينة')
    account_type = models.CharField(
        max_length=20,
        choices=AccountType.choices,
        default=AccountType.CASH,
        verbose_name='نوع الحساب'
    )
    bank_account_number = models.CharField(max_length=100, blank=True, verbose_name='رقم الحساب البنكي / IBAN')
    opening_balance = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=Decimal('0.000'),
        verbose_name='الرصيد الافتتاحي (دينار)'
    )
    current_balance = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=Decimal('0.000'),
        verbose_name='الرصيد الفعلي الحالي (دينار)'
    )
    is_active = models.BooleanField(default=True, verbose_name='نشط')
    notes = models.TextField(blank=True, verbose_name='ملاحظات')

    class Meta:
        verbose_name = 'حساب خزينة / بنك'
        verbose_name_plural = 'الخزينة والحسابات البنكية'

    def __str__(self):
        return f"{self.name} ({self.current_balance} دينار)"


class ExpenseCategory(models.Model):
    """
    تصنيفات المصاريف التشغيلية
    """
    name = models.CharField(max_length=100, unique=True, verbose_name='اسم التصنيف')
    code = models.CharField(max_length=50, blank=True, verbose_name='الرمز')

    class Meta:
        verbose_name = 'تصنيف مصاريف'
        verbose_name_plural = 'تصنيفات المصاريف'

    def __str__(self):
        return self.name


class Expense(models.Model):
    """
    المصاريف:
    - الفصل بين تاريخ الاستحقاق وتاريخ الدفع
    - المصروف غير المدفوع لا يدخل المدفوعات النقدية
    - المصاريف المتكررة محمية من تكرار الاستحقاق (recurrence_key)
    """
    class Status(models.TextChoices):
        ACCRUED = 'ACCRUED', 'مستحق (غير مدفوع)'
        PAID = 'PAID', 'مدفوع فعلاً'
        CANCELLED = 'CANCELLED', 'ملغى'

    class Recurrence(models.TextChoices):
        NONE = 'NONE', 'غير متكرر'
        MONTHLY = 'MONTHLY', 'شهري'

    category = models.ForeignKey(
        ExpenseCategory,
        on_delete=models.PROTECT,
        related_name='expenses',
        verbose_name='تصنيف المصروف'
    )
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        verbose_name='مبلغ المصروف (دينار)'
    )
    due_date = models.DateField(
        default=timezone.now,
        verbose_name='تاريخ الاستحقاق (الفترة التي يخصها المصروف)'
    )
    payment_date = models.DateField(
        null=True,
        blank=True,
        verbose_name='تاريخ الدفع النقدي الفعلي'
    )
    treasury_account = models.ForeignKey(
        TreasuryAccount,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='expenses_paid',
        verbose_name='الخزينة / الحساب المدفوع منه'
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PAID,
        verbose_name='حالة المصروف'
    )
    beneficiary = models.CharField(
        max_length=150,
        blank=True,
        verbose_name='الجهة المستفيدة / المورد'
    )
    description = models.CharField(
        max_length=255,
        verbose_name='بيان المصروف'
    )
    is_recurring = models.BooleanField(
        default=False,
        verbose_name='مصروف متكرر'
    )
    recurrence_key = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        unique=True,
        verbose_name='مفتاح عدم التكرار (منع الاستحقاق المزدوج)'
    )
    receipt_image = models.ImageField(
        upload_to='expense_receipts/%Y/%m/',
        null=True,
        blank=True,
        verbose_name='صورة الفاتورة / السند'
    )
    is_approved = models.BooleanField(
        default=False,
        verbose_name='معتمد من المدير'
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='approved_expenses',
        verbose_name='المدير المعتمد'
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name='المحرر'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'مصروف'
        verbose_name_plural = 'المصاريف التشغيلية'
        ordering = ['-due_date']

    def __str__(self):
        return f"{self.category.name}: {self.amount} دينار ({self.get_status_display()})"


class Check(models.Model):
    """
    الشيكات:
    حالات مستقلة: مسجل، قيد التحصيل، محصل، مرتجع، ملغى.
    الشيك غير المحصل ليس نقداً متاحاً ولا يضاف للنقدية بمجرد تسجيله.
    عند التحصيل يحدث الحالة والخزينة والذمة. وعند الارتجاع يعيد المبلغ للذمة.
    """
    class Status(models.TextChoices):
        REGISTERED = 'REGISTERED', 'مسجل (قيد الانتظار)'
        UNDER_COLLECTION = 'UNDER_COLLECTION', 'قيد التحصيل بالبنك'
        COLLECTED = 'COLLECTED', 'محصّل (نقد فعلي)'
        RETURNED = 'RETURNED', 'مرتجع (إعادة المبلغ للذمة)'
        CANCELLED = 'CANCELLED', 'ملغى'

    check_number = models.CharField(
        max_length=60,
        verbose_name='رقم الشيك'
    )
    trader = models.ForeignKey(
        'operations.Trader',
        on_delete=models.PROTECT,
        related_name='checks',
        verbose_name='التاجر الساحب'
    )
    bank_name = models.CharField(
        max_length=150,
        verbose_name='اسم البنك المسحوب عليه'
    )
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        verbose_name='مبلغ الشيك (دينار)'
    )
    due_date = models.DateField(
        verbose_name='تاريخ استحقاق الشيك'
    )
    status = models.CharField(
        max_length=25,
        choices=Status.choices,
        default=Status.REGISTERED,
        verbose_name='حالة الشيك'
    )
    target_treasury = models.ForeignKey(
        TreasuryAccount,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='collected_checks',
        verbose_name='الحساب البنكي / الخزينة المودع بها عند التحصيل'
    )
    collected_at = models.DateField(
        null=True,
        blank=True,
        verbose_name='تاريخ التحصيل الفعلي'
    )
    status_history = models.JSONField(
        default=list,
        blank=True,
        verbose_name='سجل تواريخ وحالات الشيك'
    )
    notes = models.TextField(blank=True, verbose_name='ملاحظات الشيك')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name='المسجل'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'شيك'
        verbose_name_plural = 'الشيكات'
        ordering = ['due_date']

    def __str__(self):
        return f"شيك #{self.check_number} - {self.trader.name}: {self.amount} د.أ ({self.get_status_display()})"


class Payment(models.Model):
    """
    الدفعة والتحصيل:
    الدفعة ليست إيراداً جديداً بل تحصيل لمستحق.
    وترتبط بالفواتير التي تسددها مع إمكانية التوزيع على أكثر من فاتورة.
    """
    class Method(models.TextChoices):
        CASH = 'CASH', 'نقدي (كاش)'
        BANK_TRANSFER = 'BANK_TRANSFER', 'تحويل بنكي'
        CHECK = 'CHECK', 'شيك بنكي'

    receipt_number = models.CharField(
        max_length=60,
        unique=True,
        verbose_name='رقم سند القبض'
    )
    trader = models.ForeignKey(
        'operations.Trader',
        on_delete=models.PROTECT,
        related_name='payments',
        verbose_name='التاجر'
    )
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        verbose_name='المبلغ المحصل (دينار)'
    )
    payment_method = models.CharField(
        max_length=20,
        choices=Method.choices,
        default=Method.CASH,
        verbose_name='طريقة القبض'
    )
    treasury_account = models.ForeignKey(
        TreasuryAccount,
        on_delete=models.PROTECT,
        related_name='payments_received',
        null=True,
        blank=True,
        verbose_name='الخزينة المودع بها'
    )
    check_obj = models.OneToOneField(
        Check,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='linked_payment',
        verbose_name='الشيك المرتبط (إن وجد)'
    )
    invoices = models.ManyToManyField(
        'invoicing.Invoice',
        blank=True,
        related_name='payments',
        verbose_name='الفواتير المسددة بها'
    )
    payment_date = models.DateField(
        default=timezone.now,
        verbose_name='تاريخ القبض الفعلي'
    )
    notes = models.TextField(blank=True, verbose_name='البيان / ملاحظات')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name='المحرر'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'سند قبض / دفعة'
        verbose_name_plural = 'سندات القبض والدفعات'
        ordering = ['-payment_date', '-created_at']

    def __str__(self):
        return f"{self.receipt_number} - {self.trader.name}: {self.amount} د.أ"

    @classmethod
    def generate_receipt_number(cls):
        year = timezone.now().year
        count = cls.objects.filter(receipt_number__startswith=f"RV-{year}-").count() + 1
        return f"RV-{year}-{count:04d}"


class InternalTransfer(models.Model):
    """
    التحويل الداخلي بين حسابات المنشأة:
    ليس إيراداً ولا مصروفاً، ولا يغير إجمالي أموال المنشأة.
    """
    transfer_number = models.CharField(
        max_length=60,
        unique=True,
        verbose_name='رقم التحويل'
    )
    from_account = models.ForeignKey(
        TreasuryAccount,
        on_delete=models.PROTECT,
        related_name='outgoing_transfers',
        verbose_name='من حساب / خزينة'
    )
    to_account = models.ForeignKey(
        TreasuryAccount,
        on_delete=models.PROTECT,
        related_name='incoming_transfers',
        verbose_name='إلى حساب / خزينة'
    )
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        verbose_name='المبلغ المحول (دينار)'
    )
    transfer_date = models.DateField(
        default=timezone.now,
        verbose_name='تاريخ التحويل'
    )
    notes = models.TextField(blank=True, verbose_name='البيان والمبرر')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name='المحرر'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'تحويل داخلي'
        verbose_name_plural = 'التحويلات الداخلية'

    def __str__(self):
        return f"{self.transfer_number}: {self.amount} د.أ من {self.from_account.name} إلى {self.to_account.name}"

    @classmethod
    def generate_transfer_number(cls):
        year = timezone.now().year
        count = cls.objects.filter(transfer_number__startswith=f"TR-{year}-").count() + 1
        return f"TR-{year}-{count:04d}"


class TraderLedgerEntry(models.Model):
    """
    كشف حساب التاجر التفصيلي:
    تتبع كل رقم حتى الفاتورة أو الدفعة الأصلية.
    مدين (فواتير/عجز) − دائن (تحصيلات/تسويات) = الرصيد التراكمي
    """
    class EntryType(models.TextChoices):
        INVOICE = 'INVOICE', 'فاتورة مشغل'
        PAYMENT = 'PAYMENT', 'سند قبض / تحصيل'
        CHECK_RETURNED = 'CHECK_RETURNED', 'شيك مرتجع (إعادة قيد الذمة)'
        CREDIT_NOTE = 'CREDIT_NOTE', 'إشعار دائن / تسوية'

    trader = models.ForeignKey(
        'operations.Trader',
        on_delete=models.CASCADE,
        related_name='ledger_entries',
        verbose_name='التاجر'
    )
    entry_type = models.CharField(
        max_length=25,
        choices=EntryType.choices,
        verbose_name='نوع القيد'
    )
    reference_number = models.CharField(
        max_length=100,
        verbose_name='رقم المرجع (الفاتورة / السند / الشيك)'
    )
    debit = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=Decimal('0.000'),
        verbose_name='مدين (مستحق على التاجر)'
    )
    credit = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=Decimal('0.000'),
        verbose_name='دائن (مسدد من التاجر)'
    )
    balance_after = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=Decimal('0.000'),
        verbose_name='الرصيد التراكمي بعد القيد (دينار)'
    )
    description = models.CharField(
        max_length=255,
        verbose_name='البيان'
    )
    timestamp = models.DateTimeField(
        default=timezone.now,
        verbose_name='التاريخ والوقت'
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name='المستخدم'
    )

    class Meta:
        verbose_name = 'قيد كشف حساب تاجر'
        verbose_name_plural = 'قيود كشف حساب التاجر'
        ordering = ['timestamp', 'id']

    def __str__(self):
        return f"{self.trader.name} - {self.description}: مدين {self.debit} / دائن {self.credit}"
