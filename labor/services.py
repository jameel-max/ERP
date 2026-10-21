from decimal import Decimal
from django.db import transaction
from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.utils import timezone

from core.models import AuditLog, SystemSetting
from .models import Workshop, DailyLaborLog
from packing.models import PackingSession


class LaborService:
    """
    إدارة وحساب تكاليف الورش والعمال
    تكلفة الورشة = عدد العمال × ساعات العمل × سعر الساعة + عدد العمال × بدل المواصلات
    """
    @classmethod
    @transaction.atomic
    def record_or_update_daily_log(cls, workshop, work_date, worker_count, work_hours, hourly_rate=None, transport_allowance=None, notes="", user=None):
        if worker_count <= 0:
            raise ValidationError("عدد العمال يجب أن يكون أكبر من صفر.")
        if work_hours <= 0:
            raise ValidationError("ساعات العمل يجب أن تكون أكبر من صفر.")

        # سعر الساعة: ما صرّح به المستخدم ← ما عليه الورشة ← سعر ساعة النظام في الإعدادات
        if hourly_rate is not None:
            rate = hourly_rate
        else:
            rate = workshop.default_hourly_rate or SystemSetting.get_settings().worker_hourly_rate
        if transport_allowance is not None:
            transport = transport_allowance
        else:
            transport = workshop.default_transport_daily or SystemSetting.get_settings().worker_transport_daily

        log, created = DailyLaborLog.objects.get_or_create(
            workshop=workshop,
            work_date=work_date,
            defaults={
                'worker_count': worker_count,
                'work_hours': Decimal(str(work_hours)),
                'hourly_rate': Decimal(str(rate)),
                'transport_allowance_daily': Decimal(str(transport)),
                'notes': notes,
                'created_by': user
            }
        )

        if not created:
            # تعديل سجل اليوم دون إنشاء تكلفة ثانية مكررة
            log.worker_count = worker_count
            log.work_hours = Decimal(str(work_hours))
            log.hourly_rate = Decimal(str(rate))
            log.transport_allowance_daily = Decimal(str(transport))
            log.notes = notes

        log.calculate_cost()
        log.save()

        AuditLog.log(
            user=user,
            action='RECORD_LABOR_LOG',
            instance=log,
            reason=f'توثيق يوم عمل ورشة {workshop.name} بتاريخ {work_date}',
            details={'workers': worker_count, 'hours': str(work_hours), 'total_cost': str(log.total_cost)}
        )
        return log

    @classmethod
    def calculate_cost_per_kg(cls, start_date=None, end_date=None):
        """
        تكلفة الكيلوغرام = تكلفة الورشة ÷ الإنتاج المعتمد للفترة نفسها
        مع حماية صارمة من القسمة على صفر عند غياب الإنتاج
        """
        from django.core.cache import cache
        from django.db import transaction

        # نخزّن تكلفة الكيلو للفترة الكاملة (تُستخدم في اللوحات) لمدة قصيرة
        # لتقليل استعلامات الشبكة الكثيرة. on_commit يمنع التسرّب بين الاختبارات.
        _cache_full = start_date is None and end_date is None
        _cache_key = 'labor_cost_per_kg_full'
        if _cache_full:
            _cached = cache.get(_cache_key)
            if _cached is not None:
                return _cached

        labor_qs = DailyLaborLog.objects.filter(is_approved=True)
        packing_qs = PackingSession.objects.filter(status__in=[PackingSession.Status.COMPLETED, PackingSession.Status.APPROVED])

        if start_date:
            labor_qs = labor_qs.filter(work_date__gte=start_date)
            packing_qs = packing_qs.filter(started_at__date__gte=start_date)
        if end_date:
            labor_qs = labor_qs.filter(work_date__lte=end_date)
            packing_qs = packing_qs.filter(started_at__date__lte=end_date)

        total_labor_cost = labor_qs.aggregate(total=Sum('total_cost'))['total'] or Decimal('0.000')
        total_packed_kg = packing_qs.aggregate(total=Sum('output_packed_kg'))['total'] or Decimal('0.000')

        if total_packed_kg > 0:
            cost_per_kg = (total_labor_cost / total_packed_kg).quantize(Decimal('0.001'))
        else:
            cost_per_kg = Decimal('0.000')

        _result = {
            'total_labor_cost': total_labor_cost,
            'total_packed_kg': total_packed_kg,
            'cost_per_kg': cost_per_kg
        }
        if _cache_full:
            transaction.on_commit(lambda: cache.set(_cache_key, _result, 60))
        return _result


class EmployeeService:
    """
    رواتب الموظفين الشهرية — قسم مستقل تماماً عن الورش.
    (القاعدة 4: رواتب الموظفين لا تدخل في كلفة الكيلو الموضب ولا فاتورة التاجر.)
    """

    @classmethod
    @transaction.atomic
    def record_salary_payment(cls, employee, year, month, amount, status='ACCRUED',
                              treasury_account=None, payment_date=None, notes='', user=None):
        """
        تسجيل صرف راتب شهر واحد لموظف.
        - `amount` هو **الراتب الأساسي (gross)** المُدخل. تُطرح منه خصومات الشهر تلقائياً
          ليُصرف الصافي فقط من الخزينة.
        - الراتب المدفوع فعلاً يخصم الصافي من الخزينة مرة واحدة فقط.
        - المستحق لا يمسّ الخزينة.
        - منع الازدواج: قيد واحد لكل موظف/سنة/شهر.
        """
        from .models import EmployeeSalaryPayment

        if not (1 <= int(month) <= 12):
            raise ValidationError('الشهر يجب أن يكون بين 1 و 12.')
        gross = Decimal(str(amount))
        if gross <= 0:
            raise ValidationError('مبلغ الراتب يجب أن يكون أكبر من صفر.')

        if EmployeeSalaryPayment.objects.filter(employee=employee, year=year, month=month).exists():
            raise ValidationError(
                f'راتب شهر {int(month):02d}/{year} للموظف {employee.name} مسجّل مسبقاً — '
                'لا يمكن تسجيله مرتين لنفس الشهر.'
            )

        # مجموع خصومات الشهر (غياب/سلفة/تأخير...) محسوب على أساس الراتب المُدخل.
        # لا يتجاوز الخصم الراتب: لا يُسمح بصافي سالب، لكن يُسمح بصافٍ صفر
        # (الشهر مُستوفى خصومات كاملة) فيُسجَّل القيد بلا حركة خزينة.
        deductions_total = employee.deductions_for(year, month)
        if deductions_total > gross:
            deductions_total = gross
        net_amount = (gross - deductions_total).quantize(Decimal('0.001'))

        if status == EmployeeSalaryPayment.Status.PAID:
            if not treasury_account:
                raise ValidationError('صرف الراتب المدفوع يتطلب تحديد الخزينة أو الحساب المدفوع منه.')
            payment_date = payment_date or timezone.now().date()

        payment = EmployeeSalaryPayment.objects.create(
            employee=employee, year=year, month=month,
            gross_amount=gross, deductions_total=deductions_total, amount=net_amount,
            status=status, payment_date=payment_date,
            treasury_account=treasury_account, notes=notes, created_by=user,
        )

        if status == EmployeeSalaryPayment.Status.PAID and treasury_account:
            treasury_account.current_balance -= net_amount
            treasury_account.save()

        AuditLog.log(
            user=user,
            action='RECORD_SALARY_PAYMENT',
            instance=payment,
            reason=(
                f'صرف راتب {employee.name} لشهر {int(month):02d}/{year}: '
                f'الأساسي {gross} − الخصومات {deductions_total} = الصافي {net_amount} دينار ({status})'
            ),
            details={
                'gross': str(gross), 'deductions': str(deductions_total),
                'net': str(net_amount), 'status': status, 'treasury': str(treasury_account),
            },
        )
        return payment

    @classmethod
    @transaction.atomic
    def record_deduction(cls, employee, year, month, amount, deduction_type='ABSENCE',
                         deduction_date=None, reason='', user=None):
        """
        تسجيل خصم على راتب موظف لشهر محدد.
        الخصم سجل مستقل لا يعدّل الراتب الأساسي، ويُجمَّع عند الصرف لحساب الصافي.
        """
        from .models import EmployeeDeduction

        if not (1 <= int(month) <= 12):
            raise ValidationError('الشهر يجب أن يكون بين 1 و 12.')
        amount = Decimal(str(amount))
        if amount <= 0:
            raise ValidationError('مبلغ الخصم يجب أن يكون أكبر من صفر.')
        if deduction_type not in EmployeeDeduction.Type.values:
            raise ValidationError('نوع الخصم غير معروف.')

        deduction = EmployeeDeduction.objects.create(
            employee=employee, deduction_type=deduction_type,
            year=year, month=month, amount=amount,
            deduction_date=deduction_date or timezone.now().date(),
            reason=reason, created_by=user,
        )
        AuditLog.log(
            user=user,
            action='RECORD_EMPLOYEE_DEDUCTION',
            instance=deduction,
            reason=(
                f'تسجيل خصم {deduction.get_deduction_type_display()} على {employee.name} '
                f'لشهر {int(month):02d}/{year} بقيمة {amount} دينار'
            ),
            details={
                'employee': employee.name, 'type': deduction_type,
                'amount': str(amount), 'period': deduction.period_label,
            },
        )
        return deduction

    @classmethod
    @transaction.atomic
    def delete_deduction(cls, deduction, reason='', user=None):
        """حذف خصم مسجّل بخطأ — مع قيد تدقيق. لا يمسّ الخزينة (الخصم لم يُصرف بعد)."""
        from .models import EmployeeDeduction

        snapshot = {
            'employee': deduction.employee.name,
            'type': deduction.get_deduction_type_display(),
            'amount': str(deduction.amount),
            'period': deduction.period_label,
        }
        AuditLog.log(
            user=user,
            action='DELETE_EMPLOYEE_DEDUCTION',
            instance=deduction,
            reason=reason or (
                f'حذف خصم {snapshot["type"]} عن {snapshot["employee"]} لشهر {snapshot["period"]}'
            ),
            details=snapshot,
        )
        deduction.delete()
        return snapshot

    @classmethod
    @transaction.atomic
    def delete_salary_payment(cls, payment, reason='', user=None):
        """
        حذف قيد راتب مسجّل بخطأ — مع قيد عكسي موثق: إن كان مدفوعاً يُعاد المبلغ للخزينة.
        """
        from .models import EmployeeSalaryPayment

        if payment.status == EmployeeSalaryPayment.Status.PAID and payment.treasury_account:
            payment.treasury_account.current_balance += payment.amount
            payment.treasury_account.save()

        snapshot = {
            'employee': payment.employee.name,
            'period': payment.period_label,
            'amount': str(payment.amount),
            'status': payment.status,
        }
        AuditLog.log(
            user=user,
            action='DELETE_SALARY_PAYMENT',
            instance=payment,
            reason=reason or f'حذف قيد راتب {snapshot["employee"]} لشهر {snapshot["period"]}',
            details=snapshot,
        )
        payment.delete()
        return snapshot
