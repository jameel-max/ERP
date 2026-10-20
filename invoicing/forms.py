from django import forms
from django.utils import timezone
from .models import Invoice
from operations.models import Trader, Crop, TruckReceipt


class InvoiceForm(forms.ModelForm):
    class Meta:
        model = Invoice
        fields = ['trader', 'year', 'crop', 'packing_method', 'notes']
        widgets = {
            'trader': forms.Select(attrs={'class': 'form-select'}),
            'year': forms.NumberInput(attrs={'class': 'form-control'}),
            'crop': forms.Select(attrs={'class': 'form-select'}),
            'packing_method': forms.Select(attrs={'class': 'form-select'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.pk:
            self.fields['year'].initial = timezone.now().year


class LinkTruckToInvoiceForm(forms.Form):
    trucks = forms.ModelMultipleChoiceField(
        queryset=TruckReceipt.objects.all(),
        widget=forms.CheckboxSelectMultiple,
        required=False,
        label='اختر السيارات لربطها بالفاتورة'
    )

    def __init__(self, invoice, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['trucks'].queryset = TruckReceipt.objects.filter(
            trader=invoice.trader,
            crop=invoice.crop,
            arrival_date__year=invoice.year
        )
        self.fields['trucks'].initial = invoice.trucks.all()
