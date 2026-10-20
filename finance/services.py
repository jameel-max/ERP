from decimal import Decimal
from django.db import transaction
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.db.models import Sum

from core.models import Notification, AuditLog
from operations.models import Trader
from invoicing.models import Invoice
from .models import TreasuryAccount, Expense, ExpenseCategory, Check, Payment, InternalTransfer, TraderLedgerEntry


class FinanceService:
    """
    خدمة الإدارة المالية والخزينة والذمم والشيكات
    الفصل التام بين الإيراد المكتسب، المحصل، المصروف المستحق، والمدفوع فعلاً
    """
    @classmethod
    @transaction.atomic
    def record_payment(cls, trader, amount, payment_method, treasury_account=None, invoices=None, check_obj=None, notes="", user=None):
        if amount <= 0:
            raise ValidationError("مبلغ الدفعة يجب أن يكون أكبر من صفر.")

        if payment_method in [Payment.Method.CASH, Payment.Method.BANK_TRANSFER] and not treasury_account:
            raise ValidationError("يجب تحديد الخزينة أو الحساب البنكي المودع به للقبض النقدي أو البنكي.")

        receipt_number = Payment.generate_receipt_number()

        payment = Payment.objects.create(
            receipt_number=receipt_number,
            trader=trader,
            amount=amount,
            payment_method=payment_method,
            treasury_account=treasury_account,
            check_obj=check_obj,
            notes=notes,
            created_by=user,
            payment_date=timezone.now().date()
        )

        if invoices:
            payment.invoices.set(invoices)
            # توزيع الدفعة على الفواتير المحددة
            remaining_to_distribute = amount
            for inv in invoices:
                if remaining_to_distribute <= 0:
                    break
                due = inv.remaining_balance
                applied = min(due, remaining_to_distribute)
                inv.paid_amount += applied
                inv.calculate_totals()
                if inv.remaining_balance == 0:
                    inv.status = Invoice.Status.PAID
                inv.save()
                remaining_to_distribute -= applied

        # تحديث رصيد الخزينة عند القبض الفعلي فقط.
        # الشيك غير المحصل لا يُنشئ دفعة أصلاً، لذلك يُضاف للنقدية عند التحصيل الفعلي
        # (سند قبض بطريقة CHECK يُنشأ في collect_check بعد تحديث حالة الشيك إلى محصّل).
        if treasury_account and payment_method in [
            Payment.Method.CASH, Payment.Method.BANK_TRANSFER, Payment.Method.CHECK
        ]:
            treasury_account.current_balance += amount
            treasury_account.save()

        # قيد في كشف حساب التاجر (دائن = سداد مستحق)
        last_entry = TraderLedgerEntry.objects.filter(trader=trader).order_by('-timestamp', '-id').first()
        prev_balance = last_entry.balance_after if last_entry else Decimal('0.000')
        new_balance = prev_balance - amount

        TraderLedgerEntry.objects.create(
            trader=trader,
            entry_type=TraderLedgerEntry.EntryType.PAYMENT,
            reference_number=receipt_number,
            debit=Decimal('0.000'),
            credit=amount,
            balance_after=new_balance,
            description=f"سند قبض رقم {receipt_number} ({payment.get_payment_method_display()})",
            created_by=user
        )

        AuditLog.log(
            user=user,
            action='RECORD_PAYMENT',
            instance=payment,
            reason=f'قبض دفعة بقيمة {amount} دينار من التاجر {trader.name}',
            details={'receipt_number': receipt_number, 'method': payment_method, 'amount': str(amount)}
        )
        return payment

    @classmethod
    @transaction.atomic
    def collect_check(cls, check, target_treasury, collected_date=None, user=None):
        """
        تحصيل الشيك:
        يحدث الحالة إلى COLLECTED ويضيف النقد إلى الخزينة ويسجل سند قبض للتاجر
        """
        if check.status == Check.Status.COLLECTED:
            raise ValidationError("الشيك محصل مسبقاً.")

        check.status = Check.Status.COLLECTED
        check.target_treasury = target_treasury
        check.collected_at = collected_date or timezone.now().date()
        check.status_history.append({
            'status': Check.Status.COLLECTED,
            'date': str(check.collected_at),
            'treasury': target_treasury.name,
            'user': user.username if user else 'نظام'
        })
        check.save()

        # إنشاء سند قبض رسمي مربوط بالشيك وتحديث الخزينة والذمة
        payment = cls.record_payment(
            trader=check.trader,
            amount=check.amount,
            payment_method=Payment.Method.CHECK,
            treasury_account=target_treasury,
            check_obj=check,
            notes=f"تحصيل شيك بنكي رقم {check.check_number} على بنك {check.bank_name}",
            user=user
        )
        return check

    @classmethod
    @transaction.atomic
    def return_check(cls, check, reason="شيك مرتجع لعدم كفاية الرصيد", user=None):
        """
        ارتجاع الشيك:
        يسجل الحركة العكسية ويعيد المبلغ إلى الذمة في كشف حساب التاجر
        """
        was_collected = (check.status == Check.Status.COLLECTED)

        check.status = Check.Status.RETURNED
        check.status_history.append({
            'status': Check.Status.RETURNED,
            'date': str(timezone.now().date()),
            'reason': reason,
            'user': user.username if user else 'نظام'
        })
        check.save()

        # إذا كان محصلاً، يتم خصم النقد من الخزينة
        if was_collected and check.target_treasury:
            check.target_treasury.current_balance -= check.amount
            check.target_treasury.save()

        # إعادة المبلغ للذمة في كشف حساب التاجر
        last_entry = TraderLedgerEntry.objects.filter(trader=check.trader).order_by('-timestamp', '-id').first()
        prev_balance = last_entry.balance_after if last_entry else Decimal('0.000')
        new_balance = prev_balance + check.amount

        TraderLedgerEntry.objects.create(
            trader=check.trader,
            entry_type=TraderLedgerEntry.EntryType.CHECK_RETURNED,
            reference_number=f"RET-CHK-{check.check_number}",
            debit=check.amount,
            credit=Decimal('0.000'),
            balance_after=new_balance,
            description=f"إعادة قيد ذمة - شيك مرتجع رقم {check.check_number} ({reason})",
            created_by=user
        )

        AuditLog.log(
            user=user,
            action='RETURN_CHECK',
            instance=check,
            reason=reason,
            details={'check_number': check.check_number, 'amount': str(check.amount)}
        )
        return check

    @classmethod
    @transaction.atomic
    def record_expense(cls, category, amount, due_date, status=Expense.Status.PAID, payment_date=None, treasury_account=None, beneficiary="", description="", is_recurring=False, recurrence_key=None, receipt_image=None, user=None):
        """
        تسجيل المصروف:
        - منع تكرار المصاريف المتكررة بنفس المفتاح
        - المصروف غير المدفوع لا يخصم من الخزينة ولا يدخل التدفق النقدي
        """
        if amount <= 0:
            raise ValidationError("مبلغ المصروف يجب أن يكون أكبر من صفر.")

        if is_recurring and recurrence_key:
            if Expense.objects.filter(recurrence_key=recurrence_key).exists():
                raise ValidationError(f"تم تسجيل استحقاق هذا المصروف المتكرر مسبقاً بمفتاح: {recurrence_key}")

        if status == Expense.Status.PAID:
            if not treasury_account:
                raise ValidationError("المصروف المدفوع يتطلب تحديد الخزينة أو الحساب المسدد منه.")
            payment_date = payment_date or timezone.now().date()

        expense = Expense.objects.create(
            category=category,
            amount=amount,
            due_date=due_date,
            payment_date=payment_date,
            treasury_account=treasury_account,
            status=status,
            beneficiary=beneficiary,
            description=description,
            is_recurring=is_recurring,
            recurrence_key=recurrence_key,
            receipt_image=receipt_image,
            is_approved=user.is_manager if user else False,
            approved_by=user if user and user.is_manager else None,
            created_by=user
        )

        # خصم النقد من الخزينة فقط إذا كان مدفوعاً فعلاً
        if status == Expense.Status.PAID and treasury_account:
            treasury_account.current_balance -= amount
            treasury_account.save()

        AuditLog.log(
            user=user,
            action='RECORD_EXPENSE',
            instance=expense,
            reason=f'تسجيل مصروف {category.name} بقيمة {amount} دينار ({status})',
            details={'amount': str(amount), 'status': status, 'treasury': str(treasury_account)}
        )
        return expense

    @classmethod
    @transaction.atomic
    def cancel_expense(cls, expense, reason, user=None):
        """
        إلغاء مصروف بقيد عكسي موثق (لا حذف صامت):
        - إذا كان المصروف مدفوعاً فعلاً يُعاد المبلغ إلى الخزينة التي دُفع منها.
        - يحتفظ بالقيم السابقة في سجل التدقيق مع السبب والمستخدم.
        """
        if expense.status == Expense.Status.CANCELLED:
            raise ValidationError("المصروف ملغى مسبقاً.")
        if not reason:
            raise ValidationError("سبب الإلغاء إلزامي لتوثيق القيد العكسي.")

        previous_status = expense.status
        previous_value = str(expense.amount)

        # قيد عكسي: إعادة المبلغ للخزينة إذا كان قد دُفع فعلاً
        if expense.status == Expense.Status.PAID and expense.treasury_account:
            expense.treasury_account.current_balance += expense.amount
            expense.treasury_account.save()

        expense.status = Expense.Status.CANCELLED
        expense.save()

        AuditLog.log(
            user=user,
            action='CANCEL_EXPENSE',
            instance=expense,
            reason=reason,
            details={
                'previous_status': previous_status,
                'new_status': expense.status,
                'amount': previous_value,
                'treasury': str(expense.treasury_account),
            }
        )
        return expense

    @classmethod
    @transaction.atomic
    def internal_transfer(cls, from_account, to_account, amount, notes="", user=None):
        """
        التحويل الداخلي:
        من حساب إلى حساب، لا يغير إجمالي أموال المنشأة ولا ينشئ إيراداً أو مصروفاً
        """
        if from_account.id == to_account.id:
            raise ValidationError("لا يمكن التحويل لنفس الحساب.")
        if amount <= 0:
            raise ValidationError("مبلغ التحويل يجب أن يكون أكبر من صفر.")
        if from_account.current_balance < amount:
            raise ValidationError(f"رصيد الحساب المحول منه ({from_account.current_balance} د.أ) غير كافٍ لتحويل {amount} د.أ.")

        transfer_number = InternalTransfer.generate_transfer_number()

        from_account.current_balance -= amount
        from_account.save()

        to_account.current_balance += amount
        to_account.save()

        transfer = InternalTransfer.objects.create(
            transfer_number=transfer_number,
            from_account=from_account,
            to_account=to_account,
            amount=amount,
            transfer_date=timezone.now().date(),
            notes=notes,
            created_by=user
        )

        AuditLog.log(
            user=user,
            action='INTERNAL_TRANSFER',
            instance=transfer,
            reason=f'تحويل داخلي {amount} د.أ من {from_account.name} إلى {to_account.name}',
            details={'transfer_number': transfer_number, 'amount': str(amount)}
        )
        return transfer

    @classmethod
    def calculate_financial_kpis(cls, start_date=None, end_date=None):
        """
        حساب المؤشرات المالية بدقة:
        - الإيراد المكتسب (الفواتير المعتمدة)
        - المصاريف التشغيلية المعتمدة
        - النتيجة التشغيلية الأولية
        - التدفق النقدي الفعلي (المقبوضات النقدية الفعلية - المدفوعات النقدية الفعلية)
        - الذمم المدينة
        """
        from django.core.cache import cache
        from django.db import transaction

        # نخزّن مؤشرات الفترة الكاملة (تُستخدم في لوحات التحكم) لمدة قصيرة.
        # قاعدة البيانات خارجية ومسافة الشبكة كبيرة (≈170ms لكل استعلام)،
        # والمؤشرات لا تحتاج تحديثاً لحظياً. استخدام on_commit يمنع تسرّب
        # الكاش بين اختبارات TestCase (التي تُلغي معاملاتها) مع بقائه سريعاً في التشغيل.
        _cache_full = start_date is None and end_date is None
        _cache_key = 'finance_kpis_full'
        if _cache_full:
            _cached = cache.get(_cache_key)
            if _cached is not None:
                return _cached

        invoices_qs = Invoice.objects.filter(status__in=[Invoice.Status.APPROVED, Invoice.Status.PAID])
        expenses_qs = Expense.objects.exclude(status=Expense.Status.CANCELLED)
        payments_qs = Payment.objects.filter(payment_method__in=[
            Payment.Method.CASH, Payment.Method.BANK_TRANSFER, Payment.Method.CHECK
        ])
        checks_qs = Check.objects.all()

        if start_date:
            invoices_qs = invoices_qs.filter(created_at__date__gte=start_date)
            expenses_qs = expenses_qs.filter(due_date__gte=start_date)
            payments_qs = payments_qs.filter(payment_date__gte=start_date)
            checks_qs = checks_qs.filter(due_date__gte=start_date)
        if end_date:
            invoices_qs = invoices_qs.filter(created_at__date__lte=end_date)
            expenses_qs = expenses_qs.filter(due_date__lte=end_date)
            payments_qs = payments_qs.filter(payment_date__lte=end_date)
            checks_qs = checks_qs.filter(due_date__lte=end_date)

        # 1. الإيرادات المكتسبة
        earned_revenue = invoices_qs.aggregate(total=Sum('grand_total'))['total'] or Decimal('0.000')

        # 2. المصاريف التشغيلية التي تخص الفترة (أساس الاستحقاق)
        operating_expenses = expenses_qs.aggregate(total=Sum('amount'))['total'] or Decimal('0.000')

        # 3. النتيجة التشغيلية الأولية
        operating_result = (earned_revenue - operating_expenses).quantize(Decimal('0.001'))

        # 4. التدفق النقدي الفعلي (أساس نقدي)
        actual_cash_in = payments_qs.aggregate(total=Sum('amount'))['total'] or Decimal('0.000')
        actual_cash_out = expenses_qs.filter(status=Expense.Status.PAID).aggregate(total=Sum('amount'))['total'] or Decimal('0.000')
        net_cash_flow = (actual_cash_in - actual_cash_out).quantize(Decimal('0.001'))

        # 5. صافي الذمم المدينة المستحقة على التجار
        total_invoiced = Invoice.objects.filter(status__in=[Invoice.Status.APPROVED, Invoice.Status.PAID]).aggregate(total=Sum('grand_total'))['total'] or Decimal('0.000')
        total_collected = Payment.objects.aggregate(total=Sum('amount'))['total'] or Decimal('0.000')
        net_receivables = max(Decimal('0.000'), total_invoiced - total_collected).quantize(Decimal('0.001'))

        # 6. أرصدة الخزينة والبنوك الإجمالية
        total_treasury_balance = TreasuryAccount.objects.aggregate(total=Sum('current_balance'))['total'] or Decimal('0.000')

        # 7. الشيكات قيد التحصيل
        checks_under_collection = checks_qs.filter(status__in=[Check.Status.REGISTERED, Check.Status.UNDER_COLLECTION]).aggregate(total=Sum('amount'))['total'] or Decimal('0.000')

        _result = {
            'earned_revenue': earned_revenue,
            'operating_expenses': operating_expenses,
            'operating_result': operating_result,
            'actual_cash_in': actual_cash_in,
            'actual_cash_out': actual_cash_out,
            'net_cash_flow': net_cash_flow,
            'net_receivables': net_receivables,
            'total_treasury_balance': total_treasury_balance,
            'checks_under_collection': checks_under_collection,
        }
        if _cache_full:
            transaction.on_commit(lambda: cache.set(_cache_key, _result, 60))
        return _result
