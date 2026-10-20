from django.db import IntegrityError, transaction


def save_form_transactionally(form, save_func, duplicate_message=None):
    """
    ينفّذ عمليات الحفظ داخل معاملة ذرية واحدة، ويحوّل خطأ القيد الفريد
    (IntegrityError) إلى خطأ نموذج واضح بالعربية بدل ظهور صفحة 500.

    المعاملات:
        form: نموذج Django سيُضاف إليه الخطأ عند حدوث تكرار.
        save_func: دالة بلا معاملات تُنفّذ الحفظ وتُرجع الناتج المطلوب.
        duplicate_message: نص الخطأ العربي (له قيمة افتراضية مناسبة).

    يُرجع:
        (True, result) عند نجاح الحفظ، أو (False, None) عند حدوث تكرار.
    """
    if duplicate_message is None:
        duplicate_message = (
            'تعذّر الحفظ: توجد بيانات مكررة تخالف قيداً فريداً '
            '(اسم أو رمز مستخدم مسبقاً). الرجاء تعديل القيم والمحاولة مرة أخرى.'
        )
    try:
        with transaction.atomic():
            result = save_func()
    except IntegrityError:
        form.add_error(None, duplicate_message)
        return False, None
    return True, result
