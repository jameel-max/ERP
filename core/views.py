from decimal import Decimal

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from django.db.models import Sum, Count
from django.db import transaction
from django.apps import apps
from django.urls import reverse
from django.http import Http404
from django.db.models.deletion import ProtectedError, RestrictedError, CASCADE, PROTECT, RESTRICT

from .models import Notification, SystemSetting, AuditLog, Attachment
from .forms import CustomLoginForm, SystemSettingForm
from .permissions import manager_required, owner_required, accountant_required
from .delete_registry import is_deletable
from .utils import save_form_transactionally
from .attachments import MAX_IMAGES_PER_OBJECT, is_attachable, save_uploaded_images
from .wipe_service import wipe_database, CONFIRM_PHRASE
from operations.models import TruckReceipt, Pallet, ColdStore, BoxType, Trader
from operations.models import annotate_cold_store_occupancy, annotate_shelf_occupancy
from invoicing.models import Invoice


def user_login(request):
    if request.user.is_authenticated:
        return redirect('home')
    if request.method == 'POST':
        form = CustomLoginForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)
            messages.success(request, f'مرحباً بك، {user.get_full_name() or user.username}!')
            return redirect('home')
        else:
            messages.error(request, 'خطأ في اسم المستخدم أو كلمة المرور.')
    else:
        form = CustomLoginForm()
    return render(request, 'core/login.html', {'form': form})


def user_logout(request):
    logout(request)
    messages.info(request, 'تم تسجيل الخروج بنجاح.')
    return redirect('login')


@login_required
def home(request):
    """
    لوحة التحكم الرئيسية:
    - ملخص سريع: سيارات اليوم، بضاعة بالبرادات، طبالي قيد التوضيب، رصيد الصناديق، إشغال البرادات.
    - جرس الإشعارات
    - روابط سريعة
    """
    today = timezone.now().date()
    settings_obj = SystemSetting.get_settings()

    # نخزّن سياق الصفحة الرئيسية (مؤشرات تجميعية عامة) لمدة قصيرة.
    # قاعدة البيانات خارجية ومسافة الشبكة كبيرة ≈170ms لكل استعلام، والصفحة
    # تعرض مؤشرات لا تتغير كل ثانية. on_commit يمنع تسرّب الكاش بين اختبارات TestCase.
    from django.core.cache import cache
    from django.db import transaction
    _cache_key = 'core_home_context'
    cached = cache.get(_cache_key)
    if cached is not None:
        return render(request, 'core/home.html', cached)

    # استعلامات مجمّعة واحدة بدل عدة استعلامات (لأن قاعدة البيانات خارجية والشبكة بطيئة)
    pallet_stats = Pallet.objects.filter(status=Pallet.Status.IN_COOLING).aggregate(
        count=Count('id'),
        boxes=Sum('box_count'),
        net=Sum('estimated_net_weight'),
    )

    # 1. سيارات اليوم
    today_trucks_count = TruckReceipt.objects.filter(arrival_date=today).count()

    # 2. بضاعة بالبرادات
    cooling_pallets_count = pallet_stats['count'] or 0
    cooling_boxes_count = pallet_stats['boxes'] or 0
    cooling_net_weight = pallet_stats['net'] or Decimal('0.000')

    # 3. طبالي قيد التوضيب + 4. رصيد الصناديق الفارغة (استعلام مزدوج واحد)
    dual = Pallet.objects.filter(status=Pallet.Status.OUT_FOR_PACKING).aggregate(c=Count('id'))
    packing_pallets_count = dual['c'] or 0
    total_crates_stock = BoxType.objects.aggregate(total=Sum('company_stock'))['total'] or 0

    # 5. إشغال البرادات (باستعلام مجمّع واحد لكل البرادات والأسياخ)
    cold_stores = annotate_cold_store_occupancy(
        ColdStore.objects.prefetch_related('shelves').all()
    )
    all_shelves = [sh for s in cold_stores for sh in s.shelves.all()]
    annotate_shelf_occupancy(all_shelves)

    # 6. تنبيهات الأقدم أولاً FIFO (فلترة من قاعدة البيانات بدل تحميل كل الطبالي)
    fifo_alerts = []
    if settings_obj.fifo_alert_days:
        threshold_days = int(settings_obj.fifo_alert_days)
        cutoff = timezone.now() - timezone.timedelta(days=threshold_days)
        fifo_alerts = list(
            Pallet.objects.filter(
                status=Pallet.Status.IN_COOLING,
                entered_cooling_at__lte=cutoff,
            ).select_related('trader', 'crop', 'cold_store', 'shelf')[:20]
        )

    # أحدث 5 سيارات (نحوّلها لقائمة لتخزينها في الكاش)
    recent_trucks = list(TruckReceipt.objects.select_related(
        'trader', 'crop', 'box_type', 'cold_store'
    ).order_by('-created_at')[:5])

    # أحدث الإشعارات
    unresolved_notifications = list(Notification.objects.filter(is_resolved=False).order_by('-created_at')[:6])

    context = {
        'today_trucks_count': today_trucks_count,
        'cooling_pallets_count': cooling_pallets_count,
        'cooling_boxes_count': cooling_boxes_count,
        'cooling_net_weight': cooling_net_weight,
        'packing_pallets_count': packing_pallets_count,
        'total_crates_stock': total_crates_stock,
        'cold_stores': cold_stores,
        'fifo_alerts': fifo_alerts,
        'recent_trucks': recent_trucks,
        'unresolved_notifications': unresolved_notifications,
        'traders_count': Trader.objects.filter(is_active=True).count(),
    }
    # نخزّن السياق بعد الـ commit فقط (سليم للاختبارات وسريع في التشغيل)
    transaction.on_commit(lambda: cache.set(_cache_key, context, 30))
    return render(request, 'core/home.html', context)


@login_required
def notification_list(request):
    notifications = Notification.objects.all().order_by('-created_at')
    return render(request, 'core/notifications.html', {'notifications': notifications})


@login_required
def notification_resolve(request, pk):
    notif = get_object_or_404(Notification, pk=pk)
    notif.is_resolved = True
    notif.resolved_at = timezone.now()
    notif.save()
    messages.success(request, f'تم وضع الإشعار "{notif.title}" كمنتهي.')
    return redirect(request.META.get('HTTP_REFERER', 'notifications'))


@login_required
@manager_required
def system_settings(request):
    settings_obj = SystemSetting.get_settings()
    if request.method == 'POST':
        form = SystemSettingForm(request.POST, instance=settings_obj)
        if form.is_valid():
            form.save()
            AuditLog.log(
                user=request.user,
                action='UPDATE_SYSTEM_SETTINGS',
                instance=settings_obj,
                reason='تعديل إعدادات النظام من لوحة المدير',
                details={k: str(v) for k, v in form.cleaned_data.items()}
            )
            messages.success(request, 'تم حفظ إعدادات النظام بنجاح.')
            return redirect('system_settings')
    else:
        form = SystemSettingForm(instance=settings_obj)
    return render(request, 'core/settings.html', {'form': form, 'settings_obj': settings_obj})


@login_required
@manager_required
def audit_logs(request):
    logs = AuditLog.objects.select_related('user').order_by('-timestamp')[:200]
    return render(request, 'core/audit_logs.html', {'logs': logs})


@login_required
@owner_required
def database_wipe(request):
    """
    تفريغ قاعدة البيانات — لمالك النظام فقط.

    النموذجان:
    - OPERATIONS: يحذف الحركات ويُبقي البيانات المرجعية (التجار، البرادات، الصناديق...).
    - FULL: يحذف كل شيء ما عدا المستخدمين وإعدادات النظام.

    يتطلب كتابة عبارة التأكيد حرفيًا. لا رجعة في العملية.
    """
    from operations.models import Trader, BoxType, ColdStore
    from finance.models import TreasuryAccount, Payment, Expense

    if request.method == 'POST':
        mode = request.POST.get('mode', 'OPERATIONS')
        if mode not in ('OPERATIONS', 'FULL'):
            mode = 'OPERATIONS'
        confirm_text = request.POST.get('confirm_text', '')
        try:
            deleted, total = wipe_database(request.user, mode, confirm_text)
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect('database_wipe')
        except Exception:
            messages.error(
                request,
                'حدث خطأ أثناء التفريغ ولم يتم حذف أي بيانات. راجع سجل التدقيق أو تواصل مع المسؤول.',
            )
            return redirect('database_wipe')

        # نعرض ملخص ما تم حذفه فعلياً (لا أرقام تقديرية)
        summary = [{'label': label, 'count': count} for label, count in deleted]
        messages.success(
            request,
            f'تم التفريغ بنجاح. إجمالي السجلات المحذوفة: {total}.',
        )
        return render(request, 'core/database_wipe_done.html', {
            'summary': summary,
            'total': total,
            'mode': mode,
        })

    context = {
        'confirm_phrase': CONFIRM_PHRASE,
        'traders_count': Trader.objects.count(),
        'box_types_count': BoxType.objects.count(),
        'cold_stores_count': ColdStore.objects.count(),
        'treasury_count': TreasuryAccount.objects.count(),
        'payments_count': Payment.objects.count(),
        'expenses_count': Expense.objects.count(),
        'pallets_count': Pallet.objects.count(),
        'invoices_count': Invoice.objects.count(),
    }
    return render(request, 'core/database_wipe.html', context)


# ==========================================
# الحذف المنفرد (Delete single record)
# ==========================================

def _safe_next(request, candidate=None):
    """يعيد رابطاً داخلياً آمناً للعودة بعده، وإلا لوحة التحكم."""
    url = candidate or request.META.get('HTTP_REFERER', '')
    if not url:
        return reverse('home')
    host = request.get_host()
    # نقبل الروابط النسبية أو التي تنتمي لنفس الموقع فقط (منع إعادة التوجيه المفتوح)
    if url.startswith('/') or host in url:
        return url
    return reverse('home')


def _related_summary(obj):
    """ملخّص السجلات المرتبطة التي ستتأثر بالحذف (للعرض في صفحة التأكيد)."""
    summary = []
    for rel in obj._meta.related_objects:
        accessor = rel.get_accessor_name()
        if not accessor:
            continue
        try:
            count = getattr(obj, accessor).count()
        except Exception:
            continue
        if not count:
            continue
        on_delete = rel.field.remote_field.on_delete
        if on_delete is CASCADE:
            effect = 'يُحذف معه تلقائياً'
            blocking = False
        elif on_delete in (PROTECT, RESTRICT):
            effect = 'يمنع الحذف — يجب حذفه أولاً'
            blocking = True
        else:
            effect = 'يُبقى مع إلغاء الربط'
            blocking = False
        summary.append({
            'model': rel.related_model._meta.verbose_name,
            'count': count,
            'effect': effect,
            'blocking': blocking,
        })
    return summary


@login_required
@manager_required
def generic_delete(request, app_label, model_name, pk):
    """
    حذف أي سجل «بشكل منفرد» من النظام مع شاشة تأكيد واضحة وسجل تدقيق.

    - لا يسمح إلا بالأنواع المدرجة في core/delete_registry.py.
    - إن كان السجل مرتبطاً بسجلات محميّة (PROTECT) يُرفض الحذف برسالة عربية واضحة
      تشرح ما يجب حذفه أولاً — لا يظهر أي خطأ 500.
    """
    if not is_deletable(app_label, model_name):
        messages.error(request, 'هذا النوع من السجلات غير قابل للحذف المنفرد من هذه الشاشة.')
        return redirect('home')

    try:
        model = apps.get_model(app_label, model_name)
    except LookupError:
        messages.error(request, 'النوع المطلوب غير موجود في النظام.')
        return redirect('home')

    obj = get_object_or_404(model, pk=pk)
    label = str(obj)
    related = _related_summary(obj)
    blocking = [r for r in related if r['blocking']]
    next_url = _safe_next(request, request.POST.get('next') if request.method == 'POST' else None)

    if request.method == 'POST':
        if blocking:
            detail = '، '.join(f"{r['count']} {r['model']}" for r in blocking)
            messages.error(
                request,
                f'لا يمكن حذف «{label}» لوجود سجلات مرتبطة به: {detail}. '
                'احذف السجلات المرتبطة أولاً ثم أعد المحاولة.',
            )
            return redirect(request.path)

        obj_pk = obj.pk
        try:
            with transaction.atomic():
                obj.delete()
        except (ProtectedError, RestrictedError) as exc:
            protected = [str(p) for p in getattr(exc, 'protected_objects', []) or []]
            shown = '، '.join(protected[:5]) if protected else 'سجلات أخرى مرتبطة'
            messages.error(
                request,
                f'لا يمكن حذف «{label}» لأن هناك سجلات مرتبطة به ({shown}). '
                'احذف السجلات المرتبطة أولاً.',
            )
            return redirect(request.path)

        AuditLog.objects.create(
            user=request.user if request.user.is_authenticated else None,
            action=f'DELETE_{model.__name__.upper()}',
            model_name=model.__name__,
            object_id=str(obj_pk),
            object_repr=label[:255],
            details={'related': [{'model': r['model'], 'count': r['count']} for r in related]},
            reason='حذف سجل بشكل منفرد',
        )
        messages.success(request, f'تم حذف «{label}» بنجاح.')
        return redirect(next_url)

    return render(request, 'core/confirm_delete.html', {
        'obj': obj,
        'label': label,
        'model_label': model._meta.verbose_name,
        'app_label': app_label,
        'model_name': model_name,
        'related': related,
        'blocking': blocking,
        'next_url': _safe_next(request),
    })


# ==========================================
# نظام إرفاق الصور العام (عدة صور لكل سجل)
# ==========================================

@login_required
@accountant_required
def attachments_upload(request, app_label, model_name, pk):
    """
    رفع صورة أو أكثر (تحديد متعدد) وإرفاقها بسجل محدد.

    - يتحقق من أن نوع السجل مسموح (ATTACHABLE_MODELS) ومن وجود السجل نفسه.
    - يتحقق من الامتداد الحجم وصحة الصورة، ويتجاهل الملفات غير الصالحة برسالة واضحة.
    """
    if request.method != 'POST':
        raise Http404

    next_url = _safe_next(request, request.POST.get('next'))

    if not is_attachable(app_label, model_name):
        messages.error(request, 'لا يمكن إرفاق صور لهذا النوع من السجلات.')
        return redirect(next_url)

    try:
        model = apps.get_model(app_label, model_name)
    except LookupError:
        messages.error(request, 'النوع المطلوب غير موجود في النظام.')
        return redirect(next_url)

    obj = get_object_or_404(model, pk=pk)

    files = request.FILES.getlist('images')
    caption = (request.POST.get('caption') or '').strip()[:200]

    if not files:
        messages.error(request, 'لم تختر أي صورة. الرجاء اختيار صورة واحدة على الأقل.')
        return redirect(next_url)

    saved, rejected = save_uploaded_images(
        obj, files, caption=caption, user=request.user if request.user.is_authenticated else None
    )

    if saved:
        AuditLog.objects.create(
            user=request.user if request.user.is_authenticated else None,
            action='UPLOAD_ATTACHMENTS',
            model_name=model.__name__,
            object_id=str(obj.pk),
            object_repr=str(obj)[:255],
            details={'count': saved, 'rejected': rejected},
            reason='إرفاق صور بالسجل',
        )
        message = f'تم رفع {saved} صورة بنجاح.'
        if rejected:
            message += f' وتم تجاهل {rejected} ملفاً (نوع غير مدعوم، أو حجم كبير، أو تجاوز الحد الأقصى).'
        messages.success(request, message)
    else:
        messages.error(
            request,
            'لم يتم رفع أي صورة: تأكد أن الملفات صور صالحة (JPG/PNG/WEBP/GIF) '
            f'وحجم كل صورة أقل من 8 ميجابايت وأنك لم تتجاوز الحد الأقصى ({MAX_IMAGES_PER_OBJECT} صورة).'
        )

    return redirect(next_url)


@login_required
@accountant_required
def attachment_delete(request, pk):
    """حذف صورة مرفقة بشكل منفرد (مع حذف ملفها من التخزين)."""
    attachment = get_object_or_404(Attachment, pk=pk)
    next_url = _safe_next(request, request.POST.get('next'))

    if request.method == 'POST':
        label = str(attachment)
        with transaction.atomic():
            attachment.delete()
        AuditLog.objects.create(
            user=request.user if request.user.is_authenticated else None,
            action='DELETE_ATTACHMENT',
            model_name='Attachment',
            object_id=str(pk),
            object_repr=label[:255],
            reason='حذف صورة مرفقة بشكل منفرد',
        )
        messages.success(request, 'تم حذف الصورة.')

    return redirect(next_url)
