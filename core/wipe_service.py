"""
خدمة تفريغ قاعدة البيانات.

القاعدة: التفريغ إجراء لا رجعة فيه، لذا:
- من صلاحيات مالك النظام فقط (شخص واحد مسؤول عن كل الإجراءات).
- لا يعمل إلا بعبارة تأكيد مكتوبة يدوياً من المستخدم.
- يُسجَّل في سجل التدقيق (AuditLog) مع عدد السجلات المحذوفة لكل جدول.

نموذجان:
1) OPERATIONS — يحذف الحركات والمعاملات ويُبقي البيانات المرجعية
   (التجار، أنواع الصناديق، البرادات، الأسياخ، الحسابات، المستخدمين، الإعدادات).
2) FULL — يحذف كل شيء ما عدا المستخدمين وإعدادات النظام.
"""

from decimal import Decimal
from django.db import transaction

from .models import Notification, AuditLog

# عبارة التأكيد الواجب كتابتها حرفيًا لتنفيذ التفريغ
CONFIRM_PHRASE = 'تفريغ نهائي'


# الجداول المرجعية التي تبقى في نموذج OPERATIONS
# (تُحذف في نموذج FULL لأن أرصدتها مشتقة من الحركات)
def _wipe_operations():
    """
    يحذف كل الحركات ويُبقي البيانات المرجعية.
    يعيد قائمة (اسم الجدول, عدد السجلات المحذوفة).
    """
    # استيراد داخلي لتجنب دورة الاستيراد عند إقلاع Django
    from operations.models import (
        TruckReceipt, Pallet, PalletMovementLog, ColdStoreTemperatureLog,
        TraderPackingRate,
    )
    from packing.models import (
        PackingSession, PackingOutput, PackingWaste, PackedPallet, DeliveryNote,
    )
    from invoicing.models import Invoice, InvoiceItem
    from boxes.models import TraderBoxBalance, TraderBoxTransaction, FarmBoxRecord
    from labor.models import DailyLaborLog
    from finance.models import (
        Expense, Payment, Check, InternalTransfer, TraderLedgerEntry,
        TreasuryAccount, ExpenseCategory,
    )
    from operations.models import BoxType

    deleted = []

    # الحركات فقط — بدون لمس الجداول المرجعية
    for label, qs in [
        ('سجلات حركة الطبالي', PalletMovementLog.objects.all()),
        ('سجلات درجات الحرارة', ColdStoreTemperatureLog.objects.all()),
        ('إذونات التسليم', DeliveryNote.objects.all()),
        ('الطبالي الموضبة', PackedPallet.objects.all()),
        ('مخرجات التوضيب', PackingOutput.objects.all()),
        ('هالك التوضيب', PackingWaste.objects.all()),
        ('جلسات التوضيب', PackingSession.objects.all()),
        ('بنود الفواتير', InvoiceItem.objects.all()),
        ('الفواتير', Invoice.objects.all()),
        ('حركات صناديق التجار', TraderBoxTransaction.objects.all()),
        ('أرصدة صناديق التجار', TraderBoxBalance.objects.all()),
        ('سجلات صناديق المزرعة', FarmBoxRecord.objects.all()),
        ('سجلات العمالة', DailyLaborLog.objects.all()),
        ('التسويات الداخلية', InternalTransfer.objects.all()),
        ('قيود دفتر التاجر', TraderLedgerEntry.objects.all()),
        ('الشيكات', Check.objects.all()),
        ('سندات القبض', Payment.objects.all()),
        ('المصاريف', Expense.objects.all()),
        ('الطبالي', Pallet.objects.all()),
        ('إيصالات السيارات', TruckReceipt.objects.all()),
        ('الإشعارات', Notification.objects.all()),
        ('سجل التدقيق', AuditLog.objects.all()),
    ]:
        count = qs.count()
        if count:
            qs.delete()
            deleted.append((label, count))

    # إعادة تصفير الأرصدة المشتقة من الحركات المحذوفة
    for bt in BoxType.objects.all():
        bt.company_stock = 0
        bt.save(update_fields=['company_stock'])

    for acc in TreasuryAccount.objects.all():
        acc.current_balance = acc.opening_balance or Decimal('0.000')
        acc.save(update_fields=['current_balance'])

    # تُحذف أسعار التوضيب لأنها خاصة بتاجر وليست مرجعية بحد ذاتها
    rates_count = TraderPackingRate.objects.count()
    if rates_count:
        TraderPackingRate.objects.all().delete()
        deleted.append(('أسعار التوضيب', rates_count))

    return deleted


def _wipe_full():
    """يحذف كل شيء باستثناء المستخدمين وإعدادات النظام. يعيد قائمة (تسمية, عدد)."""
    from operations.models import (
        Trader, Crop, BoxType, ColdStore, Shelf, TruckReceipt, Pallet,
        PalletMovementLog, ColdStoreTemperatureLog, TraderPackingRate,
        PackagingGroup,
    )
    from packing.models import (
        PackingSession, PackingOutput, PackingWaste, PackedPallet, DeliveryNote,
    )
    from invoicing.models import Invoice, InvoiceItem
    from boxes.models import (
        TraderBoxBalance, TraderBoxTransaction, FarmBoxType, FarmBoxRecord,
    )
    from labor.models import Workshop, DailyLaborLog
    from finance.models import (
        Expense, Payment, Check, InternalTransfer, TraderLedgerEntry,
        TreasuryAccount, ExpenseCategory,
    )

    deleted = []

    # ترتيب الحذف: الحركات أولاً ثم الجداول المرجعية
    for label, qs in [
        ('سجلات حركة الطبالي', PalletMovementLog.objects.all()),
        ('سجلات درجات الحرارة', ColdStoreTemperatureLog.objects.all()),
        ('إذونات التسليم', DeliveryNote.objects.all()),
        ('الطبالي الموضبة', PackedPallet.objects.all()),
        ('مخرجات التوضيب', PackingOutput.objects.all()),
        ('هالك التوضيب', PackingWaste.objects.all()),
        ('جلسات التوضيب', PackingSession.objects.all()),
        ('بنود الفواتير', InvoiceItem.objects.all()),
        ('الفواتير', Invoice.objects.all()),
        ('حركات صناديق التجار', TraderBoxTransaction.objects.all()),
        ('أرصدة صناديق التجار', TraderBoxBalance.objects.all()),
        ('سجلات صناديق المزرعة', FarmBoxRecord.objects.all()),
        ('أنواع الصناديق الواردة من المزرعة', FarmBoxType.objects.all()),
        ('سجلات العمالة', DailyLaborLog.objects.all()),
        ('التسويات الداخلية', InternalTransfer.objects.all()),
        ('قيود دفتر التاجر', TraderLedgerEntry.objects.all()),
        ('الشيكات', Check.objects.all()),
        ('سندات القبض', Payment.objects.all()),
        ('المصاريف', Expense.objects.all()),
        ('الطبالي', Pallet.objects.all()),
        ('إيصالات السيارات', TruckReceipt.objects.all()),
        ('أسعار التوضيب', TraderPackingRate.objects.all()),
        ('الورش', Workshop.objects.all()),
        ('التجار', Trader.objects.all()),
        ('الأسياخ', Shelf.objects.all()),
        ('البرادات', ColdStore.objects.all()),
        ('أنواع الصناديق', BoxType.objects.all()),
        ('مجموعات التغليف', PackagingGroup.objects.all()),
        ('أصناف البضاعة', Crop.objects.all()),
        ('حسابات الخزينة', TreasuryAccount.objects.all()),
        ('تصنيفات المصاريف', ExpenseCategory.objects.all()),
        ('الإشعارات', Notification.objects.all()),
        ('سجل التدقيق', AuditLog.objects.all()),
    ]:
        count = qs.count()
        if count:
            qs.delete()
            deleted.append((label, count))

    return deleted


def wipe_database(user, mode, confirm_text):
    """
    ينفّذ تفريغ قاعدة البيانات بعد التحقق من التأكيد.

    يرفع ValueError إذا كانت عبارة التأكيد غير صحيحة.
    يعيد قائمة (تسمية الجدول, عدد السجلات المحذوفة).
    """
    if (confirm_text or '').strip() != CONFIRM_PHRASE:
        raise ValueError(
            f'عبارة التأكيد غير صحيحة. اكتب "{CONFIRM_PHRASE}" حرفيًا لتنفيذ التفريغ.'
        )

    # القاعدة 20: إما يُنفَّذ التفريغ كاملاً أو لا يتغير شيء (لا حذف جزئي)
    with transaction.atomic():
        if mode == 'FULL':
            deleted = _wipe_full()
        else:
            deleted = _wipe_operations()

        total = sum(c for _, c in deleted)

        class _WipeMarker:
            """وسم دلالي ليظهر اسمًا واضحاً في سجل التدقيق بدلاً من NoneType."""
            def __str__(self):
                return f'قاعدة البيانات ({mode})'

        # يُسجَّل بعد الحذف مباشرة (السجل السابق محذوف أصلاً)
        AuditLog.log(
            user=user,
            action='WIPE_DATABASE',
            instance=_WipeMarker(),
            reason=f'تفريغ قاعدة البيانات — النموذج: {mode}',
            details={
                'mode': mode,
                'total_records': total,
                'per_table': {label: count for label, count in deleted},
            },
        )
    return deleted, total
