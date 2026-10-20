from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Sum, Count, Q
from decimal import Decimal

from core.permissions import accountant_required
from core.utils import save_form_transactionally
from core.models import AuditLog
from operations.models import BoxType, Trader, TruckReceipt
from .models import (
    TraderBoxBalance, TraderBoxTransaction, FarmBoxType, FarmBoxRecord,
    EmptyBoxRecipient, EmptyBoxDelivery,
)
from .forms import (
    BoxTransactionForm, FarmBoxTypeForm, FarmBoxRecordForm,
    EmptyBoxRecipientForm, EmptyBoxDeliveryForm,
)
from .services import BoxService, EmptyBoxService


@login_required
def boxes_dashboard(request):
    """
    داشبورد جرد الصناديق الفارغة:
    - مخزون الشركة لكل نوع
    - مجموع الصناديق في عهدة التجار
    - أرصدة التجار وقيم العجز
    """
    box_types = BoxType.objects.filter(is_box=True)
    total_company_stock = box_types.aggregate(total=Sum('company_stock'))['total'] or 0

    trader_balances = TraderBoxBalance.objects.select_related('trader', 'box_type').order_by('trader__name')
    total_trader_custody = trader_balances.aggregate(total=Sum('net_balance'))['total'] or 0
    total_financial_deficit = trader_balances.aggregate(total=Sum('total_financial_value'))['total'] or Decimal('0.000')

    context = {
        'box_types': box_types,
        'total_company_stock': total_company_stock,
        'trader_balances': trader_balances,
        'total_trader_custody': total_trader_custody,
        'total_financial_deficit': total_financial_deficit,
    }
    return render(request, 'boxes/dashboard.html', context)


@login_required
def trader_boxes_statement(request, trader_id):
    """
    كشف حساب صناديق لتاجر معين:
    المأخوذ، المعَاد، والرصيد لكل نوع صندوق، مع سجل الحركات التفصيلي
    """
    trader = get_object_or_404(Trader, pk=trader_id)
    balances = TraderBoxBalance.objects.filter(trader=trader).select_related('box_type')
    transactions = TraderBoxTransaction.objects.filter(trader=trader).select_related('box_type', 'created_by').order_by('-timestamp')

    total_deficit_value = sum(b.total_financial_value for b in balances)

    context = {
        'trader': trader,
        'balances': balances,
        'transactions': transactions,
        'total_deficit_value': total_deficit_value,
    }
    return render(request, 'boxes/trader_statement.html', context)


@login_required
@accountant_required
def record_box_movement(request):
    """
    تسجيل حركة صناديق يدوياً (صرف أو إرجاع)
    """
    if request.method == 'POST':
        form = BoxTransactionForm(request.POST)
        if form.is_valid():
            try:
                BoxService.record_box_movement(
                    trader=form.cleaned_data['trader'],
                    box_type=form.cleaned_data['box_type'],
                    transaction_type=form.cleaned_data['transaction_type'],
                    quantity=form.cleaned_data['quantity'],
                    source=form.cleaned_data['source'],
                    destination=form.cleaned_data['destination'],
                    reason=form.cleaned_data['reason'],
                    user=request.user
                )
                messages.success(request, 'تم تسجيل حركة الصناديق وتحديث الأرصدة بنجاح.')
                return redirect('boxes_dashboard')
            except Exception as e:
                messages.error(request, f'خطأ: {str(e)}')
    else:
        form = BoxTransactionForm()

    return render(request, 'boxes/record_movement.html', {'form': form, 'title': 'تسجيل حركة صناديق'})


@login_required
def reverse_box_movement(request, pk):
    """
    عكس حركة صناديق بقيد تسوية موثق (بصلاحية المدير).
    """
    trans = get_object_or_404(TraderBoxTransaction, pk=pk)
    if request.method == 'POST':
        if not (request.user.is_manager or request.user.is_superuser):
            messages.error(request, 'عذراً، عكس الحركات المخزنية يتطلب صلاحية مدير النظام.')
            return redirect('boxes_dashboard')
        reason = request.POST.get('reason', '').strip() or 'عكس حركة صناديق من لوحة المدير'
        try:
            BoxService.reverse_movement(trans, reason=reason, user=request.user)
            messages.warning(request, f'تم عكس حركة الصناديق (الكمية {trans.quantity}) بقيد تسوية موثق.')
        except Exception as e:
            messages.error(request, f'خطأ: {str(e)}')
        return redirect('trader_boxes_statement', trader_id=trans.trader_id)
    return redirect('boxes_dashboard')


# ==================== القسم المستقل: الصناديق الواردة من المزرعة ====================

@login_required
def farm_boxes_dashboard(request):
    """
    لوحة الصناديق الواردة من المزرعة — قسم مستقل تماماً عن مخزون صناديق الشركة.
    كل سجل مرتبط بالتاجر صاحب السيارة، وتعرض لكل سيارة: عدد الصناديق الواردة
    وعدد الفارغة الناتجة عن التفريغ، مع ملخص لكل تاجر.
    """
    selected_trader = None
    trader_id = request.GET.get('trader')
    if trader_id:
        selected_trader = Trader.objects.filter(pk=trader_id).first()

    records = FarmBoxRecord.objects.select_related(
        'trader', 'truck_receipt', 'truck_receipt__crop', 'truck_receipt__box_type',
        'recorded_by',
    ).order_by('-created_at')
    if selected_trader:
        records = records.filter(trader=selected_trader)

    totals = records.aggregate(
        total_incoming=Sum('incoming_count'),
        total_empty=Sum('empty_count'),
    )

    # ملخص مستقل لكل تاجر: صناديقه الواردة وفارغها الناتج
    trader_totals = (
        FarmBoxRecord.objects
        .values('trader_id', 'trader__name', 'trader__code')
        .annotate(
            total_incoming=Sum('incoming_count'),
            total_empty=Sum('empty_count'),
            trucks_count=Count('truck_receipt', distinct=True),
        )
        .order_by('trader__name')
    )

    # رصيد الصناديق الفارغة لكل نوع (قسم مستقل، لا يمسّ company_stock).
    # النوع يُشتق من سيارة الاستلام نفسها، والسجلات مرتبطة بالسيارة.
    type_filter = Q()
    if selected_trader:
        type_filter = Q(truck_receipts__farm_box_record__trader=selected_trader)
    type_totals = FarmBoxType.objects.annotate(
        total_incoming=Sum('truck_receipts__farm_box_record__incoming_count', filter=type_filter),
        total_empty=Sum('truck_receipts__farm_box_record__empty_count', filter=type_filter),
    ).order_by('name')

    # السيارات التي لم يُسجَّل لها صناديق مزرعة بعد
    trucks_qs = TruckReceipt.objects.filter(
        farm_box_record__isnull=True
    ).select_related('trader', 'crop', 'box_type')
    if selected_trader:
        trucks_qs = trucks_qs.filter(trader=selected_trader)
    trucks_without_record = trucks_qs.order_by('-created_at')[:50]

    return render(request, 'boxes/farm_boxes.html', {
        'records': records,
        'total_incoming': totals['total_incoming'] or 0,
        'total_empty': totals['total_empty'] or 0,
        'type_totals': type_totals,
        'trader_totals': trader_totals,
        'selected_trader': selected_trader,
        'trucks_without_record': trucks_without_record,
    })


@login_required
@accountant_required
def farm_box_type_create(request):
    if request.method == 'POST':
        form = FarmBoxTypeForm(request.POST)
        if form.is_valid():
            obj = form.save()
            AuditLog.log(request.user, 'CREATE_FARM_BOX_TYPE', obj, reason='إضافة نوع صندوق وارد من المزرعة')
            messages.success(request, f'تمت إضافة نوع الصندوق الوارد "{obj.name}".')
            return redirect('farm_boxes_dashboard')
    else:
        form = FarmBoxTypeForm()
    return render(request, 'boxes/farm_box_type_form.html', {
        'form': form,
        'title': 'إضافة نوع صندوق وارد من المزرعة',
    })


@login_required
@accountant_required
def farm_box_type_edit(request, pk):
    obj = get_object_or_404(FarmBoxType, pk=pk)
    if request.method == 'POST':
        form = FarmBoxTypeForm(request.POST, instance=obj)
        if form.is_valid():
            obj = form.save()
            AuditLog.log(request.user, 'UPDATE_FARM_BOX_TYPE', obj, reason='تعديل نوع صندوق وارد من المزرعة')
            messages.success(request, f'تم تعديل نوع الصندوق "{obj.name}".')
            return redirect('farm_boxes_dashboard')
    else:
        form = FarmBoxTypeForm(instance=obj)
    return render(request, 'boxes/farm_box_type_form.html', {
        'form': form,
        'title': f'تعديل نوع صندوق وارد: {obj.name}',
    })


@login_required
@accountant_required
def farm_box_record_create(request, truck_pk=None):
    """
    تسجيل عدد الصناديق الواردة والسيارات الفارغة الناتجة لسيارة معينة.
    عند القدوم من صفحة السيارة يُثبَّت حقل السيارة تلقائياً.
    """
    truck = None
    if truck_pk is not None:
        truck = get_object_or_404(TruckReceipt, pk=truck_pk)
        existing = FarmBoxRecord.objects.filter(truck_receipt=truck).first()
        if existing:
            messages.info(request, f'يوجد سجل صناديق مزرعة لهذه السيارة بالفعل ({truck.lot_number}). يمكنك تعديله.')
            return redirect('farm_box_record_edit', pk=existing.pk)

    # عند القدوم من صفحة التاجر: تُحصر السيارات المتاحة بسيارات هذا التاجر
    trader = None
    if truck is None:
        trader_id = request.GET.get('trader')
        if trader_id:
            trader = Trader.objects.filter(pk=trader_id).first()

    if request.method == 'POST':
        form = FarmBoxRecordForm(request.POST, truck=truck)
        if form.is_valid():
            def _save_record():
                rec = form.save(commit=False)
                rec.recorded_by = request.user
                rec.save()
                return rec

            ok, record = save_form_transactionally(
                form, _save_record,
                'يوجد سجل صناديق مزرعة لهذه السيارة بالفعل. الرجاء تعديل السجل الموجود بدل إنشاء سجل جديد.',
            )
            if ok:
                AuditLog.log(request.user, 'CREATE_FARM_BOX_RECORD', record, reason='تسجيل صناديق مزرعة لسيارة')
                messages.success(
                    request,
                    f'تم تسجيل صناديق التاجر {record.trader.name} — السيارة {record.truck_receipt.lot_number}: '
                    f'وارد {record.incoming_count} / فارغ ناتج {record.empty_count}.'
                )
                return redirect('farm_boxes_dashboard')
    else:
        initial = {}
        if truck is not None:
            initial['incoming_count'] = truck.box_count
        form = FarmBoxRecordForm(truck=truck, trader=trader, initial=initial)

    return render(request, 'boxes/farm_box_record_form.html', {
        'form': form,
        'truck': truck,
        'trader': trader,
        'title': 'تسجيل صناديق مزرعة',
    })


@login_required
@accountant_required
def farm_box_record_edit(request, pk):
    record = get_object_or_404(FarmBoxRecord, pk=pk)
    if request.method == 'POST':
        form = FarmBoxRecordForm(request.POST, instance=record)
        if form.is_valid():
            ok, record = save_form_transactionally(
                form, form.save,
                'يوجد سجل صناديق مزرعة آخر لهذه السيارة بالفعل. الرجاء التحقق والمحاولة مرة أخرى.',
            )
            if ok:
                AuditLog.log(request.user, 'UPDATE_FARM_BOX_RECORD', record, reason='تعديل سجل صناديق مزرعة')
                messages.success(request, f'تم تعديل سجل صناديق السيارة {record.truck_receipt.lot_number}.')
                return redirect('farm_boxes_dashboard')
    else:
        form = FarmBoxRecordForm(instance=record)
    return render(request, 'boxes/farm_box_record_form.html', {
        'form': form,
        'truck': record.truck_receipt,
        'title': f'تعديل سجل صناديق: {record.truck_receipt.lot_number}',
    })


# ==================== قسم تسليم الفارغ: إرجاع إلى المزرعة أو بيعه ====================

def _empty_box_overview(selected_kind=None, selected_recipient=None):
    """
    ملخص قسم تسليم الفارغ:
    - المتاح لكل نوع = الفارغ الناتج عن التفريغ − ما سُلِّم فعلاً (إرجاعاً أو بيعاً).
    - إجماليات الإرجاع والبيع + قيمة البيع المقبوضة والمعلّقة.
    """
    available_map = EmptyBoxService.available_empty()
    type_rows = []
    for box_type in FarmBoxType.objects.filter(is_active=True).order_by('name'):
        available = available_map.get(box_type.pk, 0)
        if available == 0:
            continue
        type_rows.append({'box_type': box_type, 'available': available})

    deliveries = EmptyBoxDelivery.objects.select_related(
        'recipient', 'box_type', 'treasury_account', 'created_by', 'delivered_by',
    ).order_by('-delivery_date', '-id')
    if selected_kind:
        deliveries = deliveries.filter(kind=selected_kind)
    if selected_recipient:
        deliveries = deliveries.filter(recipient=selected_recipient)

    delivered_qs = EmptyBoxDelivery.objects.filter(status=EmptyBoxDelivery.Status.DELIVERED)
    totals = delivered_qs.aggregate(
        returned=Sum('quantity', filter=Q(kind=EmptyBoxDelivery.Kind.RETURN)),
        sold=Sum('quantity', filter=Q(kind=EmptyBoxDelivery.Kind.SALE)),
    )
    sale_amounts = delivered_qs.filter(kind=EmptyBoxDelivery.Kind.SALE).aggregate(
        total=Sum('total_amount'),
        collected=Sum(
            'total_amount',
            filter=Q(payment_method__in=[
                EmptyBoxDelivery.PaymentMethod.CASH,
                EmptyBoxDelivery.PaymentMethod.BANK_TRANSFER,
            ]),
        ),
    )
    total_sale = sale_amounts['total'] or Decimal('0.000')
    collected = sale_amounts['collected'] or Decimal('0.000')

    return {
        'available_types': type_rows,
        'deliveries': deliveries,
        'total_returned': totals['returned'] or 0,
        'total_sold': totals['sold'] or 0,
        'total_sale_amount': total_sale,
        'total_collected_amount': collected,
        'total_receivable_amount': total_sale - collected,
        'recipients': EmptyBoxRecipient.objects.order_by('kind', 'name'),
    }


@login_required
def empty_box_delivery_list(request):
    """
    لوحة «تسليم الفارغ»: المتاح فعلاً لكل نوع + أوامر التسليم (إرجاع/بيع) + الإجماليات.
    """
    selected_kind = request.GET.get('kind') or ''
    if selected_kind not in dict(EmptyBoxDelivery.Kind.choices):
        selected_kind = ''
    selected_recipient = None
    recipient_id = request.GET.get('recipient')
    if recipient_id:
        selected_recipient = EmptyBoxRecipient.objects.filter(pk=recipient_id).first()

    context = _empty_box_overview(selected_kind, selected_recipient)
    context.update({
        'selected_kind': selected_kind,
        'selected_recipient': selected_recipient,
        'kind_choices': EmptyBoxDelivery.Kind.choices,
    })
    return render(request, 'boxes/empty_box_delivery_list.html', context)


@login_required
@accountant_required
def empty_box_recipient_create(request):
    if request.method == 'POST':
        form = EmptyBoxRecipientForm(request.POST)
        if form.is_valid():
            ok, obj = save_form_transactionally(
                form, form.save,
                'توجد جهة بنفس الاسم والنوع بالفعل. عدّل الاسم أو النوع، أو افتح الجهة الموجودة.',
            )
            if ok:
                AuditLog.log(request.user, 'CREATE_EMPTY_BOX_RECIPIENT', obj,
                             reason=f'إضافة جهة مستلمة للفارغ: {obj.name}')
                messages.success(request, f'تمت إضافة الجهة «{obj.name}» ({obj.get_kind_display()}).')
                return redirect('empty_box_delivery_list')
    else:
        form = EmptyBoxRecipientForm()
    return render(request, 'boxes/empty_box_recipient_form.html', {
        'form': form,
        'title': 'إضافة جهة مستلمة للفارغ',
    })


@login_required
@accountant_required
def empty_box_recipient_edit(request, pk):
    obj = get_object_or_404(EmptyBoxRecipient, pk=pk)
    if request.method == 'POST':
        form = EmptyBoxRecipientForm(request.POST, instance=obj)
        if form.is_valid():
            ok, obj = save_form_transactionally(
                form, form.save,
                'توجد جهة أخرى بنفس الاسم والنوع. عدّل الاسم أو النوع، أو افتح الجهة الموجودة.',
            )
            if ok:
                AuditLog.log(request.user, 'UPDATE_EMPTY_BOX_RECIPIENT', obj,
                             reason=f'تعديل الجهة المستلمة للفارغ: {obj.name}')
                messages.success(request, f'تم تعديل الجهة «{obj.name}».')
                return redirect('empty_box_delivery_list')
    else:
        form = EmptyBoxRecipientForm(instance=obj)
    return render(request, 'boxes/empty_box_recipient_form.html', {
        'form': form,
        'title': f'تعديل جهة مستلمة: {obj.name}',
    })


@login_required
@accountant_required
def empty_box_delivery_create(request):
    """إنشاء أمر تسليم فارغ (إرجاع إلى المزرعة أو بيع لمنظمة)."""
    if request.method == 'POST':
        form = EmptyBoxDeliveryForm(request.POST)
        if form.is_valid():
            try:
                delivery = EmptyBoxService.create_delivery(form.cleaned_data, user=request.user)
            except Exception as e:
                messages.error(request, f'خطأ: {e}')
            else:
                label = 'بيع' if delivery.is_sale else 'إرجاع'
                messages.success(
                    request,
                    f'تم تسجيل أمر تسليم ({label}) رقم {delivery.reference_number}: '
                    f'{delivery.quantity} صندوق من نوع «{delivery.box_type.name}» إلى {delivery.recipient.name}.'
                )
                return redirect('empty_box_delivery_detail', pk=delivery.pk)
    else:
        form = EmptyBoxDeliveryForm(initial={
            'box_type': request.GET.get('box_type') or None,
        })
    return render(request, 'boxes/empty_box_delivery_form.html', {
        'form': form,
        'title': 'أمر تسليم صناديق فارغة جديد',
    })


@login_required
@accountant_required
def empty_box_delivery_edit(request, pk):
    delivery = get_object_or_404(EmptyBoxDelivery, pk=pk)
    if delivery.status == EmptyBoxDelivery.Status.DELIVERED:
        messages.error(
            request,
            f'أمر التسليم {delivery.reference_number} مُعتمد ولا يمكن تعديله — '
            f'لتصحيحه أنشئ أمراً جديداً أو استخدم حذف السجل مع توثيق السبب.'
        )
        return redirect('empty_box_delivery_detail', pk=delivery.pk)

    if request.method == 'POST':
        form = EmptyBoxDeliveryForm(request.POST, instance=delivery)
        if form.is_valid():
            try:
                updated = EmptyBoxService.update_delivery(
                    delivery, form.cleaned_data, user=request.user
                )
            except Exception as e:
                messages.error(request, f'خطأ: {e}')
            else:
                messages.success(request, f'تم تعديل أمر التسليم {updated.reference_number}.')
                return redirect('empty_box_delivery_detail', pk=updated.pk)
    else:
        form = EmptyBoxDeliveryForm(instance=delivery)
    return render(request, 'boxes/empty_box_delivery_form.html', {
        'form': form,
        'delivery': delivery,
        'title': f'تعديل أمر تسليم: {delivery.reference_number}',
    })


@login_required
def empty_box_delivery_detail(request, pk):
    delivery = get_object_or_404(
        EmptyBoxDelivery.objects.select_related(
            'recipient', 'box_type', 'treasury_account', 'created_by', 'delivered_by'
        ), pk=pk
    )
    return render(request, 'boxes/empty_box_delivery_detail.html', {
        'delivery': delivery,
        'available_for_type': EmptyBoxService.available_empty(delivery.box_type_id).get(
            delivery.box_type_id, 0
        ),
    })


@login_required
@accountant_required
def empty_box_delivery_confirm(request, pk):
    """اعتماد التسليم الفعلي وقيد مبلغ البيع في الخزينة (مرة واحدة فقط)."""
    delivery = get_object_or_404(EmptyBoxDelivery, pk=pk)
    if request.method != 'POST':
        return redirect('empty_box_delivery_detail', pk=delivery.pk)
    try:
        EmptyBoxService.confirm_delivery(delivery, user=request.user)
    except Exception as e:
        messages.error(request, f'خطأ: {e}')
    else:
        if delivery.is_sale and delivery.payment_method in (
            EmptyBoxDelivery.PaymentMethod.CASH, EmptyBoxDelivery.PaymentMethod.BANK_TRANSFER
        ):
            messages.success(
                request,
                f'تم اعتماد تسليم {delivery.quantity} صندوق وقيد مبلغ {delivery.total_amount} دينار '
                f'في {delivery.treasury_account.name if delivery.treasury_account else "الخزينة"}.'
            )
        else:
            messages.success(
                request,
                f'تم اعتماد تسليم {delivery.quantity} صندوق من نوع «{delivery.box_type.name}» '
                f'إلى {delivery.recipient.name}.'
            )
    return redirect('empty_box_delivery_detail', pk=delivery.pk)
