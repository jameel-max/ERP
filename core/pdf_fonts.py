"""
دعم الخط العربي في ملفات PDF المولَّدة بـ ReportLab.

المشكلة: ReportLab يستخدم خط Helvetica المدمج افتراضياً، وهو لا يحتوي أي حرف عربي
فيظهر النص العربي كمربعات سوداء (□□□□)، كما أنه لا يقوم بتشكيل الحروف العربية
(ربط الحروف ببعضها) ولا بترتيب الاتجاه من اليمين لليسار.

الحل:
- تضمين خط عربي محلي (Amiri) داخل المشروع في static/fonts — لا نعتمد على خط
  خارجي قد لا يكون متاحاً عند الطباعة أو على الخادم.
- تشكيل النص العربي عبر arabic_reshaper (ربط الحروف) ثم python-bidi (ترتيب RTL).

الاستخدام:

    from core.pdf_fonts import register_arabic_fonts, arabic, ARABIC_FONT, ARABIC_FONT_BOLD

    register_arabic_fonts()
    Paragraph(arabic('مرحباً'), style)
"""

import os

from django.conf import settings
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# أسماء الخطوط المسجّلة داخل ReportLab
ARABIC_FONT = 'Amiri'
ARABIC_FONT_BOLD = 'Amiri-Bold'

_FONT_DIR = os.path.join(settings.BASE_DIR, 'static', 'fonts')
_FONT_FILES = {
    ARABIC_FONT: 'Amiri-Regular.ttf',
    ARABIC_FONT_BOLD: 'Amiri-Bold.ttf',
}

_registered = False


def register_arabic_fonts():
    """
    تسجيل خطوط Amiri العربية في ReportLab مرة واحدة فقط لكل عملية.
    تُعيد True إن نجح التسجيل، وFalse إن لم تُوجد ملفات الخط (حتى لا يتعطّل
    توليد الفاتورة بالكامل، مع سقوط آمن إلى الخط الافتراضي).
    """
    global _registered
    if _registered:
        return True

    try:
        for name, filename in _FONT_FILES.items():
            path = os.path.join(_FONT_DIR, filename)
            if not os.path.exists(path):
                return False
            pdfmetrics.registerFont(TTFont(name, path))

        # تمكين <b> و <i> داخل Paragraph باستخدام الخط العربي العريض
        pdfmetrics.registerFontFamily(
            ARABIC_FONT,
            normal=ARABIC_FONT,
            bold=ARABIC_FONT_BOLD,
            italic=ARABIC_FONT,
            boldItalic=ARABIC_FONT_BOLD,
        )
        _registered = True
        return True
    except Exception:  # noqa: BLE001
        # لا نُسقط توليد الفاتورة بسبب خط مفقود
        return False


def arabic(text):
    """
    تجهيز نص للعرض داخل ReportLab:
    1. تشكيل الحروف العربية (ربط الحروف).
    2. ترتيب النص ثنائي الاتجاه (RTL) مع إبقاء الأرقام واللاتيني صحيحين.

    النصوص الفارغة أو غير النصية تُعاد كما هي بأمان.
    """
    if text is None:
        return ''
    text = str(text)
    if text == '':
        return ''
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display

        reshaped = arabic_reshaper.reshape(text)
        return get_display(reshaped)
    except Exception:  # noqa: BLE001
        # عند غياب المكتبات نُعيد النص كما هو بدل إسقاط الفاتورة
        return text
