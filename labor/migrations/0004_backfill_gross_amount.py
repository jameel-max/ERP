"""
ترحيل بيانات: تهيئة الحقل الجديد gross_amount للمدفوعات القديمة.

قبل إضافة الخصومات، كان مبلغ الراتب يُخزَّن في `amount` مباشرة. بعد إضافة
`gross_amount` (الأساسي) و`deductions_total` (الخصومات)، نحتاج تهيئة القيود
القديمة بحيث: الأساسي = المبلغ المحفوظ، الخصومات = صفر، والصافي = المبلغ المحفوظ.
هكذا لا تتغيّر أي قيمة تاريخية ولا تظهر أصفار خاطئة في التقارير.
"""

from django.db import migrations, models


def backfill_gross_amount(apps, schema_editor):
    EmployeeSalaryPayment = apps.get_model('labor', 'EmployeeSalaryPayment')
    EmployeeSalaryPayment.objects.all().update(
        gross_amount=models.F('amount'),
        deductions_total=0,
    )


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('labor', '0003_employeesalarypayment_deductions_total_and_more'),
    ]

    operations = [
        migrations.RunPython(backfill_gross_amount, noop),
    ]
