from django import forms
from .models import PackingSession, PackingOutput, PackingWaste, DeliveryNote, PackedPallet
from operations.models import Trader, Crop, Pallet, BoxType, ColdStore, Shelf


class StartPackingSessionForm(forms.ModelForm):
    source_pallets = forms.ModelMultipleChoiceField(
        queryset=Pallet.objects.filter(status=Pallet.Status.IN_COOLING),
        widget=forms.SelectMultiple(attrs={'class': 'form-select select2', 'size': '6'}),
        label='اختر الطبالي لإخراجها من البراد للبدء بالتوضيب'
    )

    class Meta:
        model = PackingSession
        fields = ['trader', 'crop', 'packing_method', 'workshop_name', 'source_pallets', 'notes']
        widgets = {
            'trader': forms.Select(attrs={'class': 'form-select'}),
            'crop': forms.Select(attrs={'class': 'form-select'}),
            'packing_method': forms.Select(attrs={'class': 'form-select'}),
            'workshop_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'مثال: ورشة أم السعيد / خط الماكينة 1'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # تخصيص عرض الطبالي في القائمة
        self.fields['source_pallets'].label_from_instance = lambda obj: f"{obj.pallet_code} - {obj.trader.name} ({obj.crop.name} - {obj.box_count} صندوق - {obj.cold_store.name})"


class PackedPalletCoolingForm(forms.Form):
    cold_store = forms.ModelChoiceField(
        queryset=ColdStore.objects.all(),
        widget=forms.Select(attrs={'class': 'form-select', 'id': 'id_cold_store'}),
        label='البراد المستهدف'
    )
    shelf = forms.ModelChoiceField(
        queryset=Shelf.objects.all(),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select', 'id': 'id_shelf'}),
        label='السيخ / الرف (اختياري)'
    )
    notes = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'مثال: تبريد بضاعة موضبة جاهزة للتصدير'}),
        label='ملاحظات'
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['shelf'].queryset = Shelf.objects.select_related('cold_store').order_by(
            'cold_store__name', 'shelf_number'
        )
        self.fields['shelf'].label_from_instance = lambda s: f"{s.cold_store.name} – السيخ {s.shelf_number}"


class DeliveryNoteForm(forms.ModelForm):
    class Meta:
        model = DeliveryNote
        fields = ['trader', 'driver_name', 'truck_plate', 'receiver_name', 'delivery_date', 'notes']
        widgets = {
            'trader': forms.Select(attrs={'class': 'form-select'}),
            'driver_name': forms.TextInput(attrs={'class': 'form-control'}),
            'truck_plate': forms.TextInput(attrs={'class': 'form-control'}),
            'receiver_name': forms.TextInput(attrs={'class': 'form-control'}),
            'delivery_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }
