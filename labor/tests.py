from django.test import TestCase
from django.utils import timezone
from django.urls import reverse
from django.core.exceptions import ValidationError
from decimal import Decimal

from core.models import User, SystemSetting, AuditLog
from operations.models import Trader, Crop
from invoicing.models import Invoice, InvoiceItem
from invoicing.services import InvoiceService
from packing.models import PackingSession
from finance.models import TreasuryAccount
from labor.models import Workshop, DailyLaborLog, Employee, EmployeeSalaryPayment, EmployeeDeduction
from labor.services import LaborService, EmployeeService
from labor.forms import EmployeeForm


class Phase3LaborTests(TestCase):
    """
    اختبارات المرحلة الثالثة: الورش والعمالة
    (القاعدتان 4 و 14)
    """

    def setUp(self):
        self.settings = SystemSetting.get_settings()
        self.settings.save()
        self.manager = User.objects.create_user(
            username='labor_manager', password='pass', role=User.Role.MANAGER
        )
        self.accountant = User.objects.create_user(
            username='labor_accountant', password='pass', role=User.Role.ACCOUNTANT
        )
        self.trader = Trader.objects.create(code='T001', name='ابو معتز القيسي')
        self.crop = Crop.objects.create(name='نكتارين')
        self.workshop = Workshop.objects.create(
            name='ورشة أم السعيد',
            default_hourly_rate=Decimal('1.000'),
            default_transport_daily=Decimal('1.000'),
        )

    def test_rule_4_workshop_wages_not_added_to_invoice(self):
        """
        القاعدة 4: أجور الورش لا تُضاف تلقائياً إلى فاتورة التاجر.
        """
        # تسجيل يوم عمل بتكلفة 900 دينار
        log = LaborService.record_or_update_daily_log(
            workshop=self.workshop, work_date=timezone.now().date(),
            worker_count=100, work_hours=Decimal('8.00'),
            user=self.accountant,
        )
        self.assertEqual(log.total_cost, Decimal('900.000'))

        # فاتورة التاجر لا يجب أن تحتوي على أي بند أجور ورشة
        invoice = Invoice.objects.create(
            invoice_number='2026-T001-001', trader=self.trader, year=2026,
            crop=self.crop, status=Invoice.Status.DRAFT,
            packing_rate_snapshot=Decimal('0.000'), created_by=self.accountant,
        )
        InvoiceService.sync_invoice_with_operations(invoice, user=self.accountant)
        invoice.refresh_from_db()

        self.assertEqual(invoice.grand_total, Decimal('0.000'))
        self.assertEqual(InvoiceItem.objects.filter(invoice=invoice).count(), 0)
        for item in InvoiceItem.objects.filter(invoice=invoice):
            self.assertNotIn('ورشة', item.description)

    def test_rule_14_no_production_no_division_by_zero(self):
        """
        القاعدة 14: غياب الإنتاج لا يسبب قسمة على صفر في تكلفة الكيلو.
        """
        # لا يوجد إنتاج معتمد أصلاً
        result = LaborService.calculate_cost_per_kg()
        self.assertEqual(result['cost_per_kg'], Decimal('0.000'))
        self.assertEqual(result['total_packed_kg'], Decimal('0.000'))

        # نضيف تكلفة ورشة بدون إنتاج: يجب أن تبقى النتيجة صفراً بدون خطأ
        LaborService.record_or_update_daily_log(
            workshop=self.workshop, work_date=timezone.now().date(),
            worker_count=10, work_hours=Decimal('8.00'), user=self.accountant,
        )
        result = LaborService.calculate_cost_per_kg()
        self.assertEqual(result['total_labor_cost'], Decimal('90.000'))
        self.assertEqual(result['cost_per_kg'], Decimal('0.000'))

        # عند وجود إنتاج معتمد: التكلفة/كغ = التكلفة ÷ الإنتاج
        PackingSession.objects.create(
            session_code='PS-TEST-001', trader=self.trader, crop=self.crop,
            packing_method=PackingSession.Method.MACHINE,
            output_packed_kg=Decimal('100.000'),
            status=PackingSession.Status.COMPLETED,
            created_by=self.accountant,
        )
        result = LaborService.calculate_cost_per_kg()
        # 90.000 ÷ 100 = 0.900
        self.assertEqual(result['cost_per_kg'], Decimal('0.900'))

    def test_labor_log_same_day_updates_without_duplicating(self):
        """
        حماية من تكرار استحقاق أجور الورش لنفس اليوم (تُحدّث لا تُكرّر).
        """
        LaborService.record_or_update_daily_log(
            workshop=self.workshop, work_date=timezone.now().date(),
            worker_count=5, work_hours=Decimal('8.00'), user=self.accountant,
        )
        LaborService.record_or_update_daily_log(
            workshop=self.workshop, work_date=timezone.now().date(),
            worker_count=7, work_hours=Decimal('8.00'), user=self.accountant,
        )
        self.assertEqual(DailyLaborLog.objects.filter(workshop=self.workshop).count(), 1)
        log = DailyLaborLog.objects.get(workshop=self.workshop)
        self.assertEqual(log.worker_count, 7)


class EmployeeSalaryTests(TestCase):
    """
    الموظفون برواتب شهرية — قسم مستقل عن الورش:
    - لا يدخلون في كلفة الكيلو الموضب.
    - الراتب المدفوع يخصم من الخزينة مرة واحدة فقط.
    - منع تكرار راتب الشهر نفسه لنفس الموظف.
    """

    def setUp(self):
        self.settings = SystemSetting.get_settings()
        self.settings.save()
        self.manager = User.objects.create_user(
            username='emp_manager', password='pass', role=User.Role.MANAGER
        )
        self.accountant = User.objects.create_user(
            username='emp_accountant', password='pass', role=User.Role.ACCOUNTANT
        )
        self.viewer = User.objects.create_user(
            username='emp_viewer', password='pass', role=User.Role.VIEWER
        )
        self.employee = Employee.objects.create(
            name='أحمد محمد', job_title='محاسب', monthly_salary=Decimal('500.000'),
        )
        self.account = TreasuryAccount.objects.create(
            name='الصندوق', account_type=TreasuryAccount.AccountType.CASH,
            opening_balance=Decimal('1000.000'), current_balance=Decimal('1000.000'),
        )

    def test_employee_list_and_detail_render(self):
        self.client.force_login(self.accountant)
        self.assertEqual(self.client.get(reverse('employee_list')).status_code, 200)
        self.assertEqual(self.client.get(reverse('employee_detail', args=[self.employee.pk])).status_code, 200)

    def test_manager_creates_employee(self):
        self.client.force_login(self.manager)
        resp = self.client.post(reverse('employee_create'), {
            'name': 'سالم علي', 'job_title': 'حارس', 'phone': '0790000000',
            'monthly_salary': '300.000', 'hire_date': '', 'is_active': 'on', 'notes': '',
        })
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(Employee.objects.filter(name='سالم علي').exists())

    def test_viewer_cannot_create_employee(self):
        self.client.force_login(self.viewer)
        self.client.post(reverse('employee_create'), {
            'name': 'ممنوع', 'monthly_salary': '100.000', 'is_active': 'on',
        })
        self.assertFalse(Employee.objects.filter(name='ممنوع').exists())

    def test_paid_salary_deducts_treasury_once(self):
        EmployeeService.record_salary_payment(
            employee=self.employee, year=2026, month=6, amount=Decimal('500.000'),
            status=EmployeeSalaryPayment.Status.PAID, treasury_account=self.account,
            user=self.accountant,
        )
        self.account.refresh_from_db()
        self.assertEqual(self.account.current_balance, Decimal('500.000'))

    def test_accrued_salary_does_not_touch_treasury(self):
        EmployeeService.record_salary_payment(
            employee=self.employee, year=2026, month=6, amount=Decimal('500.000'),
            status=EmployeeSalaryPayment.Status.ACCRUED, user=self.accountant,
        )
        self.account.refresh_from_db()
        self.assertEqual(self.account.current_balance, Decimal('1000.000'))

    def test_duplicate_salary_same_month_rejected(self):
        EmployeeService.record_salary_payment(
            employee=self.employee, year=2026, month=6, amount=Decimal('500.000'),
            status=EmployeeSalaryPayment.Status.ACCRUED, user=self.accountant,
        )
        with self.assertRaises(ValidationError):
            EmployeeService.record_salary_payment(
                employee=self.employee, year=2026, month=6, amount=Decimal('500.000'),
                status=EmployeeSalaryPayment.Status.ACCRUED, user=self.accountant,
            )
        self.assertEqual(EmployeeSalaryPayment.objects.filter(employee=self.employee).count(), 1)

    def test_paid_salary_requires_treasury(self):
        with self.assertRaises(ValidationError):
            EmployeeService.record_salary_payment(
                employee=self.employee, year=2026, month=6, amount=Decimal('500.000'),
                status=EmployeeSalaryPayment.Status.PAID, user=self.accountant,
            )

    def test_salary_does_not_affect_cost_per_kg(self):
        """رواتب الموظفين لا تدخل في كلفة الكيلو الموضب."""
        before = LaborService.calculate_cost_per_kg()
        EmployeeService.record_salary_payment(
            employee=self.employee, year=2026, month=6, amount=Decimal('900.000'),
            status=EmployeeSalaryPayment.Status.PAID, treasury_account=self.account, user=self.accountant,
        )
        after = LaborService.calculate_cost_per_kg()
        self.assertEqual(before['total_labor_cost'], after['total_labor_cost'])
        self.assertEqual(before['cost_per_kg'], after['cost_per_kg'])

    def test_delete_paid_salary_returns_money_to_treasury(self):
        payment = EmployeeService.record_salary_payment(
            employee=self.employee, year=2026, month=6, amount=Decimal('500.000'),
            status=EmployeeSalaryPayment.Status.PAID, treasury_account=self.account, user=self.accountant,
        )
        self.account.refresh_from_db()
        self.assertEqual(self.account.current_balance, Decimal('500.000'))
        EmployeeService.delete_salary_payment(payment, reason='خطأ', user=self.manager)
        self.account.refresh_from_db()
        self.assertEqual(self.account.current_balance, Decimal('1000.000'))
        self.assertEqual(EmployeeSalaryPayment.objects.filter(employee=self.employee).count(), 0)

    def test_salary_pay_view_records_payment(self):
        self.client.force_login(self.accountant)
        resp = self.client.post(reverse('employee_salary_pay'), {
            'employee': self.employee.pk, 'year': 2026, 'month': 7,
            'amount': '500.000', 'status': 'PAID', 'payment_date': '2026-07-31',
            'treasury_account': self.account.pk, 'notes': '',
        })
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(EmployeeSalaryPayment.objects.filter(
            employee=self.employee, year=2026, month=7).exists())

    def test_salary_report_renders(self):
        self.client.force_login(self.accountant)
        self.assertEqual(self.client.get(reverse('salary_report')).status_code, 200)

    def test_employee_name_required(self):
        form = EmployeeForm(data={'name': '   ', 'monthly_salary': '100.000'})
        self.assertFalse(form.is_valid())
        self.assertIn('name', form.errors)


class EmployeeDeductionTests(TestCase):
    """
    خصومات رواتب الموظفين (غياب/سلفة/تأخير/جزاء/أخرى):
    - سجل مستقل لا يعدّل الراتب الأساسي.
    - يُجمَّع تلقائياً مع راتب الشهر نفسه، ويُصرف الصافي فقط من الخزينة.
    - لا صافي سالب: الخصم لا يتجاوز الراتب.
    - إضافة: محاسب أو مدير، حذف: مدير فقط.
    - لا يؤثر على كلفة الكيلو الموضب (القاعدة 4).
    """

    def setUp(self):
        self.settings = SystemSetting.get_settings()
        self.settings.save()
        self.manager = User.objects.create_user(
            username='ded_manager', password='pass', role=User.Role.MANAGER
        )
        self.accountant = User.objects.create_user(
            username='ded_accountant', password='pass', role=User.Role.ACCOUNTANT
        )
        self.viewer = User.objects.create_user(
            username='ded_viewer', password='pass', role=User.Role.VIEWER
        )
        self.employee = Employee.objects.create(
            name='سامي خليل', job_title='عامل', monthly_salary=Decimal('600.000'),
        )
        self.account = TreasuryAccount.objects.create(
            name='الصندوق', account_type=TreasuryAccount.AccountType.CASH,
            opening_balance=Decimal('5000.000'), current_balance=Decimal('5000.000'),
        )

    def test_deduction_does_not_change_base_salary(self):
        EmployeeService.record_deduction(
            employee=self.employee, year=2026, month=6, amount=Decimal('100.000'),
            deduction_type='ABSENCE', reason='غياب يومين', user=self.accountant,
        )
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.monthly_salary, Decimal('600.000'))
        self.assertEqual(self.employee.deductions_for(2026, 6), Decimal('100.000'))
        self.assertEqual(self.employee.net_salary_for(2026, 6), Decimal('500.000'))

    def test_paid_salary_deducts_only_net_from_treasury(self):
        EmployeeService.record_deduction(
            employee=self.employee, year=2026, month=6, amount=Decimal('150.000'),
            deduction_type='ADVANCE', user=self.accountant,
        )
        payment = EmployeeService.record_salary_payment(
            employee=self.employee, year=2026, month=6, amount=Decimal('600.000'),
            status=EmployeeSalaryPayment.Status.PAID, treasury_account=self.account,
            user=self.accountant,
        )
        self.assertEqual(payment.gross_amount, Decimal('600.000'))
        self.assertEqual(payment.deductions_total, Decimal('150.000'))
        self.assertEqual(payment.amount, Decimal('450.000'))
        self.account.refresh_from_db()
        # خُصم الصافي فقط (450) لا الأساسي (600)
        self.assertEqual(self.account.current_balance, Decimal('4550.000'))

    def test_accrued_salary_with_deductions_does_not_touch_treasury(self):
        EmployeeService.record_deduction(
            employee=self.employee, year=2026, month=6, amount=Decimal('100.000'),
            deduction_type='LATENESS', user=self.accountant,
        )
        payment = EmployeeService.record_salary_payment(
            employee=self.employee, year=2026, month=6, amount=Decimal('600.000'),
            status=EmployeeSalaryPayment.Status.ACCRUED, user=self.accountant,
        )
        self.assertEqual(payment.amount, Decimal('500.000'))
        self.account.refresh_from_db()
        self.assertEqual(self.account.current_balance, Decimal('5000.000'))

    def test_deduction_larger_than_salary_is_clamped(self):
        EmployeeService.record_deduction(
            employee=self.employee, year=2026, month=6, amount=Decimal('900.000'),
            deduction_type='PENALTY', user=self.accountant,
        )
        payment = EmployeeService.record_salary_payment(
            employee=self.employee, year=2026, month=6, amount=Decimal('600.000'),
            status=EmployeeSalaryPayment.Status.PAID, treasury_account=self.account,
            user=self.accountant,
        )
        # الخصم لا يتجاوز الراتب، ولا صافي سالب: الصافي صفر
        self.assertEqual(payment.deductions_total, Decimal('600.000'))
        self.assertEqual(payment.amount, Decimal('0.000'))
        # صافٍ صفر لا يمسّ الخزينة
        self.account.refresh_from_db()
        self.assertEqual(self.account.current_balance, Decimal('5000.000'))

    def test_deduction_rejects_non_positive_amount(self):
        with self.assertRaises(ValidationError):
            EmployeeService.record_deduction(
                employee=self.employee, year=2026, month=6, amount=Decimal('0.000'),
                deduction_type='ABSENCE', user=self.accountant,
            )

    def test_accountant_can_add_deduction_view(self):
        self.client.force_login(self.accountant)
        resp = self.client.post(reverse('employee_deduction_add'), {
            'employee': self.employee.pk, 'deduction_type': 'ABSENCE',
            'year': 2026, 'month': 6, 'amount': '120.000',
            'deduction_date': '2026-06-10', 'reason': 'غياب 3 أيام',
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self.employee.deductions.count(), 1)

    def test_viewer_cannot_add_deduction(self):
        self.client.force_login(self.viewer)
        self.client.post(reverse('employee_deduction_add'), {
            'employee': self.employee.pk, 'deduction_type': 'ABSENCE',
            'year': 2026, 'month': 6, 'amount': '120.000',
        })
        self.assertEqual(self.employee.deductions.count(), 0)

    def test_only_manager_can_delete_deduction(self):
        d = EmployeeService.record_deduction(
            employee=self.employee, year=2026, month=6, amount=Decimal('50.000'),
            deduction_type='OTHER', user=self.accountant,
        )
        # المحاسب ممنوع
        self.client.force_login(self.accountant)
        self.client.post(reverse('employee_deduction_delete', args=[d.pk]), {'reason': 'خطأ'})
        self.assertEqual(EmployeeDeduction.objects.filter(pk=d.pk).count(), 1)
        # المدير مسموح
        self.client.force_login(self.manager)
        self.client.post(reverse('employee_deduction_delete', args=[d.pk]), {'reason': 'خطأ'})
        self.assertEqual(EmployeeDeduction.objects.filter(pk=d.pk).count(), 0)

    def test_employee_detail_shows_deductions_section(self):
        EmployeeService.record_deduction(
            employee=self.employee, year=2026, month=6, amount=Decimal('75.000'),
            deduction_type='ABSENCE', reason='غياب', user=self.accountant,
        )
        self.client.force_login(self.accountant)
        resp = self.client.get(reverse('employee_detail', args=[self.employee.pk]))
        html = resp.content.decode('utf-8')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('سجل الخصومات', html)
        self.assertIn('تسجيل خصم على الراتب', html)
        self.assertIn('75.000', html)

    def test_deductions_do_not_affect_cost_per_kg(self):
        """القاعدة 4: الخصومات جزء من رواتب الموظفين ولا تدخل في كلفة الكيلو الموضب."""
        before = LaborService.calculate_cost_per_kg()
        EmployeeService.record_deduction(
            employee=self.employee, year=2026, month=6, amount=Decimal('200.000'),
            deduction_type='ABSENCE', user=self.accountant,
        )
        after = LaborService.calculate_cost_per_kg()
        self.assertEqual(before['total_labor_cost'], after['total_labor_cost'])
        self.assertEqual(before['cost_per_kg'], after['cost_per_kg'])

    def test_delete_deduction_logs_audit(self):
        d = EmployeeService.record_deduction(
            employee=self.employee, year=2026, month=6, amount=Decimal('50.000'),
            deduction_type='OTHER', user=self.accountant,
        )
        EmployeeService.delete_deduction(d, reason='خطأ إدخال', user=self.manager)
        self.assertTrue(
            AuditLog.objects.filter(action='DELETE_EMPLOYEE_DEDUCTION').exists()
        )
