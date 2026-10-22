from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.utils import timezone
from django.db.models import Sum, Count, Q
from django.db import transaction
from decimal import Decimal

from core.permissions import accountant_required, manager_required
from core.models import AuditLog
from core.pagination import paginate
from operations.models import Trader
from .models import TreasuryAccount, Expense, ExpenseCategory, Check, Payment, InternalTransfer, TraderLedgerEntry
from .forms import (
    TreasuryAccountForm, ExpenseForm, ExpenseCategoryForm,
    PaymentForm, CheckForm, InternalTransferForm,
)
from .services import FinanceService


# ==========================================
# 1. الخزينة والحسابات البنكية (Treasury)
# ==========================================

@login_required
def treasury_dashboard(request):
    """
    لوحة الخزينة والمقبوضات والمدفوعات
    """
    accounts = TreasuryAccount.objects.all()
    total_balance = accounts.aggregate(total=Sum('current_balance'))['total'] or Decimal('0.000')

    recent_payments = Payment.objects.select_related('trader', 'treasury_account').order_by('-payment_date')[:5]
    recent_expenses = Expense.objects.select_related('category', 'treasury_account').order_by('-due_date')[:5]
    checks_under_collection = Check.objects.filter(status__in=[Check.Status.REGISTERED, Check.Status.UNDER_COLLECTION])

    kpis = FinanceService.calculate_financial_kpis()

    context = {
        'accounts': accounts,
        'total_balance': total_balance,
        'recent_payments': recent_payments,
        'recent_expenses': recent_expenses,
        'checks_under_collection': checks_under_collection,
        'kpis': kpis,
    }
    return render(request, 'finance/treasury_dashboard.html', context)


@login_required
@manager_required
def treasury_account_create(request):
    if request.method == 'POST':
        form = TreasuryAccountForm(request.POST)
        if form.is_valid():
            acc = form.save(commit=False)
            acc.current_balance = acc.opening_balance
            acc.save()
            messages.success(request, f'تمت إضافة الحساب {acc.name} بنجاح.')
            return redirect('treasury_dashboard')
    else:
        form = TreasuryAccountForm()
    return render(request, 'finance/account_form.html', {'form': form, 'title': 'إضافة حساب خزينة أو بنك'})


# ==========================================
# 2. سندات القبض والتحصيلات (Payments)
# ==========================================

@login_required
def payment_list(request):
    payments = Payment.objects.select_related('trader', 'treasury_account', 'created_by').prefetch_related(
        'invoices'
    ).order_by('-payment_date', '-pk')
    query = (request.GET.get('q') or '').strip()
    if query:
        payments = payments.filter(
            Q(receipt_number__icontains=query) | Q(trader__name__icontains=query)
            | Q(trader__code__icontains=query) | Q(notes__icontains=query)
        )
    page = paginate(request, payments)
    return render(request, 'finance/payment_list.html', {**page, 'payments': page['page_obj'], 'query': query})


@login_required
@accountant_required
def payment_create(request):
    """
    تسجيل سند قبض / تحصيل من تاجر
    """
    if request.method == 'POST':
        form = PaymentForm(request.POST)
        if form.is_valid():
            try:
                payment = FinanceService.record_payment(
                    trader=form.cleaned_data['trader'],
                    amount=form.cleaned_data['amount'],
                    payment_method=form.cleaned_data['payment_method'],
                    treasury_account=form.cleaned_data['treasury_account'],
                    invoices=form.cleaned_data['invoices'],
                    notes=form.cleaned_data['notes'],
                    user=request.user
                )
                messages.success(request, f'تم تسجيل سند القبض {payment.receipt_number} بنجاح.')
                return redirect('payment_list')
            except Exception as e:
                messages.error(request, f'خطأ: {str(e)}')
    else:
        initial = {}
        if request.GET.get('trader'):
            initial['trader'] = request.GET.get('trader')
        form = PaymentForm(initial=initial)

    return render(request, 'finance/payment_form.html', {'form': form, 'title': 'سند قبض / تحصيل دفعة'})


# ==========================================
# 3. المصاريف التشغيلية (Expenses)
# ==========================================

@login_required
def expense_list(request):
    expenses = Expense.objects.select_related('category', 'treasury_account', 'created_by').order_by('-due_date')
    query = (request.GET.get('q') or '').strip()
    if query:
        expenses = expenses.filter(
            Q(description__icontains=query) | Q(beneficiary__icontains=query)
            | Q(category__name__icontains=query)
        )
    page = paginate(request, expenses)
    return render(request, 'finance/expense_list.html', {**page, 'expenses': page['page_obj'], 'query': query})


@login_required
@accountant_required
def expense_create(request):
    """
    تسجيل مصروف تشغيلي
    """
    if request.method == 'POST':
        form = ExpenseForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                exp = FinanceService.record_expense(
                    category=form.cleaned_data['category'],
                    amount=form.cleaned_data['amount'],
                    due_date=form.cleaned_data['due_date'],
                    status=form.cleaned_data['status'],
                    treasury_account=form.cleaned_data['treasury_account'],
                    beneficiary=form.cleaned_data['beneficiary'],
                    description=form.cleaned_data['description'],
                    is_recurring=form.cleaned_data['is_recurring'],
                    recurrence_key=form.cleaned_data['recurrence_key'],
                    receipt_image=request.FILES.get('receipt_image'),
                    user=request.user
                )
                messages.success(request, f'تم تسجيل المصروف بقيمة {exp.amount} دينار بنجاح.')
                return redirect('expense_list')
            except Exception as e:
                messages.error(request, f'خطأ: {str(e)}')
    else:
        form = ExpenseForm(initial={'due_date': timezone.now().date()})

    return render(request, 'finance/expense_form.html', {'form': form, 'title': 'تسجيل مصروف تشغيلي'})


# ==========================================
# 3-ب. تصنيفات المصاريف (إدارة وإضافة سريعة)
# ==========================================

@login_required
def expense_category_list(request):
    """إدارة تصنيفات المصاريف: عرض الأصناف وعدد مصاريف كل تصنيف."""
    categories = ExpenseCategory.objects.annotate(
        expenses_count=Count('expenses'),
        expenses_total=Sum('expenses__amount'),
    ).order_by('name')
    return render(request, 'finance/expense_category_list.html', {
        'categories': categories,
        'form': ExpenseCategoryForm(),
    })


@login_required
@accountant_required
def expense_category_create(request):
    """إضافة تصنيف مصاريف جديد (من صفحة إدارة التصنيفات)."""
    if request.method == 'POST':
        form = ExpenseCategoryForm(request.POST)
        if form.is_valid():
            obj = form.save()
            AuditLog.log(request.user, 'CREATE_EXPENSE_CATEGORY', obj,
                         reason=f'إضافة تصنيف مصاريف: {obj.name}')
            messages.success(request, f'تمت إضافة التصنيف «{obj.name}».')
            return redirect('expense_category_list')
        for field, errs in form.errors.items():
            for e in errs:
                messages.error(request, e)
        return redirect('expense_category_list')
    return redirect('expense_category_list')


@login_required
@accountant_required
def expense_category_edit(request, pk):
    """تعديل اسم/رمز تصنيف مصاريف."""
    obj = get_object_or_404(ExpenseCategory, pk=pk)
    if request.method == 'POST':
        form = ExpenseCategoryForm(request.POST, instance=obj)
        if form.is_valid():
            obj = form.save()
            AuditLog.log(request.user, 'UPDATE_EXPENSE_CATEGORY', obj,
                         reason=f'تعديل تصنيف مصاريف: {obj.name}')
            messages.success(request, f'تم تعديل التصنيف «{obj.name}».')
            return redirect('expense_category_list')
        for field, errs in form.errors.items():
            for e in errs:
                messages.error(request, e)
        return redirect('expense_category_list')
    return redirect('expense_category_list')


@login_required
@accountant_required
def expense_category_quick_add(request):
    """
    إضافة تصنيف مصاريف سريعة من داخل نموذج المصروف دون مغادرة الصفحة (JSON).
    يعيد {ok, id, name} عند النجاح، أو {ok:false, error} عند وجود تصنيف بنفس الاسم.
    """
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'طريقة غير مسموحة.'}, status=405)
    name = (request.POST.get('name') or '').strip()
    code = (request.POST.get('code') or '').strip()
    if not name:
        return JsonResponse({'ok': False, 'error': 'اسم التصنيف مطلوب.'}, status=400)

    existing = ExpenseCategory.objects.filter(name__iexact=name).first()
    if existing:
        # تصنيف موجود بنفس الاسم: أعده ليُختار تلقائياً بدل رفض العملية
        return JsonResponse({'ok': True, 'id': existing.pk, 'name': existing.name, 'existed': True})

    try:
        with transaction.atomic():
            obj = ExpenseCategory.objects.create(name=name, code=code)
            AuditLog.log(request.user, 'CREATE_EXPENSE_CATEGORY', obj,
                         reason=f'إضافة سريعة لتصنيف مصاريف من نموذج المصروف: {obj.name}')
    except Exception as e:  # noqa: BLE001
        return JsonResponse({'ok': False, 'error': f'تعذّرت الإضافة: {e}'}, status=400)
    return JsonResponse({'ok': True, 'id': obj.pk, 'name': obj.name, 'existed': False})


@login_required
@manager_required
def expense_approve(request, pk):
    if request.method != 'POST':
        from django.http import HttpResponseNotAllowed
        return HttpResponseNotAllowed(['POST'])
    exp = get_object_or_404(Expense, pk=pk)
    exp.is_approved = True
    exp.approved_by = request.user
    exp.save()
    messages.success(request, f'تم اعتماد المصروف {exp.description} بقيمة {exp.amount} دينار.')
    return redirect('expense_list')


@login_required
@manager_required
def expense_cancel(request, pk):
    """
    إلغاء مصروف بقيد عكسي موثق (لا حذف صامت) بصلاحية المدير.
    """
    exp = get_object_or_404(Expense, pk=pk)
    if request.method == 'POST':
        reason = request.POST.get('reason', '').strip() or 'إلغاء مصروف من لوحة المدير'
        try:
            FinanceService.cancel_expense(exp, reason=reason, user=request.user)
            messages.warning(request, f'تم إلغاء المصروف وإعادة قيده العكسي بنجاح. السبب: {reason}')
        except Exception as e:
            messages.error(request, f'خطأ: {str(e)}')
    return redirect('expense_list')


# ==========================================
# 4. الشيكات وإدارة دورة حياتها (Checks)
# ==========================================

@login_required
def check_list(request):
    checks = Check.objects.select_related('trader', 'target_treasury').order_by('due_date')
    query = (request.GET.get('q') or '').strip()
    if query:
        checks = checks.filter(
            Q(check_number__icontains=query) | Q(bank_name__icontains=query)
            | Q(trader__name__icontains=query) | Q(trader__code__icontains=query)
        )
    page = paginate(request, checks)
    treasury_accounts = TreasuryAccount.objects.filter(is_active=True)
    return render(request, 'finance/check_list.html', {
        **page, 'checks': page['page_obj'], 'query': query,
        'treasury_accounts': treasury_accounts,
    })


@login_required
@accountant_required
def check_create(request):
    if request.method == 'POST':
        form = CheckForm(request.POST)
        if form.is_valid():
            check = form.save(commit=False)
            check.created_by = request.user
            check.status = Check.Status.REGISTERED
            check.status_history = [{
                'status': Check.Status.REGISTERED,
                'date': str(timezone.now().date()),
                'user': request.user.username,
                'action': 'تسجيل شيك جديد'
            }]
            check.save()
            messages.success(request, f'تم تسجيل الشيك رقم {check.check_number} بنجاح.')
            return redirect('check_list')
    else:
        form = CheckForm(initial={'due_date': timezone.now().date()})
    return render(request, 'finance/check_form.html', {'form': form, 'title': 'تسجيل شيك بنكي جديد'})


@login_required
@accountant_required
def check_collect(request, pk):
    check = get_object_or_404(Check, pk=pk)
    if request.method == 'POST':
        treasury_id = request.POST.get('target_treasury')
        target_treasury = get_object_or_404(TreasuryAccount, pk=treasury_id)
        try:
            FinanceService.collect_check(check, target_treasury=target_treasury, user=request.user)
            messages.success(request, f'تم تحصيل الشيك #{check.check_number} وإيداع {check.amount} دينار في {target_treasury.name}.')
        except Exception as e:
            messages.error(request, f'خطأ: {str(e)}')
    return redirect('check_list')


@login_required
@accountant_required
def check_return(request, pk):
    check = get_object_or_404(Check, pk=pk)
    if request.method == 'POST':
        reason = request.POST.get('reason', 'شيك مرتجع')
        try:
            FinanceService.return_check(check, reason=reason, user=request.user)
            messages.warning(request, f'تم تسجيل ارتجاع الشيك #{check.check_number} وإعادة المبلغ إلى ذمة التاجر {check.trader.name}.')
        except Exception as e:
            messages.error(request, f'خطأ: {str(e)}')
    return redirect('check_list')


# ==========================================
# 5. التحويلات الداخلية وكشف حساب التاجر
# ==========================================

@login_required
@accountant_required
def internal_transfer_create(request):
    if request.method == 'POST':
        form = InternalTransferForm(request.POST)
        if form.is_valid():
            try:
                transfer = FinanceService.internal_transfer(
                    from_account=form.cleaned_data['from_account'],
                    to_account=form.cleaned_data['to_account'],
                    amount=form.cleaned_data['amount'],
                    notes=form.cleaned_data['notes'],
                    user=request.user
                )
                messages.success(request, f'تم التحويل الداخلي {transfer.transfer_number} بقيمة {transfer.amount} دينار بنجاح.')
                return redirect('treasury_dashboard')
            except Exception as e:
                messages.error(request, f'خطأ: {str(e)}')
    else:
        form = InternalTransferForm()
    return render(request, 'finance/transfer_form.html', {'form': form, 'title': 'تحويل داخلي بين الحسابات'})


@login_required
def trader_financial_statement(request, trader_id):
    """
    كشف الحساب المالي المرتب للتاجر:
    استلام، توضيب، صناديق، دفعات، والرصيد التراكمي
    """
    trader = get_object_or_404(Trader, pk=trader_id)
    entries = trader.ledger_entries.all().order_by('timestamp', 'id')
    invoices = trader.invoices.all().order_by('-year', '-created_at')
    payments = trader.payments.all().order_by('-payment_date')
    checks = trader.checks.all().order_by('-due_date')

    total_debit = sum(e.debit for e in entries)
    total_credit = sum(e.credit for e in entries)
    current_net_debt = total_debit - total_credit

    context = {
        'trader': trader,
        'entries': entries,
        'invoices': invoices,
        'payments': payments,
        'checks': checks,
        'total_debit': total_debit,
        'total_credit': total_credit,
        'current_net_debt': current_net_debt,
    }
    return render(request, 'finance/trader_statement.html', context)
