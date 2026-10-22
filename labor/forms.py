from django import forms
from django.utils import timezone
from decimal import Decimal
from .models import Workshop, DailyLaborLog, Employee, EmployeeSalaryPayment, EmployeeDeduction
from packing.models import PackingSession


class WorkshopForm(forms.ModelForm):
    class Meta:
        model = Workshop
        fields = ['name', 'supervisor_name', 'phone', 'default_hourly_rate', 'default_transport_daily', 'is_active', 'notes']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'مثال: ورشة أم السعيد'}),
            'supervisor_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'اسم المشرف'}),
            'phone': forms.TextInput(attrs={'class': 'form-control'}),
            'default_hourly_rate': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.001'}),
            'default_transport_daily': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.001'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }


class DailyLaborLogForm(forms.ModelForm):
    class Meta:
        model = DailyLaborLog
        fields = [
            'workshop', 'packing_session', 'work_date', 'worker_count',
            'work_hours', 'hourly_rate', 'transport_allowance_daily', 'notes',
        ]
        widgets = {
            'workshop': forms.Select(attrs={'class': 'form-select'}),
            'packing_session': forms.Select(attrs={
                'class': 'form-select select2',
                'data-placeholder': 'اختر العملية إن كان هذا اليوم يخص جلسة توضيب محددة',
            }),
            'work_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'worker_count': forms.NumberInput(attrs={'class': 'form-control', 'min': '1'}),
            'work_hours': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.5', 'min': '0.5'}),
            'hourly_rate': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.001'}),
            'transport_allowance_daily': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.001'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['packing_session'].required = False
        self.fields['packing_session'].queryset = PackingSession.objects.select_related(
            'trader', 'crop'
        ).order_by('-started_at')
        self.fields['packing_session'].empty_label = 'يوم عام غير مرتبط بعملية محددة'
        self.fields['packing_session'].label_from_instance = lambda session: (
            f'{session.session_code} — {session.trader.name} / {session.crop.name} '
            f'— {session.get_status_display()} — {session.started_at:%Y-%m-%d}'
        )


class EmployeeForm(forms.ModelForm):
    """
    بطاقة موظف براتب شهري — قسم مستقل عن الورش.
    """
    class Meta:
        model = Employee
        fields = ['name', 'job_title', 'phone', 'monthly_salary', 'hire_date', 'is_active', 'notes']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'مثال: أحمد محمد'}),
            'job_title': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'مثال: محاسب، حارس، سائق'}),
            'phone': forms.TextInput(attrs={'class': 'form-control'}),
            'monthly_salary': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.001', 'min': '0'}),
            'hire_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
        }

    def clean_name(self):
        name = (self.cleaned_data.get('name') or '').strip()
        if not name:
            raise forms.ValidationError('اسم الموظف مطلوب.')
        return name

    def clean_monthly_salary(self):
        salary = self.cleaned_data.get('monthly_salary')
        if salary is not None and salary < 0:
            raise forms.ValidationError('الراتب الشهري لا يمكن أن يكون سالباً.')
        return salary


class EmployeeDeductionForm(forms.ModelForm):
    """
    تسجيل خصم على راتب موظف (غياب/سلفة/تأخير/جزاء/أخرى) لشهر محدد.
    """
    MONTH_CHOICES = [
        (1, 'كانون الثاني (1)'), (2, 'شباط (2)'), (3, 'آذار (3)'),
        (4, 'نيسان (4)'), (5, 'أيار (5)'), (6, 'حزيران (6)'),
        (7, 'تموز (7)'), (8, 'آب (8)'), (9, 'أيلول (9)'),
        (10, 'تشرين الأول (10)'), (11, 'تشرين الثاني (11)'), (12, 'كانون الأول (12)'),
    ]

    year = forms.IntegerField(
        widget=forms.NumberInput(attrs={'class': 'form-control', 'min': '2000', 'max': '2100'}),
        initial=lambda: timezone.now().year,
        label='السنة',
    )
    month = forms.TypedChoiceField(
        choices=MONTH_CHOICES,
        coerce=int,
        widget=forms.Select(attrs={'class': 'form-select'}),
        initial=lambda: timezone.now().month,
        label='الشهر',
    )

    class Meta:
        model = EmployeeDeduction
        fields = ['deduction_type', 'year', 'month', 'amount', 'deduction_date', 'reason']
        widgets = {
            'deduction_type': forms.Select(attrs={'class': 'form-select'}),
            'amount': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.001', 'min': '0'}),
            'deduction_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'reason': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'مثال: غياب 3 أيام'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        now = timezone.now()
        self.fields['year'].initial = now.year
        self.fields['month'].initial = now.month

    def clean_amount(self):
        amount = self.cleaned_data.get('amount')
        if amount is None or amount <= 0:
            raise forms.ValidationError('مبلغ الخصم يجب أن يكون أكبر من صفر.')
        return amount


class EmployeeSalaryPaymentForm(forms.ModelForm):
    """
    صرف راتب شهري لموظف.
    """
    MONTH_CHOICES = [
        (1, 'كانون الثاني (1)'), (2, 'شباط (2)'), (3, 'آذار (3)'),
        (4, 'نيسان (4)'), (5, 'أيار (5)'), (6, 'حزيران (6)'),
        (7, 'تموز (7)'), (8, 'آب (8)'), (9, 'أيلول (9)'),
        (10, 'تشرين الأول (10)'), (11, 'تشرين الثاني (11)'), (12, 'كانون الأول (12)'),
    ]

    year = forms.IntegerField(
        widget=forms.NumberInput(attrs={'class': 'form-control', 'min': '2000', 'max': '2100'}),
        initial=lambda: timezone.now().year,
        label='السنة',
    )
    month = forms.TypedChoiceField(
        choices=MONTH_CHOICES,
        coerce=int,
        widget=forms.Select(attrs={'class': 'form-select'}),
        initial=lambda: timezone.now().month,
        label='الشهر',
    )

    class Meta:
        model = EmployeeSalaryPayment
        fields = ['employee', 'year', 'month', 'amount', 'status', 'payment_date', 'treasury_account', 'notes']
        labels = {
            'amount': 'الراتب الأساسي قبل الخصم (دينار)',
        }
        widgets = {
            'employee': forms.Select(attrs={'class': 'form-select'}),
            'amount': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.001', 'min': '0'}),
            'status': forms.Select(attrs={'class': 'form-select'}),
            'payment_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'treasury_account': forms.Select(attrs={'class': 'form-select'}),
            'notes': forms.TextInput(attrs={'class': 'form-control'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from finance.models import TreasuryAccount
        self.fields['treasury_account'].queryset = TreasuryAccount.objects.filter(is_active=True)
        self.fields['treasury_account'].required = False
        self.fields['treasury_account'].empty_label = '— لا يوجد (مستحق فقط) —'
        self.fields['employee'].queryset = Employee.objects.filter(is_active=True)
        now = timezone.now()
        self.fields['year'].initial = now.year
        self.fields['month'].initial = now.month

    def clean_amount(self):
        amount = self.cleaned_data.get('amount')
        if amount is None or amount <= 0:
            raise forms.ValidationError('مبلغ الراتب يجب أن يكون أكبر من صفر.')
        return amount

    def clean(self):
        cleaned = super().clean()
        status = cleaned.get('status')
        treasury = cleaned.get('treasury_account')
        if status == EmployeeSalaryPayment.Status.PAID and not treasury:
            self.add_error('treasury_account', 'صرف الراتب المدفوع يتطلب تحديد الخزينة أو الحساب المدفوع منه.')
        # منع الازدواج: قيد واحد لكل موظف/سنة/شهر
        employee = cleaned.get('employee')
        year = cleaned.get('year')
        month = cleaned.get('month')
        if employee and year and month:
            qs = EmployeeSalaryPayment.objects.filter(employee=employee, year=year, month=month)
            if self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise forms.ValidationError(
                    f'راتب شهر {int(month):02d}/{year} للموظف {employee.name} مسجّل مسبقاً — '
                    'لا يمكن تسجيله مرتين لنفس الشهر.'
                )
        return cleaned
