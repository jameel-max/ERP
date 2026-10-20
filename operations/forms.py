from django import forms
from decimal import Decimal
from .models import Trader, Crop, BoxType, ColdStore, Shelf, TruckReceipt, TraderPackingRate, Pallet, PackagingGroup
from boxes.models import FarmBoxType


class TraderForm(forms.ModelForm):
    class Meta:
        model = Trader
        fields = ['code', 'name', 'phone', 'national_id', 'notes', 'is_active']
        widgets = {
            'code': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'مثال: T001'}),
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'اسم التاجر'}),
            'phone': forms.TextInput(attrs={'class': 'form-control', 'placeholder': '079XXXXXXX'}),
            'national_id': forms.TextInput(attrs={'class': 'form-control'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }


class TraderPackingRateForm(forms.ModelForm):
    class Meta:
        model = TraderPackingRate
        fields = ['method', 'rate_per_kg', 'effective_date', 'notes']
        widgets = {
            'method': forms.Select(attrs={'class': 'form-select'}),
            'rate_per_kg': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.001'}),
            'effective_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'notes': forms.TextInput(attrs={'class': 'form-control'}),
        }


class CropForm(forms.ModelForm):
    class Meta:
        model = Crop
        fields = ['name', 'code', 'notes']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'مثال: نكتارين'}),
            'code': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'رمز مختصر (اختياري)'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }


class ColdStoreForm(forms.ModelForm):
    shelves_count = forms.IntegerField(
        initial=4,
        min_value=1,
        max_value=50,
        label='عدد الأسياخ داخل البراد',
        widget=forms.NumberInput(attrs={'class': 'form-control', 'min': '1'}),
        help_text='يتم إنشاء الأسياخ تلقائياً بأرقام متسلسلة (المعتمد: 4 أسياخ لكل براد).',
    )

    class Meta:
        model = ColdStore
        fields = ['code', 'name', 'capacity_pallets', 'current_temperature', 'notes']
        widgets = {
            'code': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'مثال: CS-01'}),
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'مثال: البراد الأول'}),
            'capacity_pallets': forms.NumberInput(attrs={'class': 'form-control', 'min': '1'}),
            'current_temperature': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.1'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }


class ShelfForm(forms.ModelForm):
    class Meta:
        model = Shelf
        fields = ['shelf_number', 'capacity_pallets', 'notes']
        widgets = {
            'shelf_number': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'مثال: 3'}),
            'capacity_pallets': forms.NumberInput(attrs={'class': 'form-control', 'min': '1'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
        }


class PackagingGroupForm(forms.ModelForm):
    class Meta:
        model = PackagingGroup
        fields = ['name', 'notes', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'مثال: الصناديق'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }


class BoxTypeForm(forms.ModelForm):
    class Meta:
        model = BoxType
        fields = ['group', 'name', 'size', 'color_or_type', 'tare_weight', 'discount_price', 'company_stock', 'is_box', 'is_active']
        widgets = {
            'group': forms.Select(attrs={'class': 'form-select'}),
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'اسم الصندوق'}),
            'size': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'المقاس (اختياري)'}),
            'color_or_type': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'اللون / النوع'}),
            'tare_weight': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.001'}),
            'discount_price': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.001'}),
            'company_stock': forms.NumberInput(attrs={'class': 'form-control'}),
            'is_box': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['group'].queryset = PackagingGroup.objects.filter(is_active=True)
        self.fields['group'].required = False
        self.fields['group'].empty_label = '— بدون مجموعة —'


class TruckReceiptForm(forms.ModelForm):
    class Meta:
        model = TruckReceipt
        fields = [
            'lot_number', 'trader', 'crop', 'box_type', 'box_count',
            'boxes_per_pallet', 'pallet_count',
            'gross_weight', 'tare_weight', 'weight_deduction_applied',
            'on_pallets', 'cold_store', 'shelf', 'temperature',
            'farm_name', 'export_certificate_no',
            'driver_name', 'truck_plate', 'arrival_date', 'scale_ticket_image', 'notes'
        ]
        widgets = {
            'lot_number': forms.TextInput(attrs={'class': 'form-control', 'readonly': 'readonly'}),
            'trader': forms.Select(attrs={'class': 'form-select select2'}),
            'crop': forms.Select(attrs={'class': 'form-select'}),
            'box_type': forms.Select(attrs={'class': 'form-select'}),
            'box_count': forms.NumberInput(attrs={'class': 'form-control', 'min': '1'}),
            'boxes_per_pallet': forms.NumberInput(attrs={'class': 'form-control', 'min': '1', 'step': '1'}),
            'gross_weight': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.001'}),
            'tare_weight': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.001'}),
            'weight_deduction_applied': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'on_pallets': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'pallet_count': forms.NumberInput(attrs={'class': 'form-control', 'min': '0'}),
            'cold_store': forms.Select(attrs={'class': 'form-select'}),
            'shelf': forms.Select(attrs={'class': 'form-select'}),
            'temperature': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.1'}),
            'farm_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'اسم المزرعة (اختياري)'}),
            'export_certificate_no': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'رقم شهادة التصدير (اختياري)'}),
            'driver_name': forms.TextInput(attrs={'class': 'form-control'}),
            'truck_plate': forms.TextInput(attrs={'class': 'form-control'}),
            'arrival_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'scale_ticket_image': forms.FileInput(attrs={'class': 'form-control'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # نوع الصناديق في الاستلام يُسحب من الصناديق الواردة من المزرعة فقط،
        # وليس من صناديق الشركة المستخدمة في توضيب البضاعة.
        self.fields['box_type'].queryset = FarmBoxType.objects.filter(is_active=True)
        # «عدد الطبالي» يُحسب تلقائياً من (عدد الصناديق ÷ عدد الصناديق على الطبلية)، فلا يُعدَّل يدوياً.
        self.fields['pallet_count'].required = False
        self.fields['pallet_count'].disabled = True
        # «عدد الصناديق على الطبلية»: المعيار 80، ويُمنع تعديله بعد توليد الطبالي.
        self.fields['boxes_per_pallet'].required = False
        if self.instance.pk and self.instance.pallets.exists():
            self.fields['boxes_per_pallet'].disabled = True
            self.fields['boxes_per_pallet'].help_text = 'لا يمكن تغيير التوزيع بعد توليد طبالي هذه السيارة.'
        if not self.instance.pk:
            self.fields['lot_number'].initial = TruckReceipt.generate_lot_number()
            self.fields['weight_deduction_applied'].initial = True
            self.fields['on_pallets'].initial = True
            self.fields['boxes_per_pallet'].initial = 80
        # الأسياخ المرتبطة بالبرادات (يُفضّل عرضها مقسّمة حسب البراد؛
        # الواجهة تُفلتر القائمة عبر JS حسب البراد المختار).
        self.fields['shelf'].queryset = Shelf.objects.select_related('cold_store').order_by(
            'cold_store__code', 'shelf_number'
        )
        self.fields['shelf'].required = False
        self.fields['shelf'].empty_label = '— بدون سيخ —'

    def clean(self):
        cleaned = super().clean()
        box_count = cleaned.get('box_count') or 0
        boxes_per_pallet = cleaned.get('boxes_per_pallet')
        if boxes_per_pallet is None:
            # حقل فارغ/غير مُرسل → القيمة الحالية أو المعيار 80
            boxes_per_pallet = getattr(self.instance, 'boxes_per_pallet', None) or 80
            cleaned['boxes_per_pallet'] = boxes_per_pallet
        elif boxes_per_pallet < 1:
            self.add_error('boxes_per_pallet', 'عدد الصناديق على الطبلية يجب أن يكون أكبر من صفر.')

        # «عدد الطبالي» يُحسب تلقائياً = تقريب لأعلى (عدد الصناديق ÷ عدد الصناديق على الطبلية)
        # (يُطبَّق حتى لبضاعة البكمات، إذ يبقى ترتيبها على طبالي في المشغل).
        if box_count and boxes_per_pallet and boxes_per_pallet > 0:
            cleaned['pallet_count'] = -(-box_count // boxes_per_pallet)
        else:
            cleaned['pallet_count'] = 0

        # السيخ المختار يجب أن ينتمي للبراد المختار (منع الخطأ قبل الوصول للخدمة).
        cold_store = cleaned.get('cold_store')
        shelf = cleaned.get('shelf')
        if shelf and cold_store and shelf.cold_store_id != cold_store.id:
            self.add_error('shelf', 'السيخ المختار لا ينتمي للبراد المختار. اختَر سيخاً من أسياخ البراد نفسه.')
        return cleaned


class PalletTransferForm(forms.Form):
    to_cold_store = forms.ModelChoiceField(
        queryset=ColdStore.objects.all(),
        label='البراد المستهدف',
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    to_shelf = forms.ModelChoiceField(
        queryset=Shelf.objects.all(),
        required=False,
        label='السيخ / الرف المستهدف',
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    reason = forms.CharField(
        max_length=255,
        required=False,
        label='سبب النقل / ملاحظات',
        widget=forms.TextInput(attrs={'class': 'form-control'})
    )
