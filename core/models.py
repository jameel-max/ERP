from django.db import models
from django.contrib.auth.models import AbstractUser
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.utils import timezone
from decimal import Decimal
import json
import os
import uuid


class User(AbstractUser):
    class Role(models.TextChoices):
        OWNER = 'OWNER', 'مالك النظام'
        MANAGER = 'MANAGER', 'مدير'
        ACCOUNTANT = 'ACCOUNTANT', 'محاسب'
        VIEWER = 'VIEWER', 'مشاهد'

    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.ACCOUNTANT,
        verbose_name='الصلاحية'
    )

    @property
    def is_owner(self):
        """مالك النظام: شخص واحد مسؤول عن كل الإجراءات والموافقات."""
        return self.role == self.Role.OWNER or self.is_superuser

    @property
    def is_manager(self):
        return self.role in [self.Role.OWNER, self.Role.MANAGER] or self.is_superuser

    @property
    def is_accountant(self):
        return self.role in [self.Role.OWNER, self.Role.MANAGER, self.Role.ACCOUNTANT] or self.is_superuser

    @property
    def is_viewer(self):
        return self.role == self.Role.VIEWER

    def __str__(self):
        return f"{self.get_full_name() or self.username} ({self.get_role_display()})"


class SystemSetting(models.Model):
    company_name_en = models.CharField(
        max_length=100,
        default='Royal Fruit',
        verbose_name='اسم الشركة (إنجليزي)'
    )
    company_name_ar = models.CharField(
        max_length=100,
        default='رويال فروت',
        verbose_name='اسم الشركة (عربي)'
    )
    brand_color = models.CharField(
        max_length=20,
        default='#1B4D3E',
        verbose_name='لون الهوية الرئيسي'
    )
    empty_crate_weight = models.DecimalField(
        max_digits=6,
        decimal_places=3,
        default=Decimal('0.800'),
        verbose_name='وزن الصندوق الفارغ الافتراضي (كغ)'
    )
    empty_pallet_weight = models.DecimalField(
        max_digits=6,
        decimal_places=3,
        default=Decimal('10.000'),
        verbose_name='وزن الطبلية الفارغة (كغ)'
    )
    worker_hourly_rate = models.DecimalField(
        max_digits=6,
        decimal_places=3,
        default=Decimal('1.000'),
        verbose_name='سعر ساعة العامل الافتراضي (دينار)'
    )
    worker_transport_daily = models.DecimalField(
        max_digits=6,
        decimal_places=3,
        default=Decimal('1.000'),
        verbose_name='بدل المواصلات اليومي الافتراضي (دينار)'
    )
    rounding_policy = models.CharField(
        max_length=30,
        default='ROUND_HALF_UP',
        verbose_name='سياسة التقريب'
    )
    currency_symbol = models.CharField(
        max_length=10,
        default='دينار',
        verbose_name='رمز العملة'
    )
    currency_decimals = models.PositiveSmallIntegerField(
        default=3,
        verbose_name='خانات العملة العشرية'
    )
    weight_decimals = models.PositiveSmallIntegerField(
        default=3,
        verbose_name='خانات الوزن العشرية'
    )
    weight_tolerance_pct = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name='حد سماح فرق الوزن % (فارغ = يتطلب اعتماد المدير)'
    )
    fifo_alert_days = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name='مدة تنبيه الأقدم أولاً بالأيام (فارغ = غير مفعل)'
    )
    packed_cooling_daily_rate = models.DecimalField(
        max_digits=8,
        decimal_places=3,
        default=Decimal('0.000'),
        verbose_name='أجرة تبريد الطبلية الموضبة اليومية (دينار) — 0 = غير محتسبة'
    )
    # ======================================================================
    # أسعار وتكاليف تشغيلية: أي سعر يفترضه النظام في أي شاشة يُضبط من هنا،
    # ولا يُكتب أي سعر أو مصروف ثابت داخل الشيفرة (طلب البند السابع).
    # ======================================================================
    default_packing_rate_per_kg = models.DecimalField(
        max_digits=6,
        decimal_places=3,
        default=Decimal('0.070'),
        verbose_name='سعر الكيلو الموضب الافتراضي — مكينة (دينار/كغ)',
        help_text='يُستخدم عند إنشاء فاتورة توضيب «مكينة» لتاجر بلا سعر توضيب ساري. '
                  'سعر التاجر عند وجوده يُجمَّد داخل الفاتورة ويحلّ محل هذا السعر.'
    )
    default_manual_packing_rate_per_kg = models.DecimalField(
        max_digits=6,
        decimal_places=3,
        default=Decimal('0.070'),
        verbose_name='سعر الكيلو الموضب الافتراضي — يدوي (دينار/كغ)',
        help_text='يُستخدم عند إنشاء فاتورة توضيب «يدوي» لتاجر بلا سعر توضيب ساري. '
                  'سعر التاجر عند وجوده يُجمَّد داخل الفاتورة ويحلّ محل هذا السعر.'
    )
    default_work_hours = models.DecimalField(
        max_digits=4,
        decimal_places=2,
        default=Decimal('8.00'),
        verbose_name='ساعات العمل الافتراضية اليومية (ساعة)',
        help_text='القيمة المبدئية المعبّأة في نموذج تسجيل يوم عمل الورشة.'
    )
    default_exchange_rate = models.DecimalField(
        max_digits=18,
        decimal_places=6,
        default=Decimal('1.000000'),
        verbose_name='سعر الصرف الافتراضي مقابل الدينار',
        help_text='القيمة المبدئية في نموذج تسجيل الشحنات التجارية ودفعاتها. لكل شحنة تعديل مستقل.'
    )
    scan_base_url = models.CharField(
        max_length=200,
        blank=True,
        default='',
        verbose_name='عنوان السيرفر لروابط مسح QR (مثال: http://192.168.1.50:8000) — فارغ = عنوان الصفحة الحالي'
    )

    # البند خامساً: قوائم الحقول الحرجة (تمنع الحفظ) وغير الحرجة (تُنشئ إشعاراً) قابلة للتعديل من الإعدادات
    critical_fields = models.CharField(
        max_length=500,
        default='trader,crop,box_type,box_count,gross_weight,tare_weight,pallet_count,cold_store',
        verbose_name='الحقول الحرجة التي تمنع حفظ السيارة (مفصولة بفواصل)'
    )
    non_critical_fields = models.CharField(
        max_length=500,
        default='driver_name,truck_plate,shelf,temperature,notes,scale_ticket_image',
        verbose_name='الحقول غير الحرجة التي تُنشئ إشعاراً برابط تعديل (مفصولة بفواصل)'
    )

    # أسماء الحقول المعروضة للمستخدم (المفاتيح البرمجية -> التسمية العربية)
    CRITICAL_FIELD_LABELS = {
        'trader': 'التاجر',
        'crop': 'نوع البضاعة',
        'box_type': 'نوع الصناديق',
        'box_count': 'عدد الصناديق',
        'gross_weight': 'الوزن القائم',
        'tare_weight': 'الوزن الفارغ',
        'pallet_count': 'عدد الطبالي',
        'cold_store': 'البراد',
    }
    NON_CRITICAL_FIELD_LABELS = {
        'driver_name': 'اسم السائق',
        'truck_plate': 'رقم السيارة',
        'shelf': 'السيخ / الرف',
        'temperature': 'درجة الحرارة',
        'notes': 'الوصف / الملاحظات',
        'scale_ticket_image': 'صورة القبان',
        'farm_name': 'اسم المزرعة',
        'export_certificate_no': 'رقم شهادة التصدير',
    }
    # كامل الحقول المعروفة القابلة للتصنيف (يسمح بترقية/تخفيض أي حقل بين القائمتين)
    ALL_FIELD_LABELS = {**CRITICAL_FIELD_LABELS, **NON_CRITICAL_FIELD_LABELS}

    class Meta:
        verbose_name = 'إعدادات النظام'
        verbose_name_plural = 'إعدادات النظام'

    def __str__(self):
        return f"إعدادات {self.company_name_en}"

    # ==================== الإعدادات المركزية (مع تخزين مؤقت قصير) ====================
    # قاعدة البيانات خارجية ومسافة الشبكة كبيرة، لذا نخزّن الإعداد في الذاكرة
    # لتجنب استعلام إضافي في كل صفحة.
    _SETTINGS_CACHE_KEY = 'core_system_settings'
    _SETTINGS_CACHE_TTL = 30  # ثانية

    @classmethod
    def get_settings(cls):
        from django.core.cache import cache
        from django.db import transaction

        obj = cache.get(cls._SETTINGS_CACHE_KEY)
        if obj is not None:
            return obj
        obj, _ = cls.objects.get_or_create(id=1)
        # نخزّن الكائن في الذاكرة فقط بعد نجاح الـ commit. في اختبارات TestCase
        # لا تُنفَّذ معاملات on_commit (تُلغى مع الـ rollback) لذا لا تتسرب
        # الإعدادات بين الاختبارات، بينما في التشغيل الحقيقي تعمل بشكل طبيعي.
        transaction.on_commit(
            lambda: cache.set(cls._SETTINGS_CACHE_KEY, obj, cls._SETTINGS_CACHE_TTL)
        )
        return obj

    @classmethod
    def invalidate_settings_cache(cls):
        from django.core.cache import cache
        cache.delete(cls._SETTINGS_CACHE_KEY)

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        # أي تعديل من صفحة الإعدادات يُبطل النسخة المخزّنة فوراً
        self.invalidate_settings_cache()

    def get_critical_fields(self):
        """
        قائمة مفاتيح الحقول الحرجة المعتمدة (افتراضياً حسب المواصفات).
        أي مفتاح معروف يمكن ترقيته ليصبح حرجاً.
        """
        keys = [f.strip() for f in (self.critical_fields or '').split(',') if f.strip()]
        valid = [k for k in keys if k in self.ALL_FIELD_LABELS]
        return valid or list(self.CRITICAL_FIELD_LABELS.keys())

    def get_non_critical_fields(self):
        """
        قائمة مفاتيح الحقول غير الحرجة المعتمدة (افتراضياً حسب المواصفات).
        أي مفتاح معروف يمكن تخفيضه ليصبح غير حرج.
        """
        keys = [f.strip() for f in (self.non_critical_fields or '').split(',') if f.strip()]
        valid = [k for k in keys if k in self.ALL_FIELD_LABELS]
        return valid or list(self.NON_CRITICAL_FIELD_LABELS.keys())


class Notification(models.Model):
    class Severity(models.TextChoices):
        INFO = 'INFO', 'معلومة'
        WARNING = 'WARNING', 'تنبيه'
        CRITICAL = 'CRITICAL', 'حرج'

    title = models.CharField(max_length=255, verbose_name='العنوان')
    message = models.TextField(verbose_name='الرسالة')
    severity = models.CharField(
        max_length=20,
        choices=Severity.choices,
        default=Severity.WARNING,
        verbose_name='درجة الأهمية'
    )
    link = models.CharField(max_length=255, blank=True, verbose_name='رابط التعديل المباشر')
    is_resolved = models.BooleanField(default=False, verbose_name='تم الحل')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='تاريخ الإنشاء')
    resolved_at = models.DateTimeField(null=True, blank=True, verbose_name='تاريخ الحل')
    related_model = models.CharField(max_length=50, blank=True, verbose_name='الكيان المرتبط')
    related_id = models.CharField(max_length=50, blank=True, verbose_name='رقم الكيان المرتبط')

    class Meta:
        verbose_name = 'إشعار'
        verbose_name_plural = 'الإشعارات'
        ordering = ['-created_at']

    # مفتاح الكاش المشترك مع معالج السياق (يُبطل عند أي إنشاء/حل لإشعار)
    _CONTEXT_CACHE_KEY = 'core_notification_context'

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        # أي تغيير (إنشاء أو حل) يُبطل الكاش فوراً ليعود العدّاد صحيحاً
        from django.core.cache import cache
        cache.delete(self._CONTEXT_CACHE_KEY)

    def __str__(self):
        return f"[{self.get_severity_display()}] {self.title}"


class AuditLog(models.Model):
    user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name='المستخدم'
    )
    action = models.CharField(max_length=50, verbose_name='نوع العملية')
    model_name = models.CharField(max_length=100, verbose_name='الجدول / الكيان')
    object_id = models.CharField(max_length=100, verbose_name='معرف السجل')
    object_repr = models.CharField(max_length=255, blank=True, verbose_name='وصف السجل')
    details = models.JSONField(default=dict, blank=True, verbose_name='تفاصيل التغيير')
    reason = models.TextField(blank=True, verbose_name='السبب / المبرر')
    ip_address = models.GenericIPAddressField(null=True, blank=True, verbose_name='عنوان IP')
    timestamp = models.DateTimeField(auto_now_add=True, verbose_name='الوقت والتاريخ')

    class Meta:
        verbose_name = 'سجل تدقيق'
        verbose_name_plural = 'سجلات التدقيق'
        ordering = ['-timestamp']

    def __str__(self):
        return f"{self.timestamp:%Y-%m-%d %H:%M} | {self.user} | {self.action} on {self.model_name}:{self.object_id}"

    @classmethod
    def log(cls, user, action, instance, reason="", details=None, ip_address=None):
        return cls.objects.create(
            user=user if getattr(user, 'is_authenticated', False) else None,
            action=action,
            model_name=instance.__class__.__name__,
            object_id=str(getattr(instance, 'pk', '')),
            object_repr=str(instance)[:255],
            details=details or {},
            reason=reason,
            ip_address=ip_address
        )


def attachment_upload_path(instance, filename):
    """مسار حفظ الصورة: media/attachments/<app>/<model>/<object_id>/<uuid>.<ext>."""
    ext = os.path.splitext(filename or '')[1].lower()
    if ext not in ('.jpg', '.jpeg', '.png', '.webp', '.gif'):
        ext = '.jpg'
    ct = instance.content_type
    return f"attachments/{ct.app_label}/{ct.model}/{instance.object_id}/{uuid.uuid4().hex}{ext}"


class Attachment(models.Model):
    """
    صورة مرفقة بأي سجل في النظام (علاقة عامة عبر نوع المحتوى).

    يدعم النظام أكثر من صورة واحدة لكل سجل، ويمكن حذفها منفردة.
    """

    content_type = models.ForeignKey(
        ContentType, on_delete=models.CASCADE, verbose_name='نوع السجل'
    )
    object_id = models.PositiveBigIntegerField(verbose_name='معرف السجل')
    content_object = GenericForeignKey('content_type', 'object_id')

    image = models.ImageField(upload_to=attachment_upload_path, verbose_name='الصورة')
    caption = models.CharField(max_length=200, blank=True, verbose_name='وصف الصورة')
    uploaded_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='uploaded_attachments', verbose_name='رافع الصورة'
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='وقت الرفع')

    class Meta:
        verbose_name = 'صورة مرفقة'
        verbose_name_plural = 'الصور المرفقة'
        ordering = ['created_at', 'id']
        indexes = [
            models.Index(fields=['content_type', 'object_id']),
        ]

    def __str__(self):
        return self.caption or f'صورة #{self.pk}'

    @property
    def filename(self):
        return os.path.basename(self.image.name) if self.image else ''


@receiver(post_delete, sender=Attachment)
def _delete_attachment_file(sender, instance, **kwargs):
    """حذف ملف الصورة من التخزين عند حذف السجل (يمنع تراكم ملفات يتيمة)."""
    if instance.image:
        try:
            instance.image.delete(save=False)
        except Exception:
            pass
