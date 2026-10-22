from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import transaction
from django.http import JsonResponse
from django.db.models import Sum, Count, Q
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.dateparse import parse_date
from decimal import Decimal
import json

from core.permissions import accountant_required, manager_required, write_required
from core.utils import save_form_transactionally
from core.models import Notification, AuditLog, SystemSetting
from core.pagination import paginate
from .models import Trader, Crop, BoxType, ColdStore, Shelf, TruckReceipt, Pallet, PalletMovementLog, TraderPackingRate, ColdStoreTemperatureLog, PackagingGroup, annotate_shelf_occupancy, annotate_cold_store_occupancy
from .forms import (
    TraderForm, TraderPackingRateForm, BoxTypeForm, CropForm,
    ColdStoreForm, ShelfForm, TruckReceiptForm, PalletTransferForm, PackagingGroupForm,
)
from .services import ReceptionService, StorageService, ColdStoreCapacityError
from .move_utils import error_response, move_payload, resolve_target, success_response, validation_error_message
from .qr_utils import generate_qr_base64, build_scan_url
from .csv_importer import import_traders_from_csv, import_boxes_from_csv
from invoicing.models import Invoice
from packing.models import PackedPallet
from boxes.models import FarmBoxRecord


# ==========================================
# 1. التجار (Traders)
# ==========================================

@login_required
def trader_list(request):
    query = request.GET.get('q', '').strip()
    traders = Trader.objects.all().order_by('name')
    if query:
        traders = traders.filter(
            Q(name__icontains=query) | Q(code__icontains=query) | Q(phone__icontains=query)
        ).order_by('name')
    page = paginate(request, traders)
    return render(request, 'operations/trader_list.html', {
        **page, 'traders': page['page_obj'], 'query': query,
    })


@login_required
@accountant_required
def trader_create(request):
    if request.method == 'POST':
        form = TraderForm(request.POST)
        if form.is_valid():
            ok, trader = save_form_transactionally(
                form, form.save,
                'يوجد تاجر آخر بنفس الرمز. الرجاء التحقق من الرمز والمحاولة مرة أخرى.',
            )
            if ok:
                AuditLog.log(request.user, 'CREATE_TRADER', trader, reason='إضافة تاجر جديد')
                messages.success(request, f'تمت إضافة التاجر {trader.name} بنجاح.')
                return redirect('trader_detail', pk=trader.pk)
    else:
        form = TraderForm(initial={'code': Trader.generate_next_code()})
    return render(request, 'operations/trader_form.html', {'form': form, 'title': 'إضافة تاجر جديد'})


@login_required
def trader_detail(request, pk):
    """
    صفحة التاجر: معلوماته، كل فواتيره مقسمة حسب السنة ثم نوع البضاعة،
    مكان بضاعته الحالي في البرادات، وأسعار التوضيب الخاصة به.
    """
    trader = get_object_or_404(Trader, pk=pk)

    # بضاعة التاجر الحالية في البرادات
    stored_pallets = Pallet.objects.filter(
        trader=trader,
        status=Pallet.Status.IN_COOLING
    ).select_related('cold_store', 'shelf', 'crop', 'box_type')

    # الفواتير مقسمة حسب السنة ثم نوع البضاعة
    invoices = Invoice.objects.filter(trader=trader).select_related('crop').order_by('-year', '-created_at')
    invoices_by_year = {}
    for inv in invoices:
        if inv.year not in invoices_by_year:
            invoices_by_year[inv.year] = {}
        crop_name = inv.crop.name
        if crop_name not in invoices_by_year[inv.year]:
            invoices_by_year[inv.year][crop_name] = []
        invoices_by_year[inv.year][crop_name].append(inv)

    # سيارات التاجر
    trucks = TruckReceipt.objects.filter(trader=trader).select_related('crop', 'box_type', 'cold_store')

    # أسعار التوضيب للتاجر
    rates = trader.packing_rates.all().order_by('-effective_date')

    rate_form = TraderPackingRateForm()

    # صناديق المزرعة المسجّلة باسم هذا التاجر (قسم مستقل عن مخزون الشركة)
    farm_box_records = FarmBoxRecord.objects.filter(trader=trader).select_related(
        'truck_receipt', 'truck_receipt__crop', 'truck_receipt__box_type'
    ).order_by('-created_at')
    farm_box_totals = farm_box_records.aggregate(
        total_incoming=Sum('incoming_count'),
        total_empty=Sum('empty_count'),
    )

    context = {
        'trader': trader,
        'stored_pallets': stored_pallets,
        'invoices_by_year': invoices_by_year,
        'trucks': trucks,
        'rates': rates,
        'rate_form': rate_form,
        'farm_box_records': farm_box_records,
        'farm_box_incoming': farm_box_totals['total_incoming'] or 0,
        'farm_box_empty': farm_box_totals['total_empty'] or 0,
    }
    return render(request, 'operations/trader_detail.html', context)


@login_required
@manager_required
def trader_add_rate(request, pk):
    trader = get_object_or_404(Trader, pk=pk)
    if request.method == 'POST':
        form = TraderPackingRateForm(request.POST)
        if form.is_valid():
            def _create_rate():
                rate = form.save(commit=False)
                rate.trader = trader
                rate.save()
                return rate

            ok, rate = save_form_transactionally(
                form, _create_rate,
                'يوجد سعر توضيب مطابق لنفس الطريقة وتاريخ السريان لهذا التاجر. '
                'الرجاء تعديل التاريخ أو الطريقة.',
            )
            if ok:
                AuditLog.log(request.user, 'ADD_TRADER_RATE', rate, reason=f'إضافة سعر توضيب للتاجر {trader.name}')
                messages.success(request, 'تمت إضافة سعر التوضيب بنجاح.')
            else:
                messages.error(request, 'خطأ في بيانات سعر التوضيب.')
        else:
            messages.error(request, 'خطأ في بيانات سعر التوضيب.')
    return redirect('trader_detail', pk=trader.pk)


# ==========================================
# 2. السيارات والاستلام (Trucks & Reception)
# ==========================================

@login_required
def truck_list(request):
    trucks = TruckReceipt.objects.select_related('trader', 'crop', 'box_type', 'cold_store', 'shelf').order_by('-created_at')

    # فلتر البحث: برقم الدفعة أو اسم التاجر، و/أو بتاريخ الوصول
    q = (request.GET.get('q') or '').strip()
    date_str = (request.GET.get('date') or '').strip()
    if q:
        trucks = trucks.filter(
            Q(lot_number__icontains=q) | Q(trader__name__icontains=q) | Q(trader__code__icontains=q)
        )
    if date_str:
        parsed = parse_date(date_str)
        if parsed:
            trucks = trucks.filter(arrival_date=parsed)

    page = paginate(request, trucks)
    return render(request, 'operations/truck_list.html', {
        **page, 'trucks': page['page_obj'],
    })


def _shelves_snapshot():
    """
    لقطة JSON لأسياخ كل براد مع السعة المتاحة لكل سيخ.
    تُستخدم في القوائم المنسدلة التابعة: اختيار البراد يفلتر أسياخه،
    والأسياخ الممتلئة تُخفى ولا تظهر حتى يتوفر فيها مكان.
    """
    shelves = list(
        Shelf.objects.select_related('cold_store').order_by('cold_store__code', 'shelf_number')
    )
    annotate_shelf_occupancy(shelves)
    stores = {
        store.pk: store for store in annotate_cold_store_occupancy(ColdStore.objects.all())
    }
    stackable_by_store = dict(
        Pallet.objects.filter(
            status=Pallet.Status.IN_COOLING,
            double_tier_base__isnull=True,
            shelf__isnull=False,
        ).annotate(
            active_upper_count=Count(
                'double_tier_upper_pallets',
                filter=Q(double_tier_upper_pallets__status=Pallet.Status.IN_COOLING),
            )
        ).filter(active_upper_count=0).values_list('cold_store_id')
        .annotate(c=Count('id')).values_list('cold_store_id', 'c')
    )
    data = []
    for shelf in shelves:
        capacity = shelf.effective_capacity
        occupied = shelf.storage_slot_occupied
        free = max(0, capacity - occupied) if capacity else None
        racks_available = stores[shelf.cold_store_id].double_tier_racks_available
        can_consider_racks = bool(
            racks_available and (stackable_by_store.get(shelf.cold_store_id, 0) or (free is not None and free > 0))
        )
        is_full = bool(capacity) and occupied >= capacity
        data.append({
            'id': shelf.id,
            'store_id': shelf.cold_store_id,
            'label': f"السيخ {shelf.shelf_number}" + (
                f" — متاح {free} موضع أرضي" if free is not None else ''
            ) + (f" — يوجد {racks_available} حامل طابقين" if can_consider_racks else ''),
            'free': free,
            'full': is_full and not can_consider_racks,
        })
    return data


@login_required
@accountant_required
def truck_create(request):
    """
    استلام بضاعة (سيارة جديدة)
    """
    capacity_issue = None
    if request.method == 'POST':
        form = TruckReceiptForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                # المعالجة عبر ReceptionService
                truck = ReceptionService.process_truck_reception(
                    data=form.cleaned_data,
                    files=request.FILES,
                    user=request.user,
                    use_double_tier_racks=request.POST.get('use_double_tier_racks') == '1',
                )
                if truck.on_pallets and truck.pallet_count:
                    messages.success(request, f'تم استلام السيارة بنجاح برقم دفعة: {truck.lot_number} وتوليد {truck.pallet_count} طبلية.')
                else:
                    messages.success(request, f'تم استلام السيارة بنجاح برقم دفعة: {truck.lot_number} (بضاعة بكمات بدون طبالي).')
                if truck.missing_fields_summary:
                    messages.warning(request, f'تنبيه: حقول غير حرجة ناقصة ({truck.missing_fields_summary}). تم إدراجها في جرس الإشعارات.')
                return redirect('truck_detail', pk=truck.pk)
            except ValidationError as e:
                if isinstance(e, ColdStoreCapacityError):
                    capacity_issue = e
                else:
                    messages.error(request, str(e.message if hasattr(e, 'message') else e))
            except Exception as e:
                messages.error(request, f'حدث خطأ غير متوقع: {str(e)}')
    else:
        initial = {
            'lot_number': TruckReceipt.generate_lot_number(),
            'arrival_date': timezone.now().date(),
            'weight_deduction_applied': True,
        }
        # إذا تم تمرير تاجر أو بضاعة في الرابط
        if request.GET.get('trader'):
            initial['trader'] = request.GET.get('trader')
        if request.GET.get('crop'):
            initial['crop'] = request.GET.get('crop')
        form = TruckReceiptForm(initial=initial)

    return render(request, 'operations/truck_form.html', {
        'form': form,
        'title': 'استلام سيارة جديدة (وصول بضاعة)',
        'shelves_list': _shelves_snapshot(),
        'capacity_issue': capacity_issue,
    })


@login_required
def truck_detail(request, pk):
    truck = get_object_or_404(
        TruckReceipt.objects.select_related(
            'trader', 'crop', 'box_type', 'cold_store', 'shelf', 'created_by',
            'farm_box_record',
        ),
        pk=pk
    )
    pallets = truck.pallets.select_related('cold_store', 'shelf').all()
    temp_logs = ColdStoreTemperatureLog.objects.filter(
        pallet__truck_receipt=truck
    ).select_related('cold_store', 'recorded_by', 'pallet')[:50]
    return render(request, 'operations/truck_detail.html', {
        'truck': truck,
        'pallets': pallets,
        'temp_logs': temp_logs,
    })


@login_required
@accountant_required
def truck_edit(request, pk):
    truck = get_object_or_404(TruckReceipt, pk=pk)
    if request.method == 'POST':
        form = TruckReceiptForm(request.POST, request.FILES, instance=truck)
        if form.is_valid():
            def _save_truck():
                t = form.save(commit=False)
                settings_obj = SystemSetting.get_settings()
                t.calculate_weights(empty_pallet_weight=settings_obj.empty_pallet_weight)

                # فحص الحقول غير الحرجة وتحديث الإشعار
                missing_non_critical = t.check_non_critical_missing()
                t.missing_fields_summary = ", ".join(missing_non_critical)
                t.save()
                return t, missing_non_critical

            ok, result = save_form_transactionally(
                form, _save_truck,
                'يوجد سيارة أخرى بنفس رقم الدفعة. الرجاء التحقق والمحاولة مرة أخرى.',
            )
            if not ok:
                return render(request, 'operations/truck_form.html', {
                    'form': form,
                    'title': f'تعديل السيارة {truck.lot_number}',
                    'is_edit': True,
                    'shelves_list': _shelves_snapshot(),
                })

            truck, missing_non_critical = result

            # إذا اكتملت البيانات، نحدد الإشعار كـ محلول
            if not missing_non_critical:
                Notification.objects.filter(related_model='TruckReceipt', related_id=str(truck.id)).update(
                    is_resolved=True,
                    resolved_at=timezone.now()
                )
                messages.success(request, 'تم إكمال جميع البيانات غير الحرجة للسيارة وإغلاق الإشعار المرتبط بها!')
            else:
                messages.info(request, f'تم حفظ التعديلات. لا يزال ينقص: {", ".join(missing_non_critical)}')

            AuditLog.log(request.user, 'UPDATE_TRUCK_RECEIPT', truck, reason='تعديل بيانات السيارة')
            return redirect('truck_detail', pk=truck.pk)
    else:
        form = TruckReceiptForm(instance=truck)
    return render(request, 'operations/truck_form.html', {
        'form': form,
        'title': f'تعديل السيارة {truck.lot_number}',
        'is_edit': True,
        'shelves_list': _shelves_snapshot(),
    })


# ==========================================
# 3. الطبالي والـ QR والملصقات (Pallets & QR)
# ==========================================

@login_required
def pallet_detail(request, pk):
    pallet = get_object_or_404(
        Pallet.objects.select_related(
            'truck_receipt', 'trader', 'crop', 'box_type', 'cold_store', 'shelf', 'double_tier_base'
        ).prefetch_related('double_tier_upper_pallets'),
        pk=pk
    )
    qr_data = pallet.get_qr_data()
    # الرمز المطبوع/المعروض يفتح صفحة الطبلية عند المسح
    qr_url = build_scan_url(request, 'pallet_qr_scan', pallet.pallet_code)
    qr_image_b64 = generate_qr_base64(qr_url)
    movements = pallet.movements.select_related('from_cold_store', 'to_cold_store', 'from_shelf', 'to_shelf', 'moved_by').order_by('-timestamp')

    transfer_form = PalletTransferForm(initial={'to_cold_store': pallet.cold_store, 'to_shelf': pallet.shelf})

    context = {
        'pallet': pallet,
        'qr_data': qr_data,
        'qr_url': qr_url,
        'qr_image_b64': qr_image_b64,
        'movements': movements,
        'transfer_form': transfer_form,
    }
    return render(request, 'operations/pallet_detail.html', context)


def pallet_qr_scan(request, pallet_code):
    """
    صفحة عامة أو داخلية تفتح عند مسح رمز الـ QR للطبلية بكاميرا الهاتف أو القارئ
    """
    pallet = get_object_or_404(
        Pallet.objects.select_related('truck_receipt', 'trader', 'crop', 'box_type', 'cold_store', 'shelf'),
        pallet_code=pallet_code
    )
    qr_data = pallet.get_qr_data()
    return render(request, 'operations/pallet_qr_scan.html', {'pallet': pallet, 'qr_data': qr_data})


@login_required
def pallet_print_label(request, pk):
    pallet = get_object_or_404(Pallet, pk=pk)
    qr_url = build_scan_url(request, 'pallet_qr_scan', pallet.pallet_code)
    qr_image_b64 = generate_qr_base64(qr_url)
    return render(request, 'operations/pallet_label_print.html', {
        'pallet': pallet,
        'qr_url': qr_url,
        'qr_image_b64': qr_image_b64,
    })


@login_required
def truck_print_all_labels(request, pk):
    truck = get_object_or_404(TruckReceipt, pk=pk)
    pallets = truck.pallets.select_related('trader', 'crop', 'box_type', 'cold_store', 'shelf').all()
    pallets_with_qr = []
    for p in pallets:
        qr_url = build_scan_url(request, 'pallet_qr_scan', p.pallet_code)
        pallets_with_qr.append({
            'pallet': p,
            'qr_url': qr_url,
            'qr_image_b64': generate_qr_base64(qr_url)
        })
    return render(request, 'operations/truck_labels_bulk_print.html', {
        'truck': truck,
        'pallets_with_qr': pallets_with_qr,
    })


# ==========================================
# 4. البرادات والأسياخ (Cold Stores)
# ==========================================

@login_required
def cold_store_list(request):
    """
    عرض كروت البرادات والأسياخ مع رسمة بصرية:
    كل براد مع أسياخه، وبضاعة كل تاجر بلون مميز
    """
    stores = ColdStore.objects.prefetch_related('shelves').all()
    shelves_data = []

    # لوحة ألوان ثابتة لكل تاجر لتمييز البضاعة بصرياً (البند 5)
    color_palette = ['#15803d', '#b45309', '#1d4ed8', '#7c3aed', '#be123c', '#047857', '#0369a1', '#9333ea']
    traders_ordered = list(Trader.objects.order_by('name'))
    trader_colors = {t.id: color_palette[i % len(color_palette)] for i, t in enumerate(traders_ordered)}
    trader_legend = [{'name': t.name, 'code': t.code, 'color': trader_colors[t.id]} for t in traders_ordered]

    # تجميع الطبالي دفعة واحدة بدل استعلام لكل سيخ (منع N+1)
    upper_base_ids = set(Pallet.objects.filter(
        status=Pallet.Status.IN_COOLING, double_tier_base__isnull=False
    ).values_list('double_tier_base_id', flat=True))
    pallets_by_shelf = {}
    for p in Pallet.objects.filter(status=Pallet.Status.IN_COOLING).select_related(
        'trader', 'crop', 'box_type', 'double_tier_base'
    ).order_by('shelf_id', 'pallet_code'):
        p.has_upper_tier = p.pk in upper_base_ids
        pallets_by_shelf.setdefault(p.shelf_id, []).append(p)

    packed_by_shelf = {}
    for p in PackedPallet.objects.filter(status=PackedPallet.Status.IN_COOLING).select_related(
        'trader', 'crop', 'box_type'
    ).order_by('shelf_id', 'packed_pallet_code'):
        packed_by_shelf.setdefault(p.shelf_id, []).append(p)

    # عدد الطبالي المشغولة لكل براد بدل استعلامين لكل براد (الخاصية occupied/occupancy)
    occupied_by_store = dict(
        Pallet.objects.filter(status=Pallet.Status.IN_COOLING)
        .values_list('cold_store_id')
        .annotate(c=Count('id'))
        .values_list('cold_store_id', 'c')
    )
    packed_occupied_by_store = dict(
        PackedPallet.objects.filter(status=PackedPallet.Status.IN_COOLING)
        .values_list('cold_store_id')
        .annotate(c=Count('id'))
        .values_list('cold_store_id', 'c')
    )
    double_tier_occupied_by_store = dict(
        Pallet.objects.filter(
            status=Pallet.Status.IN_COOLING, double_tier_base__isnull=False
        ).values_list('cold_store_id')
        .annotate(c=Count('id'))
        .values_list('cold_store_id', 'c')
    )

    # أسياخ كل البرادات مع سعتها الفعلية والمتاح فيها (استعلامات مجمّعة لمنع N+1)
    shelves_by_store = {}
    for shelf in annotate_shelf_occupancy(
        Shelf.objects.filter(cold_store__in=stores).order_by('cold_store_id', 'shelf_number')
    ):
        shelves_by_store.setdefault(shelf.cold_store_id, []).append(shelf)

    for store in stores:
        store_shelves = []
        store_packed_count = 0
        for shelf in shelves_by_store.get(store.id, []):
            pallets = pallets_by_shelf.get(shelf.id, [])

            # إلحاق لون التاجر المميز بكل طبلية لعرضها بصرياً في القالب
            for p in pallets:
                p.trader_color = trader_colors.get(p.trader_id, '#1B4D3E')

            # الطبالي الموضبة التي أُعيدت إلى البراد (خدمة التبريد اليومي)
            packed_pallets = packed_by_shelf.get(shelf.id, [])
            store_packed_count += len(packed_pallets)

            raw_boxes = sum(p.remaining_box_count for p in pallets)
            packed_boxes = sum(p.box_count for p in packed_pallets)
            raw_count = len(pallets)
            packed_count = len(packed_pallets)
            store_shelves.append({
                'shelf': shelf,
                'pallets': pallets,
                # Keep the legacy display key aligned with the real shelf
                # occupancy: raw and packed pallets share the same capacity.
                'pallet_count': raw_count + packed_count,
                'raw_pallet_count': raw_count,
                'packed_pallet_count': packed_count,
                'total_pallet_count': raw_count + packed_count,
                'storage_slot_occupied': shelf.storage_slot_occupied,
                # These are different business units (farm crates vs sale
                # cartons); never add them into a misleading combined total.
                'raw_boxes': raw_boxes,
                'packed_boxes': packed_boxes,
                'packed_pallets': packed_pallets,
                'capacity': shelf.effective_capacity,
                'free': shelf.free_pallets,
            })
        unassigned_raw = pallets_by_shelf.get(None, [])
        unassigned_raw = [p for p in unassigned_raw if p.cold_store_id == store.id]
        for p in unassigned_raw:
            p.trader_color = trader_colors.get(p.trader_id, '#1B4D3E')
        unassigned_packed = [
            p for p in packed_by_shelf.get(None, []) if p.cold_store_id == store.id
        ]
        occupied_count = occupied_by_store.get(store.id, 0)
        packed_count = packed_occupied_by_store.get(store.id, 0)
        upper_count = double_tier_occupied_by_store.get(store.id, 0)
        occupied_slots = max(0, occupied_count + packed_count - upper_count)
        shelves_data.append({
            'store': store,
            'shelves': store_shelves,
            'unassigned_raw': unassigned_raw,
            'unassigned_packed': unassigned_packed,
            'occupied': occupied_count + packed_count,
            'occupied_slots': occupied_slots,
            'double_tier_racks_in_use': upper_count,
            'double_tier_racks_available': max(0, store.double_tier_rack_count - upper_count),
            'raw_occupied': occupied_count,
            'packed_occupied': packed_count,
            'occupancy_rate': (
                round((occupied_slots / store.capacity_pallets) * 100, 1)
                if store.capacity_pallets and store.capacity_pallets > 0 else None
            ),
        })

    recent_temp_logs = ColdStoreTemperatureLog.objects.select_related(
        'cold_store', 'recorded_by', 'pallet'
    )[:30]

    return render(request, 'operations/cold_stores.html', {
        'shelves_data': shelves_data,
        # نقل الطبالي بالسحب والإفلات متاح فقط لمن يملك صلاحية النقل
        'can_move': request.user.is_accountant,
        'trader_colors': trader_colors,
        'trader_legend': trader_legend,
        'recent_temp_logs': recent_temp_logs,
    })


@login_required
@manager_required
def cold_store_create(request):
    """
    إضافة براد جديد مع إنشاء أسياخه تلقائياً (المعتمد: 4 أسياخ لكل براد).
    """
    if request.method == 'POST':
        form = ColdStoreForm(request.POST)
        if form.is_valid():
            shelves_count = form.cleaned_data['shelves_count']

            def _create_store():
                store = form.save()
                for i in range(1, shelves_count + 1):
                    Shelf.objects.create(cold_store=store, shelf_number=str(i))
                return store

            ok, store = save_form_transactionally(
                form, _create_store,
                'يوجد براد آخر بنفس الاسم أو الرمز. الرجاء التحقق من القيم والمحاولة مرة أخرى.',
            )
            if ok:
                AuditLog.log(
                    request.user, 'CREATE_COLD_STORE', store,
                    reason=f'إضافة براد جديد مع {shelves_count} أسياخ',
                )
                messages.success(request, f'تمت إضافة {store.name} مع {shelves_count} أسياخ بنجاح.')
                return redirect('cold_store_list')
    else:
        form = ColdStoreForm()
    return render(request, 'operations/cold_store_form.html', {
        'form': form,
        'title': 'إضافة براد جديد',
    })


@login_required
@manager_required
def cold_store_edit(request, pk):
    store = get_object_or_404(ColdStore, pk=pk)
    if request.method == 'POST':
        form = ColdStoreForm(request.POST, instance=store)
        if form.is_valid():
            updated = form.save()
            AuditLog.log(
                request.user, 'UPDATE_COLD_STORE', updated,
                reason=f'تحديث إعدادات البراد {updated.name}',
            )
            messages.success(request, f'تم تحديث إعدادات {updated.name} والحوامل المتاحة.')
            return redirect('cold_store_list')
    else:
        form = ColdStoreForm(instance=store)
    return render(request, 'operations/cold_store_form.html', {
        'form': form,
        'store': store,
        'title': f'إعدادات البراد {store.name}',
    })


@login_required
@manager_required
def shelf_create(request, store_pk):
    """
    إضافة سيخ/رف إضافي إلى براد موجود.
    """
    store = get_object_or_404(ColdStore, pk=store_pk)
    if request.method == 'POST':
        form = ShelfForm(request.POST)
        if form.is_valid():
            def _create_shelf():
                shelf = form.save(commit=False)
                shelf.cold_store = store
                shelf.save()
                return shelf

            ok, shelf = save_form_transactionally(
                form, _create_shelf,
                f'يوجد سيخ بنفس الرقم ({form.cleaned_data.get("shelf_number", "")}) داخل هذا البراد. '
                'الرجاء اختيار رقم آخر.',
            )
            if ok:
                AuditLog.log(request.user, 'CREATE_SHELF', shelf, reason=f'إضافة سيخ إلى {store.name}')
                messages.success(request, f'تمت إضافة السيخ {shelf.shelf_number} إلى {store.name}.')
                return redirect('cold_store_list')
    else:
        next_number = str(store.shelves.count() + 1)
        form = ShelfForm(initial={'shelf_number': next_number})
    return render(request, 'operations/shelf_form.html', {
        'form': form,
        'store': store,
        'title': f'إضافة سيخ إلى {store.name}',
    })


@login_required
def cold_store_temperature_log(request):
    """
    سجل درجات حرارة البرادات:
    - عرض آخر القياسات (إدخال/إخراج/دوري).
    - إضافة قياس دوري يدوي من قبل المحاسب أو المدير.
    """
    stores = ColdStore.objects.all()
    selected_store = None
    store_id = request.GET.get('store') or request.POST.get('cold_store')

    if request.method == 'POST':
        if not (request.user.is_accountant or request.user.is_manager or request.user.is_superuser):
            messages.error(request, 'عذراً، تسجيل درجات الحرارة يتطلب صلاحية محاسب أو مدير.')
            return redirect('cold_store_temperature_log')

        temp_value = request.POST.get('temperature')
        notes = (request.POST.get('notes') or '').strip()
        log_type = request.POST.get('log_type') or ColdStoreTemperatureLog.LogType.PERIODIC
        pallet_id = request.POST.get('pallet') or None

        if not store_id or not temp_value:
            messages.error(request, 'يجب اختيار البراد وإدخال درجة الحرارة.')
            return redirect('cold_store_temperature_log')

        store = get_object_or_404(ColdStore, pk=store_id)
        pallet_obj = Pallet.objects.filter(pk=pallet_id).first() if pallet_id else None
        try:
            temp_dec = Decimal(str(temp_value))
        except Exception:
            messages.error(request, 'قيمة درجة الحرارة غير صحيحة.')
            return redirect('cold_store_temperature_log')

        ColdStoreTemperatureLog.objects.create(
            cold_store=store,
            temperature=temp_dec,
            log_type=log_type,
            pallet=pallet_obj,
            notes=notes,
            recorded_by=request.user,
        )
        store.current_temperature = temp_dec
        store.save(update_fields=['current_temperature'])

        AuditLog.log(
            user=request.user,
            action='LOG_TEMPERATURE',
            instance=store,
            reason=f'تسجيل درجة حرارة {store.name}: {temp_dec}°C',
            details={'temperature': str(temp_dec), 'log_type': log_type},
        )
        messages.success(request, f'تم تسجيل درجة الحرارة {temp_dec}°C للبراد {store.name}.')
        return redirect(f"{request.path}?store={store.pk}")

    if store_id:
        selected_store = ColdStore.objects.filter(pk=store_id).first()

    logs_qs = ColdStoreTemperatureLog.objects.select_related('cold_store', 'recorded_by', 'pallet')
    if selected_store:
        logs_qs = logs_qs.filter(cold_store=selected_store)
    logs = logs_qs[:200]

    return render(request, 'operations/temperature_log.html', {
        'stores': stores,
        'selected_store': selected_store,
        'logs': logs,
        'log_types': ColdStoreTemperatureLog.LogType.choices,
    })


@login_required
@accountant_required
def pallet_transfer(request, pk):
    """
    نقل طبلية من سيخ أو براد إلى آخر وتحديث الموقعين وسجل التدقيق
    """
    pallet = get_object_or_404(Pallet, pk=pk)
    if request.method == 'POST':
        form = PalletTransferForm(request.POST)
        if form.is_valid():
            to_store = form.cleaned_data['to_cold_store']
            to_shelf = form.cleaned_data['to_shelf']
            reason = form.cleaned_data['reason']
            try:
                StorageService.transfer_pallet(
                    pallet=pallet,
                    to_cold_store=to_store,
                    to_shelf=to_shelf,
                    user=request.user,
                    reason=reason
                )
                messages.success(request, f'تم نقل الطبلية {pallet.pallet_code} بنجاح إلى {to_store.name}.')
            except ValidationError as e:
                messages.error(request, str(e.message if hasattr(e, 'message') else e))
            except Exception as e:
                messages.error(request, f'خطأ أثناء النقل: {str(e)}')
    return redirect('pallet_detail', pk=pallet.pk)


@login_required
def pallet_move(request, pk):
    """
    نقل طبلية عبر السحب والإفلات من صفحة البرادات والأسياخ (استجابة JSON).
    الفحص يجري في الخادم: صلاحية محاسب/مدير + سعة البراد والسيخ المستهدفين.
    """
    if request.method != 'POST':
        return error_response('طريقة الطلب غير مدعومة.', status=405)
    if not request.user.is_accountant:
        return error_response('نقل الطبالي يتطلب صلاحية محاسب أو مدير.', status=403)

    pallet = get_object_or_404(Pallet, pk=pk)
    to_store, to_shelf, err = resolve_target(move_payload(request), Shelf, ColdStore)
    if err:
        return error_response(err)

    if (pallet.cold_store_id == to_store.pk
            and pallet.shelf_id == (to_shelf.pk if to_shelf else None)):
        return success_response(
            f'الطبلية {pallet.pallet_code} موجودة أصلاً في هذا الموقع — لا حاجة للنقل.',
            pallet.pk, to_store.pk, to_shelf.pk if to_shelf else None, moved=False,
        )

    try:
        StorageService.transfer_pallet(
            pallet=pallet,
            to_cold_store=to_store,
            to_shelf=to_shelf,
            user=request.user,
            reason='نقل عبر السحب والإفلات من خريطة البرادات والأسياخ',
        )
    except ValidationError as e:
        return error_response(validation_error_message(e))
    except Exception as e:  # noqa: BLE001
        return error_response(f'تعذّر النقل: {e}')

    location = to_store.name + (f' — السيخ {to_shelf.shelf_number}' if to_shelf else '')
    return success_response(
        f'تم نقل الطبلية {pallet.pallet_code} إلى {location}',
        pallet.pk, to_store.pk, to_shelf.pk if to_shelf else None,
    )


# ==========================================
# 5. أنواع الصناديق (Box Types)
# ==========================================

@login_required
def box_type_list(request):
    box_types = BoxType.objects.all()
    return render(request, 'operations/box_types.html', {'box_types': box_types})


@login_required
@accountant_required
def box_type_create(request):
    if request.method == 'POST':
        form = BoxTypeForm(request.POST)
        if form.is_valid():
            box = form.save()
            AuditLog.log(request.user, 'CREATE_BOX_TYPE', box, reason='إضافة نوع صندوق جديد')
            messages.success(request, f'تمت إضافة نوع الصندوق {box.name} بنجاح.')
            return redirect('box_type_list')
    else:
        form = BoxTypeForm()
    return render(request, 'operations/box_type_form.html', {'form': form, 'title': 'إضافة نوع صندوق جديد'})


@login_required
@accountant_required
def box_type_edit(request, pk):
    box = get_object_or_404(BoxType, pk=pk)
    if request.method == 'POST':
        form = BoxTypeForm(request.POST, instance=box)
        if form.is_valid():
            box = form.save()
            AuditLog.log(request.user, 'UPDATE_BOX_TYPE', box, reason='تعديل نوع الصندوق')
            messages.success(request, f'تم تعديل نوع الصندوق {box.name} بنجاح.')
            return redirect('box_type_list')
    else:
        form = BoxTypeForm(instance=box)
    return render(request, 'operations/box_type_form.html', {'form': form, 'title': f'تعديل الصندوق: {box.name}'})


# ==========================================
# موارد التغليف (مجموعات + أصناف)
# ==========================================

@login_required
def packaging_resources(request):
    """
    صفحة موارد التغليف: كل مجموعة مستقلة وتحتها أصنافها.
    """
    groups = PackagingGroup.objects.prefetch_related('items').order_by('name')
    ungrouped = BoxType.objects.filter(group__isnull=True).order_by('name')
    return render(request, 'operations/packaging_resources.html', {
        'groups': groups,
        'ungrouped': ungrouped,
    })


@login_required
@accountant_required
def packaging_group_create(request):
    """
    زر «إضافة نوع تغليف»: ينشئ مجموعة جديدة (مثل: الصناديق، ورق التغليف).
    """
    if request.method == 'POST':
        form = PackagingGroupForm(request.POST)
        if form.is_valid():
            ok, group = save_form_transactionally(
                form, form.save,
                'توجد مجموعة تغليف أخرى بنفس الاسم. الرجاء استخدام اسم مختلف.',
            )
            if ok:
                AuditLog.log(request.user, 'CREATE_PACKAGING_GROUP', group, reason='إضافة مجموعة تغليف')
                messages.success(request, f'تمت إضافة مجموعة التغليف "{group.name}". أضف أصنافها الآن.')
                return redirect('packaging_resources')
    else:
        form = PackagingGroupForm()
    return render(request, 'operations/packaging_group_form.html', {
        'form': form,
        'title': 'إضافة نوع تغليف (مجموعة جديدة)',
    })


@login_required
@accountant_required
def packaging_group_edit(request, pk):
    group = get_object_or_404(PackagingGroup, pk=pk)
    if request.method == 'POST':
        form = PackagingGroupForm(request.POST, instance=group)
        if form.is_valid():
            ok, group = save_form_transactionally(
                form, form.save,
                'يوجد نوع تغليف آخر بنفس الاسم. الرجاء استخدام اسم مختلف.',
            )
            if ok:
                AuditLog.log(request.user, 'UPDATE_PACKAGING_GROUP', group, reason='تعديل مجموعة تغليف')
                messages.success(request, f'تم تعديل مجموعة "{group.name}".')
                return redirect('packaging_resources')
    else:
        form = PackagingGroupForm(instance=group)
    return render(request, 'operations/packaging_group_form.html', {
        'form': form,
        'title': f'تعديل مجموعة: {group.name}',
    })


@login_required
@accountant_required
def packaging_item_create(request, group_pk):
    group = get_object_or_404(PackagingGroup, pk=group_pk)
    if request.method == 'POST':
        form = BoxTypeForm(request.POST)
        if form.is_valid():
            item = form.save(commit=False)
            if not item.group_id:
                item.group = group
            item.save()
            AuditLog.log(request.user, 'CREATE_PACKAGING_ITEM', item, reason=f'إضافة صنف إلى {group.name}')
            messages.success(request, f'تمت إضافة الصنف "{item.name}" إلى مجموعة "{group.name}".')
            return redirect('packaging_resources')
    else:
        form = BoxTypeForm(initial={'group': group})
    return render(request, 'operations/box_type_form.html', {
        'form': form,
        'title': f'إضافة صنف إلى مجموعة: {group.name}',
        'cancel_url': 'packaging_resources',
    })


@login_required
@accountant_required
def packaging_item_edit(request, pk):
    item = get_object_or_404(BoxType, pk=pk)
    if request.method == 'POST':
        form = BoxTypeForm(request.POST, instance=item)
        if form.is_valid():
            item = form.save()
            AuditLog.log(request.user, 'UPDATE_PACKAGING_ITEM', item, reason='تعديل صنف تغليف')
            messages.success(request, f'تم تعديل الصنف "{item.name}".')
            return redirect('packaging_resources')
    else:
        form = BoxTypeForm(instance=item)
    return render(request, 'operations/box_type_form.html', {
        'form': form,
        'title': f'تعديل الصنف: {item.name}',
        'cancel_url': 'packaging_resources',
    })


# ==========================================
# أنواع البضاعة (Crops)
# ==========================================

@login_required
def crop_list(request):
    crops = Crop.objects.all()
    return render(request, 'operations/crops.html', {'crops': crops})


@login_required
@accountant_required
def crop_create(request):
    if request.method == 'POST':
        form = CropForm(request.POST)
        if form.is_valid():
            ok, crop = save_form_transactionally(
                form, form.save,
                'يوجد نوع بضاعة آخر بنفس الاسم. الرجاء التحقق والمحاولة مرة أخرى.',
            )
            if ok:
                AuditLog.log(request.user, 'CREATE_CROP', crop, reason='إضافة نوع بضاعة جديد')
                messages.success(request, f'تمت إضافة نوع البضاعة «{crop.name}» بنجاح.')
                return redirect('crop_list')
    else:
        form = CropForm()
    return render(request, 'operations/crop_form.html', {'form': form, 'title': 'إضافة نوع بضاعة جديد'})


@login_required
@accountant_required
def crop_edit(request, pk):
    crop = get_object_or_404(Crop, pk=pk)
    if request.method == 'POST':
        form = CropForm(request.POST, instance=crop)
        if form.is_valid():
            crop = form.save()
            AuditLog.log(request.user, 'UPDATE_CROP', crop, reason='تعديل نوع البضاعة')
            messages.success(request, f'تم تعديل نوع البضاعة «{crop.name}» بنجاح.')
            return redirect('crop_list')
    else:
        form = CropForm(instance=crop)
    return render(request, 'operations/crop_form.html', {'form': form, 'title': f'تعديل نوع البضاعة: {crop.name}'})


# ==========================================
# 6. استيراد CSV (CSV Importer)
# ==========================================

@login_required
@manager_required
def import_csv_view(request):
    results = None
    if request.method == 'POST':
        import_type = request.POST.get('import_type')
        csv_file = request.FILES.get('csv_file')
        if not csv_file:
            messages.error(request, 'يرجى اختيار ملف CSV أولاً.')
        else:
            try:
                if import_type == 'traders':
                    results = import_traders_from_csv(csv_file, user=request.user)
                    messages.success(request, f"تم استيراد {results['imported']} تاجر جديد، وتحديث {results['updated']} تاجر.")
                elif import_type == 'boxes':
                    results = import_boxes_from_csv(csv_file, user=request.user)
                    messages.success(request, f"تم استيراد {results['imported']} نوع صندوق جديد، وتحديث {results['updated']} نوع.")
                else:
                    messages.error(request, 'نوع الاستيراد غير معروف.')
            except Exception as e:
                messages.error(request, f'خطأ أثناء استيراد الملف: {str(e)}')

    return render(request, 'operations/import_csv.html', {'results': results})
