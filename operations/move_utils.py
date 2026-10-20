"""
أدوات مشتركة لنقل الطبالي عبر السحب والإفلات من صفحة البرادات والأسياخ.
الواجهة تعرض زر/سحب، والفحص الكامل للصلاحيات والقدرة يكون دائماً في الخادم.
"""
import json

from django.http import JsonResponse


def move_payload(request):
    """
    يقرأ بيانات النقل من الطلب (منشور عادي أو JSON) ويعيد كائناً يشبه request.POST.
    يقبل الاثنين حتى تعمل الصفحة نفسها وإن غيّر المتصفح نوع المحتوى.
    """
    content_type = request.content_type or ''
    if 'json' in content_type:
        try:
            raw = request.body.decode('utf-8')
        except UnicodeDecodeError:
            raw = ''
        try:
            data = json.loads(raw or '{}')
        except ValueError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        return {k: ('' if v is None else v) for k, v in data.items()}
    return request.POST


def resolve_target(data, shelf_model, cold_store_model):
    """
    يحدد البراد والسيخ المستهدفين من بيانات الطلب.
    يعيد (cold_store, shelf, error_message) — وخطأ بالعربية عند غياب الهدف أو وجوده.
    """
    shelf_id = data.get('shelf') or None
    store_id = data.get('cold_store') or None

    if shelf_id:
        shelf = shelf_model.objects.filter(pk=shelf_id).select_related('cold_store').first()
        if shelf is None:
            return None, None, 'السيخ المطلوب غير موجود. حدّث الصفحة ثم أعد المحاولة.'
        if store_id and str(shelf.cold_store_id) != str(store_id):
            return None, None, 'السيخ المحدد لا ينتمي إلى البراد المحدد. أعد المحاولة.'
        return shelf.cold_store, shelf, None

    if store_id:
        store = cold_store_model.objects.filter(pk=store_id).first()
        if store is None:
            return None, None, 'البراد المطلوب غير موجود. حدّث الصفحة ثم أعد المحاولة.'
        return store, None, None

    return None, None, 'لم يُحدَّد سيخ أو براد مستهدف. أفلِط البطاقة داخل أحد الأسياخ.'


def error_response(message, status=400):
    """استجابة خطأ JSON برسالة عربية تُعرض كتنبيه في الصفحة."""
    return JsonResponse({'ok': False, 'message': str(message)}, status=status)


def success_response(message, pallet_id, store_id, shelf_id=None, moved=True):
    """استجابة نجاح JSON تحمل بيانات الموقع الجديد لتحديث الصفحة إن رغبت."""
    return JsonResponse({
        'ok': True,
        'moved': moved,
        'message': str(message),
        'pallet_id': pallet_id,
        'store_id': store_id,
        'shelf_id': shelf_id,
    })


def validation_error_message(exc):
    """استخراج أول رسالة عربية من ValidationError بأي شكلك جاءت."""
    messages = getattr(exc, 'messages', None)
    if messages:
        return messages[0]
    return str(getattr(exc, 'message', exc))
