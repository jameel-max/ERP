import io
from decimal import Decimal, ROUND_HALF_UP
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image as ReportLabImage
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

from core.models import SystemSetting, AuditLog
from .models import Invoice, InvoiceItem
from packing.models import PackingSession, PackedPallet, PackingSessionPackagingCharge
from boxes.models import TraderBoxBalance


class InvoiceService:
    """
    خدمة احتساب الفواتير وربطها بعمليات التوضيب وحسابات الصناديق وتوليد الـ PDF
    """
    @classmethod
    @transaction.atomic
    def sync_invoice_with_operations(cls, invoice, user=None):
        """
        تحديث الفاتورة من عمليات التوضيب وعجز الصناديق الفعلي
        """
        if invoice.status == Invoice.Status.APPROVED:
            return invoice

        # 1. جلب عمليات التوضيب المكتملة أو المعتمدة لنفس التاجر والبضاعة والسنة
        sessions = PackingSession.objects.filter(
            trader=invoice.trader,
            crop=invoice.crop,
            started_at__year=invoice.year,
            status__in=[PackingSession.Status.COMPLETED, PackingSession.Status.APPROVED]
        )

        total_packed_kg = sum(s.output_packed_kg for s in sessions)
        invoice.total_packed_kg = total_packed_kg

        # حذف البنود القديمة غير اليدوية
        invoice.items.all().delete()

        # إضافة بند أجور التوضيب
        if total_packed_kg > 0:
            InvoiceItem.objects.create(
                invoice=invoice,
                item_type=InvoiceItem.ItemType.PACKING,
                description=f"أجور توضيب {invoice.crop.name} ({invoice.get_packing_method_display()}) شامل التغليف والطبالي",
                quantity=total_packed_kg,
                unit_price=invoice.packing_rate_snapshot
            )

        # 2. احتساب عجز الصناديق في عهدة التاجر
        box_balances = TraderBoxBalance.objects.filter(trader=invoice.trader, net_balance__gt=0)
        total_box_deduction = Decimal('0.000')

        for bal in box_balances:
            price = bal.box_type.discount_price
            val = (Decimal(bal.net_balance) * price).quantize(Decimal('0.001'))
            total_box_deduction += val

            InvoiceItem.objects.create(
                invoice=invoice,
                item_type=InvoiceItem.ItemType.BOX_DEDUCTION,
                description=f"خصم عجز صناديق فارغة: {bal.box_type.name} (عدد {bal.net_balance} صندوق)",
                quantity=Decimal(bal.net_balance),
                unit_price=price
            )

        invoice.box_discount_subtotal = total_box_deduction

        # 3. أجرة خدمة التبريد اليومية للطبالي الموضبة (عدد أيام التبريد × الأجرة اليومية المعتمدة)
        cooling_rate = SystemSetting.get_settings().packed_cooling_daily_rate or Decimal('0.000')
        cooling_pallets = PackedPallet.objects.filter(
            trader=invoice.trader,
            crop=invoice.crop,
            created_at__year=invoice.year,
        ).exclude(cooling_logs__isnull=True).distinct().prefetch_related('cooling_logs')

        cooling_days = 0
        cooling_total = Decimal('0.000')
        for p in cooling_pallets:
            days = p.cooling_days_total
            if days <= 0:
                continue
            cooling_days += days
            cooling_total += (Decimal(days) * cooling_rate).quantize(Decimal('0.001'), rounding=ROUND_HALF_UP)

        if cooling_days > 0 and cooling_rate > 0:
            InvoiceItem.objects.create(
                invoice=invoice,
                item_type=InvoiceItem.ItemType.COOLING,
                description=f"أجرة تبريد يومية للطبالي الموضبة ({cooling_days} يوم-طبلية)",
                quantity=Decimal(cooling_days),
                unit_price=cooling_rate,
            )
        invoice.cooling_subtotal = cooling_total

        # 4. خصم أصناف التغليف العادية المستخدمة في التوضيب (من كل مجموعات التغليف)
        # المزامنة قابلة للتكرار: نعيد ترحيل القيود غير المرحّلة + المرحّلة لهذه الفاتورة نفسها،
        # فلا تُفقد البنود عند إعادة الحساب ولا يُضاعف المبلغ عند الترحيل المتكرر.
        packaging_charges = list(PackingSessionPackagingCharge.objects.filter(
            trader=invoice.trader,
            session__crop=invoice.crop,
            session__started_at__year=invoice.year,
        ).filter(Q(is_posted=False) | Q(posted_invoice=invoice)))
        packaging_total = Decimal('0.000')
        newly_posted = []
        for charge in packaging_charges:
            if charge.total_amount <= 0:
                continue
            packaging_total += charge.total_amount
            InvoiceItem.objects.create(
                invoice=invoice,
                item_type=InvoiceItem.ItemType.SERVICE,
                description=(
                    f"خصم أصناف تغليف على التاجر — توضيب {charge.session.session_code} "
                    f"({charge.session.crop.name})"
                ),
                quantity=Decimal('1.000'),
                unit_price=charge.total_amount,
            )
            if not charge.is_posted:
                newly_posted.append(charge.pk)
        if newly_posted:
            PackingSessionPackagingCharge.objects.filter(pk__in=newly_posted).update(
                is_posted=True, posted_invoice=invoice, posted_at=timezone.now()
            )
            AuditLog.log(
                user=user,
                action='POST_PACKAGING_CHARGES',
                instance=invoice,
                reason='ترحيل خصومات أصناف التغليف إلى فاتورة التاجر',
                details={
                    'invoice_number': invoice.invoice_number,
                    'charges_count': len(newly_posted),
                    'packaging_total': str(packaging_total),
                },
            )
        invoice.other_services = packaging_total.quantize(Decimal('0.001'))

        invoice.calculate_totals()
        invoice.save()

        AuditLog.log(
            user=user,
            action='SYNC_INVOICE_OPERATIONS',
            instance=invoice,
            reason='مزامنة أوزان التوضيب وخصومات الصناديق وأجرة التبريد وأصناف التغليف في الفاتورة',
            details={
                'total_packed_kg': str(invoice.total_packed_kg),
                'cooling_days': cooling_days,
                'cooling_subtotal': str(cooling_total),
                'packaging_total': str(packaging_total),
                'grand_total': str(invoice.grand_total)
            }
        )
        return invoice

    @classmethod
    def _pdf_item_label(cls, item):
        """ترجمة نوع البند إلى تسمية عربية واضحة تُعرض في جدول بنود PDF."""
        mapping = {
            InvoiceItem.ItemType.PACKING: 'أجور توضيب',
            InvoiceItem.ItemType.BOX_DEDUCTION: 'بدل عجز صناديق',
            InvoiceItem.ItemType.COOLING: 'أجرة تبريد',
            InvoiceItem.ItemType.SERVICE: 'خدمات إضافية',
            InvoiceItem.ItemType.DISCOUNT: 'خصم',
        }
        return mapping.get(item.item_type, item.get_item_type_display())

    @classmethod
    def generate_pdf(cls, invoice):
        """
        توليد ملف PDF احترافي للفاتورة بتصميم رويال فروت — بدعم عربي كامل (RTL).

        يُستخدم خط Amiri العربي المحلي (static/fonts) بدل Helvetica، مع تشكيل
        الحروف العربية وترتيب الاتجاه، حتى لا تظهر مربعات سوداء.

        لا يتغيّر أي رقم أو قاعدة حساب: كل القيم تُقرأ من الفاتورة المحفوظة كما هي.
        """
        from core.pdf_fonts import (
            register_arabic_fonts, arabic, ARABIC_FONT, ARABIC_FONT_BOLD,
        )

        has_arabic_font = register_arabic_fonts()
        base_font = ARABIC_FONT if has_arabic_font else 'Helvetica'
        bold_font = ARABIC_FONT_BOLD if has_arabic_font else 'Helvetica-Bold'

        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            rightMargin=30,
            leftMargin=30,
            topMargin=30,
            bottomMargin=30
        )
        elements = []
        styles = getSampleStyleSheet()

        title_style = ParagraphStyle(
            'TitleStyle',
            parent=styles['Heading1'],
            fontName=bold_font,
            fontSize=18,
            leading=24,
            textColor=colors.HexColor('#1B4D3E'),
            alignment=1
        )
        subtitle_style = ParagraphStyle(
            'SubtitleStyle',
            parent=styles['Normal'],
            fontName=base_font,
            fontSize=10,
            leading=16,
            textColor=colors.HexColor('#555555'),
            alignment=1
        )
        cell_style = ParagraphStyle(
            'CellStyle',
            parent=styles['Normal'],
            fontName=base_font,
            fontSize=9,
            leading=13,
            textColor=colors.HexColor('#222222'),
        )
        cell_bold_style = ParagraphStyle(
            'CellBoldStyle',
            parent=cell_style,
            fontName=bold_font,
        )
        head_style = ParagraphStyle(
            'HeadStyle',
            parent=cell_style,
            fontName=bold_font,
            textColor=colors.white,
            alignment=1,
        )
        num_style = ParagraphStyle(
            'NumStyle',
            parent=cell_style,
            alignment=2,  # محاذاة الأرقام لليسار داخل الخلية دون انعكاس
        )
        num_bold_style = ParagraphStyle(
            'NumBoldStyle',
            parent=cell_bold_style,
            alignment=2,
        )

        # ------- الترويسة والشعار -------
        brand_text = [
            Paragraph(arabic('مشغل رويال فروت'), title_style),
            Paragraph(arabic('ROYAL FRUIT PACKHOUSE'), subtitle_style),
            Paragraph(arabic('فاتورة حساب المشغل والتوضيب المعتمدة'), subtitle_style),
        ]
        logo_path = settings.BASE_DIR / 'static' / 'images' / 'royal-fruit-logo.jpg'
        if logo_path.is_file():
            brand_logo = ReportLabImage(str(logo_path), width=48, height=48)
            brand_header = Table([[brand_text, brand_logo]], colWidths=[475, 60])
            brand_header.setStyle(TableStyle([
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
            ]))
            elements.append(brand_header)
        else:
            elements.extend(brand_text)
        elements.append(Spacer(1, 12))

        # ------- بيانات الفاتورة والتاجر -------
        # الأرقام والأكواد تُعرض بترتيبها الأصلي (لا تُشكَّل) حتى لا تنعكس.
        def _pair(label_ar, value):
            return Paragraph(arabic(label_ar), cell_style), Paragraph(str(value), num_style)

        info_rows = [
            [Paragraph(arabic('رقم الفاتورة'), cell_bold_style), Paragraph(str(invoice.invoice_number), num_bold_style),
             Paragraph(arabic('التاريخ'), cell_bold_style), Paragraph(invoice.created_at.strftime('%Y-%m-%d'), num_style)],
            [Paragraph(arabic('التاجر'), cell_bold_style), Paragraph(arabic(f'{invoice.trader.name} ({invoice.trader.code})'), cell_style),
             Paragraph(arabic('نوع البضاعة'), cell_bold_style), Paragraph(arabic(f'{invoice.crop.name} — {invoice.year}'), cell_style)],
            [Paragraph(arabic('طريقة التوضيب'), cell_bold_style), Paragraph(arabic(invoice.get_packing_method_display()), cell_style),
             Paragraph(arabic('حالة الفاتورة'), cell_bold_style), Paragraph(arabic(invoice.get_status_display()), cell_style)],
        ]
        info_table = Table(info_rows, colWidths=[70, 200, 80, 190])
        info_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#f8f9fa')),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('TOPPADDING', (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ]))
        elements.append(info_table)
        elements.append(Spacer(1, 14))

        # ------- جدول البنود (البنود فقط كما هي محفوظة) -------
        items_rows = [[
            Paragraph(arabic('البيان'), head_style),
            Paragraph(arabic('النوع'), head_style),
            Paragraph(arabic('الكمية / الوزن'), head_style),
            Paragraph(arabic('سعر الوحدة (د.أ)'), head_style),
            Paragraph(arabic('الإجمالي (د.أ)'), head_style),
        ]]
        for item in invoice.items.all():
            items_rows.append([
                Paragraph(arabic(item.description), cell_style),
                Paragraph(arabic(cls._pdf_item_label(item)), cell_style),
                Paragraph(f"{item.quantity}", num_style),
                Paragraph(f"{item.unit_price}", num_style),
                Paragraph(f"{item.total_price}", num_bold_style),
            ])
        if len(items_rows) == 1:
            items_rows.append([Paragraph(arabic('لا توجد بنود على هذه الفاتورة بعد.'), cell_style), '', '', '', ''])

        items_table = Table(items_rows, colWidths=[210, 80, 80, 80, 90], repeatRows=1)
        items_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1B4D3E')),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#cccccc')),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('TOPPADDING', (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ]))
        elements.append(items_table)
        elements.append(Spacer(1, 14))

        # ------- جدول المجاميع (منفصل عن البنود) -------
        # ملاحظة: بدل عجز الصناديق مبلغ يُطالَب به التاجر ويُضاف في calculate_totals،
        # لذا يُعرض بعلامة (+) وبتسمية «بدل عجز صناديق» لا «خصم».
        summary_rows = [
            [Paragraph(arabic('مجموع أجور التوضيب'), cell_style),
             Paragraph(f"{invoice.packing_subtotal}", num_bold_style)],
        ]
        if invoice.box_discount_subtotal > 0:
            summary_rows.append([
                Paragraph(arabic('بدل عجز صناديق فارغة (مبلغ يُطالَب به التاجر)'), cell_style),
                Paragraph(f"+ {invoice.box_discount_subtotal}", num_style),
            ])
        if invoice.cooling_subtotal > 0:
            summary_rows.append([
                Paragraph(arabic('أجرة تبريد يومية للطبالي الموضبة'), cell_style),
                Paragraph(f"+ {invoice.cooling_subtotal}", num_style),
            ])
        if invoice.other_services > 0:
            summary_rows.append([
                Paragraph(arabic('خدمات إضافية (أصناف تغليف وغيرها)'), cell_style),
                Paragraph(f"+ {invoice.other_services}", num_style),
            ])
        if invoice.general_discount > 0:
            summary_rows.append([
                Paragraph(arabic('خصم عام'), cell_style),
                Paragraph(f"- {invoice.general_discount}", num_style),
            ])
        summary_rows.append([
            Paragraph(arabic('الإجمالي النهائي المستحق (دينار أردني)'), cell_bold_style),
            Paragraph(f"{invoice.grand_total}", num_bold_style),
        ])
        if invoice.paid_amount > 0:
            summary_rows.append([
                Paragraph(arabic('المبلغ المسدد'), cell_style),
                Paragraph(f"- {invoice.paid_amount}", num_style),
            ])
            summary_rows.append([
                Paragraph(arabic('المبلغ المتبقي'), cell_bold_style),
                Paragraph(f"{invoice.remaining_balance}", num_bold_style),
            ])

        summary_table = Table(summary_rows, colWidths=[400, 140])
        summary_table.setStyle(TableStyle([
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#cccccc')),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('TOPPADDING', (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
            ('BACKGROUND', (0, len(summary_rows) - 1), (-1, len(summary_rows) - 1), colors.HexColor('#e8f5e9')),
        ]))
        elements.append(summary_table)
        elements.append(Spacer(1, 22))

        # ------- التوقيع والذيل -------
        sign_table = Table(
            [[Paragraph(arabic('توقيع المحاسب'), cell_style), Paragraph(arabic('توقيع إدارة المشغل'), cell_style)]],
            colWidths=[270, 270],
        )
        sign_table.setStyle(TableStyle([
            ('LINEABOVE', (0, 0), (-1, 0), 0.5, colors.HexColor('#999999')),
            ('TOPPADDING', (0, 0), (-1, 0), 12),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ]))
        elements.append(sign_table)
        elements.append(Spacer(1, 12))

        elements.append(Paragraph(
            arabic('معتمد من إدارة رويال فروت — العملة: الدينار الأردني بثلاث خانات عشرية'),
            subtitle_style,
        ))

        doc.build(elements)
        buffer.seek(0)
        return buffer
