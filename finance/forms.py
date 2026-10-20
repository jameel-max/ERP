from django import forms
from .models import TreasuryAccount, Expense, ExpenseCategory, Check, Payment, InternalTransfer
from operations.models import Trader
from invoicing.models import Invoice


class TreasuryAccountForm(forms.ModelForm):
    class Meta:
        model = TreasuryAccount
        fields = ['name', 'account_type', 'bank_account_number', 'opening_balance', 'is_active', 'notes']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'مثال: الصندوق الرئيسي / البنك العربي'}),
            'account_type': forms.Select(attrs={'class': 'form-select'}),
            'bank_account_number': forms.TextInput(attrs={'class': 'form-control'}),
            'opening_balance': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.001'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
        }


class ExpenseCategoryForm(forms.ModelForm):
    """تصنيف مصاريف جديد (يُستخدم في الإضافة السريعة من نموذج المصروف وفي صفحة الإدارة)."""
    class Meta:
        model = ExpenseCategory
        fields = ['name', 'code']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'مثال: كهرباء وماء، إيجار، محروقات'}),
            'code': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'اختياري — رمز مختصر (مثال: ELEC)'}),
        }

    def clean_name(self):
        name = (self.cleaned_data.get('name') or '').strip()
        if not name:
            raise forms.ValidationError('اسم التصنيف مطلوب.')
        qs = ExpenseCategory.objects.filter(name__iexact=name)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError('يوجد تصنيف بنفس الاسم بالفعل — اختره من القائمة أو استخدم اسماً مختلفاً.')
        return name


class ExpenseForm(forms.ModelForm):
    class Meta:
        model = Expense
        fields = ['category', 'amount', 'due_date', 'status', 'treasury_account', 'beneficiary', 'description', 'is_recurring', 'recurrence_key', 'receipt_image']
        widgets = {
            'category': forms.Select(attrs={'class': 'form-select'}),
            'amount': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.001'}),
            'due_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'status': forms.Select(attrs={'class': 'form-select'}),
            'treasury_account': forms.Select(attrs={'class': 'form-select'}),
            'beneficiary': forms.TextInput(attrs={'class': 'form-control'}),
            'description': forms.TextInput(attrs={'class': 'form-control'}),
            'is_recurring': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'recurrence_key': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'مثال: RENT-2026-06'}),
            'receipt_image': forms.FileInput(attrs={'class': 'form-control'}),
        }


class PaymentForm(forms.ModelForm):
    invoices = forms.ModelMultipleChoiceField(
        queryset=Invoice.objects.filter(remaining_balance__gt=0),
        required=False,
        widget=forms.SelectMultiple(attrs={'class': 'form-select', 'size': '5'}),
        label='اختر الفواتير لتخصيص الدفعة عليها'
    )

    class Meta:
        model = Payment
        fields = ['trader', 'amount', 'payment_method', 'treasury_account', 'invoices', 'notes']
        widgets = {
            'trader': forms.Select(attrs={'class': 'form-select'}),
            'amount': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.001'}),
            'payment_method': forms.Select(attrs={'class': 'form-select'}),
            'treasury_account': forms.Select(attrs={'class': 'form-select'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
        }


class CheckForm(forms.ModelForm):
    class Meta:
        model = Check
        fields = ['check_number', 'trader', 'bank_name', 'amount', 'due_date', 'notes']
        widgets = {
            'check_number': forms.TextInput(attrs={'class': 'form-control'}),
            'trader': forms.Select(attrs={'class': 'form-select'}),
            'bank_name': forms.TextInput(attrs={'class': 'form-control'}),
            'amount': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.001'}),
            'due_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
        }


class InternalTransferForm(forms.ModelForm):
    class Meta:
        model = InternalTransfer
        fields = ['from_account', 'to_account', 'amount', 'notes']
        widgets = {
            'from_account': forms.Select(attrs={'class': 'form-select'}),
            'to_account': forms.Select(attrs={'class': 'form-select'}),
            'amount': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.001'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
        }
