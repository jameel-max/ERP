from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from django.db import transaction
from django.db.models import Q
from django.core.exceptions import ValidationError
from django.http import HttpResponse

from core.permissions import accountant_required, manager_required
from core.models import AuditLog, SystemSetting
from core.pagination import paginate
from operations.models import Trader, Crop, TraderPackingRate, TruckReceipt
from .models import Invoice, InvoiceItem
from .forms import InvoiceForm, LinkTruckToInvoiceForm
from .services import InvoiceService


@login_required
def invoice_list(request):
    invoices = Invoice.objects.select_related('trader', 'crop', 'created_by').order_by('-year', '-created_at')
    year = (request.GET.get('year') or '').strip()
    trader_id = (request.GET.get('trader') or '').strip()
    query = (request.GET.get('q') or '').strip()
    if year.isdigit():
        invoices = invoices.filter(year=year)
    else:
        year = ''
    if trader_id.isdigit():
        invoices = invoices.filter(trader_id=trader_id)
    else:
        trader_id = ''
    if query:
        invoices = invoices.filter(
            Q(invoice_number__icontains=query) | Q(trader__name__icontains=query) | Q(crop__name__icontains=query)
        )
    page = paginate(request, invoices)
    return render(request, 'invoicing/invoice_list.html', {
        **page, 'invoices': page['page_obj'], 'query': query,
        'year_filter': year or '', 'trader_filter': trader_id or '',
    })


@login_required
@accountant_required
def invoice_create(request):
    """
    إنشاء فاتورة جديدة:
    المسار: صفحة التاجر ← الفواتير لسنة 2026 ← مشمش ← إضافة سيارة
    أو يدوياً لكل تاجر + سنة + نوع بضاعة.
    """
    initial = {'year': timezone.now().year}
    if request.GET.get('trader'):
        initial['trader'] = request.GET.get('trader')
    if request.GET.get('crop'):
        initial['crop'] = request.GET.get('crop')

    if request.method == 'POST':
        form = InvoiceForm(request.POST)
        if form.is_valid():
            trader = form.cleaned_data['trader']
            year = form.cleaned_data['year']
            crop = form.cleaned_data['crop']
            method = form.cleaned_data['packing_method']

            # جلب سعر التوضيب الساري للتاجر لهذه الطريقة.
            # نستخدم تاريخ اليوم المحلي (توقيت الأعمال) لا تاريخ UTC، حتى لا يُتجاهل
            # سعر يبدأ سريانه اليوم أثناء ساعات المساء/الليل بتوقيت النظام.
            rate_obj = TraderPackingRate.objects.filter(
                trader=trader,
                method=method,
                effective_date__lte=timezone.localdate()
            ).order_by('-effective_date').first()

            # سعر التوضيب: أولاً سعر التاجر الساري، وإلا السعر الافتراضي من إعدادات النظام
            # (لا يُفترض أي سعر داخل الشيفرة — كل سعر يُضبط من صفحة إعدادات النظام).
            # لكل طريقة توضيب سعر افتراضي مستقل: مكينة / يدوي.
            if rate_obj:
                packing_rate = rate_obj.rate_per_kg
            else:
                settings_obj = SystemSetting.get_settings()
                if method == Invoice.PackingMethod.MANUAL:
                    packing_rate = settings_obj.default_manual_packing_rate_per_kg
                else:
                    packing_rate = settings_obj.default_packing_rate_per_kg

            invoice_number = Invoice.generate_invoice_number(trader, year)

            invoice = form.save(commit=False)
            invoice.invoice_number = invoice_number
            invoice.packing_rate_snapshot = packing_rate
            invoice.created_by = request.user
            invoice.calculate_totals()
            invoice.save()

            if rate_obj is None:
                messages.info(
                    request,
                    f'لا يوجد سعر توضيب ساري لهذا التاجر بهذه الطريقة، فاستُخدم السعر الافتراضي '
                    f'من إعدادات النظام: {packing_rate} دينار / كغ. يمكن تغييره من صفحة إعدادات النظام '
                    f'أو بإضافة سعر توضيب للتاجر.'
                )

            # مزامنة بنود التوضيب والصناديق تلقائياً
            InvoiceService.sync_invoice_with_operations(invoice, user=request.user)

            AuditLog.log(request.user, 'CREATE_INVOICE', invoice, reason=f'إنشاء فاتورة جديدة {invoice.invoice_number}')
            messages.success(request, f'تم إنشاء الفاتورة {invoice.invoice_number} بنجاح.')
            return redirect('invoice_detail', pk=invoice.pk)
    else:
        form = InvoiceForm(initial=initial)

    return render(request, 'invoicing/invoice_form.html', {'form': form, 'title': 'إنشاء فاتورة جديدة'})


@login_required
def invoice_detail(request, pk):
    invoice = get_object_or_404(
        Invoice.objects.select_related('trader', 'crop', 'created_by', 'approved_by').prefetch_related('trucks', 'items'),
        pk=pk
    )
    linked_trucks = invoice.trucks.select_related('box_type', 'cold_store')

    # حساب إجمالي الصافي المستلم من السيارات المرتبطة
    total_received_net = sum(t.calculated_net_weight for t in linked_trucks)

    link_form = LinkTruckToInvoiceForm(invoice)

    context = {
        'invoice': invoice,
        'linked_trucks': linked_trucks,
        'total_received_net': total_received_net,
        'link_form': link_form,
    }
    return render(request, 'invoicing/invoice_detail.html', context)


@login_required
@accountant_required
def invoice_sync(request, pk):
    """
    إعادة مزامنة الفاتورة مع التوضيب الفعلي وعجز الصناديق
    """
    invoice = get_object_or_404(Invoice, pk=pk)
    if invoice.status == Invoice.Status.APPROVED:
        messages.error(request, 'لا يمكن تعديل بنود فاتورة معتمدة نهائياً.')
        return redirect('invoice_detail', pk=invoice.pk)

    InvoiceService.sync_invoice_with_operations(invoice, user=request.user)
    messages.success(request, 'تمت مزامنة كميات التوضيب الفعلي وخصومات الصناديق بنجاح.')
    return redirect('invoice_detail', pk=invoice.pk)


@login_required
def invoice_pdf(request, pk):
    """
    توليد وتحميل الفاتورة PDF
    """
    invoice = get_object_or_404(Invoice, pk=pk)
    pdf_buffer = InvoiceService.generate_pdf(invoice)
    response = HttpResponse(pdf_buffer.getvalue(), content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="{invoice.invoice_number}.pdf"'
    return response


@login_required
@accountant_required
def invoice_link_trucks(request, pk):
    invoice = get_object_or_404(Invoice, pk=pk)
    if invoice.status == Invoice.Status.APPROVED:
        messages.error(request, 'لا يمكن تعديل سيارات فاتورة معتمدة.')
        return redirect('invoice_detail', pk=invoice.pk)

    if request.method == 'POST':
        form = LinkTruckToInvoiceForm(invoice, request.POST)
        if form.is_valid():
            selected_trucks = form.cleaned_data['trucks']
            invoice.trucks.set(selected_trucks)
            AuditLog.log(request.user, 'LINK_TRUCKS_INVOICE', invoice, reason='تحديث السيارات المرتبطة بالفاتورة')
            messages.success(request, f'تم تحديث السيارات المرتبطة بالفاتورة: {selected_trucks.count()} سيارة.')
    return redirect('invoice_detail', pk=invoice.pk)


@login_required
@manager_required
def invoice_approve(request, pk):
    """
    اعتماد الفاتورة بصلاحية المدير
    """
    invoice = get_object_or_404(Invoice, pk=pk)
    if invoice.status == Invoice.Status.APPROVED:
        messages.warning(request, 'الفاتورة معتمدة مسبقاً.')
        return redirect('invoice_detail', pk=invoice.pk)

    invoice.status = Invoice.Status.APPROVED
    invoice.approved_by = request.user
    invoice.approved_at = timezone.now()
    invoice.save()

    AuditLog.log(request.user, 'APPROVE_INVOICE', invoice, reason='اعتماد الفاتورة نهائياً')
    messages.success(request, f'تم اعتماد الفاتورة {invoice.invoice_number} بنجاح.')
    return redirect('invoice_detail', pk=invoice.pk)
