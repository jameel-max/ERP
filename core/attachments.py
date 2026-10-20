"""
إعدادات نظام «إرفاق الصور» العام.

يسمح بربط عدة صور بأي سجل في الموقع (شحنات، سيارات، طبالي، تجار، جلسات توضيب، فواتير).
كل القيود هنا مقصودة لتبقى واضحة للمستخدم برسائل عربية.
"""

# السجلات المسموح إرفاق الصور بها (app_label, model_name)
ATTACHABLE_MODELS = {
    ('trade', 'tradeshipment'),
    ('operations', 'truckreceipt'),
    ('operations', 'pallet'),
    ('operations', 'trader'),
    ('packing', 'packingsession'),
    ('invoicing', 'invoice'),
}

# الحد الأقصى لعدد الصور لكل سجل
MAX_IMAGES_PER_OBJECT = 30

# الحد الأقصى لحجم الصورة الواحدة (8 ميجابايت)
MAX_IMAGE_SIZE = 8 * 1024 * 1024

# امتدادات الصور المسموحة
ALLOWED_IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.gif'}


def is_attachable(app_label, model_name):
    """هل يُسمح بإرفاق الصور لهذا النوع من السجلات؟"""
    return (app_label, (model_name or '').lower()) in ATTACHABLE_MODELS


def save_uploaded_images(obj, files, caption='', user=None):
    """
    يتحقق من الصور المرفوعة ويحفظها على السجل (عدة صور دفعة واحدة).

    يُرجع: (saved, rejected) — عدد الصور المحفوظة وعدد الملفات المرفوضة.
    """
    import os

    from django.contrib.contenttypes.models import ContentType
    from django.core.exceptions import ValidationError
    from django.db import transaction
    from django.forms import ImageField as ImageFormField

    from core.models import Attachment

    model = type(obj)
    if not is_attachable(model._meta.app_label, model._meta.model_name):
        return 0, len(list(files))

    content_type = ContentType.objects.get_for_model(model)
    current = Attachment.objects.filter(content_type=content_type, object_id=obj.pk).count()
    validator = ImageFormField()

    saved = 0
    rejected = 0
    for uploaded in files:
        if current + saved >= MAX_IMAGES_PER_OBJECT:
            rejected += 1
            continue
        ext = os.path.splitext(uploaded.name or '')[1].lower()
        if ext not in ALLOWED_IMAGE_EXTENSIONS:
            rejected += 1
            continue
        if uploaded.size > MAX_IMAGE_SIZE:
            rejected += 1
            continue
        try:
            validator.clean(uploaded)
        except ValidationError:
            rejected += 1
            continue

        with transaction.atomic():
            Attachment.objects.create(
                content_type=content_type,
                object_id=obj.pk,
                image=uploaded,
                caption=(caption or '')[:200],
                uploaded_by=user,
            )
        saved += 1

    return saved, rejected
