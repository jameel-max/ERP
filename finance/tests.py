from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.db.models import Sum
from datetime import timedelta
from decimal import Decimal

from core.models import User, AuditLog, SystemSetting
from operations.models import Trader, Crop, BoxType, ColdStore, Shelf
from invoicing.models import Invoice
from finance.models import (
    TreasuryAccount, ExpenseCategory, Expense, Check, Payment,
    InternalTransfer, TraderLedgerEntry,
)
from finance.services import FinanceService


class Phase3FinanceTests(TestCase):
    """
    اختبارات المرحلة الثالثة: القواعد المالية والمحاسبية
    (القواعد 2، 5، 6، 7، 8، 9، 11، 12، 13، 15، 21، 22، 23، 24)
    """

    def setUp(self):
        self.settings = SystemSetting.get_settings()
        self.settings.save()

        self.manager = User.objects.create_user(
            username='fin_manager', password='pass', role=User.Role.MANAGER
        )
        self.accountant = User.objects.create_user(
            username='fin_accountant', password='pass', role=User.Role.ACCOUNTANT
        )
        self.viewer = User.objects.create_user(
            username='fin_viewer', password='pass', role=User.Role.VIEWER
        )

        self.trader = Trader.objects.create(code='T001', name='ابو معتز القيسي')
        self.crop = Crop.objects.create(name='نكتارين')

        self.cash = TreasuryAccount.objects.create(
            name='الصندوق النقدي', account_type=TreasuryAccount.AccountType.CASH,
            opening_balance=Decimal('0.000'), current_balance=Decimal('0.000'),
        )
        self.bank = TreasuryAccount.objects.create(
            name='الحساب البنكي', account_type=TreasuryAccount.AccountType.BANK,
            opening_balance=Decimal('0.000'), current_balance=Decimal('0.000'),
        )
        self.category = ExpenseCategory.objects.create(name='كهرباء وماء')

    def _approved_invoice(self, total='100.000', number='2026-T001-001', created_at=None):
        invoice = Invoice.objects.create(
            invoice_number=number,
            trader=self.trader,
            year=2026,
            crop=self.crop,
            status=Invoice.Status.APPROVED,
            packing_rate_snapshot=Decimal('0.000'),
            grand_total=Decimal(total),
            remaining_balance=Decimal(total),
            created_by=self.accountant,
        )
        if created_at:
            Invoice.objects.filter(pk=invoice.pk).update(created_at=created_at)
        return invoice

    # ---------------- قاعدة 2 ----------------
    def test_rule_2_payment_does_not_duplicate_revenue(self):
        """
        القاعدة 2: تسجيل دفعة لا يسجل الإيراد مرة أخرى.
        """
        self._approved_invoice(total='100.000')

        kpis_before = FinanceService.calculate_financial_kpis()
        self.assertEqual(kpis_before['earned_revenue'], Decimal('100.000'))

        FinanceService.record_payment(
            trader=self.trader, amount=Decimal('40.000'),
            payment_method=Payment.Method.CASH, treasury_account=self.cash,
            user=self.accountant,
        )

        kpis_after = FinanceService.calculate_financial_kpis()
        # الإيراد المكتسب ثابت، والمقبوضات النقدية زادت فقط
        self.assertEqual(kpis_after['earned_revenue'], Decimal('100.000'))
        self.assertEqual(kpis_after['actual_cash_in'], Decimal('40.000'))
        self.assertEqual(Payment.objects.count(), 1)
        # الذمة انخفضت بمقدار الدفعة
        self.assertEqual(kpis_after['net_receivables'], Decimal('60.000'))

    # ---------------- قاعدة 5 ----------------
    def test_rule_5_unpaid_expense_not_cash_out(self):
        """
        القاعدة 5: المصروف غير المدفوع لا يظهر كمدفوعات نقدية.
        """
        FinanceService.record_expense(
            category=self.category, amount=Decimal('30.000'),
            due_date=timezone.now().date(), status=Expense.Status.ACCRUED,
            description='مستحق غير مدفوع', user=self.accountant,
        )

        kpis = FinanceService.calculate_financial_kpis()
        self.assertEqual(kpis['operating_expenses'], Decimal('30.000'))
        self.assertEqual(kpis['actual_cash_out'], Decimal('0.000'))
        self.cash.refresh_from_db()
        self.assertEqual(self.cash.current_balance, Decimal('0.000'))

        # عند الدفع الفعلي فقط يدخل التدفق النقدي ويُخصم من الخزينة
        FinanceService.record_expense(
            category=self.category, amount=Decimal('20.000'),
            due_date=timezone.now().date(), status=Expense.Status.PAID,
            treasury_account=self.cash, description='مدفوع فعلاً', user=self.accountant,
        )
        kpis = FinanceService.calculate_financial_kpis()
        self.assertEqual(kpis['actual_cash_out'], Decimal('20.000'))
        self.cash.refresh_from_db()
        self.assertEqual(self.cash.current_balance, Decimal('-20.000'))

    # ---------------- قاعدة 6 ----------------
    def test_rule_6_uncollected_check_is_not_cash(self):
        """
        القاعدة 6: الشيك غير المحصل لا يظهر ضمن المقبوضات حتى يُحصّل فعلاً.
        """
        check = Check.objects.create(
            check_number='CHK-1001', trader=self.trader, bank_name='البنك العربي',
            amount=Decimal('50.000'), due_date=timezone.now().date(),
            status=Check.Status.REGISTERED, created_by=self.accountant,
        )

        kpis = FinanceService.calculate_financial_kpis()
        self.assertEqual(kpis['actual_cash_in'], Decimal('0.000'))
        self.assertEqual(kpis['checks_under_collection'], Decimal('50.000'))
        self.assertEqual(Payment.objects.count(), 0)

        # التحصيل الفعلي: يُنشئ سند قبض ويضيف للنقدية والخزينة
        FinanceService.collect_check(check, target_treasury=self.bank, user=self.accountant)
        kpis = FinanceService.calculate_financial_kpis()
        self.assertEqual(kpis['actual_cash_in'], Decimal('50.000'))
        self.assertEqual(kpis['checks_under_collection'], Decimal('0.000'))
        self.bank.refresh_from_db()
        self.assertEqual(self.bank.current_balance, Decimal('50.000'))

    # ---------------- قاعدة 7 ----------------
    def test_rule_7_revenue_timing_vs_collection(self):
        """
        القاعدة 7: اختلاف توقيت الإيراد عن التحصيل ينعكس صحيحاً على المؤشرات.
        """
        self._approved_invoice(total='100.000')
        kpis = FinanceService.calculate_financial_kpis()
        # إيراد مكتسب بدون أي تحصيل نقدي
        self.assertEqual(kpis['earned_revenue'], Decimal('100.000'))
        self.assertEqual(kpis['actual_cash_in'], Decimal('0.000'))
        self.assertEqual(kpis['net_cash_flow'], Decimal('0.000'))

        FinanceService.record_payment(
            trader=self.trader, amount=Decimal('100.000'),
            payment_method=Payment.Method.BANK_TRANSFER, treasury_account=self.bank,
            user=self.accountant,
        )
        kpis = FinanceService.calculate_financial_kpis()
        # الإيراد ثابت والتحصيل تحقق الآن: فصل تام بين أساس الاستحقاق وأساس النقد
        self.assertEqual(kpis['earned_revenue'], Decimal('100.000'))
        self.assertEqual(kpis['actual_cash_in'], Decimal('100.000'))
        self.assertEqual(kpis['net_cash_flow'], Decimal('100.000'))

    # ---------------- قاعدة 8 & 21 ----------------
    def test_rule_8_and_21_recurring_expense_no_duplicate_accrual(self):
        """
        القاعدة 8 و 21: إعادة إرسال/تشغيل المصروف المتكرر لا ينشئ استحقاقاً مكرراً.
        """
        key = 'RENT-2026-01'
        FinanceService.record_expense(
            category=self.category, amount=Decimal('500.000'),
            due_date=timezone.now().date(), status=Expense.Status.ACCRUED,
            description='إيجار شهري', is_recurring=True, recurrence_key=key,
            user=self.accountant,
        )
        self.assertEqual(Expense.objects.filter(recurrence_key=key).count(), 1)

        with self.assertRaises(ValidationError):
            FinanceService.record_expense(
                category=self.category, amount=Decimal('500.000'),
                due_date=timezone.now().date(), status=Expense.Status.ACCRUED,
                description='إيجار شهري مكرر', is_recurring=True, recurrence_key=key,
                user=self.accountant,
            )
        self.assertEqual(Expense.objects.filter(recurrence_key=key).count(), 1)

    # ---------------- قاعدة 9 ----------------
    def test_rule_9_unauthorized_cannot_approve_or_cancel(self):
        """
        القاعدة 9: المستخدم غير المخوّل لا يعتمد مصروفاً أو تسوية (بصلاحية المدير فقط).
        """
        expense = FinanceService.record_expense(
            category=self.category, amount=Decimal('15.000'),
            due_date=timezone.now().date(), status=Expense.Status.PAID,
            treasury_account=self.cash, description='مصروف للاختبار', user=self.accountant,
        )
        client = Client()

        # المحاسب: لا يمكنه الاعتماد ولا الإلغاء
        client.force_login(self.accountant)
        self.assertEqual(client.get(reverse('expense_approve', args=[expense.pk])).status_code, 403)
        self.assertEqual(client.post(reverse('expense_cancel', args=[expense.pk])).status_code, 403)

        # المشاهد: لا يمكنه إجراء تسوية مالية
        client.force_login(self.viewer)
        self.assertEqual(client.post(reverse('expense_cancel', args=[expense.pk])).status_code, 403)

        # المدير: يملك الصلاحية
        client.force_login(self.manager)
        response = client.get(reverse('expense_approve', args=[expense.pk]))
        self.assertIn(response.status_code, (200, 302))
        expense.refresh_from_db()
        self.assertTrue(expense.is_approved)

    # ---------------- قاعدة 11 ----------------
    def test_rule_11_cancel_financial_entry_reverses_with_audit(self):
        """
        القاعدة 11: إلغاء قيد مالي يترك سجلاً تدقيقياً واضحاً ويعكس أثره على الخزينة.
        """
        expense = FinanceService.record_expense(
            category=self.category, amount=Decimal('30.000'),
            due_date=timezone.now().date(), status=Expense.Status.PAID,
            treasury_account=self.cash, description='مصروف مدفوع للإلغاء', user=self.accountant,
        )
        self.cash.refresh_from_db()
        self.assertEqual(self.cash.current_balance, Decimal('-30.000'))

        FinanceService.cancel_expense(expense, reason='ترحيل خاطئ', user=self.manager)
        expense.refresh_from_db()
        self.cash.refresh_from_db()
        self.assertEqual(expense.status, Expense.Status.CANCELLED)
        self.assertEqual(self.cash.current_balance, Decimal('0.000'))

        log = AuditLog.objects.filter(action='CANCEL_EXPENSE', object_id=str(expense.pk)).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.details.get('previous_status'), Expense.Status.PAID)
        self.assertEqual(log.details.get('new_status'), Expense.Status.CANCELLED)
        self.assertEqual(log.reason, 'ترحيل خاطئ')

    # ---------------- قاعدة 12 ----------------
    def test_rule_12_report_totals_match_detailed_records(self):
        """
        القاعدة 12: مجموع التقارير يطابق مجموع السجلات التفصيلية.
        """
        self._approved_invoice(total='250.000')
        FinanceService.record_payment(
            trader=self.trader, amount=Decimal('100.000'),
            payment_method=Payment.Method.CASH, treasury_account=self.cash, user=self.accountant,
        )
        FinanceService.record_expense(
            category=self.category, amount=Decimal('40.000'),
            due_date=timezone.now().date(), status=Expense.Status.PAID,
            treasury_account=self.cash, description='مصروف', user=self.accountant,
        )
        FinanceService.internal_transfer(
            from_account=self.cash, to_account=self.bank, amount=Decimal('10.000'),
            user=self.accountant,
        )

        kpis = FinanceService.calculate_financial_kpis()

        # رصيد الخزينة الكلي = مجموع أرصدة الحسابات
        total_from_accounts = TreasuryAccount.objects.aggregate(
            total=Sum('current_balance')
        )['total'] or Decimal('0.000')
        self.assertEqual(kpis['total_treasury_balance'], total_from_accounts)

        # المصاريف = مجموع سجلات المصاريف غير الملغاة
        expenses_total = Expense.objects.exclude(
            status=Expense.Status.CANCELLED
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0.000')
        self.assertEqual(kpis['operating_expenses'], expenses_total)

        # المقبوضات = مجموع سندات القبض
        payments_total = Payment.objects.aggregate(total=Sum('amount'))['total'] or Decimal('0.000')
        self.assertEqual(kpis['actual_cash_in'], payments_total)

    # ---------------- قاعدة 13 ----------------
    def test_rule_13_period_comparison_uses_correct_ranges(self):
        """
        القاعدة 13: المقارنة بين الفترات تستخدم نطاقات زمنية صحيحة.
        """
        # نستخدم التاريخ المحلي (آسيا/عمّان) لأن الفلترة تتم بالوقت المحلي
        # وهذا ما يفعله أيضاً calculate_financial_kpis عبر created_at__date
        today = timezone.localdate()
        old_date = timezone.now() - timedelta(days=90)

        self._approved_invoice(total='100.000', number='2026-T001-100', created_at=timezone.now())
        self._approved_invoice(total='70.000', number='2026-T001-101', created_at=old_date)

        kpis_all = FinanceService.calculate_financial_kpis()
        self.assertEqual(kpis_all['earned_revenue'], Decimal('170.000'))

        kpis_today = FinanceService.calculate_financial_kpis(
            start_date=today - timedelta(days=1), end_date=today
        )
        self.assertEqual(kpis_today['earned_revenue'], Decimal('100.000'))

        kpis_old = FinanceService.calculate_financial_kpis(
            start_date=today - timedelta(days=120), end_date=today - timedelta(days=60)
        )
        self.assertEqual(kpis_old['earned_revenue'], Decimal('70.000'))

    # ---------------- قاعدة 15 ----------------
    def test_rule_15_decimal_precision(self):
        """
        القاعدة 15: المبالغ المالية تُحفظ وتُحسب بدقة عشرية (3 خانات للدينار).
        """
        FinanceService.record_payment(
            trader=self.trader, amount=Decimal('0.333'),
            payment_method=Payment.Method.CASH, treasury_account=self.cash, user=self.accountant,
        )
        FinanceService.record_payment(
            trader=self.trader, amount=Decimal('0.333'),
            payment_method=Payment.Method.CASH, treasury_account=self.cash, user=self.accountant,
        )
        FinanceService.record_payment(
            trader=self.trader, amount=Decimal('0.334'),
            payment_method=Payment.Method.CASH, treasury_account=self.cash, user=self.accountant,
        )
        self.cash.refresh_from_db()
        self.assertEqual(self.cash.current_balance, Decimal('1.000'))
        self.assertIsInstance(self.cash.current_balance, Decimal)

        kpis = FinanceService.calculate_financial_kpis()
        # المؤشرات تُحفظ وتُحسب كقيم عشرية دقيقة
        self.assertIsInstance(kpis['actual_cash_in'], Decimal)
        self.assertEqual(kpis['actual_cash_in'], Decimal('1.000'))

    # ---------------- قاعدة 22 ----------------
    def test_rule_22_missing_basic_data_prevents_approval(self):
        """
        القاعدة 22: نقص البيانات الأساسية يمنع الاعتماد/التسجيل عندما تكون النتيجة غير موثوقة.
        """
        # مصروف مدفوع بدون تحديد الخزينة مرفوض
        with self.assertRaises(ValidationError):
            FinanceService.record_expense(
                category=self.category, amount=Decimal('10.000'),
                due_date=timezone.now().date(), status=Expense.Status.PAID,
                treasury_account=None, description='بدون خزينة', user=self.accountant,
            )

        # قبض نقدي بدون حساب خزينة مرفوض
        with self.assertRaises(ValidationError):
            FinanceService.record_payment(
                trader=self.trader, amount=Decimal('10.000'),
                payment_method=Payment.Method.CASH, treasury_account=None, user=self.accountant,
            )

        # مبلغ غير منطقي مرفوض
        with self.assertRaises(ValidationError):
            FinanceService.record_expense(
                category=self.category, amount=Decimal('0.000'),
                due_date=timezone.now().date(), status=Expense.Status.ACCRUED,
                description='صفر', user=self.accountant,
            )

    # ---------------- قاعدة 23 ----------------
    def test_rule_23_internal_transfer_keeps_total(self):
        """
        القاعدة 23: التحويل الداخلي لا يغيّر إجمالي أموال المنشأة.
        """
        FinanceService.record_payment(
            trader=self.trader, amount=Decimal('100.000'),
            payment_method=Payment.Method.CASH, treasury_account=self.cash, user=self.accountant,
        )
        total_before = TreasuryAccount.objects.aggregate(total=Sum('current_balance'))['total']

        FinanceService.internal_transfer(
            from_account=self.cash, to_account=self.bank, amount=Decimal('40.000'),
            user=self.accountant,
        )

        total_after = TreasuryAccount.objects.aggregate(total=Sum('current_balance'))['total']
        self.assertEqual(total_before, total_after)
        self.cash.refresh_from_db()
        self.bank.refresh_from_db()
        self.assertEqual(self.cash.current_balance, Decimal('60.000'))
        self.assertEqual(self.bank.current_balance, Decimal('40.000'))
        # التحويل ليس إيراداً ولا مصروفاً
        kpis = FinanceService.calculate_financial_kpis()
        self.assertEqual(kpis['operating_expenses'], Decimal('0.000'))

    # ---------------- قاعدة 24 ----------------
    def test_rule_24_account_balance_matches_its_movements(self):
        """
        القاعدة 24: رصيد كل حساب يطابق مجموع حركاته المعتمدة.
        الرصيد = افتتاحي + مقبوضات - مصاريف مدفوعة من الحساب + تحويلات داخلة - تحويلات خارجة.
        """
        acc = TreasuryAccount.objects.create(
            name='حساب اختبار الرصيد', account_type=TreasuryAccount.AccountType.CASH,
            opening_balance=Decimal('10.000'), current_balance=Decimal('10.000'),
        )
        FinanceService.record_payment(
            trader=self.trader, amount=Decimal('200.000'),
            payment_method=Payment.Method.CASH, treasury_account=acc, user=self.accountant,
        )
        FinanceService.record_expense(
            category=self.category, amount=Decimal('60.000'),
            due_date=timezone.now().date(), status=Expense.Status.PAID,
            treasury_account=acc, description='مصروف من الحساب', user=self.accountant,
        )
        FinanceService.internal_transfer(
            from_account=acc, to_account=self.bank, amount=Decimal('50.000'), user=self.accountant,
        )
        FinanceService.internal_transfer(
            from_account=self.bank, to_account=acc, amount=Decimal('20.000'), user=self.accountant,
        )

        expected = (
            Decimal('10.000')
            + Payment.objects.filter(treasury_account=acc).aggregate(t=Sum('amount'))['t']
            - Expense.objects.filter(treasury_account=acc, status=Expense.Status.PAID).aggregate(t=Sum('amount'))['t']
            - InternalTransfer.objects.filter(from_account=acc).aggregate(t=Sum('amount'))['t']
            + InternalTransfer.objects.filter(to_account=acc).aggregate(t=Sum('amount'))['t']
        )
        acc.refresh_from_db()
        self.assertEqual(acc.current_balance, expected)


class ExpenseCategoryTests(TestCase):
    """
    تصنيفات المصاريف: الإدارة الكاملة + الإضافة السريعة من نموذج المصروف.
    """

    def setUp(self):
        self.manager = User.objects.create_user(
            username='cat_manager', password='pass', role=User.Role.MANAGER
        )
        self.accountant = User.objects.create_user(
            username='cat_accountant', password='pass', role=User.Role.ACCOUNTANT
        )
        self.viewer = User.objects.create_user(
            username='cat_viewer', password='pass', role=User.Role.VIEWER
        )
        self.category = ExpenseCategory.objects.create(name='كهرباء وماء')

    def test_category_list_page_renders(self):
        self.client.force_login(self.accountant)
        resp = self.client.get(reverse('expense_category_list'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'كهرباء وماء')

    def test_expense_form_shows_quick_add_controls(self):
        self.client.force_login(self.accountant)
        resp = self.client.get(reverse('expense_create'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'saveNewCategory')
        self.assertContains(resp, reverse('expense_category_quick_add'))

    def test_create_category_from_management_page(self):
        self.client.force_login(self.accountant)
        resp = self.client.post(reverse('expense_category_create'), {'name': 'إيجار', 'code': 'RENT'})
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(ExpenseCategory.objects.filter(name='إيجار', code='RENT').exists())

    def test_duplicate_category_name_rejected_with_message_not_500(self):
        self.client.force_login(self.accountant)
        resp = self.client.post(reverse('expense_category_create'), {'name': 'كهرباء وماء', 'code': ''})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(ExpenseCategory.objects.filter(name='كهرباء وماء').count(), 1)

    def test_edit_category(self):
        self.client.force_login(self.accountant)
        resp = self.client.post(
            reverse('expense_category_edit', args=[self.category.pk]),
            {'name': 'كهرباء ومياه', 'code': 'ELEC'},
        )
        self.assertEqual(resp.status_code, 302)
        self.category.refresh_from_db()
        self.assertEqual(self.category.name, 'كهرباء ومياه')
        self.assertEqual(self.category.code, 'ELEC')

    def test_quick_add_creates_new_category_and_returns_json(self):
        self.client.force_login(self.accountant)
        resp = self.client.post(
            reverse('expense_category_quick_add'), {'name': 'محروقات', 'code': 'FUEL'}
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data['ok'])
        self.assertFalse(data['existed'])
        obj = ExpenseCategory.objects.get(pk=data['id'])
        self.assertEqual(obj.name, 'محروقات')
        self.assertEqual(data['name'], 'محروقات')

    def test_quick_add_returns_existing_category_instead_of_error(self):
        self.client.force_login(self.accountant)
        resp = self.client.post(
            reverse('expense_category_quick_add'), {'name': 'كهرباء وماء'}
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data['ok'])
        self.assertTrue(data['existed'])
        self.assertEqual(data['id'], self.category.pk)
        self.assertEqual(ExpenseCategory.objects.filter(name='كهرباء وماء').count(), 1)

    def test_quick_add_requires_name(self):
        self.client.force_login(self.accountant)
        resp = self.client.post(reverse('expense_category_quick_add'), {'name': '   '})
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.json()['ok'])

    def test_quick_add_rejects_get(self):
        self.client.force_login(self.accountant)
        resp = self.client.get(reverse('expense_category_quick_add'))
        self.assertEqual(resp.status_code, 405)

    def test_viewer_cannot_create_category(self):
        self.client.force_login(self.viewer)
        resp = self.client.post(reverse('expense_category_create'), {'name': 'ممنوع', 'code': ''})
        self.assertFalse(ExpenseCategory.objects.filter(name='ممنوع').exists())
        resp2 = self.client.post(reverse('expense_category_quick_add'), {'name': 'ممنوع2'})
        self.assertFalse(ExpenseCategory.objects.filter(name='ممنوع2').exists())

    def test_category_used_by_expense_is_protected_from_silent_delete(self):
        """حذف تصنيف مرتبط بمصروف محمي على مستوى قاعدة البيانات (PROTECT)."""
        from django.db.models import ProtectedError
        Expense.objects.create(
            category=self.category, amount=Decimal('10.000'),
            due_date=timezone.now().date(), status=Expense.Status.ACCRUED,
            description='مصروف اختبار',
        )
        with self.assertRaises(ProtectedError):
            self.category.delete()

