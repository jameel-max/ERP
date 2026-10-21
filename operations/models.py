from django.db import models
from django.db.models import Count
from django.utils import timezone
from django.conf import settings
from decimal import Decimal, ROUND_HALF_UP
import json


class Trader(models.Model):
    """
    التاجر (كلمة مزارع تعني تاجر في كل النظام)
    """
    code = models.CharField(max_length=20, unique=True, verbose_name='رمز التاجر')
    name = models.CharField(max_length=150, verbose_name='اسم التاجر')
    phone = models.CharField(max_length=50, blank=True, verbose_name='رقم الهاتف')
    national_id = models.CharField(max_length=50, blank=True, verbose_name='الرقم الوطني / الضريبي')
    notes = models.TextField(blank=True, verbose_name='ملاحظات')
    is_active = models.BooleanField(default=True, verbose_name='نشط')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='تاريخ الإضافة')

    class Meta:
        verbose_name = 'تاجر'
        verbose_name_plural = 'التجار'
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.code})"

    @classmethod
    def generate_next_code(cls):
        count = cls.objects.count() + 1
        return f"T{count:03d}"


class Crop(models.Model):
    """
    نوع البضاعة (نكتارين، مشمش، خوخ، دراق...)
    """
    name = models.CharField(max_length=100, unique=True, verbose_name='نوع البضاعة')
    code = models.CharField(max_length=50, blank=True, verbose_name='الرمز')
    notes = models.TextField(blank=True, verbose_name='ملاحظات')

    class Meta:
        verbose_name = 'نوع البضاعة'
        verbose_name_plural = 'أنواع البضاعة'
        ordering = ['name']

    def __str__(self):
        return self.name


class PackagingGroup(models.Model):
    """
    مجموعة موارد التغليف (مثل: الصناديق، ورق التغليف، الستيكرات).
    كل مجموعة مستقلة وتحتوي على الأصناف التابعة لها من نوع BoxType.
    """
    name = models.CharField(max_length=100, unique=True, verbose_name='اسم المجموعة')
    notes = models.TextField(blank=True, verbose_name='ملاحظات')
    is_active = models.BooleanField(default=True, verbose_name='نشطة')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='تاريخ الإضافة')

    class Meta:
        verbose_name = 'مجموعة تغليف'
        verbose_name_plural = 'مجموعات التغليف'
        ordering = ['name']

    def __str__(self):
        return self.name

    @property
    def items_count(self):
        return self.items.count()


class BoxType(models.Model):
    """
    أنواع الصناديق يضيفها المحاسب: الاسم، اللون/النوع، الوزن الفارغ، سعر الخصم على التاجر
    """
    group = models.ForeignKey(
        PackagingGroup,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='items',
        verbose_name='مجموعة التغليف'
    )
    name = models.CharField(max_length=100, verbose_name='اسم الصندوق')
    size = models.CharField(
        max_length=100,
        blank=True,
        verbose_name='المقاس'
    )
    color_or_type = models.CharField(max_length=100, verbose_name='اللون / النوع (مثل: عكاز أزرق 8KG)')
    tare_weight = models.DecimalField(
        max_digits=6,
        decimal_places=3,
        default=Decimal('0.800'),
        verbose_name='الوزن الفارغ (كغ)'
    )
    discount_price = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        default=Decimal('0.000'),
        verbose_name='سعر الخصم على التاجر (دينار)'
    )
    company_stock = models.IntegerField(
        default=0,
        verbose_name='رصيد مخزون الشركة من الصناديق الفارغة'
    )
    is_box = models.BooleanField(
        default=True,
        verbose_name='يُستخدم كصندوق في الاستلام والتوضيب'
    )
    is_active = models.BooleanField(default=True, verbose_name='نشط')

    class Meta:
        verbose_name = 'نوع الصندوق'
        verbose_name_plural = 'أنواع الصناديق'
        ordering = ['name']

    def __str__(self):
        return f"{self.name} - {self.color_or_type} ({self.tare_weight} كغ)"


class TraderPackingRate(models.Model):
    """
    سعر كيلو موضب خاص بكل تاجر حسب الطريقة (مكينة/يدوي) مع تاريخ سريان
    """
    class Method(models.TextChoices):
        MACHINE = 'MACHINE', 'مكينة'
        MANUAL = 'MANUAL', 'يدوي (عاملات)'

    trader = models.ForeignKey(
        Trader,
        on_delete=models.CASCADE,
        related_name='packing_rates',
        verbose_name='التاجر'
    )
    method = models.CharField(
        max_length=20,
        choices=Method.choices,
        verbose_name='طريقة التوضيب'
    )
    rate_per_kg = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        verbose_name='سعر الكيلو الموضب (دينار)'
    )
    effective_date = models.DateField(
        default=timezone.now,
        verbose_name='تاريخ السريان'
    )
    notes = models.TextField(blank=True, verbose_name='ملاحظات')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'سعر توضيب التاجر'
        verbose_name_plural = 'أسعار توضيب التجار'
        ordering = ['-effective_date']

    def __str__(self):
        return f"{self.trader.name} - {self.get_method_display()}: {self.rate_per_kg} دينار/كغ"


class ColdStore(models.Model):
    """
    البرادات (البراد الأول، البراد الثاني...)
    """
    name = models.CharField(max_length=100, unique=True, verbose_name='اسم البراد')
    code = models.CharField(max_length=50, unique=True, verbose_name='رمز البراد')
    capacity_pallets = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name='السعة الإجمالية للطبالي (فارغ = غير محدد)',
        help_text='إجمالي سعة البراد بالطبالي، وتُستخدم لحساب سعة كل سيخ تلقائياً.',
    )
    current_temperature = models.DecimalField(
        max_digits=4,
        decimal_places=1,
        null=True,
        blank=True,
        verbose_name='درجة الحرارة الحالية (°C)'
    )
    notes = models.TextField(blank=True, verbose_name='ملاحظات')

    class Meta:
        verbose_name = 'براد'
        verbose_name_plural = 'البرادات'
        ordering = ['code']

    def __str__(self):
        return self.name

    @property
    def occupied_pallets(self):
        # إن حُسب العدد مسبقاً باستعلام مجمّع (annotate_cold_store_occupancy) نستخدمه
        # بدل استعلام جديد لكل براد — مهم جداً لأن قاعدة البيانات خارجية ومسافة الشبكة كبيرة.
        if hasattr(self, '_occupied_pallets_cached'):
            return self._occupied_pallets_cached
        return self.pallets.filter(status='IN_COOLING').count()

    @property
    def packed_occupied_pallets(self):
        """عدد الطبالي الموضبة المبرّدة داخل هذا البراد (خدمة التبريد اليومي)."""
        if hasattr(self, '_packed_occupied_pallets_cached'):
            return self._packed_occupied_pallets_cached
        return self.packed_pallets.filter(status='IN_COOLING').count()

    @property
    def total_occupied_pallets(self):
        """
        إجمالي إشغال البراد بالطبالي = الطبالي الخام في التبريد + الطبالي الموضبة المبرّدة.
        السعة (capacity_pallets) تشمل النوعين معاً.
        """
        return self.occupied_pallets + self.packed_occupied_pallets

    @property
    def occupancy_rate(self):
        occupied = self.total_occupied_pallets
        if self.capacity_pallets and self.capacity_pallets > 0:
            return round((occupied / self.capacity_pallets) * 100, 1)
        return None


class Shelf(models.Model):
    """
    السيخ / الرف داخل البراد
    """
    cold_store = models.ForeignKey(
        ColdStore,
        on_delete=models.CASCADE,
        related_name='shelves',
        verbose_name='البراد'
    )
    shelf_number = models.CharField(max_length=50, verbose_name='رقم السيخ / الرف')
    capacity_pallets = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name='سعة السيخ للطبالي (فارغ = تُحسب من سعة البراد)',
        help_text='اتركها فارغة ليُحسب الحد تلقائياً = سعة البراد ÷ عدد الأسياخ، '
                  'وعندها تنتقل الطبالي تلقائياً إلى السيخ التالي عند امتلاء السيخ.',
    )
    notes = models.TextField(blank=True, verbose_name='ملاحظات')

    class Meta:
        verbose_name = 'سيخ / رف'
        verbose_name_plural = 'الأسياخ والرفوف'
        unique_together = ('cold_store', 'shelf_number')
        ordering = ['cold_store', 'shelf_number']

    def __str__(self):
        return f"{self.cold_store.name} - السيخ {self.shelf_number}"

    @property
    def occupied_pallets(self):
        # إن حُسب العدد مسبقاً باستعلام مجمّع (annotate_shelf_occupancy) نستخدمه بدل استعلام جديد
        if hasattr(self, '_occupied_pallets_cached'):
            return self._occupied_pallets_cached
        return self.pallets.filter(status='IN_COOLING').count()

    @property
    def packed_occupied_pallets(self):
        """عدد الطبالي الموضبة المبرّدة على هذا السيخ."""
        if hasattr(self, '_packed_occupied_pallets_cached'):
            return self._packed_occupied_pallets_cached
        return self.packed_pallets.filter(status='IN_COOLING').count()

    @property
    def total_occupied_pallets(self):
        """إجمالي إشغال السيخ = الخام في التبريد + الموضب المبرّد (السعة تشمل النوعين)."""
        return self.occupied_pallets + self.packed_occupied_pallets

    @property
    def effective_capacity(self):
        """
        السعة الفعلية للسيخ بالطبالي:
        - إن حُدّدت سعة للسيخ نستخدمها كما هي.
        - وإلا فتُحسب تلقائياً من إجمالي سعة البراد ÷ عدد أسياخه
          (حتى تمتلئ الأسياخ وتنتقل الطبالي تلقائياً إلى السيخ التالي).
        - وإن لم تُحدّد سعة للبراد أيضاً فهي غير محدودة (None).
        """
        if hasattr(self, '_effective_capacity_cached'):
            return self._effective_capacity_cached
        if self.capacity_pallets and self.capacity_pallets > 0:
            return self.capacity_pallets
        store = self.cold_store
        if store and store.capacity_pallets and store.capacity_pallets > 0:
            shelves_count = store.shelves.count()
            if shelves_count:
                # تقريب للأعلى: ceil(سعة البراد ÷ عدد الأسياخ)
                return -(-store.capacity_pallets // shelves_count)
        return None

    @property
    def free_pallets(self):
        """عدد الطبالي المتاحة في السيخ (None = غير محدود). يشمل احتساب الموضب المبرّد."""
        capacity = self.effective_capacity
        if not capacity or capacity <= 0:
            return None
        return max(0, capacity - self.total_occupied_pallets)

    @property
    def is_full(self):
        """هل امتلأ السيخ (مشغول ≥ السعة)؟ الأسياخ غير المحدودة لا تمتلئ."""
        capacity = self.effective_capacity
        return bool(capacity) and self.total_occupied_pallets >= capacity


def annotate_cold_store_occupancy(stores):
    """
    يملأ عدد الطبالي المشغولة لكل براد (خام + موضب مبرّد) باستعلامات مجمّعة بدل استعلام لكل براد.
    (يُستخدم في الصفحات التي تعرض كل البرادات معاً)
    """
    from packing.models import PackedPallet

    stores = list(stores)
    counts = dict(
        Pallet.objects.filter(status=Pallet.Status.IN_COOLING)
        .values_list('cold_store_id')
        .annotate(c=Count('id'))
        .values_list('cold_store_id', 'c')
    )
    packed_counts = dict(
        PackedPallet.objects.filter(status=PackedPallet.Status.IN_COOLING)
        .values_list('cold_store_id')
        .annotate(c=Count('id'))
        .values_list('cold_store_id', 'c')
    )
    for store in stores:
        store._occupied_pallets_cached = counts.get(store.id, 0)
        store._packed_occupied_pallets_cached = packed_counts.get(store.id, 0)
    return stores


def annotate_shelf_occupancy(shelves):
    """
    يملأ عدد الطبالي المشغولة لكل سيخ (خام + موضب مبرّد) باستعلامات مجمّعة بدل استعلام لكل سيخ،
    ويحسب السعة الفعلية (المشتقة من سعة البراد عند عدم تحديد سعة للسيخ) بنفس الاستعلامات المجمّعة.
    """
    from packing.models import PackedPallet

    shelves = list(shelves)
    counts = dict(
        Pallet.objects.filter(status=Pallet.Status.IN_COOLING)
        .values_list('shelf_id')
        .annotate(c=Count('id'))
        .values_list('shelf_id', 'c')
    )
    packed_counts = dict(
        PackedPallet.objects.filter(status=PackedPallet.Status.IN_COOLING)
        .values_list('shelf_id')
        .annotate(c=Count('id'))
        .values_list('shelf_id', 'c')
    )
    store_capacities = dict(ColdStore.objects.values_list('id', 'capacity_pallets'))
    shelves_per_store = dict(
        Shelf.objects.values_list('cold_store_id')
        .annotate(c=Count('id'))
        .values_list('cold_store_id', 'c')
    )
    for shelf in shelves:
        shelf._occupied_pallets_cached = counts.get(shelf.id, 0)
        shelf._packed_occupied_pallets_cached = packed_counts.get(shelf.id, 0)
        capacity = shelf.capacity_pallets
        if not capacity or capacity <= 0:
            store_capacity = store_capacities.get(shelf.cold_store_id)
            shelves_count = shelves_per_store.get(shelf.cold_store_id, 0)
            if store_capacity and store_capacity > 0 and shelves_count:
                capacity = -(-store_capacity // shelves_count)
            else:
                capacity = None
        shelf._effective_capacity_cached = capacity
    return shelves


class TruckReceipt(models.Model):
    """
    السيارة (وصول بضاعة) ودفعة الاستلام (Lot)
    """
    class Status(models.TextChoices):
        RECEIVED = 'RECEIVED', 'تم الاستلام'
        IN_COOLING = 'IN_COOLING', 'في التبريد'
        PARTIALLY_PACKED = 'PARTIALLY_PACKED', 'موضب جزئياً'
        PACKED = 'PACKED', 'موضب بالكامل'
        DISPATCHED = 'DISPATCHED', 'تم التسليم'

    # رقم الدفعة: سنة-رقم تسلسلي، مثال: 2026-0143
    lot_number = models.CharField(
        max_length=50,
        unique=True,
        verbose_name='رقم الدفعة (Lot)'
    )

    # حقول حرجة (تمنع الحفظ إذا نقصت)
    trader = models.ForeignKey(
        Trader,
        on_delete=models.PROTECT,
        related_name='truck_receipts',
        verbose_name='التاجر'
    )
    crop = models.ForeignKey(
        Crop,
        on_delete=models.PROTECT,
        related_name='truck_receipts',
        verbose_name='نوع البضاعة'
    )
    # نوع الصناديق الواردة من المزرعة (قسم مستقل عن صناديق الشركة المستخدمة في التوضيب)
    box_type = models.ForeignKey(
        'boxes.FarmBoxType',
        on_delete=models.PROTECT,
        related_name='truck_receipts',
        verbose_name='نوع الصناديق الواردة من المزرعة'
    )
    box_count = models.PositiveIntegerField(verbose_name='عدد الصناديق')
    gross_weight = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        verbose_name='الوزن القائم (كغ)'
    )
    tare_weight = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        verbose_name='الوزن الفارغ للسيارة (كغ)'
    )
    # خيار النزول على طبالي: قد تصل البضاعة بكماتٍ (غير محمّلة على طبالي) فيتعذّر تحميلها على طبالي.
    on_pallets = models.BooleanField(
        default=True,
        verbose_name='نزلت البضاعة على طبالي'
    )
    boxes_per_pallet = models.PositiveIntegerField(
        default=80,
        verbose_name='عدد الصناديق على الطبلية'
    )
    pallet_count = models.PositiveIntegerField(
        default=0,
        blank=True,
        verbose_name='عدد الطبالي'
    )
    cold_store = models.ForeignKey(
        ColdStore,
        on_delete=models.PROTECT,
        related_name='truck_receipts',
        verbose_name='البراد'
    )

    # حقول غير حرجة (تُحفظ ناقصة ويُنشأ لها إشعار برابط تعديل)
    driver_name = models.CharField(max_length=150, blank=True, verbose_name='اسم السائق')
    truck_plate = models.CharField(max_length=50, blank=True, verbose_name='رقم السيارة')
    arrival_date = models.DateField(default=timezone.now, verbose_name='تاريخ القدوم')
    shelf = models.ForeignKey(
        Shelf,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='truck_receipts',
        verbose_name='السيخ / الرف'
    )
    temperature = models.DecimalField(
        max_digits=4,
        decimal_places=1,
        null=True,
        blank=True,
        verbose_name='درجة الحرارة (°C)'
    )
    scale_ticket_image = models.ImageField(
        upload_to='scale_tickets/%Y/%m/',
        null=True,
        blank=True,
        verbose_name='صورة القبان / المستندات'
    )
    notes = models.TextField(blank=True, verbose_name='ملاحظات / وصف العيوب')
    # حقول اختيارية تُفعَّل عند الحاجة (المزرعة وشهادة التصدير مثل GlobalG.A.P)
    farm_name = models.CharField(
        max_length=150,
        blank=True,
        verbose_name='اسم المزرعة (اختياري)'
    )
    export_certificate_no = models.CharField(
        max_length=100,
        blank=True,
        verbose_name='رقم شهادة التصدير - GlobalG.A.P (اختياري)'
    )

    # حسابات القبان والأوزان
    apparent_net_weight = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=Decimal('0.000'),
        verbose_name='الصافي الظاهر (القائم - الفارغ)'
    )
    crates_tare_weight = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=Decimal('0.000'),
        verbose_name='وزن الصناديق الفارغة (كغ)'
    )
    pallets_tare_weight = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=Decimal('0.000'),
        verbose_name='وزن الطبالي الفارغة (كغ)'
    )
    calculated_net_weight = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=Decimal('0.000'),
        verbose_name='الصافي المحسوب (كغ)'
    )
    weight_deduction_applied = models.BooleanField(
        default=True,
        verbose_name='تطبيق خصم الصناديق والطبالي من القبان'
    )
    auto_description = models.CharField(
        max_length=255,
        blank=True,
        verbose_name='الوصف التلقائي للموقع'
    )

    # الحالة والتتبع
    status = models.CharField(
        max_length=30,
        choices=Status.choices,
        default=Status.IN_COOLING,
        verbose_name='الحالة'
    )
    missing_fields_summary = models.TextField(
        blank=True,
        verbose_name='ملخص الحقول الناقصة غير الحرجة'
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name='المستخدم المُنشئ'
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='وقت الوصول التلقائي')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='تاريخ آخر تعديل')

    class Meta:
        verbose_name = 'سيارة / دفعة استلام'
        verbose_name_plural = 'السيارات والوصول'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.lot_number} - {self.trader.name} ({self.crop.name})"

    @classmethod
    def generate_lot_number(cls, year=None):
        if not year:
            year = timezone.now().year
        prefix = f"{year}-"
        last_receipt = cls.objects.filter(lot_number__startswith=prefix).order_by('-lot_number').first()
        if last_receipt:
            try:
                seq = int(last_receipt.lot_number.split('-')[1]) + 1
            except (IndexError, ValueError):
                seq = cls.objects.filter(lot_number__startswith=prefix).count() + 1
        else:
            seq = 1
        return f"{year}-{seq:04d}"

    def calculate_weights(self, empty_pallet_weight=None):
        """
        حساب الأوزان طبقاً للبند 1 من الجزء الثاني:
        الصافي الظاهر = القائم - الفارغ
        إذا تضمنت قراءة الميزان وزن الصناديق والطبالي:
        الصافي المحسوب = القائم - الفارغ - (الصناديق × وزن الصندوق) - (الطبالي × وزن الطبلية)

        أوزان التغليف الفارغة تُقرأ من إعدادات النظام عند عدم تمريرها،
        فلا يُفترض أي وزن تشغيلي داخل الشيفرة.
        """
        from core.models import SystemSetting

        settings_obj = None
        if empty_pallet_weight is None:
            settings_obj = SystemSetting.get_settings()
            empty_pallet_weight = settings_obj.empty_pallet_weight

        self.apparent_net_weight = max(Decimal('0.000'), self.gross_weight - self.tare_weight)
        if self.box_type:
            box_tare = self.box_type.tare_weight
        else:
            box_tare = (settings_obj or SystemSetting.get_settings()).empty_crate_weight
        self.crates_tare_weight = Decimal(self.box_count) * box_tare
        # خصم وزن الطبالي يُطبَّق فقط إذا نزلت البضاعة من السيارة محمّلة على طبالي.
        # البضاعة الواصلة بكمات تُرتَّب على طبالي داخل المشغل، فلا يُخصم وزنها من قراءة القبان.
        if self.on_pallets:
            self.pallets_tare_weight = Decimal(self.pallet_count) * empty_pallet_weight
        else:
            self.pallets_tare_weight = Decimal('0.000')

        if self.weight_deduction_applied:
            self.calculated_net_weight = max(
                Decimal('0.000'),
                self.apparent_net_weight - self.crates_tare_weight - self.pallets_tare_weight
            )
        else:
            self.calculated_net_weight = self.apparent_net_weight

        # توليد الوصف التلقائي: "البراد الثاني – السيخ الأول – 4°C"
        desc_parts = [self.cold_store.name if self.cold_store else 'بدون براد']
        if self.shelf:
            desc_parts.append(f"السيخ {self.shelf.shelf_number}")
        if self.temperature is not None:
            desc_parts.append(f"{self.temperature}°C")
        self.auto_description = " – ".join(desc_parts)

    def check_non_critical_missing(self):
        """
        فحص الحقول غير الحرجة الناقصة وفق القائمة المعتمدة في الإعدادات (البند خامساً).
        الفحص لا يمنع الحفظ، بل يُنشئ إشعاراً برابط تعديل مباشر.
        """
        from core.models import SystemSetting
        settings_obj = SystemSetting.get_settings()

        checks = {
            'driver_name': lambda s: not s.driver_name,
            'truck_plate': lambda s: not s.truck_plate,
            'shelf': lambda s: not s.shelf,
            'temperature': lambda s: s.temperature is None,
            'notes': lambda s: not s.notes,
            'scale_ticket_image': lambda s: not s.scale_ticket_image,
            'farm_name': lambda s: not s.farm_name,
            'export_certificate_no': lambda s: not s.export_certificate_no,
        }

        missing = []
        for field in settings_obj.get_non_critical_fields():
            check = checks.get(field)
            if check and check(self):
                missing.append(SystemSetting.ALL_FIELD_LABELS.get(field, field))
        return missing


class Pallet(models.Model):
    """
    الطبالي المنفردة:
    ترقيم: الدفعة + رقم الطبلية (مثال: 2026-0143-P03)
    رمز SSCC (GS1)
    حالة: في البراد -> خرجت للتوضيب -> موضبة -> مسلّمة
    """
    class Status(models.TextChoices):
        IN_COOLING = 'IN_COOLING', 'في البراد'
        OUT_FOR_PACKING = 'OUT_FOR_PACKING', 'خرجت للتوضيب'
        OPEN_PARTIAL = 'OPEN_PARTIAL', 'مفتوحة (خام متبقٍ)'
        PACKED = 'PACKED', 'موضبة'
        DELIVERED = 'DELIVERED', 'مسلّمة'

    pallet_code = models.CharField(
        max_length=60,
        unique=True,
        verbose_name='رقم الطبلية'
    )
    sscc_code = models.CharField(
        max_length=30,
        unique=True,
        verbose_name='رمز GS1 SSCC'
    )
    truck_receipt = models.ForeignKey(
        TruckReceipt,
        on_delete=models.CASCADE,
        related_name='pallets',
        verbose_name='دفعة السيارة'
    )
    trader = models.ForeignKey(
        Trader,
        on_delete=models.PROTECT,
        related_name='pallets',
        verbose_name='التاجر'
    )
    crop = models.ForeignKey(
        Crop,
        on_delete=models.PROTECT,
        related_name='pallets',
        verbose_name='نوع البضاعة'
    )
    box_type = models.ForeignKey(
        'boxes.FarmBoxType',
        on_delete=models.PROTECT,
        related_name='pallets',
        verbose_name='نوع الصناديق الواردة من المزرعة'
    )
    box_count = models.PositiveIntegerField(
        default=0,
        verbose_name='عدد الصناديق على الطبلية'
    )
    consumed_box_count = models.PositiveIntegerField(
        default=0,
        verbose_name='عدد الصناديق المستهلكة من الطبلية'
    )
    remaining_box_count = models.PositiveIntegerField(
        default=0,
        verbose_name='رصيد الصناديق المتبقي في الطبلية'
    )
    estimated_net_weight = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        default=Decimal('0.000'),
        verbose_name='الوزن الصافي التقديري (كغ)'
    )
    cold_store = models.ForeignKey(
        ColdStore,
        on_delete=models.PROTECT,
        related_name='pallets',
        verbose_name='البراد'
    )
    shelf = models.ForeignKey(
        Shelf,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='pallets',
        verbose_name='السيخ / الرف'
    )
    temperature = models.DecimalField(
        max_digits=4,
        decimal_places=1,
        null=True,
        blank=True,
        verbose_name='درجة الحرارة (°C)'
    )
    status = models.CharField(
        max_length=30,
        choices=Status.choices,
        default=Status.IN_COOLING,
        verbose_name='حالة الطبلية'
    )
    entered_cooling_at = models.DateTimeField(
        default=timezone.now,
        verbose_name='وقت دخول البراد'
    )
    exited_cooling_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='وقت الخروج للتوضيب'
    )
    notes = models.TextField(blank=True, verbose_name='ملاحظات')

    class Meta:
        verbose_name = 'طبلية'
        verbose_name_plural = 'الطبالي'
        ordering = ['pallet_code']

    def __str__(self):
        return f"{self.pallet_code} ({self.trader.name} - {self.crop.name})"

    def save(self, *args, **kwargs):
        """
        عند إنشاء طبلية جديدة يُهيَّأ رصيد الصناديق المتبقي بعدد صناديقها،
        ما لم يكن قد حُدِّد صراحةً (يبقى الرصيد كما هو عند التعديل).
        """
        if self._state.adding and not self.remaining_box_count and self.box_count:
            self.remaining_box_count = self.box_count
        super().save(*args, **kwargs)

    @property
    def is_open(self):
        """هل بقيت في الطبلية صناديق خام لم تُستهلك بعد؟"""
        return self.remaining_box_count > 0

    @property
    def is_fully_consumed(self):
        """هل استُهلكت الطبلية بالكامل؟"""
        return self.remaining_box_count <= 0

    @property
    def consumed_ratio(self):
        """نسبة ما استُهلك من صناديق الطبلية (0..1) — تُستخدم لتوزيع الوزن تقديرياً."""
        if not self.box_count:
            return Decimal('0')
        used = min(self.consumed_box_count, self.box_count)
        return (Decimal(used) / Decimal(self.box_count)).quantize(Decimal('0.000001'))

    @property
    def days_in_cooling(self):
        """
        أيام البقاء بالبراد بنظام FIFO
        """
        end_time = self.exited_cooling_at or timezone.now()
        delta = end_time - self.entered_cooling_at
        return delta.days

    @classmethod
    def generate_sscc(cls, pallet_code):
        """
        توليد معيار SSCC GS1 القياسي:
        (00) + 1 digit extension + 7 digits company prefix + 9 digits serial + 1 check digit
        للتسهيل مع الحفاظ على المعيار: نولد كود 18 رقماً فريداً
        """
        clean_code = ''.join(c for c in pallet_code if c.isdigit())
        company_prefix = "6251234" # رمز المنشأة
        serial = clean_code.zfill(9)[-9:]
        raw = f"0{company_prefix}{serial}"
        # حساب رقم التدقيق Check Digit (مجموع الأوزان 3, 1 بدوران)
        total = 0
        for i, char in enumerate(reversed(raw)):
            weight = 3 if i % 2 == 0 else 1
            total += int(char) * weight
        check_digit = (10 - (total % 10)) % 10
        return f"(00) {raw}{check_digit}"

    def get_qr_data(self):
        """
        بيانات QR:
        رقم الطبلية، عدد الصناديق، التاجر والمزرعة، البضاعة، السيارة، تاريخ الوصول، البراد والسيخ، الملاحظات.
        """
        shelf_str = f"السيخ {self.shelf.shelf_number}" if self.shelf else "غير محدد"
        temp_str = f"{self.temperature}°C" if self.temperature is not None else "غير مسجلة"
        return {
            "pallet_code": self.pallet_code,
            "sscc": self.sscc_code,
            "lot_number": self.truck_receipt.lot_number,
            "trader": self.trader.name,
            "crop": self.crop.name,
            "box_type": str(self.box_type),
            "box_count": self.box_count,
            "consumed_box_count": self.consumed_box_count,
            "remaining_box_count": self.remaining_box_count,
            "net_weight_kg": str(self.estimated_net_weight),
            "arrival_date": str(self.truck_receipt.arrival_date),
            "truck_plate": self.truck_receipt.truck_plate or "غير مسجل",
            "driver_name": self.truck_receipt.driver_name or "غير مسجل",
            "cold_store": self.cold_store.name,
            "shelf": shelf_str,
            "temperature": temp_str,
            "farm_name": self.truck_receipt.farm_name or "غير محددة",
            "export_certificate_no": self.truck_receipt.export_certificate_no or "غير محددة",
            "status": self.get_status_display(),
            "notes": self.notes or self.truck_receipt.notes or "لا يوجد",
        }


class PalletMovementLog(models.Model):
    """
    سجل حركات الطبالي ونقلها بين البرادات والأسياخ:
    كل نقل أو تخزين أو توضيب ينشئ حركة موثقة تحدث الموقع والحالة والمخزون.
    """
    pallet = models.ForeignKey(
        Pallet,
        on_delete=models.CASCADE,
        related_name='movements',
        verbose_name='الطبلية'
    )
    from_cold_store = models.ForeignKey(
        ColdStore,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='outgoing_movements',
        verbose_name='من براد'
    )
    from_shelf = models.ForeignKey(
        Shelf,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='outgoing_movements',
        verbose_name='من سيخ'
    )
    to_cold_store = models.ForeignKey(
        ColdStore,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='incoming_movements',
        verbose_name='إلى براد'
    )
    to_shelf = models.ForeignKey(
        Shelf,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='incoming_movements',
        verbose_name='إلى سيخ'
    )
    movement_type = models.CharField(
        max_length=50,
        verbose_name='نوع الحركة'
    )
    moved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name='المستخدم المنفذ'
    )
    timestamp = models.DateTimeField(auto_now_add=True, verbose_name='وقت الحركة')
    notes = models.TextField(blank=True, verbose_name='ملاحظات الحركة')

    class Meta:
        verbose_name = 'سجل حركة طبلية'
        verbose_name_plural = 'سجلات حركات الطبالي'
        ordering = ['-timestamp']

    def __str__(self):
        return f"{self.pallet.pallet_code}: {self.movement_type} في {self.timestamp:%Y-%m-%d %H:%M}"


class ColdStoreTemperatureLog(models.Model):
    """
    سجل درجات الحرارة لكل براد (إدخال وإخراج وقياس دوري):
    يوثّق القراءة والوقت والمستخدم والطبلية المرتبطة إن وجدت.
    """
    class LogType(models.TextChoices):
        ENTRY = 'ENTRY', 'عند الإدخال'
        EXIT = 'EXIT', 'عند الإخراج'
        PERIODIC = 'PERIODIC', 'قياس دوري'

    cold_store = models.ForeignKey(
        ColdStore,
        on_delete=models.CASCADE,
        related_name='temperature_logs',
        verbose_name='البراد'
    )
    temperature = models.DecimalField(
        max_digits=4,
        decimal_places=1,
        verbose_name='درجة الحرارة (°C)'
    )
    log_type = models.CharField(
        max_length=20,
        choices=LogType.choices,
        default=LogType.PERIODIC,
        verbose_name='نوع القياس'
    )
    pallet = models.ForeignKey(
        'Pallet',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='temperature_logs',
        verbose_name='الطبلية المرتبطة (اختياري)'
    )
    notes = models.CharField(max_length=255, blank=True, verbose_name='ملاحظات')
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name='المستخدم المسجّل'
    )
    recorded_at = models.DateTimeField(default=timezone.now, verbose_name='وقت القياس')

    class Meta:
        verbose_name = 'سجل درجة حرارة براد'
        verbose_name_plural = 'سجلات درجات حرارة البرادات'
        ordering = ['-recorded_at']

    def __str__(self):
        return f"{self.cold_store.name} - {self.temperature}°C ({self.get_log_type_display()})"
