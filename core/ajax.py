"""
مساعدات العمليات غير المتزامنة (AJAX).

الفكرة: نفس منطق الحفظ الذرّي على الخادم تماماً — لا تغيير في القواعد المالية أو
المخزنية ولا في الأقفال. الفرق الوحيد أن الواجهة لا تتجمّد: عند طلب AJAX نُعيد
استجابة JSON مختصرة (نجاح/فشل + رسالة عربية + إعادة توجيه اختيارية) بدل صفحة كاملة.

الاستخدام في أي view:

    from core.ajax import is_ajax, ajax_response

    ...
    if is_ajax(request):
        return ajax_response(ok=True, message='تم الحفظ.', redirect=some_url)
    return redirect(some_url)
"""


def is_ajax(request):
    """هل الطلب جاء من طبقة AJAX في الواجهة؟"""
    return (
        request.headers.get('x-requested-with') == 'XMLHttpRequest'
        or request.META.get('HTTP_X_REQUESTED_WITH') == 'XMLHttpRequest'
    )


def ajax_response(ok=True, message='', redirect=None, reload=None, status=None, **extra):
    """
    استجابة JSON موحّدة لطبقة AJAX.

    - ok=False تُرجع رمز 400 افتراضياً حتى لا تُفهم كنجاح.
    - redirect: مسار يُنقل إليه المتصفح بعد النجاح.
    - reload: إن كانت False لا تُعاد الصفحة (لتحديث جزئي).
    """
    from django.http import JsonResponse

    payload = {'ok': bool(ok), 'message': message}
    if redirect:
        payload['redirect'] = redirect
    if reload is not None:
        payload['reload'] = bool(reload)
    payload.update(extra)
    if status is None:
        status = 200 if ok else 400
    return JsonResponse(payload, status=status)
