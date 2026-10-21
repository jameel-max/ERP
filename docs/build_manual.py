# -*- coding: utf-8 -*-
"""
بناء كتيّب تعليمات Royal Fruit ERP بصيغة PDF.

التشغيل:
    python docs/build_manual.py

الناتج:
    docs/Royal_Fruit_ERP_Manual_AR.pdf
"""

import os
import sys

DOCS_DIR = os.path.dirname(os.path.abspath(__file__))
if DOCS_DIR not in sys.path:
    sys.path.insert(0, DOCS_DIR)

from manual_lib import (  # noqa: E402
    S, P, PB, bullets, numbered, note, table, section, rule,
    BLACK, INK, GREY, LINE, LINE_SOFT, WHITE,
    PAGE_W, PAGE_H, MARGIN, ARABIC_FONT, ARABIC_FONT_BOLD, arabic,
)
from manual_content import (  # noqa: E402
    TITLE, SUBTITLE, SYSTEM_AR, VERSION, CH1, CH2, CH3, CH4,
)

from reportlab.lib import colors  # noqa: E402
from reportlab.lib.units import cm, mm  # noqa: E402
from reportlab.platypus import (  # noqa: E402
    BaseDocTemplate, Frame, PageTemplate, Paragraph, Spacer, Table, TableStyle,
    PageBreak, KeepTogether,
)
from reportlab.lib.styles import ParagraphStyle  # noqa: E402
from reportlab.lib.enums import TA_CENTER  # noqa: E402

OUTPUT = os.path.join(DOCS_DIR, 'Royal_Fruit_ERP_Manual_AR.pdf')

CONTENT_W = PAGE_W - 2 * MARGIN


# ============================================================================
# ترويسة وتذييل الصفحات
# ============================================================================
def on_page(canvas, doc):
    canvas.saveState()
    # ترويسة بسيطة: اسم النظام عريض يميناً، الإصدار يساراً، وخط فاصل أسود رفيع
    canvas.setFont(ARABIC_FONT_BOLD, 9)
    canvas.setFillColor(INK)
    canvas.drawRightString(PAGE_W - MARGIN, PAGE_H - 0.85 * cm, arabic(SYSTEM_AR))
    canvas.setFont(ARABIC_FONT, 8)
    canvas.setFillColor(GREY)
    canvas.drawString(MARGIN, PAGE_H - 0.85 * cm, arabic(VERSION))
    canvas.setStrokeColor(LINE)
    canvas.setLineWidth(0.5)
    canvas.line(MARGIN, PAGE_H - 1.05 * cm, PAGE_W - MARGIN, PAGE_H - 1.05 * cm)
    # تذييل: خط فاصل + رقم الصفحة
    canvas.line(MARGIN, 0.78 * cm, PAGE_W - MARGIN, 0.78 * cm)
    canvas.setFont(ARABIC_FONT, 8.5)
    canvas.setFillColor(GREY)
    canvas.drawCentredString(PAGE_W / 2.0, 0.36 * cm, str(doc.page))
    canvas.restoreState()


def on_cover(canvas, doc):
    """غلاف أبيض بسيط: إطار رقيق أسود فقط."""
    canvas.saveState()
    canvas.setStrokeColor(BLACK)
    canvas.setLineWidth(1.1)
    canvas.rect(MARGIN * 0.55, MARGIN * 0.55,
                PAGE_W - 2 * MARGIN * 0.55, PAGE_H - 2 * MARGIN * 0.55,
                stroke=1, fill=0)
    canvas.restoreState()


# ============================================================================
# الغلاف
# ============================================================================
def build_cover():
    story = [Spacer(1, 4.5 * cm)]
    story.append(Paragraph(f'<b>{arabic(TITLE)}</b>', S['cover_title']))
    story.append(Spacer(1, 0.3 * cm))
    story.append(Paragraph(f'<b>{arabic(SUBTITLE)}</b>', ParagraphStyle(
        'cs', parent=S['cover_sub'], fontSize=17, fontName=ARABIC_FONT_BOLD,
        textColor=BLACK)))
    story.append(Spacer(1, 1.0 * cm))

    # خط فاصل أسود
    line = Table([['']], colWidths=[7 * cm], rowHeights=[0.05 * cm])
    line.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, -1), BLACK)]))
    line.hAlign = 'CENTER'
    story.append(line)
    story.append(Spacer(1, 1.0 * cm))

    story.append(Paragraph(arabic(SYSTEM_AR), S['cover_sub']))
    story.append(Spacer(1, 5.6 * cm))
    story.append(Paragraph(arabic('دليل المستخدم التشغيلي والمالي'), S['cover_small']))
    story.append(Spacer(1, 0.2 * cm))
    story.append(Paragraph(f'<b>{arabic(VERSION)}</b>', ParagraphStyle(
        'cv', parent=S['cover_small'], fontSize=12, fontName=ARABIC_FONT_BOLD,
        textColor=BLACK)))
    story.append(PageBreak())
    return story


# ============================================================================
# فهرس المحتويات
# ============================================================================
TOC_ENTRIES = [
    ('1', 'نظرة عامة على النظام', 'ما هو النظام، المبدأ الأساسي، الأدوار، خريطة الأقسام'),
    ('2', 'الطريق الكامل لأول عملية', 'استلام ← توضيب ← تبريد ← تسليم ← فاتورة ← تحصيل'),
    ('3', 'المراجع التفصيلية لكل قسم', 'العمليات، التوضيب، الفواتير، المالية، الصناديق، العمالة، التجارة، التحليلات، الإعدادات'),
    ('4', 'الأدوار والأسئلة الشائعة', 'جدول الصلاحيات، مواقف عملية، قواعد الحفاظ على دقة البيانات'),
]


def build_toc():
    story = [Paragraph(arabic('فهرس المحتويات'), S['h1']), Spacer(1, 6)]
    rows = []
    for num, title, desc in TOC_ENTRIES:
        rows.append([
            Paragraph(arabic(desc), ParagraphStyle('td', parent=S['small'])),
            [Paragraph(f'<b>{arabic(title)}</b>', ParagraphStyle(
                'tt', parent=S['cellb'], textColor=BLACK)),
             Paragraph(f'<b>{arabic(num)}</b>', ParagraphStyle(
                 'tn', parent=S['small'], textColor=BLACK))],
        ])
    t = Table(rows, colWidths=[CONTENT_W * 0.66, CONTENT_W * 0.34])
    t.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
        ('LINEBELOW', (0, 0), (-1, -2), 0.4, LINE_SOFT),
        ('TOPPADDING', (0, 0), (-1, -1), 7),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 7),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
    ]))
    story.append(t)
    story.append(Spacer(1, 10))
    story.append(Paragraph(
        arabic('طريقة القراءة: إن كنت مستخدماً جديداً فابدأ بالفصل الثاني فهو يشرح أول عملية كاملة '
               'خطوة بخطوة، ثم عُد إلى الفصل الثالث كمرجع عند الحاجة.'),
        S['lead']))
    story.append(PageBreak())
    return story


# ============================================================================
# تحويل كتل المحتوى إلى عناصر
# ============================================================================
def render_blocks(blocks):
    flow = []
    for kind, payload in blocks:
        if kind == 'h2':
            flow.append(KeepTogether([P(payload, 'h2')]))
        elif kind == 'h3':
            flow.append(KeepTogether([P(payload, 'h3')]))
        elif kind == 'p':
            flow.append(P(payload, 'body'))
        elif kind == 'lead':
            flow.append(P(payload, 'lead'))
            flow.append(Spacer(1, 4))
        elif kind == 'bullets':
            flow.extend(bullets(payload))
            flow.append(Spacer(1, 4))
        elif kind == 'numbered':
            flow.extend(numbered(payload))
            flow.append(Spacer(1, 4))
        elif kind == 'callout':
            title, text, tone = payload
            flow.append(note(title, text))
        elif kind == 'table':
            headers, rows = payload[0], payload[1]
            widths = payload[2] if len(payload) > 2 else None
            flow.append(table(headers, rows, widths))
            flow.append(Spacer(1, 6))
        elif kind == 'rule':
            flow.append(rule())
        elif kind == 'pagebreak':
            flow.append(PageBreak())
    return flow


# ============================================================================
# البناء
# ============================================================================
def build():
    doc = BaseDocTemplate(
        OUTPUT, pagesize=(PAGE_W, PAGE_H),
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=1.5 * cm, bottomMargin=2.6 * cm,
        title='كتيّب تعليمات Royal Fruit ERP',
        author='Royal Fruit', subject='دليل المستخدم',
    )
    frame = Frame(MARGIN, 2.6 * cm, CONTENT_W, PAGE_H - 1.5 * cm - 2.6 * cm,
                  id='main', bottomPadding=0, topPadding=0)
    cover_frame = Frame(MARGIN, 0.6 * cm, CONTENT_W, PAGE_H - 1.2 * cm, id='cover')
    doc.addPageTemplates([
        PageTemplate(id='cover', frames=[cover_frame], onPage=on_cover),
        PageTemplate(id='main', frames=[frame], onPage=on_page),
    ])

    story = []
    story += build_cover()
    story += build_toc()

    # الفصل 1
    story.append(section('1', 'نظرة عامة على النظام',
                         'افهم النظام وأدوارك قبل أن تبدأ'))
    story += render_blocks(CH1)

    # الفصل 2
    story.append(section('2', 'الطريق الكامل لأول عملية',
                         'من إنزال البضاعة الخام إلى تحصيل الفاتورة'))
    story += render_blocks(CH2)

    # الفصل 3
    story.append(section('3', 'المراجع التفصيلية لكل قسم',
                         'شرح كل فكرة في الموقع على حدة'))
    story += render_blocks(CH3)

    # الفصل 4
    story.append(section('4', 'الأدوار والأسئلة الشائعة',
                         'من يفعل ماذا، وكيف تتصرّف في المواقف العملية'))
    story += render_blocks(CH4)

    # صفحة ختامية
    story.append(Spacer(1, 1 * cm))
    story.append(rule())
    story.append(P('نهاية الكتيّب — Royal Fruit ERP', 'small'))

    doc.build(story)
    return OUTPUT


if __name__ == '__main__':
    out = build()
    size = os.path.getsize(out)
    print('MANUAL_BUILT:', out)
    print('SIZE_BYTES:', size)
