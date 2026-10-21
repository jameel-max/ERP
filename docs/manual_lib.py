# -*- coding: utf-8 -*-
"""
أدوات بناء كتيّب تعليمات Royal Fruit ERP.

تصميم بسيط: أبيض وأسود فقط — لا ألوان.
التمييز يكون بالخط العريض (Bold) والحجم، والنقاط واضحة (نقاط • وأرقام 1. 2. 3.).

النصوص العربية تُكتب منطقية في manual_content.py، ويتم تشكيلها هنا عند الرسم
عبر دالة `ar()` (arabic_reshaper + python-bidi) لإظهارها صحيحة في ReportLab.
"""

import os
import sys

# --- تهيئة Django قبل استيراد وحدات المشروع (لاستيراد core.pdf_fonts) --------
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
if 'django' not in sys.modules:
    import django  # noqa: E402
    django.setup()

from reportlab.lib import colors  # noqa: E402
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_RIGHT  # noqa: E402
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.lib.styles import ParagraphStyle  # noqa: E402
from reportlab.lib.units import cm, mm  # noqa: E402
from reportlab.platypus import (  # noqa: E402
    Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether, HRFlowable,
)

from core.pdf_fonts import (  # noqa: E402
    register_arabic_fonts, arabic, ARABIC_FONT, ARABIC_FONT_BOLD,
)

if not register_arabic_fonts():
    raise SystemExit('تعذّر تسجيل خط Amiri — تأكد من static/fonts/Amiri-Regular.ttf')

# ---------------------------------------------------------------------------
# ألوان أحادية: أسود للعناصر المهمة، رمادي للثانوي، وخطوط رمادية فاتحة
# ---------------------------------------------------------------------------
BLACK = colors.black
INK = colors.HexColor('#111111')       # نص أساسي
GREY = colors.HexColor('#555555')      # نص ثانوي
LINE = colors.HexColor('#999999')      # خطوط الجداول
LINE_SOFT = colors.HexColor('#CCCCCC')  # خطوط فاصلة خفيفة
WHITE = colors.white

PAGE_W, PAGE_H = A4
MARGIN = 1.7 * cm


# ============================================================================
# الأنماط
# ============================================================================
def _style(name, **kw):
    base = dict(fontName=ARABIC_FONT, fontSize=10.5, leading=17,
                textColor=INK, alignment=TA_RIGHT)
    base.update(kw)
    return ParagraphStyle(name, **base)


S = {
    'body': _style('body', alignment=TA_JUSTIFY, spaceAfter=5),
    'lead': _style('lead', fontSize=11, leading=19, textColor=GREY),
    'h1': _style('h1', fontName=ARABIC_FONT_BOLD, fontSize=18, leading=26,
                 textColor=BLACK),
    'h2': _style('h2', fontName=ARABIC_FONT_BOLD, fontSize=13.5, leading=21,
                 textColor=BLACK, spaceBefore=10, spaceAfter=4),
    'h3': _style('h3', fontName=ARABIC_FONT_BOLD, fontSize=11.5, leading=18,
                 textColor=BLACK, spaceBefore=7, spaceAfter=3),
    'h4': _style('h4', fontName=ARABIC_FONT_BOLD, fontSize=10.5, leading=17,
                 textColor=BLACK, spaceBefore=5, spaceAfter=2),
    'small': _style('small', fontSize=9, leading=14, textColor=GREY),
    'cell': _style('cell', fontSize=9.5, leading=15),
    'cellb': _style('cellb', fontName=ARABIC_FONT_BOLD, fontSize=9.5,
                    leading=15, textColor=BLACK),
    'th': _style('th', fontName=ARABIC_FONT_BOLD, fontSize=10, leading=16,
                 textColor=BLACK, alignment=TA_CENTER),
    'cover_title': _style('cover_title', fontName=ARABIC_FONT_BOLD, fontSize=28,
                          leading=42, textColor=BLACK, alignment=TA_CENTER),
    'cover_sub': _style('cover_sub', fontSize=13, leading=23, textColor=GREY,
                        alignment=TA_CENTER),
    'cover_small': _style('cover_small', fontSize=11, leading=18, textColor=GREY,
                          alignment=TA_CENTER),
    'toc': _style('toc', fontSize=11, leading=20),
    'step': _style('step', fontSize=10.5, leading=17, alignment=TA_JUSTIFY),
}


def P(text, style='body'):
    """فقرة عربية مُشكّلة."""
    return Paragraph(arabic(text), S[style])


def PB(text, style='body'):
    """فقرة عربية عريضة (تمييز بالخط فقط)."""
    return Paragraph(f'<b>{arabic(text)}</b>', S[style])


def bullets(items, style='step'):
    """نقاط واضحة بعلامة • ، بارزة عن النص."""
    out = []
    for t in items:
        out.append(Paragraph(
            f'<bullet>&bull;</bullet>{arabic(t)}',
            ParagraphStyle(
                'bl', parent=S[style], leftIndent=18, bulletIndent=2,
                bulletFontName=ARABIC_FONT_BOLD, spaceAfter=4)))
    return out


def numbered(items, style='step'):
    """نقاط مرقّمة 1. 2. 3. واضحة."""
    out = []
    for i, t in enumerate(items, 1):
        out.append(Paragraph(
            f'<bullet>{i}.</bullet>{arabic(t)}',
            ParagraphStyle(
                'nl', parent=S[style], leftIndent=20, bulletIndent=2,
                bulletFontName=ARABIC_FONT_BOLD, spaceAfter=4)))
    return out


def note(title, text):
    """
    ملاحظة بسيطة بلا ألوان: إطار رقيق من أربعة خطوط فقط، والعنوان عريض.
    """
    inner = [
        Paragraph(f'<b>{arabic(title)}</b>', S['cellb']),
        P(text, 'cell'),
    ]
    t = Table([[inner]], colWidths=[PAGE_W - 2 * MARGIN - 2 * mm])
    t.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 0.6, LINE),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]))
    return KeepTogether([t, Spacer(1, 7)])


def table(headers, rows, widths=None):
    """
    جدول أبيض وأسود: خطوط رمادية، وترويسة بخط عريض وخلفية رمادية فاتحة جداً.
    """
    data = [[Paragraph(f'<b>{arabic(h)}</b>', S['th']) for h in headers]]
    for r in rows:
        data.append([Paragraph(arabic(str(c)), S['cell']) for c in r])
    avail = PAGE_W - 2 * MARGIN
    if widths is None:
        widths = [avail / len(headers)] * len(headers)
    else:
        total = sum(widths)
        widths = [avail * w / total for w in widths]
    t = Table(data, colWidths=widths, repeatRows=1, hAlign='RIGHT')
    cmds = [
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#EDEDED')),
        ('ALIGN', (0, 0), (-1, -1), 'RIGHT'),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('GRID', (0, 0), (-1, -1), 0.4, LINE_SOFT),
        ('BOX', (0, 0), (-1, -1), 0.7, LINE),
        ('LINEBELOW', (0, 0), (-1, 0), 0.9, BLACK),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]
    t.setStyle(TableStyle(cmds))
    return t


def section(num, title, subtitle):
    """ترويسة قسم: رقم + عنوان عريض + سطر شرح، مع خط أسود فاصل."""
    num_cell = Paragraph(f'<b>{arabic(str(num))}</b>', ParagraphStyle(
        'n', parent=S['h1'], fontSize=22, alignment=TA_CENTER))
    text_cell = [
        Paragraph(f'<b>{arabic(title)}</b>', S['h1']),
        Paragraph(arabic(subtitle), S['small']),
    ]
    head = Table([[num_cell, text_cell]],
                 colWidths=[1.3 * cm, PAGE_W - 2 * MARGIN - 1.3 * cm])
    head.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ALIGN', (1, 0), (1, 0), 'RIGHT'),
        ('LINEABOVE', (0, 0), (-1, 0), 1.2, BLACK),
        ('LINEBELOW', (0, 0), (-1, 0), 1.2, BLACK),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
    ]))
    return KeepTogether([head, Spacer(1, 10)])


def rule():
    return HRFlowable(width='100%', thickness=0.7, color=LINE,
                      spaceBefore=6, spaceAfter=8)
