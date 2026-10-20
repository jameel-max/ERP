from django import forms
from django.contrib.auth.forms import AuthenticationForm
from .models import SystemSetting


class CustomLoginForm(AuthenticationForm):
    username = forms.CharField(widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'اسم المستخدم'}))
    password = forms.CharField(widget=forms.PasswordInput(attrs={'class': 'form-control', 'placeholder': 'كلمة المرور'}))


class SystemSettingForm(forms.ModelForm):
    class Meta:
        model = SystemSetting
        fields = [
            'company_name_en', 'company_name_ar', 'brand_color',
            'empty_crate_weight', 'empty_pallet_weight',
            'worker_hourly_rate', 'worker_transport_daily',
            'rounding_policy', 'currency_symbol', 'currency_decimals', 'weight_decimals',
            'weight_tolerance_pct', 'fifo_alert_days',
            'packed_cooling_daily_rate',
            'default_packing_rate_per_kg', 'default_manual_packing_rate_per_kg',
            'default_work_hours', 'default_exchange_rate',
            'scan_base_url',
            'critical_fields', 'non_critical_fields'
        ]
        widgets = {
            'company_name_en': forms.TextInput(attrs={'class': 'form-control'}),
            'company_name_ar': forms.TextInput(attrs={'class': 'form-control'}),
            'brand_color': forms.TextInput(attrs={'class': 'form-control', 'type': 'color'}),
            'empty_crate_weight': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.001'}),
            'empty_pallet_weight': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.001'}),
            'worker_hourly_rate': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.001'}),
            'worker_transport_daily': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.001'}),
            'rounding_policy': forms.TextInput(attrs={'class': 'form-control'}),
            'currency_symbol': forms.TextInput(attrs={'class': 'form-control'}),
            'currency_decimals': forms.NumberInput(attrs={'class': 'form-control'}),
            'weight_decimals': forms.NumberInput(attrs={'class': 'form-control'}),
            'weight_tolerance_pct': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'fifo_alert_days': forms.NumberInput(attrs={'class': 'form-control'}),
            'packed_cooling_daily_rate': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.001'}),
            'default_packing_rate_per_kg': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.001'}),
            'default_manual_packing_rate_per_kg': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.001'}),
            'default_work_hours': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'default_exchange_rate': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.000001', 'min': '0'}),
            'scan_base_url': forms.TextInput(attrs={'class': 'form-control', 'dir': 'ltr', 'placeholder': 'http://192.168.1.50:8000'}),
            'critical_fields': forms.TextInput(attrs={'class': 'form-control', 'dir': 'ltr'}),
            'non_critical_fields': forms.TextInput(attrs={'class': 'form-control', 'dir': 'ltr'}),
        }
