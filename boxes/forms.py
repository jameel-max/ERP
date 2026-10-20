from django import forms
from django.db.models import Q
from django.utils import timezone
from decimal import Decimal
from .models import (
    TraderBoxTransaction, FarmBoxType, FarmBoxRecord,
    EmptyBoxRecipient, EmptyBoxDelivery,
)
from operations.models import Trader, BoxType, TruckReceipt


class BoxTransactionForm(forms.ModelForm):
    class Meta:
        model = TraderBoxTransaction
        fields = ['trader', 'box_type', 'transaction_type', 'quantity', 'source', 'destination', 'reason']
        widgets = {
            'trader': forms.Select(attrs={'class': 'form-select'}),
            'box_type': forms.Select(attrs={'class': 'form-select'}),
            'transaction_type': forms.Select(attrs={'class': 'form-select'}),
            'quantity': forms.NumberInput(attrs={'class': 'form-control', 'min': '1'}),
            'source': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'مثال: مستودع الشركة'}),
            'destination': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'مثال: عهدة التاجر'}),
            'reason': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'سبب الصرف أو الإرجاع'}),
        }


class FarmBoxTypeForm(forms.ModelForm):
    class Meta:
        model = FarmBoxType
        fields = ['name', 'description', 'tare_weight', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'مثال: صندوق مزرعة بلاستيك'}),
            'description': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'الوصف / المقاس (اختياري)'}),
            'tare_weight': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.001'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }


class FarmBoxRecordForm(forms.ModelForm):
    class Meta:
        model = FarmBoxRecord
        fields = ['truck_receipt', 'incoming_count', 'empty_count', 'notes']
        widgets = {
            'truck_receipt': forms.Select(attrs={'class': 'form-select select2'}),
            'incoming_count': forms.NumberInput(attrs={'class': 'form-control', 'min': '0'}),
            'empty_count': forms.NumberInput(attrs={'class': 'form-control', 'min': '0'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        truck = kwargs.pop('truck', None)
        trader = kwargs.pop('trader', None)
        super().__init__(*args, **kwargs)
        # «عدد الفارغ الناتج» يُعبَّأ تلقائياً بنفس عدد الوارد إن تُرك فارغاً
        self.fields['empty_count'].required = False
        self.fields['empty_count'].help_text = (
            'يُعبَّأ تلقائياً بنفس «عدد الصناديق الواردة»؛ عدّله فقط إن نُقصت صناديق أو تلف منها شيء.'
        )
        # السيارات التي لا تملك سجلاً بعد (مع استثناء السيارة الحالية عند التعديل)
        available = TruckReceipt.objects.select_related('trader', 'crop')
        if trader is not None:
            available = available.filter(trader=trader)
        if self.instance and self.instance.pk:
            available = available.filter(
                Q(farm_box_record__isnull=True) | Q(pk=self.instance.truck_receipt_id)
            )
        else:
            available = available.filter(farm_box_record__isnull=True)
        self.fields['truck_receipt'].queryset = available.order_by('-created_at')

        if truck is not None:
            self.fields['truck_receipt'].initial = truck
            self.fields['truck_receipt'].widget = forms.HiddenInput()
            if not self.initial.get('incoming_count'):
                self.initial['incoming_count'] = truck.box_count
            if not self.initial.get('empty_count'):
                # الافتراضي: نفس عدد الصناديق الواردة (تُفرَّغ كلها بعد التوضيب)
                self.initial['empty_count'] = truck.box_count

    def clean(self):
        cleaned = super().clean()
        # إن تُرك «عدد الفارغ الناتج» فارغاً نجعله مساوياً لعدد الصناديق الواردة
        if cleaned.get('empty_count') is None:
            cleaned['empty_count'] = cleaned.get('incoming_count') or 0
        return cleaned


# ==================== تسليم الفارغ: إرجاع إلى المزرعة أو بيع ====================


class EmptyBoxRecipientForm(forms.ModelForm):
    class Meta:
        model = EmptyBoxRecipient
        fields = ['name', 'kind', 'contact_person', 'phone', 'address', 'is_active', 'notes']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'مثال: مزرعة الوادي الأخضر'}),
            'kind': forms.Select(attrs={'class': 'form-select'}),
            'contact_person': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'اسم المسؤول (اختياري)'}),
            'phone': forms.TextInput(attrs={'class': 'form-control', 'placeholder': '0790000000'}),
            'address': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'المنطقة / العنوان'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }


class EmptyBoxDeliveryForm(forms.ModelForm):
    """
    أمر تسليم صناديق فارغة.
    الأنواع المعروضة هي أنواع الصناديق الواردة من المزرعة (boxes.FarmBoxType)
    لأنّها الأنواع التي تُسجَّل في سجلات صناديق المزرعة ويُجمَع فارغها بعد التفريغ.
    """

    class Meta:
        model = EmptyBoxDelivery
        fields = [
            'kind', 'recipient', 'box_type', 'quantity', 'unit_price',
            'delivery_date', 'driver_name', 'vehicle_plate',
            'payment_method', 'treasury_account', 'status', 'notes',
        ]
        widgets = {
            'kind': forms.Select(attrs={'class': 'form-select', 'id': 'id_kind'}),
            'recipient': forms.Select(attrs={'class': 'form-select'}),
            'box_type': forms.Select(attrs={'class': 'form-select'}),
            'quantity': forms.NumberInput(attrs={'class': 'form-control', 'min': '1'}),
            'unit_price': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.001', 'min': '0'}),
            'delivery_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}, format='%Y-%m-%d'),
            'driver_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'اسم من نقل الصناديق'}),
            'vehicle_plate': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'رقم السيارة'}),
            'payment_method': forms.Select(attrs={'class': 'form-select', 'id': 'id_payment_method'}),
            'treasury_account': forms.Select(attrs={'class': 'form-select'}),
            'status': forms.Select(attrs={'class': 'form-select'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['delivery_date'].input_formats = ['%Y-%m-%d']
        self.fields['unit_price'].required = False
        self.fields['payment_method'].required = False
        self.fields['treasury_account'].required = False
        self.fields['recipient'].queryset = EmptyBoxRecipient.objects.filter(is_active=True)
        self.fields['box_type'].queryset = FarmBoxType.objects.filter(is_active=True).order_by('name')
        self.fields['box_type'].help_text = (
            'نوع الصندوق الوارد من المزرعة الذي يُجمَع فارغه بعد التفريغ.'
        )

        from finance.models import TreasuryAccount
        self.fields['treasury_account'].queryset = TreasuryAccount.objects.filter(is_active=True)
        self.fields['unit_price'].help_text = 'للبيع فقط. للإرجاع إلى المزرعة يُصفَّر تلقائياً.'
        self.fields['payment_method'].help_text = (
            'للبيع: نقدي/بنكي يُقيَّد في الخزينة عند الاعتماد، أو «على الحساب» بلا قيد خزينة.'
        )

        if not self.instance.pk:
            self.fields['kind'].initial = EmptyBoxDelivery.Kind.RETURN
            self.fields['status'].initial = EmptyBoxDelivery.Status.DRAFT
            self.fields['delivery_date'].initial = timezone.localdate()

    def clean(self):
        cleaned = super().clean()
        kind = cleaned.get('kind')
        quantity = cleaned.get('quantity') or 0
        unit_price = cleaned.get('unit_price')

        if quantity <= 0:
            self.add_error('quantity', 'عدد الصناديق يجب أن يكون أكبر من صفر.')

        if kind == EmptyBoxDelivery.Kind.RETURN:
            # الإرجاع إلى المزرعة لا يحمل سعراً ولا قبضاً
            cleaned['unit_price'] = Decimal('0.000')
            cleaned['payment_method'] = ''
            cleaned['treasury_account'] = None
        elif kind == EmptyBoxDelivery.Kind.SALE:
            if not unit_price or unit_price <= 0:
                self.add_error('unit_price', 'البيع يتطلب سعراً للصندوق الواحد أكبر من صفر.')
            if not cleaned.get('payment_method'):
                self.add_error('payment_method', 'حدّد طريقة القبض: نقدي، بنكي، أو على الحساب.')

        return cleaned
