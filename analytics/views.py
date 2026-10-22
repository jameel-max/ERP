import io
import json
from decimal import Decimal
from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.utils import timezone
from django.db.models import Sum, Count, Avg
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

from core.permissions import manager_required
from operations.models import ColdStore, Pallet, Trader, Crop, TruckReceipt
from operations.models import annotate_cold_store_occupancy
from packing.models import PackingSession, PackedPallet
from labor.models import Workshop, DailyLaborLog
from labor.services import LaborService
from finance.services import FinanceService
from finance.models import TreasuryAccount, Expense, Payment, Check
from invoicing.models import Invoice


@login_required
@manager_required
def analytics_dashboard(request):
    """
    لوحة المؤشرات والتحليل المالي والتشغيلي للمدير:
    - الرسوم البيانية التفاعلية (Chart.js)
    - مؤشرات المدير الستة
    - مركز التحليل الذكي للبيانات الحقيقية
    """
    kpis = FinanceService.calculate_financial_kpis()
    labor_kpis = LaborService.calculate_cost_per_kg()

    # 1. مؤشرات الرندمان لآخر عمليات التوضيب
    recent_sessions = PackingSession.objects.select_related('trader', 'crop').order_by('-started_at')[:10]

    # 2. إشغال البرادات (باستعلام مجمّع واحد بدل استعلام لكل براد — منع N+1)
    cold_stores = annotate_cold_store_occupancy(ColdStore.objects.all())
    store_occupancy = []
    for cs in cold_stores:
        store_occupancy.append({
            'name': cs.name,
            'occupied': cs.total_occupied_pallets,
            'capacity': cs.capacity_pallets or 50,
            'rate': cs.occupancy_rate or 0
        })

    # 3. توزيع المقاسات الموضبة للرسم البياني
    size_distribution = PackedPallet.objects.values('size_name').annotate(
        total_weight=Sum('net_weight_kg'),
        total_boxes=Sum('box_count')
    ).order_by('-total_weight')

    chart_sizes_labels = [s['size_name'] for s in size_distribution]
    chart_sizes_data = [float(s['total_weight'] or 0) for s in size_distribution]

    # 4. مركز التحليل الذكي للبيانات الحقيقية (اكتشاف مؤشرات الجودة والتشغيل)
    smart_insights = []

    # فحص الهالك المرتفع (رندمان أقل من 75%)
    low_randeman_sessions = [s for s in recent_sessions if s.randeman_pct and s.randeman_pct < 75 and s.status in ['COMPLETED', 'APPROVED']]
    if low_randeman_sessions:
        smart_insights.append({
            'type': 'warning',
            'icon': 'bi-exclamation-triangle-fill',
            'title': 'مؤشر هالك ورندمان منخفض',
            'desc': f'سجلت {len(low_randeman_sessions)} عملية توضيب رندمان أقل من 75% (التاجر: {low_randeman_sessions[0].trader.name} - رندمان {low_randeman_sessions[0].randeman_pct}%). يُنصح بمراجعة عيوب القطف والمزرعة.'
        })

    # فحص بضاعة راكدة في البراد (أكثر من 7 أيام) — فلترة من قاعدة البيانات بدل تحميل كل الطبالي
    stagnant_cutoff = timezone.now() - timezone.timedelta(days=7)
    stagnant_count = Pallet.objects.filter(
        status=Pallet.Status.IN_COOLING,
        entered_cooling_at__lte=stagnant_cutoff,
    ).count()
    if stagnant_count > 0:
        smart_insights.append({
            'type': 'info',
            'icon': 'bi-clock-history',
            'title': 'تنبيه إدارة المخزون (الأقدم أولاً)',
            'desc': f'يوجد {stagnant_count} طبلية تجاوزت 7 أيام داخل البرادات. يوصى بإخراجها لخطوط التوضيب لتفادي التلف.'
        })

    # فحص الذمم المدينة والتحصيل
    if kpis['net_receivables'] > 0:
        smart_insights.append({
            'type': 'primary',
            'icon': 'bi-cash-coin',
            'title': 'مؤشر التحصيل والذمم المدينة',
            'desc': f'إجمالي الذمم المستحقة على التجار: {kpis["net_receivables"]} دينار. والشيكات قيد التحصيل: {kpis["checks_under_collection"]} دينار.'
        })

    context = {
        'kpis': kpis,
        'labor_kpis': labor_kpis,
        'recent_sessions': recent_sessions,
        'store_occupancy': store_occupancy,
        'smart_insights': smart_insights,
        'chart_sizes_labels': json.dumps(chart_sizes_labels, ensure_ascii=False),
        'chart_sizes_data': json.dumps(chart_sizes_data),
    }
    return render(request, 'analytics/dashboard.html', context)


@login_required
@manager_required
def export_manager_daily_excel(request):
    """
    تصدير التقرير اليومي الشامل للمدير إلى Excel
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Royal Fruit Daily Report"
    ws.sheet_view.rightToLeft = True

    # تنسيق العناوين
    header_fill = PatternFill(start_color="1B4D3E", end_color="1B4D3E", fill_type="solid")
    header_font = Font(name="Cairo", size=11, bold=True, color="FFFFFF")
    title_font = Font(name="Cairo", size=14, bold=True, color="1B4D3E")

    ws['A1'] = "تقرير المدير العام والتشغيل اليومي - رويال فروت (Royal Fruit)"
    ws['A1'].font = title_font
    ws['A2'] = f"تاريخ التقرير: {timezone.now().strftime('%Y-%m-%d %H:%M')} (توقيت عمّان)"

    # جدول المؤشرات المالية
    kpis = FinanceService.calculate_financial_kpis()
    ws.append([])
    ws.append(["المؤشر المالي والتشغيلي", "القيمة المحسوبة (دينار أردني)"])
    ws.append(["إجمالي الإيرادات المكتسبة (المعتمدة)", float(kpis['earned_revenue'])])
    ws.append(["إجمالي المصاريف التشغيلية (الاستحقاق)", float(kpis['operating_expenses'])])
    ws.append(["النتيجة التشغيلية الأولية", float(kpis['operating_result'])])
    ws.append(["المقبوضات النقدية الفعلية", float(kpis['actual_cash_in'])])
    ws.append(["المدفوعات النقدية الفعلية", float(kpis['actual_cash_out'])])
    ws.append(["صافي التدفق النقدي الفعلي", float(kpis['net_cash_flow'])])
    ws.append(["صافي الذمم المدينة على التجار", float(kpis['net_receivables'])])
    ws.append(["رصيد الخزينة والبنوك الفعلي", float(kpis['total_treasury_balance'])])

    # ترويسة الجدول
    for col in range(1, 3):
        ws.cell(row=4, column=col).fill = header_fill
        ws.cell(row=4, column=col).font = header_font

    ws.column_dimensions['A'].width = 38
    ws.column_dimensions['B'].width = 25

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)

    filename = f"Royal_Fruit_Daily_Report_{timezone.now().strftime('%Y%m%d')}.xlsx"
    response = HttpResponse(buffer.getvalue(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


def _build_daily_report_context(request):
    """
    تجميع بيانات التقرير اليومي الشامل للمدير من الحركات الحقيقية فقط.
    لا يتم توليد أي أرقام تجريبية.
    """
    now = timezone.localtime(timezone.now())
    today = now.date()

    kpis = FinanceService.calculate_financial_kpis()
    labor_kpis = LaborService.calculate_cost_per_kg()

    # حركات اليوم
    today_receipts = TruckReceipt.objects.filter(created_at__date=today).select_related('trader', 'crop', 'cold_store')
    today_sessions = PackingSession.objects.filter(started_at__date=today).select_related('trader', 'crop')
    today_expenses = Expense.objects.filter(created_at__date=today).exclude(status=Expense.Status.CANCELLED).select_related('treasury_account')
    today_payments = Payment.objects.filter(payment_date=today).select_related('trader', 'treasury_account')

    # الشيكات قيد التحصيل
    checks_under_collection = Check.objects.filter(
        status__in=[Check.Status.REGISTERED, Check.Status.UNDER_COLLECTION]
    ).select_related('trader')

    # إشغال البرادات (باستعلام مجمّع واحد بدل استعلام لكل براد — منع N+1)
    store_occupancy = []
    for cs in annotate_cold_store_occupancy(ColdStore.objects.all()):
        store_occupancy.append({
            'name': cs.name,
            'occupied': cs.total_occupied_pallets,
            'capacity': cs.capacity_pallets or 0,
            'rate': cs.occupancy_rate or 0,
        })

    # التنبيهات الذكية
    alerts = []
    low_randeman = [s for s in PackingSession.objects.filter(
        started_at__date=today,
        status__in=[PackingSession.Status.COMPLETED, PackingSession.Status.APPROVED]
    ) if s.randeman_pct and s.randeman_pct < 75]
    if low_randeman:
        alerts.append(f"يوجد {len(low_randeman)} عملية توضيب اليوم برندمان أقل من 75% — تتطلب مراجعة فورية.")

    # فلترة من قاعدة البيانات بدل تحميل كل الطبالي ثم حساب الأقدم أولاً في بايثون
    stagnant_cutoff = timezone.now() - timezone.timedelta(days=7)
    stagnant = list(Pallet.objects.filter(
        status='IN_COOLING',
        entered_cooling_at__lte=stagnant_cutoff,
    ).select_related('trader', 'cold_store')[:200])
    if stagnant:
        alerts.append(f"يوجد {len(stagnant)} طبلية تجاوزت 7 أيام في البرادات — يوصى بإخراجها (الأقدم أولاً).")

    if today_receipts.exists():
        alerts.append(f"تم استلام {today_receipts.count()} سيارة اليوم بإجمالي طبالي مناسب للتخزين.")
    if not alerts:
        alerts.append("لا توجد تنبيهات تشغيلية حرجة لهذا اليوم بناءً على البيانات المسجلة.")

    # اليوم التالي/السابق للتنقل
    return {
        'report_date': now,
        'today': today,
        'kpis': kpis,
        'labor_kpis': labor_kpis,
        'today_receipts': today_receipts,
        'today_sessions': today_sessions,
        'today_expenses': today_expenses,
        'today_payments': today_payments,
        'checks_under_collection': checks_under_collection,
        'store_occupancy': store_occupancy,
        'alerts': alerts,
    }


@login_required
@manager_required
def manager_daily_report(request):
    """
    التقرير اليومي الشامل للمدير (نسخة HTML قابلة للطباعة من المتصفح).
    """
    context = _build_daily_report_context(request)
    return render(request, 'analytics/manager_daily_report.html', context)


@login_required
@manager_required
def manager_daily_pdf(request):
    """
    التقرير اليومي الشامل للمدير بصيغة PDF (reportlab) لطباعة/أرشفة رسمية.
    """
    context = _build_daily_report_context(request)
    kpis = context['kpis']

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        rightMargin=30, leftMargin=30, topMargin=30, bottomMargin=30
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('T', parent=styles['Heading1'], fontSize=16, leading=20,
                                 textColor=colors.HexColor('#1B4D3E'), alignment=1)
    sub_style = ParagraphStyle('S', parent=styles['Normal'], fontSize=9, leading=13,
                               textColor=colors.HexColor('#555555'), alignment=1)

    elements = [Paragraph("ROYAL FRUIT PACKHOUSE - Royal Fruit Daily Manager Report", title_style)]
    elements.append(Paragraph(f"Date: {context['today']} (Amman Time)", sub_style))
    elements.append(Spacer(1, 14))

    # مؤشرات مالية
    fin_rows = [
        ["Financial Indicator", "Value (JD)"],
        ["Earned Revenue", f"{float(kpis['earned_revenue']):.3f}"],
        ["Operating Expenses", f"{float(kpis['operating_expenses']):.3f}"],
        ["Operating Result", f"{float(kpis['operating_result']):.3f}"],
        ["Actual Cash In", f"{float(kpis['actual_cash_in']):.3f}"],
        ["Actual Cash Out", f"{float(kpis['actual_cash_out']):.3f}"],
        ["Net Cash Flow", f"{float(kpis['net_cash_flow']):.3f}"],
        ["Net Receivables", f"{float(kpis['net_receivables']):.3f}"],
        ["Treasury Balance", f"{float(kpis['total_treasury_balance']):.3f}"],
        ["Checks Under Collection", f"{float(kpis['checks_under_collection']):.3f}"],
    ]
    fin_table = Table(fin_rows, colWidths=[300, 220])
    fin_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1B4D3E')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
        ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
    ]))
    elements.append(fin_table)
    elements.append(Spacer(1, 12))

    # حركات اليوم
    op_rows = [
        ["Daily Activity", "Count / Value"],
        ["Truck Receipts Today", str(context['today_receipts'].count())],
        ["Packing Sessions Today", str(context['today_sessions'].count())],
        ["Expenses Today (JD)", f"{sum((e.amount for e in context['today_expenses']), Decimal('0')):.3f}"],
        ["Payments Today (JD)", f"{sum((p.amount for p in context['today_payments']), Decimal('0')):.3f}"],
        ["Cost / Kg (JD)", str(context['labor_kpis'].get('cost_per_kg', '—'))],
    ]
    op_table = Table(op_rows, colWidths=[300, 220])
    op_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1d4ed8')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
        ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
    ]))
    elements.append(op_table)
    elements.append(Spacer(1, 12))

    # التنبيهات
    elements.append(Paragraph("Operational Alerts", sub_style))
    nc = ParagraphStyle('NC', parent=styles['Normal'], fontSize=9, leading=13)
    for a in context['alerts']:
        elements.append(Paragraph(f"- {a}", nc))
    elements.append(Spacer(1, 10))
    elements.append(Paragraph("Generated by Royal Fruit Packhouse System", sub_style))

    doc.build(elements)
    buffer.seek(0)
    filename = f"Royal_Fruit_Manager_Report_{context['today'].strftime('%Y%m%d')}.pdf"
    response = HttpResponse(buffer.getvalue(), content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response
