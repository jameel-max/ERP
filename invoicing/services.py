import io
from decimal import Decimal, ROUND_HALF_UP
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
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
    def generate_pdf(cls, invoice):
        """
        توليد ملف PDF احترافي للفاتورة بتصميم رويال فروت
        """
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
            fontSize=18,
            leading=22,
            textColor=colors.HexColor('#1B4D3E'),
            alignment=1
        )
        subtitle_style = ParagraphStyle(
            'SubtitleStyle',
            parent=styles['Normal'],
            fontSize=10,
            leading=14,
            textColor=colors.HexColor('#555555'),
            alignment=1
        )

        elements.append(Paragraph("ROYAL FRUIT PACKHOUSE - مشغل رويال فروت", title_style))
        elements.append(Paragraph("فاتورة حساب المشغل والتوضيب المعتمدة", subtitle_style))
        elements.append(Spacer(1, 15))

        # بيانات الفاتورة والتاجر
        info_data = [
            [f"Invoice No: {invoice.invoice_number}", f"Date: {invoice.created_at.strftime('%Y-%m-%d')}"],
            [f"Trader: {invoice.trader.name} ({invoice.trader.code})", f"Crop: {invoice.crop.name} - Year {invoice.year}"],
            [f"Packing Method: {invoice.get_packing_method_display()}", f"Status: {invoice.get_status_display()}"],
        ]
        info_table = Table(info_data, colWidths=[270, 270])
        info_table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#f8f9fa')),
            ('TEXTCOLOR', (0,0), (-1,-1), colors.HexColor('#222222')),
            ('FONTSIZE', (0,0), (-1,-1), 9),
            ('BOTTOMPADDING', (0,0), (-1,-1), 4),
            ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#e2e8f0')),
        ]))
        elements.append(info_table)
        elements.append(Spacer(1, 15))

        # جدول البنود
        items_data = [["Item Description / البيان", "Qty / الكمية", "Rate / السعر", "Total / الإجمالي"]]
        for item in invoice.items.all():
            items_data.append([
                item.description,
                f"{item.quantity}",
                f"{item.unit_price} JOD",
                f"{item.total_price} JOD"
            ])

        # الإجماليات
        items_data.append(["Packing Subtotal / مجموع أجور التوضيب", "", "", f"{invoice.packing_subtotal} JOD"])
        if invoice.box_discount_subtotal > 0:
            items_data.append(["Box Deficit Discount / خصم عجز الصناديق", "", "", f"+{invoice.box_discount_subtotal} JOD"])
        if invoice.cooling_subtotal > 0:
            items_data.append(["Cooling Service / أجرة تبريد يومية للطبالي الموضبة", "", "", f"+{invoice.cooling_subtotal} JOD"])
        if invoice.other_services > 0:
            items_data.append(["Additional Services / خدمات إضافية", "", "", f"+{invoice.other_services} JOD"])
        if invoice.general_discount > 0:
            items_data.append(["General Discount / خصم عام", "", "", f"-{invoice.general_discount} JOD"])
        items_data.append(["GRAND TOTAL / الإجمالي النهائي المستحق", "", "", f"{invoice.grand_total} JOD"])

        items_table = Table(items_data, colWidths=[280, 80, 90, 90])
        items_table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1B4D3E')),
            ('TEXTCOLOR', (0,0), (-1,0), colors.white),
            ('FONTSIZE', (0,0), (-1,0), 9),
            ('BOTTOMPADDING', (0,0), (-1,0), 6),
            ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#cccccc')),
            ('BACKGROUND', (0,-1), (-1,-1), colors.HexColor('#e8f5e9')),
            ('TEXTCOLOR', (0,-1), (-1,-1), colors.HexColor('#1B4D3E')),
            ('FONTNAME', (0,-1), (-1,-1), 'Helvetica-Bold'),
        ]))
        elements.append(items_table)
        elements.append(Spacer(1, 20))

        elements.append(Paragraph("Certified by Royal Fruit Management &bull; الدينار الأردني بثلاث خانات عشرية", subtitle_style))

        doc.build(elements)
        buffer.seek(0)
        return buffer
