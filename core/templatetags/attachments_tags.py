"""
وسم قوالب لعرض قسم «الصور المرفقة» القابل لإعادة الاستخدام في أي شاشة تفاصيل.

الاستخدام في القالب:
    {% load attachments_tags %}
    ...
    {% attachments object %}

حيث `object` هو سجل الموديل المطلوب إرفاق الصور به.
"""

from django import template
from django.contrib.contenttypes.models import ContentType

from core.models import Attachment
from core.attachments import MAX_IMAGES_PER_OBJECT, is_attachable

register = template.Library()


@register.inclusion_tag('core/_attachments.html', takes_context=True)
def attachments(context, obj):
    request = context.get('request')
    user = getattr(request, 'user', None)

    ct = ContentType.objects.get_for_model(obj)
    items = (
        Attachment.objects
        .filter(content_type=ct, object_id=obj.pk)
        .select_related('uploaded_by')
    )

    return {
        'obj': obj,
        'app_label': ct.app_label,
        'model_name': ct.model,
        'object_id': obj.pk,
        'attachments': items,
        'max_images': MAX_IMAGES_PER_OBJECT,
        'can_upload': bool(user and getattr(user, 'is_accountant', False) and is_attachable(ct.app_label, ct.model)),
        'can_delete': bool(user and getattr(user, 'is_accountant', False)),
    }
