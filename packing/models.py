from django.db import models
from django.conf import settings
from django.utils import timezone
from decimal import Decimal, ROUND_HALF_UP


def _as_local_date(dt):
    """
    تحويل تاريخ/وقت إلى تاريخ محلي (توقيت المشغل Asia/Amman) لاحتساب أيام التبريد.
    """
    if dt is None:
        return None
    if timezone.is_aware(dt):
        dt = timezone.localtime(dt)
    return dt.date()


def cooling_days_between(start, end):
    """
    عدد أيام التبريد = تاريخ الإخراج − تاريخ الإدخال (بأيام التقويم).
    إن كان الإدخال والإخراج في اليوم نفسه فعدد الأيام المحتسبة = 0.
    """
    if not start or not end:
        return 0
    return max(0, (_as_local_date(end) - _as_local_date(start)).days)


class PackingSession(models.Model):
    """
    جلسة / عملية التوضيب:
    - إخراج الطبالي من البراد
    - تحديد التاجر والبضاعة وطريقة التوضيب (مكينة / يدوي)
    - تسجيل الوزن الداخل والناتج بمقاسات والهالك والفرق غير المفسر
    - حساب الرندمان
    - الرقابة على حد السماح لفرق الوزن واعتماد المدير
    """
    class Status(models.TextChoices):
        DRAFT = 'DRAFT', 'قيد العمل'
        INCOMPLETE = 'INCOMPLETE', 'توضيب غير مكتمل (خام متبقٍ)'
        PENDING_APPROVAL = 'PENDING_APPROVAL', 'بانتظار اعتماد المدير (فرق وزن/تجاوز حد سماح)'
        APPROVED = 'APPROVED', 'معتمدة'
        COMPLETED = 'COMPLETED', 'مكتملة وموضبة'

    class Method(models.TextChoices):
        MACHINE = 'MACHINE', 'مكينة'
        MANUAL = 'MANUAL', 'يدوي (عاملات)'

    session_code = models.CharField(
        max_length=60,
        unique=True,
        verbose_name='رمز جلسة التوضيب'
    )
    trader = models.ForeignKey(
        'operations.Trader',
        on_delete=models.PROTECT,
        related_name='packing_sessions',
        verbose_name='التاجر'
    )
    crop = models.ForeignKey(
        'operations.Crop',
        on_delete=models.PROTECT,
        related_name='packing_sessions',
        verbose_name='نوع البضاعة'
    )
    packing_method = models.CharField(
        max_length=20,
        choices=Method.choices,
        default=Method.MACHINE,
        verbose_name='طريقة التوضيب'
    )
    workshop_name = models.CharField(
        max_length=150,
        blank=True,
        verbose_name='اسم الورشة أو خط التشغيل (مثل: ورشة أم السعيد / خط الماكينة 1)'
    )
    source_pallets = models.ManyToManyField(
        'operations.Pallet',
        related_name='packing_sessions',
        verbose_name='طبالي الاستلام المسحوبة من البراد'
    )
    input_weight_kg = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=Decimal('0.000'),
        verbose_name='الوزن الصافي الداخل للتوضيب (كغ)'
    )
    output_packed_kg = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=Decimal('0.000'),
        verbose_name='إجمالي الوزن الموضب الفعلي (كغ)'
    )
    waste_weight_kg = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=Decimal('0.000'),
        verbose_name='إجمالي الهالك المسجل والمصنف (كغ)'
    )
    unexplained_diff_kg = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=Decimal('0.000'),
        verbose_name='الفرق غير المفسر في الوزن (كغ)'
    )
    randeman_pct = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal('0.00'),
        verbose_name='نسبة الرندمان % (الموضب ÷ الداخل)'
    )
    status = models.CharField(
        max_length=30,
        choices=Status.choices,
        default=Status.DRAFT,
        verbose_name='حالة التوضيب'
    )
    requires_manager_approval = models.BooleanField(
        default=False,
        verbose_name='تتطلب اعتماد المدير'
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='approved_packing_sessions',
        verbose_name='المدير المعتمد'
    )
    approved_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='تاريخ اعتماد المدير'
    )
    approval_notes = models.TextField(
        blank=True,
        verbose_name='ملاحظات الاعتماد والرقابة'
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name='المحاسب المسؤول'
    )
    started_at = models.DateTimeField(
        default=timezone.now,
        verbose_name='تاريخ ووقت بدء التوضيب وإخراج الطبالي'
    )
    completed_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='تاريخ اكتمال التوضيب'
    )
    notes = models.TextField(blank=True, verbose_name='ملاحظات')

    # ---- تأكيد المحاسب عند إنهاء العملية: هل توضّبت كل الكميات الخام أم بقي خام؟ ----
    all_packed_confirmed = models.BooleanField(
        default=False,
        verbose_name='المحاسب أكّد توضيب جميع الكميات الخام المستخدمة'
    )
    all_packed_confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='packing_sessions_all_packed_confirmed',
        verbose_name='المحاسب الذي أكّد التوضيب الكامل'
    )
    all_packed_confirmed_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='وقت تأكيد التوضيب الكامل'
    )
    leftover_note = models.TextField(
        blank=True,
        verbose_name='ملاحظات الكميات الخام المتبقية'
    )

    class Meta:
        verbose_name = 'عملية توضيب'
        verbose_name_plural = 'عمليات التوضيب'
        ordering = ['-started_at']

    # ---------------- خصائص حالة الإغلاق والكميات المتبقية ----------------

    @property
    def is_open(self):
        """هل العملية ما زالت مفتوحة (قابلة لاستكمال التوضيب)؟"""
        return self.status in (self.Status.DRAFT, self.Status.INCOMPLETE)

    @property
    def is_closed(self):
        """هل أُغلقت العملية؟ (لا يُضاف إليها خام جديد بعد الإغلاق)"""
        return self.status in (
            self.Status.PENDING_APPROVAL,
            self.Status.APPROVED,
            self.Status.COMPLETED,
        )

    @property
    def leftover_box_count(self):
        """
        إجمالي الصناديق الخام المتبقية في الطبالي المرتبطة بهذه العملية (رصيدها الحالي).
        """
        return sum(p.remaining_box_count for p in self.source_pallets.all())

    @property
    def leftover_pallets(self):
        """الطبالي المرتبطة بالعملية التي بقي فيها خام غير موضب."""
        return [p for p in self.source_pallets.all() if p.remaining_box_count > 0]

    def delete(self, *args, **kwargs):
        """
        حذف عملية التوضيب يعكس أثرها أولاً:
        إرجاع كل الطبالي المصدر إلى البراد، حذف الطبالي الموضّبة الناتجة،
        وتحرير رصيد صناديق الفارغ المستهلك — ثم الحذف.
        """
        from django.db import transaction
        from .services import PackingService
        with transaction.atomic():
            PackingService.reverse_packing_session(
                self, user=getattr(self, '_deleted_by', None),
                reason=f'حذف جلسة التوضيب {self.session_code}',
            )
            return super().delete(*args, **kwargs)

    def __str__(self):
        return f"{self.session_code} - {self.trader.name} ({self.crop.name})"

    @classmethod
    def generate_session_code(cls):
        year = timezone.now().year
        count = cls.objects.filter(session_code__startswith=f"PACK-{year}-").count() + 1
        return f"PACK-{year}-{count:04d}"

    def calculate_metrics(self, tolerance_pct=None):
        """
        حساب المقاييس وفقاً للبند 3 من الجزء الثاني:
        - احسب الإنتاج والرندمان والهالك من الأوزان الفعلية
        - الفرق غير المفسر = الداخل - (الناتج + الهالك)
        - لا تعتمد عملية يتجاوز فرق وزنها حد السماح المعتمد في الإعدادات دون موافقة المدير
        """
        # مجموع النواتج الموضبة
        self.output_packed_kg = sum(o.net_weight_kg for o in self.outputs.all())
        # مجموع الهالك
        self.waste_weight_kg = sum(w.weight_kg for w in self.wastes.all())

        # فرق الوزن موقّع: الموجب نقص غير مفسر، والسالب يعني أن الناتج
        # والهالك تجاوزا وزن الداخل. إخفاء الفائض كصفر كان يسمح باعتماد
        # رندمان أكبر من 100% دون مراجعة.
        diff = self.input_weight_kg - (self.output_packed_kg + self.waste_weight_kg)
        self.unexplained_diff_kg = diff.quantize(Decimal('0.001'), rounding=ROUND_HALF_UP)

        # الرندمان
        if self.input_weight_kg > 0:
            self.randeman_pct = ((self.output_packed_kg / self.input_weight_kg) * Decimal('100.00')).quantize(Decimal('0.01'))
        else:
            self.randeman_pct = Decimal('0.00')

        # فحص حد السماح لفرق الوزن
        self.requires_manager_approval = False
        diff_pct = Decimal('0.00')
        if self.input_weight_kg > 0:
            diff_pct = (abs(self.unexplained_diff_kg) / self.input_weight_kg) * Decimal('100.00')
        elif self.unexplained_diff_kg != 0:
            diff_pct = Decimal('100.00')

        if tolerance_pct is None:
            # إذا لم يُحدد حد السماح، يمنع الاعتماد التلقائي ويتطلب اعتماد المدير
            if self.unexplained_diff_kg != 0:
                self.requires_manager_approval = True
        else:
            if diff_pct > tolerance_pct:
                self.requires_manager_approval = True


class PackingOutput(models.Model):
    """
    النواتج بمقاسات مختلفة (P1 small، large، M، XL…) بأوزانها
    """
    session = models.ForeignKey(
        PackingSession,
        on_delete=models.CASCADE,
        related_name='outputs',
        verbose_name='عملية التوضيب'
    )
    size_name = models.CharField(
        max_length=100,
        verbose_name='المقاس / التصنيف (P1 small, large, M, XL...)'
    )
    box_type = models.ForeignKey(
        'operations.BoxType',
        on_delete=models.PROTECT,
        verbose_name='نوع الصندوق المستخدم'
    )
    box_count = models.PositiveIntegerField(
        default=0,
        verbose_name='عدد الصناديق الموضبة'
    )
    net_weight_kg = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=Decimal('0.000'),
        verbose_name='الوزن الصافي الفعلي (كغ)'
    )
    pallet_count = models.PositiveIntegerField(
        default=1,
        verbose_name='عدد الطبالي الموضبة الناتجة'
    )

    class Meta:
        verbose_name = 'ناتج توضيب'
        verbose_name_plural = 'نواتج التوضيب'

    def __str__(self):
        return f"{self.session.session_code}: {self.size_name} ({self.net_weight_kg} كغ)"


class PackingOutputPackagingItem(models.Model):
    """
    أصناف التغليف العادية المستخدمة داخل ناتج توضيب واحد:

    - يمكن إضافة **أكثر من صنف** لنفس الناتج، وكل صنف من **مجموعة تغليف مختلفة**
      (مثال: صندوق + ورق تغليف + ستيكر + شبك حماية + فواصل).
    - السعر يُجمَّد عند الحفظ (`unit_price_snapshot`) فلا تتغير الفاتورة لو غُيّر سعر
      الصنف لاحقاً — القاعدة 22 في المواصفات (تجميد الأسعار في الحركات المالية).
    - القيمة (`total_price`) تُخصم من التاجر عبر `PackingSessionPackagingCharge` ثم
      تُرحَّل إلى الفاتورة كبند «خدمات إضافية».
    """
    output = models.ForeignKey(
        PackingOutput,
        on_delete=models.CASCADE,
        related_name='packaging_items',
        verbose_name='ناتج التوضيب'
    )
    packaging_item = models.ForeignKey(
        'operations.BoxType',
        on_delete=models.PROTECT,
        related_name='packing_output_usages',
        verbose_name='صنف التغليف'
    )
    packaging_group_name = models.CharField(
        max_length=100,
        blank=True,
        verbose_name='مجموعة التغليف (لقطة وقت الاستخدام)'
    )
    quantity = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=Decimal('1.000'),
        verbose_name='الكمية / العدد المستخدم'
    )
    unit_price_snapshot = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        default=Decimal('0.000'),
        verbose_name='سعر الوحدة المجمد على التاجر (دينار)'
    )
    total_price = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=Decimal('0.000'),
        verbose_name='إجمالي الخصم على التاجر (دينار)'
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='وقت التسجيل')

    class Meta:
        verbose_name = 'صنف تغليف داخل ناتج توضيب'
        verbose_name_plural = 'أصناف التغليف داخل نواتج التوضيب'
        ordering = ['id']

    def __str__(self):
        return f"{self.output.session.session_code}: {self.packaging_item.name} × {self.quantity}"

    def save(self, *args, **kwargs):
        three_places = Decimal('0.001')
        self.total_price = (
            Decimal(self.quantity or 0) * Decimal(self.unit_price_snapshot or 0)
        ).quantize(three_places, rounding=ROUND_HALF_UP)
        super().save(*args, **kwargs)


class PackingSessionPackagingCharge(models.Model):
    """
    قيد خصم أصناف التغليف على التاجر لجلسة توضيب واحدة:

    يجمع قيمة كل أصناف التغليف العادية المستهلكة في الجلسة (من كل النواتج)
    في صف واحد ثابت، لتُرحَّل إلى فاتورة التاجر كبند خدمات إضافية **مرة واحدة**،
    وتمنع الترحيل المزدوج لو أُعيدت مزامنة الفاتورة (`is_posted`).
    """
    session = models.OneToOneField(
        PackingSession,
        on_delete=models.CASCADE,
        related_name='packaging_charge',
        verbose_name='جلسة التوضيب'
    )
    trader = models.ForeignKey(
        'operations.Trader',
        on_delete=models.PROTECT,
        related_name='packaging_charges',
        verbose_name='التاجر المحمّل عليه الخصم'
    )
    total_amount = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=Decimal('0.000'),
        verbose_name='إجمالي خصم أصناف التغليف (دينار)'
    )
    is_posted = models.BooleanField(
        default=False,
        verbose_name='رُحّل إلى الفاتورة'
    )
    posted_invoice = models.ForeignKey(
        'invoicing.Invoice',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='packaging_charges',
        verbose_name='الفاتورة التي رُحّل إليها'
    )
    posted_at = models.DateTimeField(null=True, blank=True, verbose_name='وقت الترحيل للفاتورة')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='وقت التسجيل')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='آخر تحديث')

    class Meta:
        verbose_name = 'خصم أصناف تغليف على التاجر'
        verbose_name_plural = 'خصومات أصناف التغليف على التجار'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.session.session_code}: خصم تغليف {self.total_amount} دينار على {self.trader.name}"


class PackingWaste(models.Model):
    """
    الهالك المسجل والمصنف وفق سياسة معتمدة
    """
    session = models.ForeignKey(
        PackingSession,
        on_delete=models.CASCADE,
        related_name='wastes',
        verbose_name='عملية التوضيب'
    )
    waste_type = models.CharField(
        max_length=100,
        verbose_name='تصنيف الهالك (عفن/تالف، ضربات ميكانيكية، فرز غير مطابق...)'
    )
    weight_kg = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=Decimal('0.000'),
        verbose_name='وزن الهالك (كغ)'
    )
    notes = models.CharField(max_length=255, blank=True, verbose_name='ملاحظات')

    class Meta:
        verbose_name = 'سجل هالك'
        verbose_name_plural = 'سجلات الهالك'

    def __str__(self):
        return f"{self.session.session_code}: {self.waste_type} ({self.weight_kg} كغ)"


class PackingRawConsumption(models.Model):
    """
    سجل سحب الكميات الخام من مصدرها الفعلي (دفتر الاستهلاك):

    المبدأ الأساسي بعد تصحيح منطق التوضيب:
    **عدد طبالي الخام ≠ عدد طبالي المنتج الموضب**، والأساس في الحساب هو
    الكمية الفعلية (صناديق المزرعة المسحوبة) من مصدرها الحقيقي.

    كل سطر هنا = «سُحبت هذه الكمية من هذه الطبلية الخام هذه، أثناء هذه العملية».
    ويمكن أن تُسحَب كميات من أكثر من طبلية خام لتغذية الطبلية الموضبة نفسها،
    بما فيها طبلية موجودة في البراد لم تبدأ في هذه العملية.

    الحقول:
    - `box_count`: عدد صناديق المزرعة المسحوبة من الطبلية المصدر.
    - `estimated_weight_kg`: نصيب الطبلية المصدر من وزنها التقديري (حساب وزن الداخل).
    - `net_weight_kg`: صافي وزن البضاعة المنسوبة إلى هذه الكمية فعلياً.
    - `remaining_box_count`: رصيد الطبلية المصدر المتبقي بعد هذا السحب.
    """
    session = models.ForeignKey(
        PackingSession,
        on_delete=models.CASCADE,
        related_name='raw_consumptions',
        verbose_name='عملية التوضيب'
    )
    packed_pallet = models.ForeignKey(
        'PackedPallet',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='raw_consumptions',
        verbose_name='الطبلية الموضبة التي غُذّيت منها'
    )
    source_pallet = models.ForeignKey(
        'operations.Pallet',
        on_delete=models.PROTECT,
        related_name='packing_consumptions',
        verbose_name='الطبلية الخام المصدر'
    )
    trader = models.ForeignKey(
        'operations.Trader',
        on_delete=models.PROTECT,
        related_name='packing_consumptions',
        verbose_name='التاجر'
    )
    crop = models.ForeignKey(
        'operations.Crop',
        on_delete=models.PROTECT,
        related_name='packing_consumptions',
        verbose_name='نوع البضاعة'
    )
    box_count = models.PositiveIntegerField(
        default=0,
        verbose_name='عدد صناديق المزرعة المسحوبة'
    )
    estimated_weight_kg = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=Decimal('0.000'),
        verbose_name='نصيب الطبلية من الوزن التقديري الداخل (كغ)'
    )
    net_weight_kg = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=Decimal('0.000'),
        verbose_name='صافي وزن البضاعة المسحوبة (كغ)'
    )
    remaining_box_count = models.PositiveIntegerField(
        default=0,
        verbose_name='رصيد الطبلية المصدر بعد السحب'
    )
    cold_store = models.ForeignKey(
        'operations.ColdStore',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='packing_consumptions',
        verbose_name='البراد وقت السحب'
    )
    shelf = models.ForeignKey(
        'operations.Shelf',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='packing_consumptions',
        verbose_name='السيخ وقت السحب'
    )
    movement = models.ForeignKey(
        'operations.PalletMovementLog',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='packing_consumptions',
        verbose_name='حركة الصرف من التخزين'
    )
    notes = models.CharField(max_length=255, blank=True, verbose_name='ملاحظات')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='packing_raw_consumptions',
        verbose_name='المستخدم الذي سجّل السحب'
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='وقت السحب')

    class Meta:
        verbose_name = 'سحب كمية خام'
        verbose_name_plural = 'سحوبات الكميات الخام'
        ordering = ['id']

    def __str__(self):
        return (
            f"{self.source_pallet.pallet_code} → {self.box_count} صندوق "
            f"({self.session.session_code})"
        )


class PackingLeftoverSettlement(models.Model):
    """
    تسوية الكميات الخام المتبقية قبل إغلاق عملية التوضيب:

    لا يجوز إغلاق العملية ما دام في طبالٍ خام متبقٍ لم يُحدَّد مصيره.
    ويُسجَّل هنا مصير الباقي بوضوح:

    - `RETURN_TO_STORE`: إرجاع المتبقي إلى البراد (يبقى مخزوناً قابلاً للاستخدام).
    - `WRITE_OFF`: إتلاف/شطب المتبقي (تُوثَّق الحركة ولا تُنشأ قيود مالية تلقائية).

    ملاحظة محاسبية: هذا السجل توثيقي فقط ولا يُنشئ إيرادات أو مصروفات.
    """
    class Resolution(models.TextChoices):
        RETURN_TO_STORE = 'RETURN_TO_STORE', 'إرجاع المتبقي إلى البراد (يبقى في المخزون)'
        WRITE_OFF = 'WRITE_OFF', 'إتلاف / شطب المتبقي'

    session = models.ForeignKey(
        PackingSession,
        on_delete=models.CASCADE,
        related_name='leftover_settlements',
        verbose_name='عملية التوضيب'
    )
    pallet = models.ForeignKey(
        'operations.Pallet',
        on_delete=models.PROTECT,
        related_name='packing_leftover_settlements',
        verbose_name='الطبلية الخام'
    )
    box_count = models.PositiveIntegerField(
        default=0,
        verbose_name='عدد الصناديق التي حُدّد مصيرها'
    )
    resolution = models.CharField(
        max_length=30,
        choices=Resolution.choices,
        verbose_name='مصير الكمية'
    )
    notes = models.TextField(blank=True, verbose_name='الملاحظة أو سبب الإتلاف / الشطب')
    settled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='packing_leftover_settlements',
        verbose_name='من سجّل الإجراء'
    )
    settled_at = models.DateTimeField(auto_now_add=True, verbose_name='وقت تسجيل الإجراء')

    class Meta:
        verbose_name = 'تحديد مصير كمية خام متبقية'
        verbose_name_plural = 'تحديد مصير الكميات الخام المتبقية'
        ordering = ['-settled_at', '-id']

    def __str__(self):
        return (
            f"{self.pallet.pallet_code}: {self.get_resolution_display()} "
            f"({self.box_count} صندوق)"
        )


class PackedPallet(models.Model):
    """
    الطبلية الموضبة:
    - ترقيم مميز يتبع الطبلية الأصلية والمقاس: 2026-0143-P03-XL
    - QR مميز لكل طبلية موضبة منفصل عن QR الاستلام ومربوط به (يعرض الطبلية الأصلية والتاجر والسيارة)
    - الحالة: موضبة -> في تبريد المشغل (اختياري) -> مسلّمة
    - يمكن إعادة الطبلية الموضبة إلى برادات المشغل لخدمة التبريد اليومية
    """
    class Status(models.TextChoices):
        PACKED = 'PACKED', 'موضبة وجاهزة'
        IN_COOLING = 'IN_COOLING', 'في تبريد المشغل'
        DELIVERED = 'DELIVERED', 'مسلّمة'

    packed_pallet_code = models.CharField(
        max_length=80,
        unique=True,
        verbose_name='رمز الطبلية الموضبة'
    )
    original_pallet = models.ForeignKey(
        'operations.Pallet',
        on_delete=models.PROTECT,
        related_name='packed_pallets',
        verbose_name='طبلية الاستلام الأصلية'
    )
    session = models.ForeignKey(
        PackingSession,
        on_delete=models.CASCADE,
        related_name='packed_pallets',
        verbose_name='عملية التوضيب'
    )
    trader = models.ForeignKey(
        'operations.Trader',
        on_delete=models.PROTECT,
        related_name='packed_pallets',
        verbose_name='التاجر'
    )
    crop = models.ForeignKey(
        'operations.Crop',
        on_delete=models.PROTECT,
        verbose_name='نوع البضاعة'
    )
    size_name = models.CharField(
        max_length=100,
        verbose_name='المقاس (P1, Large, XL...)'
    )
    box_type = models.ForeignKey(
        'operations.BoxType',
        on_delete=models.PROTECT,
        verbose_name='نوع الصناديق'
    )
    box_count = models.PositiveIntegerField(
        default=0,
        verbose_name='عدد الصناديق'
    )
    net_weight_kg = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=Decimal('0.000'),
        verbose_name='الوزن الصافي الموضب (كغ)'
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PACKED,
        verbose_name='حالة الطبلية الموضبة'
    )
    # موقع التبريد الحالي (عند إعادتها إلى برادات المشغل لخدمة التبريد اليومية)
    cold_store = models.ForeignKey(
        'operations.ColdStore',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='packed_pallets',
        verbose_name='البراد الحالي'
    )
    shelf = models.ForeignKey(
        'operations.Shelf',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='packed_pallets',
        verbose_name='السيخ الحالي'
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='وقت الانتهاء من التوضيب')

    class Meta:
        verbose_name = 'طبلية موضبة'
        verbose_name_plural = 'الطبالي الموضبة'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.packed_pallet_code} ({self.trader.name} - {self.size_name})"

    @property
    def source_breakdown(self):
        """
        توزيع الكميات الخام التي غذّت هذه الطبلية على الطبالي المصدر.
        قد تكون الطبلية الموضبة واحدة فقط (للتوافق مع الشاشات القديمة)،
        لكن الحقيقية هي أنها قد تُغذّى من أكثر من طبلية خام.
        """
        return list(self.raw_consumptions.select_related('source_pallet').all())

    @property
    def source_pallet_codes(self):
        """رموز الطبالي الخام المصدر (مفصولة بفواصل)."""
        return '، '.join(c.source_pallet.pallet_code for c in self.source_breakdown)

    # ---------- خدمة التبريد اليومية للطبالي الموضبة ----------
    @property
    def open_cooling_log(self):
        """فترة التبريد المفتوحة حالياً (إن كانت الطبلية داخل البراد الآن)."""
        return self.cooling_logs.filter(exited_at__isnull=True).order_by('-entered_at').first()

    @property
    def cooling_in_at(self):
        log = self.open_cooling_log
        return log.entered_at if log else None

    @property
    def cooling_days_total(self):
        """إجمالي أيام التبريد: مجموع الفترات المغلقة + الفترة المفتوحة حتى الآن."""
        total = 0
        for log in self.cooling_logs.all():
            total += log.days_counted if log.exited_at else log.live_days
        return total

    @property
    def cooling_fee(self):
        """أجرة التبريد = إجمالي الأيام × الأجرة اليومية المعتمدة في الإعدادات."""
        from core.models import SystemSetting
        rate = SystemSetting.get_settings().packed_cooling_daily_rate or Decimal('0.000')
        return (Decimal(self.cooling_days_total) * rate).quantize(Decimal('0.001'), rounding=ROUND_HALF_UP)

    @property
    def location_display(self):
        """وصف موقع الطبلية الحالي (براد – سيخ) أو مكانها إن لم تكن في التبريد."""
        if self.status == self.Status.IN_COOLING and self.cold_store:
            if self.shelf:
                return f"{self.cold_store.name} – السيخ {self.shelf.shelf_number}"
            return self.cold_store.name
        return self.get_status_display()

    def get_qr_data(self):
        """
        بيانات QR الموضب:
        يعرض الطبلية الأصلية، التاجر، السيارة الأصلية، المقاس، والوزن الموضب،
        ومعها توزيع الكميات الخام على الطبالي المصدر (قد تكون أكثر من طبلية).
        """
        truck = self.original_pallet.truck_receipt
        breakdown = [
            f"{c.source_pallet.pallet_code}: {c.box_count} صندوق ({c.net_weight_kg} كغ)"
            for c in self.source_breakdown
        ]
        return {
            "type": "PACKED_PALLET",
            "packed_code": self.packed_pallet_code,
            "original_pallet": self.original_pallet.pallet_code,
            "original_sscc": self.original_pallet.sscc_code,
            "source_breakdown": ' | '.join(breakdown) if breakdown else "غير مسجّل",
            "lot_number": truck.lot_number,
            "truck_plate": truck.truck_plate or "غير مسجل",
            "trader": self.trader.name,
            "crop": self.crop.name,
            "size": self.size_name,
            "box_type": self.box_type.name,
            "box_count": self.box_count,
            "net_weight_kg": str(self.net_weight_kg),
            "status": self.get_status_display(),
            "packed_date": self.created_at.strftime('%Y-%m-%d %H:%M'),
        }


class PackedPalletCoolingLog(models.Model):
    """
    فترة تبريد لطبلية موضبة داخل برادات المشغل (خدمة التبريد اليومية):
    كل إدخال يفتح فترة، وكل إخراج أو نقل بين الأسياخ يغلقها ويحتسب أيامها.
    """
    class MovementType(models.TextChoices):
        IN = 'IN', 'إدخال إلى البراد'
        OUT = 'OUT', 'إخراج من البراد'
        MOVE = 'MOVE', 'نقل بين الأسياخ'

    packed_pallet = models.ForeignKey(
        PackedPallet,
        on_delete=models.CASCADE,
        related_name='cooling_logs',
        verbose_name='الطبلية الموضبة'
    )
    movement_type = models.CharField(
        max_length=10,
        choices=MovementType.choices,
        default=MovementType.IN,
        verbose_name='نوع الحركة'
    )
    cold_store = models.ForeignKey(
        'operations.ColdStore',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='packed_cooling_logs',
        verbose_name='البراد'
    )
    shelf = models.ForeignKey(
        'operations.Shelf',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='packed_cooling_logs',
        verbose_name='السيخ'
    )
    entered_at = models.DateTimeField(verbose_name='وقت الإدخال')
    exited_at = models.DateTimeField(null=True, blank=True, verbose_name='وقت الإخراج (فارغ = لا يزال بالداخل)')
    days_counted = models.PositiveIntegerField(default=0, verbose_name='عدد الأيام المحتسبة')
    moved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name='المستخدم المنفّذ'
    )
    notes = models.CharField(max_length=255, blank=True, verbose_name='ملاحظات')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='وقت التسجيل')

    class Meta:
        verbose_name = 'فترة تبريد طبلية موضبة'
        verbose_name_plural = 'فترات تبريد الطبالي الموضبة'
        ordering = ['-entered_at']

    def __str__(self):
        location = self.cold_store.name if self.cold_store else '—'
        state = 'مفتوحة' if self.exited_at is None else f'{self.days_counted} يوم'
        return f"{self.packed_pallet.packed_pallet_code} @ {location} ({state})"

    @property
    def is_open(self):
        return self.exited_at is None

    @property
    def live_days(self):
        """أيام الفترة المفتوحة حتى هذه اللحظة."""
        return cooling_days_between(self.entered_at, self.exited_at or timezone.now())

    def close(self, when=None):
        """إغلاق الفترة واحتساب أيامها."""
        self.exited_at = when or timezone.now()
        self.days_counted = cooling_days_between(self.entered_at, self.exited_at)
        self.save(update_fields=['exited_at', 'days_counted'])
        return self.days_counted


class DeliveryNote(models.Model):
    """
    بيان تسليم عند خروج الموضب للتاجر:
    يوثق رقم البيان، التاجر، السائق، رقم السيارة، والمستلم، وقائمة الطبالي الموضبة المسلمة
    """
    delivery_number = models.CharField(
        max_length=60,
        unique=True,
        verbose_name='رقم بيان التسليم'
    )
    trader = models.ForeignKey(
        'operations.Trader',
        on_delete=models.PROTECT,
        related_name='delivery_notes',
        verbose_name='التاجر المستلم'
    )
    pallets = models.ManyToManyField(
        PackedPallet,
        related_name='delivery_notes',
        verbose_name='الطبالي الموضبة المسلّمة'
    )
    driver_name = models.CharField(max_length=150, blank=True, verbose_name='اسم السائق الناقل')
    truck_plate = models.CharField(max_length=50, blank=True, verbose_name='رقم سيارة الشحن')
    receiver_name = models.CharField(max_length=150, blank=True, verbose_name='اسم المستلم / المعتمد')
    total_pallets = models.PositiveIntegerField(default=0, verbose_name='إجمالي عدد الطبالي')
    total_boxes = models.PositiveIntegerField(default=0, verbose_name='إجمالي عدد الصناديق')
    total_net_weight_kg = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=Decimal('0.000'),
        verbose_name='إجمالي الوزن الصافي المسلّم (كغ)'
    )
    delivery_date = models.DateField(default=timezone.now, verbose_name='تاريخ التسليم')
    notes = models.TextField(blank=True, verbose_name='ملاحظات وبيان الخروج')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name='المحرر'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'بيان تسليم'
        verbose_name_plural = 'بيانات التسليم'
        ordering = ['-delivery_date', '-created_at']

    def __str__(self):
        return f"{self.delivery_number} - {self.trader.name}"

    @classmethod
    def generate_delivery_number(cls):
        year = timezone.now().year
        count = cls.objects.filter(delivery_number__startswith=f"DEL-{year}-").count() + 1
        return f"DEL-{year}-{count:04d}"
