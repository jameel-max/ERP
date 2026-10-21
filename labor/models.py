from django.db import models
from django.conf import settings
from django.utils import timezone
from decimal import Decimal, ROUND_HALF_UP


class Workshop(models.Model):
    """
    الورش (مثل: ورشة أم السعيد)، ولكل ورشة قسم وصفحة
    """
    name = models.CharField(max_length=150, unique=True, verbose_name='اسم الورشة')
    supervisor_name = models.CharField(max_length=150, blank=True, verbose_name='المشرف / المسؤول')
    phone = models.CharField(max_length=50, blank=True, verbose_name='رقم الهاتف')
    default_hourly_rate = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        default=Decimal('1.000'),
        verbose_name='سعر الساعة الافتراضي (دينار)'
    )
    default_transport_daily = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        default=Decimal('1.000'),
        verbose_name='بدل المواصلات اليومي الافتراضي للعامل (دينار)'
    )
    is_active = models.BooleanField(default=True, verbose_name='نشطة')
    notes = models.TextField(blank=True, verbose_name='ملاحظات الورشة')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'ورشة عمال'
        verbose_name_plural = 'الورش والعمال'
        ordering = ['name']

    def __str__(self):
        return self.name


class DailyLaborLog(models.Model):
    """
    يوم عمل الورشة:
    تكلفة الورشة = عدد العمال × ساعات العمل × سعر الساعة + عدد العمال × بدل المواصلات اليومي
    مثال: 70 عاملاً × 10 ساعات → 70×10 + 70×1 = 770 دينار
    تكلفة داخلية منفصلة عن فاتورة التاجر وتمنع التكرار عند إعادة الاعتماد
    """
    workshop = models.ForeignKey(
        Workshop,
        on_delete=models.CASCADE,
        related_name='daily_logs',
        verbose_name='الورشة'
    )
    work_date = models.DateField(
        default=timezone.now,
        verbose_name='تاريخ يوم العمل'
    )
    worker_count = models.PositiveIntegerField(
        verbose_name='عدد العمال'
    )
    work_hours = models.DecimalField(
        max_digits=4,
        decimal_places=2,
        default=Decimal('8.00'),
        verbose_name='ساعات العمل الفعلية'
    )
    hourly_rate = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        default=Decimal('1.000'),
        verbose_name='سعر الساعة (دينار)'
    )
    transport_allowance_daily = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        default=Decimal('1.000'),
        verbose_name='بدل المواصلات اليومي للعامل (دينار)'
    )
    hours_cost = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=Decimal('0.000'),
        verbose_name='إجمالي تكلفة الساعات (دينار)'
    )
    transport_cost = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=Decimal('0.000'),
        verbose_name='إجمالي بدلات المواصلات (دينار)'
    )
    total_cost = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=Decimal('0.000'),
        verbose_name='الأجرة الإجمالية ليوم العمل (دينار)'
    )
    is_approved = models.BooleanField(
        default=True,
        verbose_name='معتمد'
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='approved_labor_logs',
        verbose_name='المعتمد'
    )
    notes = models.TextField(blank=True, verbose_name='ملاحظات العمل والمهام')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name='المحرر'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'سجل يوم عمل ورشة'
        verbose_name_plural = 'سجلات أيام العمل للورش'
        unique_together = ('workshop', 'work_date')
        ordering = ['-work_date']

    def __str__(self):
        return f"{self.workshop.name} - {self.work_date}: {self.total_cost} دينار"

    def calculate_cost(self):
        """
        تكلفة الورشة = عدد العمال × ساعات العمل × سعر الساعة + عدد العمال × بدل المواصلات
        """
        three_places = Decimal('0.001')
        workers = Decimal(self.worker_count)
        self.hours_cost = (workers * self.work_hours * self.hourly_rate).quantize(three_places, rounding=ROUND_HALF_UP)
        self.transport_cost = (workers * self.transport_allowance_daily).quantize(three_places, rounding=ROUND_HALF_UP)
        self.total_cost = (self.hours_cost + self.transport_cost).quantize(three_places, rounding=ROUND_HALF_UP)


class Employee(models.Model):
    """
    الموظفون برواتب شهرية ثابتة — قسم مستقل تماماً عن «الورش والعمالة».

    ملاحظة مهمة (القاعدة 4): رواتب الموظفين **لا علاقة لها بالورش** ولا تدخل في
    تكلفة الكيلوغرام الموضب ولا في فاتورة التاجر. هي مصروف إداري/تشغيلي مستقل
    يُدفع شهرياً من الخزينة ويُسجَّل في جدول مستقل.
    """
    name = models.CharField(max_length=150, verbose_name='اسم الموظف')
    job_title = models.CharField(max_length=150, blank=True, verbose_name='المسمى الوظيفي')
    phone = models.CharField(max_length=50, blank=True, verbose_name='رقم الهاتف')
    monthly_salary = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=Decimal('0.000'),
        verbose_name='الراتب الشهري (دينار)'
    )
    hire_date = models.DateField(
        null=True,
        blank=True,
        verbose_name='تاريخ التعيين'
    )
    is_active = models.BooleanField(default=True, verbose_name='على رأس عمله')
    notes = models.TextField(blank=True, verbose_name='ملاحظات')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'موظف براتب شهري'
        verbose_name_plural = 'الموظفون (رواتب شهرية)'
        ordering = ['name']

    def __str__(self):
        return f"{self.name} — {self.monthly_salary} دينار/شهر"

    @property
    def annual_salary(self):
        return (self.monthly_salary * 12).quantize(Decimal('0.001'), rounding=ROUND_HALF_UP)

    @property
    def paid_total(self):
        return self.salary_payments.aggregate(t=models.Sum('amount'))['t'] or Decimal('0.000')

    def deductions_for(self, year, month):
        """مجموع خصومات موظف لشهر محدد (كغ/دينار) — يُستخدم لحساب صافي الراتب."""
        from decimal import Decimal as _D
        return self.deductions.filter(year=year, month=month).aggregate(
            t=models.Sum('amount')
        )['t'] or _D('0.000')

    def net_salary_for(self, year, month):
        """
        الصافي المستحق لشهر محدد = الراتب الشهري الأساسي − مجموع خصومات الشهر.
        لا يكون سالباً أبداً (الخصومات الزائدة عن الراتب تُصفَّر).
        """
        net = self.monthly_salary - self.deductions_for(year, month)
        return net if net > 0 else Decimal('0.000')


class EmployeeDeduction(models.Model):
    """
    خصم على راتب موظف: سجل مستقل (غياب، سلفة، تأخير، أخرى) بتاريخ ومبلغ وسبب.

    القاعدة: لا يعدّل هذا السجل الراتب الأساسي المحفوظ في بطاقة الموظف، بل يُجمَّع
    عند صرف الراتب لتحديد **الصافي** الذي يُخصم من الخزينة.
    """
    class Type(models.TextChoices):
        ABSENCE = 'ABSENCE', 'غياب'
        ADVANCE = 'ADVANCE', 'سلفة'
        LATENESS = 'LATENESS', 'تأخير'
        PENALTY = 'PENALTY', 'جزاء / مخالفة'
        OTHER = 'OTHER', 'أخرى'

    employee = models.ForeignKey(
        Employee,
        on_delete=models.CASCADE,
        related_name='deductions',
        verbose_name='الموظف'
    )
    deduction_type = models.CharField(
        max_length=20,
        choices=Type.choices,
        default=Type.ABSENCE,
        verbose_name='نوع الخصم'
    )
    year = models.PositiveIntegerField(verbose_name='السنة')
    month = models.PositiveSmallIntegerField(verbose_name='الشهر (1-12)')
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=Decimal('0.000'),
        verbose_name='مبلغ الخصم (دينار)'
    )
    deduction_date = models.DateField(
        null=True,
        blank=True,
        verbose_name='تاريخ الخصم'
    )
    reason = models.CharField(
        max_length=255,
        blank=True,
        verbose_name='السبب / البيان'
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_employee_deductions',
        verbose_name='المحرر'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'خصم على راتب موظف'
        verbose_name_plural = 'خصومات رواتب الموظفين'
        ordering = ['-year', '-month', '-created_at']

    def __str__(self):
        return f"{self.employee.name} — {self.get_deduction_type_display()} {self.amount} دينار ({self.period_label})"

    @property
    def period_label(self):
        return f'{self.month:02d}/{self.year}'


class EmployeeSalaryPayment(models.Model):
    """
    صرف راتب شهري لموظف: قيد مستقل لكل شهر (منع الازدواج عبر قيد فريد لكل موظف/شهر).
    يمكن قيده في الخزينة عند الصرف الفعلي (حالة «مدفوع»)، أو تركه «مستحقاً» بلا قيد خزينة.

    عند وجود خصومات على شهر الموظف: `gross_amount` هو الراتب الأساسي، و`deductions_total`
    مجموع الخصومات، و`amount` هو **الصافي** المصروف فعلاً (gross - deductions).
    تبقى القاعدة 4 سارية: رواتب الموظفين مستقلة تماماً عن الورش وكلفة الكيلو الموضب.
    """
    class Status(models.TextChoices):
        ACCRUED = 'ACCRUED', 'مستحق (غير مدفوع)'
        PAID = 'PAID', 'مدفوع فعلاً'

    employee = models.ForeignKey(
        Employee,
        on_delete=models.CASCADE,
        related_name='salary_payments',
        verbose_name='الموظف'
    )
    year = models.PositiveIntegerField(verbose_name='السنة')
    month = models.PositiveSmallIntegerField(verbose_name='الشهر (1-12)')
    gross_amount = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=Decimal('0.000'),
        verbose_name='الراتب الأساسي قبل الخصم (دينار)'
    )
    deductions_total = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=Decimal('0.000'),
        verbose_name='مجموع الخصومات (دينار)'
    )
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=Decimal('0.000'),
        verbose_name='الصافي المصروف (دينار)'
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACCRUED,
        verbose_name='حالة الصرف'
    )
    payment_date = models.DateField(null=True, blank=True, verbose_name='تاريخ الصرف الفعلي')
    treasury_account = models.ForeignKey(
        'finance.TreasuryAccount',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='salary_payments',
        verbose_name='الخزينة / الحساب المدفوع منه'
    )
    notes = models.CharField(max_length=255, blank=True, verbose_name='ملاحظات')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name='المحرر'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'صرف راتب شهري'
        verbose_name_plural = 'صرف الرواتب الشهرية'
        unique_together = ('employee', 'year', 'month')
        ordering = ['-year', '-month', 'employee__name']

    def __str__(self):
        return f"{self.employee.name} — {self.month:02d}/{self.year}: {self.amount} دينار"

    @property
    def period_label(self):
        return f'{self.month:02d}/{self.year}'

