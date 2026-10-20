from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from django.db.models import Sum
from decimal import Decimal
from datetime import date, timedelta

from core.permissions import accountant_required, manager_required
from core.models import SystemSetting
from .models import Workshop, DailyLaborLog, Employee, EmployeeSalaryPayment
from .forms import WorkshopForm, DailyLaborLogForm, EmployeeForm, EmployeeSalaryPaymentForm
from .services import LaborService, EmployeeService


@login_required
def workshop_list(request):
    workshops = Workshop.objects.prefetch_related('daily_logs').all()
    return render(request, 'labor/workshop_list.html', {'workshops': workshops})


@login_required
@manager_required
def workshop_create(request):
    if request.method == 'POST':
        form = WorkshopForm(request.POST)
        if form.is_valid():
            workshop = form.save()
            messages.success(request, f'تمت إضافة الورشة {workshop.name} بنجاح.')
            return redirect('workshop_detail', pk=workshop.pk)
    else:
        form = WorkshopForm()
    return render(request, 'labor/workshop_form.html', {'form': form, 'title': 'إضافة ورشة عمل جديدة'})


@login_required
def workshop_detail(request, pk):
    """
    صفحة الورشة:
    - فلترة بتاريخ العمل وبالشهر.
    - كل شهر: بطاقات تحت بعضها، كل بطاقة = يوم عمل فيه: اسم الورشة، التاريخ، العمال، الساعات، الأجرة.
    - مثال: 70 عاملاً × 10 ساعات → 70×10 + 70×1 = 770 دينار.
    - مجموع شهري لكل ورشة.
    """
    workshop = get_object_or_404(Workshop, pk=pk)
    now = timezone.now()

    # استخراج الفلترة بالشهر والسنة
    selected_year = int(request.GET.get('year', now.year))
    selected_month = int(request.GET.get('month', now.month))

    logs = workshop.daily_logs.filter(
        work_date__year=selected_year,
        work_date__month=selected_month
    ).order_by('-work_date')

    monthly_total_cost = logs.aggregate(total=Sum('total_cost'))['total'] or Decimal('0.000')
    monthly_workers_sum = logs.aggregate(total=Sum('worker_count'))['total'] or 0

    system_settings = SystemSetting.get_settings()
    log_form = DailyLaborLogForm(initial={
        'workshop': workshop,
        'work_date': now.date(),
        # الأسعار تُؤخذ من الورشة، وإن لم تُحدَّد فمن إعدادات النظام (لا يُفترض سعر داخل الشيفرة)
        'hourly_rate': workshop.default_hourly_rate or system_settings.worker_hourly_rate,
        'transport_allowance_daily': workshop.default_transport_daily or system_settings.worker_transport_daily,
        'work_hours': system_settings.default_work_hours,
    })

    context = {
        'workshop': workshop,
        'logs': logs,
        'selected_year': selected_year,
        'selected_month': selected_month,
        'monthly_total_cost': monthly_total_cost,
        'monthly_workers_sum': monthly_workers_sum,
        'log_form': log_form,
    }
    return render(request, 'labor/workshop_detail.html', context)


@login_required
@accountant_required
def record_daily_labor(request):
    """
    تسجيل يوم عمل جديد للورشة
    """
    if request.method == 'POST':
        form = DailyLaborLogForm(request.POST)
        if form.is_valid():
            try:
                log = LaborService.record_or_update_daily_log(
                    workshop=form.cleaned_data['workshop'],
                    work_date=form.cleaned_data['work_date'],
                    worker_count=form.cleaned_data['worker_count'],
                    work_hours=form.cleaned_data['work_hours'],
                    hourly_rate=form.cleaned_data['hourly_rate'],
                    transport_allowance=form.cleaned_data['transport_allowance_daily'],
                    notes=form.cleaned_data['notes'],
                    user=request.user
                )
                messages.success(request, f'تم توثيق يوم عمل ورشة {log.workshop.name} بتاريخ {log.work_date} بنجاح. التكلفة: {log.total_cost} دينار.')
                return redirect('workshop_detail', pk=log.workshop.pk)
            except Exception as e:
                messages.error(request, f'خطأ: {str(e)}')
    else:
        form = DailyLaborLogForm(initial={'work_date': timezone.now().date()})
    return render(request, 'labor/daily_log_form.html', {'form': form, 'title': 'توثيق يوم عمل ورشة'})


@login_required
def labor_cost_report(request):
    """
    تقرير تكلفة الورش وكلفة الكيلوغرام الموضب:
    - مجموع تكلفة كل ورشة لفترة محددة.
    - تكلفة الكيلوغرام = تكلفة الورشة ÷ الإنتاج المعتمد للفترة نفسها (بدون قسمة على صفر).
    - فلترة بعام/شهر اختياريين.
    """
    now = timezone.now()
    selected_year = int(request.GET.get('year', now.year))
    selected_month = request.GET.get('month')

    start_date = None
    end_date = None
    if selected_month:
        start_date = date(selected_year, int(selected_month), 1)
        if int(selected_month) == 12:
            end_date = date(selected_year + 1, 1, 1) - timedelta(days=1)
        else:
            end_date = date(selected_year, int(selected_month) + 1, 1) - timedelta(days=1)

    kpis = LaborService.calculate_cost_per_kg(start_date=start_date, end_date=end_date)

    workshops = Workshop.objects.prefetch_related('daily_logs').all()
    workshop_totals = []
    for w in workshops:
        logs = w.daily_logs.filter(work_date__year=selected_year)
        if selected_month:
            logs = logs.filter(work_date__month=int(selected_month))
        workshop_totals.append({
            'workshop': w,
            'days_count': logs.count(),
            'workers_sum': logs.aggregate(total=Sum('worker_count'))['total'] or 0,
            'total_cost': logs.aggregate(total=Sum('total_cost'))['total'] or Decimal('0.000'),
        })

    context = {
        'kpis': kpis,
        'workshops': workshops,
        'workshop_totals': workshop_totals,
        'selected_year': selected_year,
        'selected_month': int(selected_month) if selected_month else '',
    }
    return render(request, 'labor/cost_report.html', context)


# ==========================================
# الموظفون (رواتب شهرية) — قسم مستقل عن الورش
# ==========================================

@login_required
def employee_list(request):
    """
    قائمة الموظفين برواتب شهرية ثابتة — لا علاقة لهم بالورش ولا بكلفة الكيلو الموضب.
    """
    employees = Employee.objects.prefetch_related('salary_payments').all()
    total_monthly = sum((e.monthly_salary for e in employees if e.is_active), Decimal('0.000'))
    active_count = sum(1 for e in employees if e.is_active)
    context = {
        'employees': employees,
        'total_monthly': total_monthly,
        'active_count': active_count,
        'total_count': len(employees),
    }
    return render(request, 'labor/employee_list.html', context)


@login_required
@manager_required
def employee_create(request):
    if request.method == 'POST':
        form = EmployeeForm(request.POST)
        if form.is_valid():
            employee = form.save()
            messages.success(request, f'تمت إضافة الموظف {employee.name} بنجاح.')
            return redirect('employee_detail', pk=employee.pk)
    else:
        form = EmployeeForm()
    return render(request, 'labor/employee_form.html', {'form': form, 'title': 'إضافة موظف براتب شهري'})


@login_required
@manager_required
def employee_edit(request, pk):
    employee = get_object_or_404(Employee, pk=pk)
    if request.method == 'POST':
        form = EmployeeForm(request.POST, instance=employee)
        if form.is_valid():
            employee = form.save()
            messages.success(request, f'تم تحديث بيانات الموظف {employee.name}.')
            return redirect('employee_detail', pk=employee.pk)
    else:
        form = EmployeeForm(instance=employee)
    return render(request, 'labor/employee_form.html', {
        'form': form, 'title': f'تعديل بيانات الموظف: {employee.name}', 'employee': employee,
    })


@login_required
def employee_detail(request, pk):
    """
    بطاقة الموظف وسجل رواتبه الشهرية (منفصل تماماً عن الورش).
    """
    employee = get_object_or_404(Employee, pk=pk)
    payments = employee.salary_payments.select_related('treasury_account', 'created_by').order_by('-year', '-month')

    now = timezone.now()
    form = EmployeeSalaryPaymentForm(initial={
        'employee': employee,
        'year': now.year,
        'month': now.month,
        'amount': employee.monthly_salary,
        'status': EmployeeSalaryPayment.Status.ACCRUED,
    })

    paid_total = payments.filter(status=EmployeeSalaryPayment.Status.PAID).aggregate(t=Sum('amount'))['t'] or Decimal('0.000')
    accrued_total = payments.filter(status=EmployeeSalaryPayment.Status.ACCRUED).aggregate(t=Sum('amount'))['t'] or Decimal('0.000')

    context = {
        'employee': employee,
        'payments': payments,
        'form': form,
        'paid_total': paid_total,
        'accrued_total': accrued_total,
        'payments_count': payments.count(),
    }
    return render(request, 'labor/employee_detail.html', context)


@login_required
@accountant_required
def employee_salary_pay(request):
    """
    تسجيل صرف راتب شهري لموظف (من بطاقة الموظف أو من قائمة عامة).
    """
    if request.method == 'POST':
        form = EmployeeSalaryPaymentForm(request.POST)
        if form.is_valid():
            try:
                payment = EmployeeService.record_salary_payment(
                    employee=form.cleaned_data['employee'],
                    year=form.cleaned_data['year'],
                    month=form.cleaned_data['month'],
                    amount=form.cleaned_data['amount'],
                    status=form.cleaned_data['status'],
                    treasury_account=form.cleaned_data.get('treasury_account'),
                    payment_date=form.cleaned_data.get('payment_date'),
                    notes=form.cleaned_data.get('notes') or '',
                    user=request.user,
                )
                messages.success(
                    request,
                    f'تم تسجيل راتب {payment.employee.name} لشهر {payment.period_label}: '
                    f'{payment.amount} دينار ({payment.get_status_display()}).'
                )
                return redirect('employee_detail', pk=payment.employee.pk)
            except Exception as e:  # noqa: BLE001
                messages.error(request, f'تعذّر تسجيل الراتب: {e}')
        else:
            for field, errs in form.errors.items():
                for err in errs:
                    messages.error(request, err)
    return redirect('employee_list')


@login_required
@manager_required
def employee_salary_delete(request, pk):
    """
    حذف قيد راتب مسجّل بخطأ (بصلاحية مدير) مع قيد عكسي موثق إن كان مدفوعاً.
    """
    payment = get_object_or_404(EmployeeSalaryPayment, pk=pk)
    employee_pk = payment.employee.pk
    if request.method == 'POST':
        reason = request.POST.get('reason', '').strip()
        try:
            snap = EmployeeService.delete_salary_payment(payment, reason=reason, user=request.user)
            messages.success(
                request,
                f'تم حذف قيد راتب {snap["employee"]} لشهر {snap["period"]} ({snap["amount"]} دينار).'
            )
        except Exception as e:  # noqa: BLE001
            messages.error(request, f'تعذّر حذف قيد الراتب: {e}')
    return redirect('employee_detail', pk=employee_pk)


@login_required
def salary_report(request):
    """
    تقرير الرواتب الشهرية: إجمالي المستحق والمصروف لكل شهر، مع فلترة بالسنة.
    """
    now = timezone.now()
    selected_year = int(request.GET.get('year', now.year))

    payments = EmployeeSalaryPayment.objects.filter(year=selected_year).select_related(
        'employee', 'treasury_account'
    ).order_by('-month', 'employee__name')

    months = []
    for m in range(1, 13):
        qs = payments.filter(month=m)
        paid = qs.filter(status=EmployeeSalaryPayment.Status.PAID).aggregate(t=Sum('amount'))['t'] or Decimal('0.000')
        accrued = qs.filter(status=EmployeeSalaryPayment.Status.ACCRUED).aggregate(t=Sum('amount'))['t'] or Decimal('0.000')
        if qs.exists():
            months.append({
                'month': m,
                'count': qs.count(),
                'paid': paid,
                'accrued': accrued,
                'total': paid + accrued,
            })

    year_total_paid = payments.filter(status=EmployeeSalaryPayment.Status.PAID).aggregate(t=Sum('amount'))['t'] or Decimal('0.000')
    year_total_accrued = payments.filter(status=EmployeeSalaryPayment.Status.ACCRUED).aggregate(t=Sum('amount'))['t'] or Decimal('0.000')

    context = {
        'payments': payments,
        'months': months,
        'selected_year': selected_year,
        'year_total_paid': year_total_paid,
        'year_total_accrued': year_total_accrued,
    }
    return render(request, 'labor/salary_report.html', context)
