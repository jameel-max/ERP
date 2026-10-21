from decimal import Decimal
from django.db import transaction
from django.db.models import Sum
from django.core.exceptions import ValidationError
from django.utils import timezone
from .models import (
    TraderBoxBalance, TraderBoxTransaction, FarmBoxType, FarmBoxRecord,
    EmptyBoxRecipient, EmptyBoxDelivery, FarmEmptyBoxConsumption,
)
from operations.models import BoxType, Trader
from core.models import Notification, AuditLog


class BoxService:
    """
    إدارة حركة ومخزون وأرصدة الصناديق لكل تاجر
    """
    @classmethod
    @transaction.atomic
    def record_box_movement(cls, trader, box_type, transaction_type, quantity, source, destination, reason, user=None, related_invoice=None):
        if quantity <= 0:
            raise ValidationError("كمية الصناديق يجب أن تكون أكبر من صفر.")

        # 1. تسجيل الحركة في السجل
        trans = TraderBoxTransaction.objects.create(
            trader=trader,
            box_type=box_type,
            transaction_type=transaction_type,
            quantity=quantity,
            source=source,
            destination=destination,
            reason=reason,
            related_invoice=related_invoice,
            created_by=user
        )

        # 2. تحديث مخزون الشركة
        if transaction_type == TraderBoxTransaction.Type.ISSUE:
            if box_type.company_stock < quantity:
                # تنبيه أو خصم
                box_type.company_stock = max(0, box_type.company_stock - quantity)
            else:
                box_type.company_stock -= quantity
            box_type.save()
        elif transaction_type == TraderBoxTransaction.Type.RETURN:
            box_type.company_stock += quantity
            box_type.save()

        # 3. تحديث رصيد التاجر
        balance, _ = TraderBoxBalance.objects.get_or_create(
            trader=trader,
            box_type=box_type,
            defaults={'issued_count': 0, 'returned_count': 0, 'net_balance': 0}
        )

        if transaction_type == TraderBoxTransaction.Type.ISSUE:
            balance.issued_count += quantity
        elif transaction_type == TraderBoxTransaction.Type.RETURN:
            balance.returned_count += quantity

        balance.recalculate()
        balance.save()

        # 4. تنبيه عجز الصناديق في جرس الإشعارات
        if balance.net_balance > 0:
            Notification.objects.create(
                title=f"رصيد عجز صناديق: {trader.name}",
                message=f"التاجر في ذمته {balance.net_balance} صندوق من نوع ({box_type.name}) بقيمة {balance.total_financial_value} دينار.",
                severity=Notification.Severity.INFO,
                link=f"/traders/{trader.id}/",
                related_model='TraderBoxBalance',
                related_id=str(balance.id)
            )

        # 5. سجل التدقيق
        AuditLog.log(
            user=user,
            action=f"BOX_{transaction_type}",
            instance=trans,
            reason=reason,
            details={
                'trader': trader.name,
                'box_type': box_type.name,
                'quantity': quantity,
                'net_balance': balance.net_balance,
                'financial_value': str(balance.total_financial_value),
            }
        )

        return trans

    @classmethod
    @transaction.atomic
    def reverse_movement(cls, trans, reason, user=None):
        """
        عكس أثر حركة صناديق بقيد تسوية موثق (لا حذف صامت):
        - ينشئ حركة ADJUSTMENT معاكسة تحدّث المخزون ورصيد التاجر.
        - يوثّق القيم السابقة والجديدة والسبب في سجل التدقيق.
        """
        if getattr(trans, 'is_reversed', False):
            raise ValidationError("تم عكس هذه الحركة مسبقاً.")
        if not reason:
            raise ValidationError("سبب عكس الحركة إلزامي للتوثيق.")

        reverse_type = {
            TraderBoxTransaction.Type.ISSUE: TraderBoxTransaction.Type.RETURN,
            TraderBoxTransaction.Type.RETURN: TraderBoxTransaction.Type.ISSUE,
            TraderBoxTransaction.Type.ADJUSTMENT: TraderBoxTransaction.Type.ADJUSTMENT,
        }[trans.transaction_type]

        reversal = TraderBoxTransaction.objects.create(
            trader=trans.trader,
            box_type=trans.box_type,
            transaction_type=reverse_type,
            quantity=trans.quantity,
            source=trans.destination,
            destination=trans.source,
            reason=f"قيد عكسي موثق للحركة #{trans.id}: {reason}",
            created_by=user
        )
        trans.is_reversed = True
        trans.reversal_reason = reason
        trans.save(update_fields=['is_reversed', 'reversal_reason'])

        # عكس أثر المخزون
        if trans.transaction_type == TraderBoxTransaction.Type.ISSUE:
            trans.box_type.company_stock += trans.quantity
        elif trans.transaction_type == TraderBoxTransaction.Type.RETURN:
            trans.box_type.company_stock = max(0, trans.box_type.company_stock - trans.quantity)
        trans.box_type.save()

        # عكس رصيد التاجر
        balance, _ = TraderBoxBalance.objects.get_or_create(
            trader=trans.trader,
            box_type=trans.box_type,
            defaults={'issued_count': 0, 'returned_count': 0, 'net_balance': 0}
        )
        if trans.transaction_type == TraderBoxTransaction.Type.ISSUE:
            balance.issued_count = max(0, balance.issued_count - trans.quantity)
        elif trans.transaction_type == TraderBoxTransaction.Type.RETURN:
            balance.returned_count = max(0, balance.returned_count - trans.quantity)
        balance.recalculate()
        balance.save()

        AuditLog.log(
            user=user,
            action='BOX_REVERSAL',
            instance=trans,
            reason=reason,
            details={
                'reversed_transaction_id': trans.id,
                'reversal_id': reversal.id,
                'reversed_type': trans.transaction_type,
                'quantity': trans.quantity,
                'net_balance_after': balance.net_balance,
            }
        )
        return reversal


class EmptyBoxService:
    """
    قسم «تسليم الفارغ» — مستقل عن عهدة التجار وعن مخزون صناديق الشركة:

    - **إرجاع إلى المزرعة:** يخرج الفارغ من حوزة المشغل بلا مقابل مالي.
    - **بيع لمنظمة/جهة:** يخرج بمقابل مالي يُقيَّد في الخزينة المحددة كإيراد.

    المصدر الوحيد لكمية التسليم هو الصناديق الفارغة المسجّلة في سجلات
    صناديق المزرعة (`FarmBoxRecord.empty_count`) ناقص ما سُلِّم سابقاً،
    فلا يمكن تسليم أكثر من الموجود فعلاً — والفحص داخل قفل صفوف
    (`select_for_update`) لأن العمليات المالية والمخزنية ذرّية (البند 10).
    """

    _DELIVERED = EmptyBoxDelivery.Status.DELIVERED

    @classmethod
    def available_empty(cls, farm_box_type_id=None):
        """
        الرصيد المتاح للتسليم لكل نوع صندوق:
        إجمالي الفارغ الناتج عن التفريغ − إجمالي ما استُهلك في التوضيب − إجمالي ما سُلِّم فعلاً (إرجاعاً أو بيعاً).

        يعيد قاموساً: {معرف نوع الصندوق الوارد: الرصيد المتاح}
        """
        produced = {
            row['truck_receipt__box_type_id']: row['total'] or 0
            for row in FarmBoxRecord.objects.values('truck_receipt__box_type_id').annotate(total=Sum('empty_count'))
        }
        delivered = {
            row['box_type_id']: row['total'] or 0
            for row in EmptyBoxDelivery.objects.filter(status=cls._DELIVERED)
            .values('box_type_id').annotate(total=Sum('quantity'))
        }
        consumed = {
            row['farm_box_type_id']: row['total'] or 0
            for row in FarmEmptyBoxConsumption.objects.values('farm_box_type_id').annotate(total=Sum('quantity'))
        }
        keys = set(produced) | set(delivered) | set(consumed)
        result = {
            k: (produced.get(k, 0) - delivered.get(k, 0) - consumed.get(k, 0))
            for k in keys
        }
        if farm_box_type_id is not None:
            return {farm_box_type_id: result.get(farm_box_type_id, 0)}
        return result

    @classmethod
    def consumed_in_packing(cls, farm_box_type_id=None):
        """إجمالي ما استُهلك في التوضيب لكل نوع صندوق مزرعة."""
        qs = FarmEmptyBoxConsumption.objects.values('farm_box_type_id').annotate(total=Sum('quantity'))
        result = {row['farm_box_type_id']: row['total'] or 0 for row in qs}
        if farm_box_type_id is not None:
            return result.get(farm_box_type_id, 0)
        return result

    @classmethod
    def validate_available(cls, box_type, quantity, exclude_delivery=None):
        """
        يتحقق أن الكمية المطلوبة لا تتجاوز الفارغ المتاح فعلاً، ويصمت إذا كان
        النوع غير مرتبط بأي سجل صناديق مزرعة بعد (لتفادي حجب الاستخدام).
        """
        produced = FarmBoxRecord.objects.filter(
            truck_receipt__box_type=box_type
        ).aggregate(total=Sum('empty_count'))['total'] or 0
        if produced == 0:
            return 0
        delivered_qs = EmptyBoxDelivery.objects.filter(
            box_type=box_type, status=cls._DELIVERED
        )
        if exclude_delivery is not None and exclude_delivery.pk:
            delivered_qs = delivered_qs.exclude(pk=exclude_delivery.pk)
        delivered = delivered_qs.aggregate(total=Sum('quantity'))['total'] or 0
        consumed = cls.consumed_in_packing(box_type.pk)
        available = produced - delivered - consumed
        if quantity > available:
            raise ValidationError(
                f'عدد الصناديق الفارغة المتاح من نوع «{box_type.name}» هو {available} فقط، '
                f'والكمية المطلوبة {quantity}. سجّل صناديق المزرعة الواردة أولاً أو عدّل الكمية.'
            )
        return available

    @classmethod
    @transaction.atomic
    def record_packing_consumption(cls, farm_box_type, quantity, trader, packing_session=None,
                                   packed_pallet=None, notes='', user=None):
        """
        تسجيل استهلاك صناديق فارغة (من المزرعة) داخل التوضيب، بفحص ذرّي للتوفّر:

        - يقفل صفّ نوع الصندوق (`select_for_update`) لمنع استهلاكين متزامنين يتجاوزان المتاح.
        - يمنع استهلاك أكثر من الرصيد المتاح فعلاً برسالة عربية واضحة (لا خطأ 500).
        - **يتصرف بصمت** إذا كان النوع لا يملك أي رصيد مُسجَّل بعد (produced == 0)،
          حتى لا يُحجب التوضيب على أنواع لم تُسجَّل سجلات مزرعة لها إطلاقاً.
        """
        quantity = int(quantity or 0)
        if quantity <= 0:
            return None

        FarmBoxType.objects.select_for_update().get(pk=farm_box_type.pk)

        produced = FarmBoxRecord.objects.filter(
            truck_receipt__box_type=farm_box_type
        ).aggregate(total=Sum('empty_count'))['total'] or 0
        if produced == 0:
            # لا يوجد رصيد مُسجَّل لهذا النوع — لا نحجب العملية، ولا نسجّل استهلاكاً.
            return None

        available = cls.validate_available(farm_box_type, quantity)
        consumption = FarmEmptyBoxConsumption.objects.create(
            farm_box_type=farm_box_type,
            quantity=quantity,
            trader=trader,
            packing_session=packing_session,
            packed_pallet=packed_pallet,
            notes=notes or '',
            created_by=user,
        )
        AuditLog.log(
            user=user,
            action='FARM_EMPTY_BOX_CONSUMED',
            instance=consumption,
            reason=(
                f'استهلاك {quantity} صندوق فارغ من نوع «{farm_box_type.name}» في التوضيب '
                f'للتاجر {trader.name}'
            ),
            details={
                'farm_box_type': farm_box_type.name,
                'quantity': quantity,
                'available_before': available,
                'packing_session': getattr(packing_session, 'session_code', None),
            },
        )
        return consumption

    @classmethod
    @transaction.atomic
    def create_delivery(cls, data, user=None):
        """إنشاء أمر تسليم فارغ (مسودة أو مُسلَّم مباشرة) بفحص ذرّي للكمية."""
        quantity = int(data.get('quantity') or 0)
        if quantity <= 0:
            raise ValidationError('عدد الصناديق يجب أن يكون أكبر من صفر.')

        box_type = data['box_type']
        # قفل صفوف النوع لمنع تسليمين متزامنين يتجاوزان المتاح
        FarmBoxType.objects.select_for_update().get(pk=box_type.pk)

        delivery = EmptyBoxDelivery(
            reference_number=EmptyBoxDelivery.generate_reference(),
            kind=data['kind'],
            recipient=data['recipient'],
            box_type=box_type,
            quantity=quantity,
            unit_price=data.get('unit_price') or Decimal('0.000'),
            delivery_date=data.get('delivery_date') or timezone.localdate(),
            driver_name=data.get('driver_name', '') or '',
            vehicle_plate=data.get('vehicle_plate', '') or '',
            payment_method=data.get('payment_method', '') or '',
            treasury_account=data.get('treasury_account'),
            status=data.get('status') or EmptyBoxDelivery.Status.DRAFT,
            notes=data.get('notes', '') or '',
            created_by=user,
        )

        if delivery.kind == EmptyBoxDelivery.Kind.RETURN:
            if data.get('unit_price') and Decimal(data['unit_price']) > 0:
                raise ValidationError(
                    'الإرجاع إلى المزرعة لا يحمل سعراً — الإرجاع خروج بلا مقابل مالي. '
                    'اختر «بيع لمنظمة / جهة» إن كان التسليم بمقابل.'
                )
            delivery.unit_price = Decimal('0.000')
            delivery.payment_method = ''
            delivery.treasury_account = None
        else:
            if delivery.unit_price <= 0:
                raise ValidationError('البيع يتطلب سعراً للصندوق الواحد أكبر من صفر.')
            if not delivery.payment_method:
                raise ValidationError('حدّد طريقة القبض للبيع (نقدي، بنكي، أو على الحساب).')

        delivery.full_clean(exclude=['reference_number', 'total_amount', 'created_by', 'delivered_by'])
        cls.validate_available(box_type, quantity)
        delivery.recalc_total()
        delivery.save()

        AuditLog.log(
            user=user, action='CREATE_EMPTY_BOX_DELIVERY', instance=delivery,
            reason=f'إنشاء أمر تسليم فارغ {delivery.reference_number}',
            details={
                'kind': delivery.kind, 'recipient': delivery.recipient.name,
                'box_type': delivery.box_type.name, 'quantity': delivery.quantity,
                'total_amount': str(delivery.total_amount), 'status': delivery.status,
            },
        )

        if delivery.status == cls._DELIVERED:
            cls._apply_delivery_effects(delivery, user=user)
        return delivery

    @classmethod
    @transaction.atomic
    def update_delivery(cls, delivery, data, user=None):
        """
        تعديل أمر تسليم **مسودة** (غير معتمد): يحدّث الحقول ويعيد حساب الإجمالي،
        ولا يقبل تعديل أمر مُعتمد (الاعتماد لقطة مالية ثابتة).
        """
        delivery = EmptyBoxDelivery.objects.select_for_update().get(pk=delivery.pk)
        if delivery.status == cls._DELIVERED:
            raise ValidationError(
                'أمر التسليم معتمد — لا يمكن تعديله بعد الاعتماد. أنشئ أمراً جديداً للتصحيح.'
            )

        quantity = int(data.get('quantity') or 0)
        if quantity <= 0:
            raise ValidationError('عدد الصناديق يجب أن يكون أكبر من صفر.')

        delivery.kind = data['kind']
        delivery.recipient = data['recipient']
        delivery.box_type = data['box_type']
        delivery.quantity = quantity
        delivery.unit_price = data.get('unit_price') or Decimal('0.000')
        delivery.delivery_date = data.get('delivery_date') or delivery.delivery_date
        delivery.driver_name = data.get('driver_name', '') or ''
        delivery.vehicle_plate = data.get('vehicle_plate', '') or ''
        delivery.payment_method = data.get('payment_method', '') or ''
        delivery.treasury_account = data.get('treasury_account')
        delivery.notes = data.get('notes', '') or ''

        if delivery.kind == EmptyBoxDelivery.Kind.RETURN:
            if data.get('unit_price') and Decimal(data['unit_price']) > 0:
                raise ValidationError(
                    'الإرجاع إلى المزرعة لا يحمل سعراً — الإرجاع خروج بلا مقابل مالي. '
                    'اختر «بيع لمنظمة / جهة» إن كان التسليم بمقابل.'
                )
            delivery.unit_price = Decimal('0.000')
            delivery.payment_method = ''
            delivery.treasury_account = None
        else:
            if delivery.unit_price <= 0:
                raise ValidationError('البيع يتطلب سعراً للصندوق الواحد أكبر من صفر.')
            if not delivery.payment_method:
                raise ValidationError('حدّد طريقة القبض للبيع (نقدي، بنكي، أو على الحساب).')

        delivery.full_clean(exclude=['reference_number', 'total_amount', 'created_by', 'delivered_by'])
        cls.validate_available(delivery.box_type, delivery.quantity, exclude_delivery=delivery)
        delivery.recalc_total()
        delivery.save()

        AuditLog.log(
            user=user, action='UPDATE_EMPTY_BOX_DELIVERY', instance=delivery,
            reason=f'تعديل أمر تسليم فارغ {delivery.reference_number}',
            details={
                'kind': delivery.kind, 'quantity': delivery.quantity,
                'total_amount': str(delivery.total_amount),
                'unit_price': str(delivery.unit_price),
            },
        )
        return delivery

    @classmethod
    @transaction.atomic
    def confirm_delivery(cls, delivery, user=None, treasury_account=None, payment_method=None):
        """
        اعتماد التسليم الفعلي:
        - إرجاع: خروج الفارغ بلا أثر مالي.
        - بيع: يُثبَّت الإجمالي (الكمية × السعر) ويُقيَّد في الخزينة إن كانت طريقة القبض نقدية/بنكية.
        """
        delivery = EmptyBoxDelivery.objects.select_for_update().get(pk=delivery.pk)
        if delivery.status == cls._DELIVERED:
            raise ValidationError('أمر التسليم معتمد مسبقاً — لا يُعتمد مرتين.')

        FarmBoxType.objects.select_for_update().get(pk=delivery.box_type_id)
        cls.validate_available(delivery.box_type, delivery.quantity, exclude_delivery=delivery)
        if delivery.is_sale:
            if treasury_account is not None:
                delivery.treasury_account = treasury_account
            if payment_method:
                delivery.payment_method = payment_method
            if not delivery.payment_method:
                raise ValidationError('حدّد طريقة القبض قبل اعتماد بيع الفارغ.')
            if delivery.unit_price <= 0:
                raise ValidationError('حدّد سعر الصندوق الواحد قبل اعتماد البيع.')
        else:
            delivery.unit_price = Decimal('0.000')
            delivery.payment_method = ''
            delivery.treasury_account = None

        delivery.recalc_total()
        delivery.status = cls._DELIVERED
        delivery.delivered_by = user
        delivery.delivered_at = timezone.now()
        delivery.save()

        cls._apply_delivery_effects(delivery, user=user)
        return delivery

    @classmethod
    @transaction.atomic
    def _apply_delivery_effects(cls, delivery, user=None):
        """الأثر المالي مرة واحدة فقط: قيد الإيراد في الخزينة عند البيع المقبوض."""
        delivery = EmptyBoxDelivery.objects.select_for_update().get(pk=delivery.pk)
        if delivery.is_sale and delivery.total_amount > 0 and not delivery.is_revenue_posted:
            if delivery.payment_method in (
                EmptyBoxDelivery.PaymentMethod.CASH,
                EmptyBoxDelivery.PaymentMethod.BANK_TRANSFER,
            ):
                if not delivery.treasury_account:
                    raise ValidationError(
                        f'بيع الفارغ بمبلغ {delivery.total_amount} دينار يتطلب تحديد الخزينة المستلمة، '
                        f'أو اختيار «على الحساب (لم يُقبض بعد)».'
                    )
                account = delivery.treasury_account
                account.current_balance += delivery.total_amount
                account.save(update_fields=['current_balance'])
                delivery.revenue_posted_at = timezone.now()
                delivery.save(update_fields=['revenue_posted_at'])
                AuditLog.log(
                    user=user, action='EMPTY_BOX_SALE_REVENUE', instance=delivery,
                    reason=f'قيد إيراد بيع فارغ {delivery.reference_number} في {account.name}',
                    details={
                        'amount': str(delivery.total_amount),
                        'treasury_account': account.name,
                        'balance_after': str(account.current_balance),
                        'quantity': delivery.quantity,
                        'unit_price': str(delivery.unit_price),
                    },
                )

        AuditLog.log(
            user=user, action='CONFIRM_EMPTY_BOX_DELIVERY', instance=delivery,
            reason=f'اعتماد تسليم فارغ {delivery.reference_number}',
            details={
                'kind': delivery.kind, 'recipient': delivery.recipient.name,
                'quantity': delivery.quantity, 'total_amount': str(delivery.total_amount),
                'payment_method': delivery.payment_method,
            },
        )
        return delivery
