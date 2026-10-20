from django import forms

from .models import TradeShipment, ShipmentCost, ShipmentPayment, ShipmentDocument


_DATE = forms.DateInput(attrs={'class': 'form-control', 'type': 'date'})


class ShipmentForm(forms.ModelForm):
    class Meta:
        model = TradeShipment
        fields = [
            'direction', 'counterparty_name', 'counterparty_country',
            'goods_description', 'crop',
            'quantity', 'quantity_unit', 'unit_price', 'currency', 'exchange_rate',
            'sale_value',
            'origin', 'destination',
            'order_date', 'departure_date', 'eta_date', 'arrival_date',
            'received_quantity', 'damaged_quantity',
            'status', 'notes',
        ]
        widgets = {
            'direction': forms.Select(attrs={'class': 'form-select'}),
            'counterparty_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'اسم المورد أو العميل'}),
            'counterparty_country': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'لبنان، تركيا، هولندا...'}),
            'goods_description': forms.TextInput(attrs={'class': 'form-control', 'placeholder': '1000 صندوق تفاح'}),
            'crop': forms.Select(attrs={'class': 'form-select'}),
            'quantity': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.001', 'min': '0'}),
            'quantity_unit': forms.Select(attrs={'class': 'form-select'}),
            'unit_price': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.001', 'min': '0'}),
            'currency': forms.Select(attrs={'class': 'form-select'}),
            'exchange_rate': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.000001', 'min': '0'}),
            'sale_value': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.001', 'min': '0'}),
            'origin': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'بلد المنشأ'}),
            'destination': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'بلد الوجهة'}),
            'order_date': _DATE,
            'departure_date': _DATE,
            'eta_date': _DATE,
            'arrival_date': _DATE,
            'received_quantity': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.001', 'min': '0'}),
            'damaged_quantity': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.001', 'min': '0'}),
            'status': forms.Select(attrs={'class': 'form-select'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }

    def clean_exchange_rate(self):
        rate = self.cleaned_data.get('exchange_rate')
        if rate is not None and rate <= 0:
            raise forms.ValidationError('سعر الصرف يجب أن يكون أكبر من صفر.')
        return rate

    def clean(self):
        cleaned = super().clean()
        received = cleaned.get('received_quantity')
        damaged = cleaned.get('damaged_quantity')
        quantity = cleaned.get('quantity')
        if received is not None and damaged is not None and received < damaged:
            self.add_error('damaged_quantity', 'الكمية التالفة لا يمكن أن تتجاوز الكمية الواصلة.')
        if received is not None and quantity is not None and received > quantity:
            self.add_error('received_quantity', 'الكمية الواصلة لا يمكن أن تتجاوز الكمية المشحونة.')
        return cleaned


class ShipmentCostForm(forms.ModelForm):
    class Meta:
        model = ShipmentCost
        fields = ['kind', 'description', 'amount', 'currency', 'exchange_rate']
        widgets = {
            'kind': forms.Select(attrs={'class': 'form-select'}),
            'description': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'بيان مختصر'}),
            'amount': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.001', 'min': '0'}),
            'currency': forms.Select(attrs={'class': 'form-select'}),
            'exchange_rate': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.000001', 'min': '0'}),
        }

    def clean_amount(self):
        amount = self.cleaned_data.get('amount')
        if amount is not None and amount <= 0:
            raise forms.ValidationError('المبلغ يجب أن يكون أكبر من صفر.')
        return amount

    def clean_exchange_rate(self):
        rate = self.cleaned_data.get('exchange_rate')
        if rate is not None and rate <= 0:
            raise forms.ValidationError('سعر الصرف يجب أن يكون أكبر من صفر.')
        return rate


class ShipmentPaymentForm(forms.ModelForm):
    class Meta:
        model = ShipmentPayment
        fields = ['kind', 'amount', 'currency', 'exchange_rate', 'payment_date', 'method', 'reference', 'notes']
        widgets = {
            'kind': forms.Select(attrs={'class': 'form-select'}),
            'amount': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.001', 'min': '0'}),
            'currency': forms.Select(attrs={'class': 'form-select'}),
            'exchange_rate': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.000001', 'min': '0'}),
            'payment_date': _DATE,
            'method': forms.Select(attrs={'class': 'form-select'}),
            'reference': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'رقم الحوالة أو الشيك'}),
            'notes': forms.TextInput(attrs={'class': 'form-control'}),
        }

    def clean_amount(self):
        amount = self.cleaned_data.get('amount')
        if amount is not None and amount <= 0:
            raise forms.ValidationError('المبلغ يجب أن يكون أكبر من صفر.')
        return amount

    def clean_exchange_rate(self):
        rate = self.cleaned_data.get('exchange_rate')
        if rate is not None and rate <= 0:
            raise forms.ValidationError('سعر الصرف يجب أن يكون أكبر من صفر.')
        return rate


class ShipmentDocumentForm(forms.ModelForm):
    class Meta:
        model = ShipmentDocument
        fields = ['kind', 'number', 'issue_date', 'received', 'notes']
        widgets = {
            'kind': forms.Select(attrs={'class': 'form-select'}),
            'number': forms.TextInput(attrs={'class': 'form-control'}),
            'issue_date': _DATE,
            'received': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'notes': forms.TextInput(attrs={'class': 'form-control'}),
        }
