from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.http import JsonResponse
from decimal import Decimal
import json

from core.ajax import is_ajax, ajax_response
from core.permissions import accountant_required, manager_required
from core.models import AuditLog, SystemSetting
from operations.models import BoxType, Pallet, Trader, ColdStore, Shelf, PackagingGroup
from boxes.models import FarmBoxType
from operations.qr_utils import generate_qr_base64, build_scan_url
from operations.move_utils import (
    error_response, move_payload, resolve_target, success_response, validation_error_message,
)
from .models import (
    PackingSession, PackingOutput, PackingWaste, PackedPallet,
    PackedPalletCoolingLog, DeliveryNote,
)
from .forms import StartPackingSessionForm, DeliveryNoteForm, PackedPalletCoolingForm
from .services import PackingService


@login_required
def packing_list(request):
    sessions = PackingSession.objects.select_related('trader', 'crop', 'created_by').order_by('-started_at')
    return render(request, 'packing/packing_list.html', {'sessions': sessions})


@login_required
@accountant_required
def packing_start(request):
    """
    بدء عملية توضيب جديدة وإخراج الطبالي من البراد
    """
    if request.method == 'POST':
        form = StartPackingSessionForm(request.POST)
        if form.is_valid():
            try:
                session = PackingService.start_packing_session(
                    trader=form.cleaned_data['trader'],
                    crop=form.cleaned_data['crop'],
                    packing_method=form.cleaned_data['packing_method'],
                    source_pallets=form.cleaned_data['source_pallets'],
                    workshop_name=form.cleaned_data['workshop_name'],
                    user=request.user
                )
                messages.success(request, f'تم بدء جلسة التوضيب {session.session_code} بنجاح وإخراج الطبالي من البراد.')
                return redirect('packing_detail', pk=session.pk)
            except Exception as e:
                messages.error(request, f'خطأ: {str(e)}')
    else:
        initial = {}
        if request.GET.get('trader'):
            initial['trader'] = request.GET.get('trader')
        form = StartPackingSessionForm(initial=initial)

    return render(request, 'packing/packing_start.html', {'form': form, 'title': 'بدء جلسة توضيب جديدة'})


@login_required
def packing_detail(request, pk):
    """
    تفاصيل جلسة التوضيب، المقاسات الناتجة، الهالك، الرندمان، والطبالي الموضبة
    """
    session = get_object_or_404(
        PackingSession.objects.select_related('trader', 'crop', 'created_by', 'approved_by').prefetch_related(
            'source_pallets', 'outputs', 'wastes', 'packed_pallets__trader', 'packed_pallets__crop', 'packed_pallets__box_type'
        ),
        pk=pk
    )
    box_types = BoxType.objects.filter(is_active=True, is_box=True)
    packaging_groups = PackagingGroup.objects.filter(is_active=True).prefetch_related('items')
    packaging_items = BoxType.objects.filter(
        is_active=True, group__isnull=False
    ).select_related('group').order_by('group__name', 'name')

    # صناديق الفارغ (من المزرعة) المتاحة لاستهلاكها في التوضيب:
    # المتاح = الفارغ الناتج عن التفريغ − المسلَّم − المستهلك سابقاً في التوضيب.
    from boxes.services import EmptyBoxService
    available_farm_empty = EmptyBoxService.available_empty()
    farm_box_types = []
    for fb in FarmBoxType.objects.filter(is_active=True).order_by('name'):
        avail = available_farm_empty.get(fb.pk, 0)
        if avail > 0:
            farm_box_types.append({'obj': fb, 'available': avail})

    packed_pallets_with_qr = []
    for p in session.packed_pallets.all():
        packed_pallets_with_qr.append({
            'pallet': p,
            'qr_b64': generate_qr_base64(build_scan_url(request, 'packed_pallet_qr_scan', p.packed_pallet_code))
        })

    context = {
        'session': session,
        'box_types': box_types,
        'packaging_groups': packaging_groups,
        'packaging_items': packaging_items,
        'packaging_charge_total': PackingService.packaging_charge_total_for_session(session),
        'packed_pallets_with_qr': packed_pallets_with_qr,
        'farm_box_types': farm_box_types,
    }
    return render(request, 'packing/packing_detail.html', context)


@login_required
@accountant_required
def packing_finalize(request, pk):
    """
    إدخال نواتج المقاسات (P1, Large, M, XL...) والهالك وإنهاء التوضيب
    """
    session = get_object_or_404(PackingSession, pk=pk)
    if session.status in [PackingSession.Status.COMPLETED, PackingSession.Status.APPROVED]:
        messages.warning(request, 'جلسة التوضيب معتمدة ومكتملة مسبقاً.')
        return redirect('packing_detail', pk=session.pk)

    if request.method == 'POST':
        try:
            # قراءة النواتج الديناميكية من النموذج
            sizes = request.POST.getlist('size_name[]')
            box_types = request.POST.getlist('box_type[]')
            box_counts = request.POST.getlist('box_count[]')
            weights = request.POST.getlist('net_weight_kg[]')

            outputs_data = []
            for i in range(len(sizes)):
                if sizes[i] and float(weights[i] or 0) > 0:
                    outputs_data.append({
                        'size_name': sizes[i].strip(),
                        'box_type': box_types[i],
                        'box_count': int(box_counts[i] or 0),
                        'net_weight_kg': Decimal(weights[i]),
                        # كل سطر = طبلية موضبة واحدة بوزنها الخاص وبرمز QR خاص بها
                        'pallet_count': 1,
                        'packaging_items': [],
                    })

            # قراءة أصناف التغليف العادية: كل صنف مرتبط برقم سطر الناتج الخاص به،
            # ويمكن إضافة أكثر من صنف لنفس السطر ومن مجموعات تغليف مختلفة.
            pkg_output_idx = request.POST.getlist('pkg_output_idx[]')
            pkg_item_ids = request.POST.getlist('pkg_item[]')
            pkg_qtys = request.POST.getlist('pkg_qty[]')
            for k in range(len(pkg_item_ids)):
                if not pkg_item_ids[k]:
                    continue
                try:
                    out_idx = int(pkg_output_idx[k]) if k < len(pkg_output_idx) else 0
                except (TypeError, ValueError):
                    out_idx = 0
                try:
                    qty = Decimal(str(pkg_qtys[k] or 0))
                except Exception:  # noqa: BLE001
                    qty = Decimal('0')
                if qty <= 0 or out_idx < 0 or out_idx >= len(outputs_data):
                    continue
                outputs_data[out_idx]['packaging_items'].append({
                    'packaging_item': pkg_item_ids[k],
                    'quantity': qty,
                })

            # قراءة صناديق الفارغ (من المزرعة) المستهلكة: كل صنف مرتبط برقم سطر الناتج
            fe_output_idx = request.POST.getlist('fe_output_idx[]')
            fe_type_ids = request.POST.getlist('fe_type[]')
            fe_qtys = request.POST.getlist('fe_qty[]')
            for k in range(len(fe_type_ids)):
                if not fe_type_ids[k]:
                    continue
                try:
                    out_idx = int(fe_output_idx[k]) if k < len(fe_output_idx) else 0
                except (TypeError, ValueError):
                    out_idx = 0
                try:
                    fe_qty = int(fe_qtys[k] or 0)
                except (TypeError, ValueError):
                    fe_qty = 0
                if fe_qty <= 0 or out_idx < 0 or out_idx >= len(outputs_data):
                    continue
                outputs_data[out_idx].setdefault('farm_empty_items', []).append({
                    'farm_box_type': fe_type_ids[k],
                    'quantity': fe_qty,
                })

            # قراءة الهالك
            waste_types = request.POST.getlist('waste_type[]')
            waste_weights = request.POST.getlist('waste_weight[]')
            wastes_data = []
            for j in range(len(waste_types)):
                if waste_types[j] and float(waste_weights[j] or 0) > 0:
                    wastes_data.append({
                        'waste_type': waste_types[j].strip(),
                        'weight_kg': Decimal(waste_weights[j]),
                        'notes': ''
                    })

            PackingService.record_outputs_and_finalize(
                session=session,
                outputs_data=outputs_data,
                wastes_data=wastes_data,
                user=request.user
            )

            if session.requires_manager_approval:
                msg = f'تم تسجيل النواتج ولكن يوجد فرق وزن غير مفسر ({session.unexplained_diff_kg} كغ). الجلسة تتطلب اعتماد المدير.'
                messages.warning(request, msg)
            else:
                msg = f'تم إكمال التوضيب بنجاح! الرندمان: {session.randeman_pct}%، الموضب: {session.output_packed_kg} كغ.'
                messages.success(request, msg)

            redirect_url = reverse('packing_detail', args=[session.pk])
            if is_ajax(request):
                return ajax_response(ok=True, message=msg, redirect=redirect_url)
            return redirect('packing_detail', pk=session.pk)
        except ValidationError as e:
            msg = ' '.join(e.messages) if e.messages else str(e)
            messages.error(request, msg)
            if is_ajax(request):
                return ajax_response(ok=False, message=msg)
        except Exception as e:
            msg = f'خطأ أثناء الحفظ: {str(e)}'
            messages.error(request, msg)
            if is_ajax(request):
                return ajax_response(ok=False, message=msg)

    return redirect('packing_detail', pk=session.pk)


@login_required
@accountant_required
def packing_edit(request, pk):
    """
    تعديل البيانات الأساسية لعملية توضيب (مسودة فقط):
    التاجر، البضاعة، طريقة التوضيب، الورشة، والطبالي المصدر.
    """
    session = get_object_or_404(PackingSession, pk=pk)
    if session.status != PackingSession.Status.DRAFT:
        messages.warning(request, 'لا يمكن تعديل جلسة توضيب بدأت أو اكتملت.')
        return redirect('packing_detail', pk=session.pk)

    if request.method == 'POST':
        form = StartPackingSessionForm(request.POST, instance=session)
        if form.is_valid():
            try:
                PackingService.update_packing_session(
                    session=session,
                    trader=form.cleaned_data['trader'],
                    crop=form.cleaned_data['crop'],
                    packing_method=form.cleaned_data['packing_method'],
                    source_pallets=form.cleaned_data['source_pallets'],
                    workshop_name=form.cleaned_data['workshop_name'],
                    notes=form.cleaned_data.get('notes'),
                    user=request.user,
                )
                messages.success(request, 'تم تحديث بيانات عملية التوضيب بنجاح.')
                if is_ajax(request):
                    return ajax_response(ok=True, message='تم تحديث بيانات عملية التوضيب بنجاح.',
                                         redirect=reverse('packing_detail', args=[session.pk]))
                return redirect('packing_detail', pk=session.pk)
            except ValidationError as e:
                msg = ' '.join(e.messages) if e.messages else str(e)
                messages.error(request, msg)
                if is_ajax(request):
                    return ajax_response(ok=False, message=msg)
            except Exception as e:  # noqa: BLE001
                msg = f'خطأ أثناء التعديل: {str(e)}'
                messages.error(request, msg)
                if is_ajax(request):
                    return ajax_response(ok=False, message=msg)
    else:
        form = StartPackingSessionForm(instance=session)

    return render(request, 'packing/packing_edit.html', {'form': form, 'session': session})


@login_required
@manager_required
def packing_manager_approve(request, pk):
    """
    اعتماد المدير لعملية التوضيب عند وجود فرق وزن غير مفسر
    """
    session = get_object_or_404(PackingSession, pk=pk)
    if request.method == 'POST':
        approval_notes = request.POST.get('approval_notes', '')
        PackingService.manager_approve_session(session, request.user, approval_notes)
        messages.success(request, f'تم اعتماد عملية التوضيب {session.session_code} من المدير بنجاح.')
    return redirect('packing_detail', pk=session.pk)


@login_required
def packed_pallets_list(request):
    """
    قائمة الطبالي الموضبة: جاهزة، في تبريد المشغل، أو مسلّمة.
    """
    status_filter = request.GET.get('status', '')
    pallets = PackedPallet.objects.select_related(
        'session', 'original_pallet', 'trader', 'crop', 'box_type', 'cold_store', 'shelf'
    ).prefetch_related('cooling_logs').order_by('-created_at')
    if status_filter in dict(PackedPallet.Status.choices):
        pallets = pallets.filter(status=status_filter)
    return render(request, 'packing/packed_pallets_list.html', {
        'pallets': pallets,
        'status_filter': status_filter,
        'status_choices': PackedPallet.Status.choices,
        'cooling_rate': SystemSetting.get_settings().packed_cooling_daily_rate,
    })


@login_required
def packed_pallet_print_label(request, pk):
    pallet = get_object_or_404(PackedPallet, pk=pk)
    qr_url = build_scan_url(request, 'packed_pallet_qr_scan', pallet.packed_pallet_code)
    qr_b64 = generate_qr_base64(qr_url)
    return render(request, 'packing/packed_pallet_label_print.html', {
        'pallet': pallet,
        'qr_url': qr_url,
        'qr_b64': qr_b64,
    })


def packed_pallet_qr_scan(request, packed_pallet_code):
    """
    صفحة تُفتح عند مسح رمز QR الملصق على الطبلية الموضبة وتعرض بياناتها كاملة
    """
    pallet = get_object_or_404(
        PackedPallet.objects.select_related(
            'trader', 'crop', 'box_type', 'original_pallet__truck_receipt',
            'session', 'cold_store', 'shelf',
        ),
        packed_pallet_code=packed_pallet_code
    )
    cooling_logs = pallet.cooling_logs.select_related('moved_by').order_by('-entered_at')
    return render(request, 'packing/packed_pallet_qr_scan.html', {
        'pallet': pallet,
        'cooling_logs': cooling_logs,
        'cooling_days_total': pallet.cooling_days_total,
        'cooling_fee': pallet.cooling_fee,
    })


# ==================== خدمة التبريد اليومية للطبالي الموضبة ====================

@login_required
@accountant_required
def packed_pallet_cooling_in(request):
    """
    إعادة الطبالي الموضبة إلى برادات المشغل (خدمة تبريد يومية)، أو نقلها بين الأسياخ.
    """
    if request.method == 'POST':
        form = PackedPalletCoolingForm(request.POST)
        selected_ids = request.POST.getlist('selected_pallets')
        if form.is_valid():
            if not selected_ids:
                messages.error(request, 'يجب تحديد طبلية موضبة واحدة على الأقل لإدخالها إلى البراد.')
            else:
                try:
                    pallets = PackedPallet.objects.filter(id__in=selected_ids).exclude(
                        status=PackedPallet.Status.DELIVERED
                    ).select_related('trader', 'crop', 'box_type', 'cold_store', 'shelf')
                    store = form.cleaned_data['cold_store']
                    PackingService.send_packed_pallets_to_cooling(
                        packed_pallets=pallets,
                        cold_store=store,
                        shelf=form.cleaned_data['shelf'],
                        user=request.user,
                        notes=form.cleaned_data['notes'],
                    )
                    messages.success(
                        request,
                        f'تم إدخال {pallets.count()} طبلية موضبة إلى {store.name} وبدأ احتساب التبريد اليومي.'
                    )
                    return redirect('packed_pallets_list')
                except Exception as e:
                    messages.error(request, f'خطأ: {str(e)}')
    else:
        form = PackedPalletCoolingForm()

    pallets = PackedPallet.objects.exclude(
        status=PackedPallet.Status.DELIVERED
    ).select_related('trader', 'crop', 'box_type', 'cold_store', 'shelf').prefetch_related(
        'cooling_logs'
    ).order_by('trader__name', 'packed_pallet_code')

    return render(request, 'packing/packed_pallet_cooling.html', {
        'form': form,
        'pallets': pallets,
        'cooling_rate': SystemSetting.get_settings().packed_cooling_daily_rate,
    })


@login_required
@accountant_required
def packed_pallet_cooling_out(request):
    """
    إخراج طبالي موضبة من البراد وإيقاف احتساب التبريد اليومي.
    """
    if request.method == 'POST':
        selected_ids = request.POST.getlist('selected_pallets')
        if not selected_ids:
            messages.error(request, 'يجب تحديد طبلية موضبة واحدة على الأقل لإخراجها من البراد.')
        else:
            try:
                pallets = PackedPallet.objects.filter(
                    id__in=selected_ids, status=PackedPallet.Status.IN_COOLING
                ).select_related('trader', 'crop', 'box_type', 'cold_store', 'shelf')
                PackingService.checkout_packed_pallets_from_cooling(
                    pallets, user=request.user, notes=request.POST.get('notes', '')
                )
                messages.success(
                    request,
                    f'تم إخراج {pallets.count()} طبلية موضبة من البراد وإيقاف احتساب التبريد.'
                )
            except Exception as e:
                messages.error(request, f'خطأ: {str(e)}')
    return redirect('packed_pallets_list')


@login_required
def packed_pallet_cooling_history(request, pk):
    """
    سجل فترات التبريد لطبلية موضبة واحدة (أيام وأجرة كل فترة).
    """
    pallet = get_object_or_404(
        PackedPallet.objects.select_related('trader', 'crop', 'box_type', 'cold_store', 'shelf'), pk=pk
    )
    logs = list(pallet.cooling_logs.select_related('cold_store', 'shelf', 'moved_by').order_by('-entered_at'))
    rate = SystemSetting.get_settings().packed_cooling_daily_rate or Decimal('0.000')
    rows = []
    for log in logs:
        days = log.days_counted if log.exited_at else log.live_days
        rows.append({
            'log': log,
            'days': days,
            'fee': (Decimal(days) * rate).quantize(Decimal('0.001')),
        })
    return render(request, 'packing/packed_pallet_cooling_history.html', {
        'pallet': pallet,
        'rows': rows,
        'rate': rate,
        'total_days': pallet.cooling_days_total,
        'total_fee': pallet.cooling_fee,
    })


@login_required
def packed_pallet_move(request, pk):
    """
    نقل طبلية موضبة بين الأسياخ/البرادات عبر السحب والإفلات (استجابة JSON).
    الإدخال أو النقل يُغلق الفترة الحالية ويفتح فترة جديدة بنوع «نقل» دون فقدان الأيام المحتسبة.
    """
    if request.method != 'POST':
        return error_response('طريقة الطلب غير مدعومة.', status=405)
    if not request.user.is_accountant:
        return error_response('نقل الطبالي يتطلب صلاحية محاسب أو مدير.', status=403)

    pallet = get_object_or_404(
        PackedPallet.objects.select_related('trader', 'crop', 'box_type', 'cold_store', 'shelf'), pk=pk
    )
    if pallet.status == PackedPallet.Status.DELIVERED:
        return error_response(f'الطبلية {pallet.packed_pallet_code} مسلّمة للتاجر ولا يمكن نقلها.')

    to_store, to_shelf, err = resolve_target(move_payload(request), Shelf, ColdStore)
    if err:
        return error_response(err)

    if (pallet.cold_store_id == to_store.pk
            and pallet.shelf_id == (to_shelf.pk if to_shelf else None)):
        return success_response(
            f'الطبلية {pallet.packed_pallet_code} موجودة أصلاً في هذا الموقع — لا حاجة للنقل.',
            pallet.pk, to_store.pk, to_shelf.pk if to_shelf else None, moved=False,
        )

    try:
        PackingService.send_packed_pallets_to_cooling(
            packed_pallets=[pallet],
            cold_store=to_store,
            shelf=to_shelf,
            user=request.user,
            notes='نقل عبر السحب والإفلات من خريطة البرادات والأسياخ',
        )
    except ValidationError as e:
        return error_response(validation_error_message(e))
    except Exception as e:  # noqa: BLE001
        return error_response(f'تعذّر النقل: {e}')

    location = to_store.name + (f' — السيخ {to_shelf.shelf_number}' if to_shelf else '')
    return success_response(
        f'تم نقل الطبلية الموضبة {pallet.packed_pallet_code} إلى {location}',
        pallet.pk, to_store.pk, to_shelf.pk if to_shelf else None,
    )


@login_required
def cooling_statement(request):
    """
    كشف خدمة التبريد اليومية للطبالي الموضبة: عدد الأيام × الأجرة اليومية المعتمدة.
    """
    trader_id = request.GET.get('trader') or ''
    only_in_cooling = request.GET.get('in_cooling') == '1'
    trader = Trader.objects.filter(id=trader_id).first() if trader_id.isdigit() else None

    statement = PackingService.build_cooling_statement(trader=trader, only_in_cooling=only_in_cooling)
    return render(request, 'packing/cooling_statement.html', {
        'statement': statement,
        'traders': Trader.objects.order_by('name'),
        'selected_trader': trader,
        'only_in_cooling': only_in_cooling,
    })


@login_required
def delivery_notes_list(request):
    notes = DeliveryNote.objects.select_related('trader', 'created_by').order_by('-delivery_date', '-created_at')
    return render(request, 'packing/delivery_notes_list.html', {'notes': notes})


@login_required
@accountant_required
def delivery_note_create(request):
    """
    إنشاء بيان تسليم عند خروج الموضب للتاجر
    """
    if request.method == 'POST':
        form = DeliveryNoteForm(request.POST)
        selected_pallet_ids = request.POST.getlist('selected_pallets')
        if form.is_valid():
            if not selected_pallet_ids:
                messages.error(request, 'يجب تحديد طبلية موضبة واحدة على الأقل للتسليم.')
            else:
                try:
                    pallets = PackedPallet.objects.filter(
                        id__in=selected_pallet_ids,
                        status__in=[PackedPallet.Status.PACKED, PackedPallet.Status.IN_COOLING],
                    ).select_related('trader', 'crop', 'box_type')
                    note = PackingService.create_delivery_note(
                        trader=form.cleaned_data['trader'],
                        packed_pallets=pallets,
                        driver_name=form.cleaned_data['driver_name'],
                        truck_plate=form.cleaned_data['truck_plate'],
                        receiver_name=form.cleaned_data['receiver_name'],
                        notes=form.cleaned_data['notes'],
                        user=request.user
                    )
                    messages.success(request, f'تم إنشاء بيان التسليم {note.delivery_number} بنجاح.')
                    return redirect('delivery_note_detail', pk=note.pk)
                except Exception as e:
                    messages.error(request, f'خطأ: {str(e)}')
    else:
        form = DeliveryNoteForm()

    # الطبالي الموضبة المتاحة للتسليم
    available_pallets = PackedPallet.objects.filter(
        status__in=[PackedPallet.Status.PACKED, PackedPallet.Status.IN_COOLING]
    ).select_related('trader', 'crop', 'box_type', 'cold_store', 'shelf')
    return render(request, 'packing/delivery_note_form.html', {'form': form, 'available_pallets': available_pallets})


@login_required
def delivery_note_detail(request, pk):
    note = get_object_or_404(DeliveryNote.objects.prefetch_related('pallets'), pk=pk)
    return render(request, 'packing/delivery_note_detail.html', {'note': note})
