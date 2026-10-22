from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from core.attachments import save_uploaded_images
from core.models import AuditLog, SystemSetting
from core.pagination import paginate
from core.permissions import accountant_required

from .forms import ShipmentForm, ShipmentCostForm, ShipmentPaymentForm, ShipmentDocumentForm
from .models import TradeShipment, ShipmentCost, ShipmentPayment, ShipmentDocument


def _save_form_images(request, shipment):
    """يحفظ الصور المرفوعة من نموذج الشحنة (تحديد متعدد) بعد حفظ الشحنة."""
    files = request.FILES.getlist('images')
    if not files:
        return
    caption = (request.POST.get('attachment_caption') or '').strip()
    saved, rejected = save_uploaded_images(shipment, files, caption=caption, user=request.user)
    if saved:
        AuditLog.log(request.user, 'UPLOAD_ATTACHMENTS', shipment,
                     reason=f'إرفاق {saved} صورة بالشحنة {shipment.reference_number}')
        message = f'تم إرفاق {saved} صورة بالشحنة.'
        if rejected:
            message += f' (تم تجاهل {rejected} ملفاً غير صالح أو تجاوز الحد).'
        messages.success(request, message)
    elif rejected:
        messages.error(request, 'لم يتم إرفاق الصور: تأكد أن الملفات صور صالحة وبحجم مناسب ولم تتجاوز الحد الأقصى.')


@login_required
def shipment_list(request):
    """قائمة شحنات الاستيراد والتصدير مع مؤشرات سريعة وفلترة."""
    direction = request.GET.get('direction', '').strip()
    status = request.GET.get('status', '').strip()
    query = request.GET.get('q', '').strip()

    shipments = TradeShipment.objects.select_related('crop', 'created_by').prefetch_related('costs', 'payments').order_by('-created_at')
    if direction in dict(TradeShipment.Direction.choices):
        shipments = shipments.filter(direction=direction)
    if status in dict(TradeShipment.Status.choices):
        shipments = shipments.filter(status=status)
    if query:
        shipments = shipments.filter(
            Q(reference_number__icontains=query)
            | Q(goods_description__icontains=query)
            | Q(counterparty_name__icontains=query)
            | Q(origin__icontains=query)
            | Q(destination__icontains=query)
        )

    shipments = list(shipments)

    kpis = {
        'count': len(shipments),
        'imports': sum(1 for s in shipments if s.direction == TradeShipment.Direction.IMPORT),
        'exports': sum(1 for s in shipments if s.direction == TradeShipment.Direction.EXPORT),
        'in_transit': sum(1 for s in shipments if s.status == TradeShipment.Status.IN_TRANSIT),
        'total_cost': sum((s.total_cost_jod for s in shipments), Decimal('0')),
        'total_profit': sum((s.profit_jod for s in shipments if s.profit_jod is not None), Decimal('0')),
        'shortages': sum(1 for s in shipments if s.has_shortage),
        'due_suppliers': sum(
            (s.counterparty_balance_jod for s in shipments if s.direction == TradeShipment.Direction.IMPORT),
            Decimal('0'),
        ),
        'due_from_customers': sum(
            (s.counterparty_balance_jod for s in shipments if s.direction == TradeShipment.Direction.EXPORT),
            Decimal('0'),
        ),
    }

    page = paginate(request, shipments)
    return render(request, 'trade/shipment_list.html', {
        **page, 'shipments': page['page_obj'],
        'kpis': kpis,
        'direction': direction,
        'status': status,
        'query': query,
        'direction_choices': TradeShipment.Direction.choices,
        'status_choices': TradeShipment.Status.choices,
        'active_nav': 'trade',
    })


@login_required
@accountant_required
def shipment_create(request):
    initial = {}
    if request.GET.get('direction') in dict(TradeShipment.Direction.choices):
        initial['direction'] = request.GET.get('direction')
    initial['currency'] = TradeShipment.Currency.JOD
    # سعر الصرف المبدئي من إعدادات النظام (لا يُفترض داخل الشيفرة)
    initial['exchange_rate'] = SystemSetting.get_settings().default_exchange_rate

    if request.method == 'POST':
        form = ShipmentForm(request.POST, request.FILES)
        if form.is_valid():
            with transaction.atomic():
                shipment = form.save(commit=False)
                shipment.reference_number = TradeShipment.generate_reference(shipment.direction)
                shipment.created_by = request.user
                shipment.save()
            AuditLog.log(request.user, 'CREATE_TRADE_SHIPMENT', shipment,
                         reason=f'تسجيل شحنة {shipment.get_direction_display()} {shipment.reference_number}')
            _save_form_images(request, shipment)
            messages.success(request, f'تم تسجيل الشحنة {shipment.reference_number} بنجاح.')
            return redirect('trade_shipment_detail', pk=shipment.pk)
    else:
        form = ShipmentForm(initial=initial)

    return render(request, 'trade/shipment_form.html', {
        'form': form,
        'title': 'تسجيل شحنة استيراد / تصدير جديدة',
        'active_nav': 'trade',
    })


@login_required
@accountant_required
def shipment_edit(request, pk):
    shipment = get_object_or_404(TradeShipment, pk=pk)
    if request.method == 'POST':
        form = ShipmentForm(request.POST, request.FILES, instance=shipment)
        if form.is_valid():
            with transaction.atomic():
                shipment = form.save()
            AuditLog.log(request.user, 'UPDATE_TRADE_SHIPMENT', shipment,
                         reason=f'تعديل شحنة {shipment.reference_number}')
            _save_form_images(request, shipment)
            messages.success(request, 'تم حفظ تعديلات الشحنة.')
            return redirect('trade_shipment_detail', pk=shipment.pk)
    else:
        form = ShipmentForm(instance=shipment)

    return render(request, 'trade/shipment_form.html', {
        'form': form,
        'title': f'تعديل الشحنة {shipment.reference_number}',
        'shipment': shipment,
        'active_nav': 'trade',
    })


@login_required
def shipment_detail(request, pk):
    shipment = get_object_or_404(
        TradeShipment.objects.select_related('crop', 'created_by').prefetch_related(
            'costs', 'payments__created_by', 'documents'
        ),
        pk=pk,
    )
    return render(request, 'trade/shipment_detail.html', {
        'shipment': shipment,
        'cost_form': ShipmentCostForm(),
        'payment_form': ShipmentPaymentForm(initial={'kind': (
            ShipmentPayment.Kind.RECEIVED if shipment.direction == TradeShipment.Direction.EXPORT
            else ShipmentPayment.Kind.PAID
        )}),
        'document_form': ShipmentDocumentForm(),
        'cost_kind_choices': ShipmentCost.Kind.choices,
        'active_nav': 'trade',
    })


@login_required
@accountant_required
def shipment_cost_create(request, pk):
    shipment = get_object_or_404(TradeShipment, pk=pk)
    if request.method == 'POST':
        form = ShipmentCostForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                cost = form.save(commit=False)
                cost.shipment = shipment
                cost.save()
            AuditLog.log(request.user, 'ADD_TRADE_COST', cost,
                         reason=f'إضافة تكلفة على الشحنة {shipment.reference_number}')
            messages.success(request, 'تمت إضافة التكلفة بنجاح.')
        else:
            messages.error(request, 'تعذّرت إضافة التكلفة: تحقّق من القيم المدخلة.')
    return redirect('trade_shipment_detail', pk=shipment.pk)


@login_required
@accountant_required
def shipment_payment_create(request, pk):
    shipment = get_object_or_404(TradeShipment, pk=pk)
    if request.method == 'POST':
        form = ShipmentPaymentForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                payment = form.save(commit=False)
                payment.shipment = shipment
                payment.created_by = request.user
                payment.save()
            AuditLog.log(request.user, 'ADD_TRADE_PAYMENT', payment,
                         reason=f'تسجيل دفعة على الشحنة {shipment.reference_number}')
            messages.success(request, 'تم تسجيل الدفعة بنجاح.')
        else:
            messages.error(request, 'تعذّر تسجيل الدفعة: تحقّق من القيم المدخلة.')
    return redirect('trade_shipment_detail', pk=shipment.pk)


@login_required
@accountant_required
def shipment_document_create(request, pk):
    shipment = get_object_or_404(TradeShipment, pk=pk)
    if request.method == 'POST':
        form = ShipmentDocumentForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                doc = form.save(commit=False)
                doc.shipment = shipment
                doc.save()
            AuditLog.log(request.user, 'ADD_TRADE_DOCUMENT', doc,
                         reason=f'إضافة مستند على الشحنة {shipment.reference_number}')
            messages.success(request, 'تمت إضافة المستند بنجاح.')
        else:
            messages.error(request, 'تعذّرت إضافة المستند: تحقّق من القيم المدخلة.')
    return redirect('trade_shipment_detail', pk=shipment.pk)


@login_required
@accountant_required
def shipment_status_update(request, pk):
    shipment = get_object_or_404(TradeShipment, pk=pk)
    new_status = request.POST.get('status', '')
    if request.method == 'POST' and new_status in dict(TradeShipment.Status.choices):
        shipment.status = new_status
        shipment.save(update_fields=['status', 'updated_at'])
        AuditLog.log(request.user, 'UPDATE_TRADE_STATUS', shipment,
                     reason=f'تحديث حالة الشحنة إلى {shipment.get_status_display()}')
        messages.success(request, f'تم تحديث حالة الشحنة إلى: {shipment.get_status_display()}.')
    else:
        messages.error(request, 'حالة غير صالحة.')
    return redirect('trade_shipment_detail', pk=shipment.pk)
