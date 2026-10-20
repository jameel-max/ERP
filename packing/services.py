from decimal import Decimal, ROUND_HALF_UP
from django.db import transaction
from django.core.exceptions import ValidationError
from django.utils import timezone

from core.models import SystemSetting, Notification, AuditLog
from operations.models import Pallet, PalletMovementLog, Trader, Crop, BoxType
from .models import (
    PackingSession, PackingOutput, PackingWaste, PackedPallet,
    PackedPalletCoolingLog, DeliveryNote,
    PackingOutputPackagingItem, PackingSessionPackagingCharge,
)
from boxes.services import BoxService


class PackingService:
    """
    خدمة التوضيب وخطوط الإنتاج والرندمان والهالك
    """
    @classmethod
    @transaction.atomic
    def start_packing_session(cls, trader, crop, packing_method, source_pallets, workshop_name="", user=None):
        if not source_pallets:
            raise ValidationError("يجب اختيار طبلية واحدة على الأقل من البراد للبدء بالتوضيب.")

        session_code = PackingSession.generate_session_code()

        # حساب الوزن الصافي الإجمالي الداخل للتوضيب
        input_weight = sum(p.estimated_net_weight for p in source_pallets)

        session = PackingSession.objects.create(
            session_code=session_code,
            trader=trader,
            crop=crop,
            packing_method=packing_method,
            workshop_name=workshop_name,
            input_weight_kg=input_weight,
            status=PackingSession.Status.DRAFT,
            created_by=user,
            started_at=timezone.now()
        )
        session.source_pallets.set(source_pallets)

        # تحديث حالة الطبالي المسحوبة من البراد وتسجيل حركتها
        now = timezone.now()
        for p in source_pallets:
            p.status = Pallet.Status.OUT_FOR_PACKING
            p.exited_cooling_at = now
            p.save()

            PalletMovementLog.objects.create(
                pallet=p,
                from_cold_store=p.cold_store,
                from_shelf=p.shelf,
                to_cold_store=None,
                to_shelf=None,
                movement_type='TO_PACKING',
                moved_by=user,
                notes=f"إخراج من البراد للتوضيب في الجلسة {session.session_code}"
            )

        AuditLog.log(
            user=user,
            action='START_PACKING_SESSION',
            instance=session,
            reason=f'بدء توضيب {len(source_pallets)} طبلية للتاجر {trader.name}',
            details={'session_code': session.session_code, 'input_weight': str(input_weight)}
        )

        return session

    @classmethod
    @transaction.atomic
    def record_outputs_and_finalize(cls, session, outputs_data, wastes_data=None, user=None):
        """
        تسجيل نواتج المقاسات والهالك وتوليد الطبالي الموضبة وحساب الرندمان
        """
        if not outputs_data:
            raise ValidationError("يجب إدخال ناتج توضيب واحد على الأقل.")

        # رقابة عدد الطبالي: كل سطر إدخال = طبلية موضبة واحدة،
        # ولا يجوز أن يتجاوز عدد الطبالي الموضبة عدد طبالي الاستلام المسحوبة من البراد في هذه الجلسة.
        max_pallets = session.source_pallets.count()
        existing_pallets = session.packed_pallets.count()
        new_pallets = sum(int(out.get('pallet_count', 1) or 1) for out in outputs_data)
        if existing_pallets + new_pallets > max_pallets:
            raise ValidationError(
                f"عدد الطبالي الموضبة الناتجة ({existing_pallets + new_pallets}) أكبر من عدد طبالي الاستلام "
                f"المتاحة في هذه الجلسة ({max_pallets}). كل سطر إدخال = طبلية موضبة واحدة، فيجب ألا يتجاوز "
                f"عدد أسطر نواتج المقاسات عدد الطبالي المسحوبة من البراد."
            )

        settings_obj = SystemSetting.get_settings()

        # 1. تسجيل النواتج
        primary_pallet = session.source_pallets.first()
        pallet_counter = 1
        packaging_charge_total = Decimal('0.000')
        packaging_items_count = 0

        for out in outputs_data:
            box_type = out['box_type'] if isinstance(out['box_type'], BoxType) else BoxType.objects.get(id=out['box_type'])
            box_count = int(out['box_count'])
            net_weight = Decimal(str(out['net_weight_kg']))
            pallet_count = int(out.get('pallet_count', 1))

            output_obj = PackingOutput.objects.create(
                session=session,
                size_name=out['size_name'],
                box_type=box_type,
                box_count=box_count,
                net_weight_kg=net_weight,
                pallet_count=pallet_count
            )

            # أصناف التغليف العادية المستخدمة في هذا الناتج (أكثر من صنف، ومن مجموعات مختلفة)
            for item in (out.get('packaging_items') or []):
                pkg_item = item['packaging_item']
                if not isinstance(pkg_item, BoxType):
                    pkg_item = BoxType.objects.get(id=pkg_item)
                qty = Decimal(str(item.get('quantity') or 0))
                if qty <= 0:
                    continue

                # تجميد السعر الحالي على التاجر داخل الحركة المالية
                unit_price = Decimal(str(item.get('unit_price', pkg_item.discount_price) or 0))
                PackingOutputPackagingItem.objects.create(
                    output=output_obj,
                    packaging_item=pkg_item,
                    packaging_group_name=(pkg_item.group.name if pkg_item.group_id else ''),
                    quantity=qty,
                    unit_price_snapshot=unit_price,
                )
                packaging_charge_total += (qty * unit_price).quantize(Decimal('0.001'), rounding=ROUND_HALF_UP)
                packaging_items_count += 1

            # توليد الطبالي الموضبة: الطبلية الأصلية + المقاس
            # كل سطر إدخال = طبلية واحدة بوزنها الخاص ورمز QR خاص بها
            # مثال: 2026-0143-P03-XL
            orig_code = primary_pallet.pallet_code if primary_pallet else session.session_code
            for i in range(pallet_count):
                base_code = f"{orig_code}-{out['size_name']}"
                packed_code = base_code
                if pallet_count > 1:
                    packed_code = f"{base_code}-{i+1}"

                # ضمان عدم تكرار رمز الطبلية داخل نفس الجلسة (لو تكرر نفس المقاس)
                suffix = 2
                while PackedPallet.objects.filter(packed_pallet_code=packed_code).exists():
                    packed_code = f"{base_code}-{suffix}"
                    suffix += 1

                # وزن نسبي للطبلية الموضبة
                p_net = (net_weight / Decimal(pallet_count)).quantize(Decimal('0.001'))
                p_boxes = box_count // pallet_count

                PackedPallet.objects.create(
                    packed_pallet_code=packed_code,
                    original_pallet=primary_pallet,
                    session=session,
                    trader=session.trader,
                    crop=session.crop,
                    size_name=out['size_name'],
                    box_type=box_type,
                    box_count=p_boxes,
                    net_weight_kg=p_net,
                    status=PackedPallet.Status.PACKED
                )

            # تسجيل حركة استهلاك الصناديق في عهدة التاجر
            BoxService.record_box_movement(
                trader=session.trader,
                box_type=box_type,
                transaction_type='ISSUE',
                quantity=box_count,
                source='مخزن المشغل',
                destination=f'توضيب نواتج {session.session_code}',
                reason=f'صرف صناديق لتوضيب مقاس {out["size_name"]} في الجلسة {session.session_code}',
                user=user
            )

        # 2. تسجيل الهالك إن وجد
        if wastes_data:
            for w in wastes_data:
                if Decimal(str(w.get('weight_kg', 0))) > 0:
                    PackingWaste.objects.create(
                        session=session,
                        waste_type=w['waste_type'],
                        weight_kg=Decimal(str(w['weight_kg'])),
                        notes=w.get('notes', '')
                    )

        # 2-ب. قيد خصم أصناف التغليف العادية على التاجر (يُرحَّل لاحقاً إلى فاتورته)
        if packaging_items_count > 0:
            charge, _ = PackingSessionPackagingCharge.objects.update_or_create(
                session=session,
                defaults={
                    'trader': session.trader,
                    'total_amount': packaging_charge_total,
                },
            )
            AuditLog.log(
                user=user,
                action='PACKING_PACKAGING_CHARGE',
                instance=charge,
                reason=f'خصم {packaging_items_count} صنف تغليف على التاجر {session.trader.name} في الجلسة {session.session_code}',
                details={
                    'session_code': session.session_code,
                    'items_count': packaging_items_count,
                    'total_amount': str(packaging_charge_total),
                },
            )

        # 3. حساب المقاييس وفحص حد السماح
        session.calculate_metrics(tolerance_pct=settings_obj.weight_tolerance_pct)
        session.completed_at = timezone.now()

        # تحديث حالة الطبالي الأصلية
        session.source_pallets.update(status=Pallet.Status.PACKED)

        if session.requires_manager_approval:
            session.status = PackingSession.Status.PENDING_APPROVAL
            Notification.objects.create(
                title=f"توضيب بانتظار اعتماد المدير: {session.session_code}",
                message=f"التاجر: {session.trader.name}. فرق وزن غير مفسر: {session.unexplained_diff_kg} كغ يتطلب تدقيق واعتماد المدير.",
                severity=Notification.Severity.CRITICAL,
                link=f"/packing/sessions/{session.id}/",
                related_model='PackingSession',
                related_id=str(session.id)
            )
        else:
            session.status = PackingSession.Status.COMPLETED

        session.save()

        AuditLog.log(
            user=user,
            action='FINALIZE_PACKING_SESSION',
            instance=session,
            reason='إنهاء التوضيب واحتساب الرندمان ونواتج المقاسات',
            details={
                'session_code': session.session_code,
                'output_packed_kg': str(session.output_packed_kg),
                'waste_weight_kg': str(session.waste_weight_kg),
                'unexplained_diff_kg': str(session.unexplained_diff_kg),
                'randeman_pct': str(session.randeman_pct),
                'packaging_charge_total': str(packaging_charge_total),
                'status': session.status,
            }
        )

        return session

    @classmethod
    def packaging_charge_total_for_session(cls, session):
        """إجمالي خصم أصناف التغليف المسجّل على الجلسة (0 إن لم يوجد)."""
        charge = getattr(session, 'packaging_charge', None)
        if charge is None:
            charge = PackingSessionPackagingCharge.objects.filter(session=session).first()
        return charge.total_amount if charge else Decimal('0.000')

    @classmethod
    @transaction.atomic
    def manager_approve_session(cls, session, manager_user, approval_notes=""):
        """
        اعتماد المدير لعملية التوضيب عند وجود فرق وزن غير مفسر
        """
        session.status = PackingSession.Status.APPROVED
        session.approved_by = manager_user
        session.approved_at = timezone.now()
        session.approval_notes = approval_notes
        session.save()

        # إغلاق الإشعار المرتبط
        Notification.objects.filter(related_model='PackingSession', related_id=str(session.id)).update(
            is_resolved=True,
            resolved_at=timezone.now()
        )

        AuditLog.log(
            user=manager_user,
            action='APPROVE_PACKING_SESSION',
            instance=session,
            reason=f'اعتماد المدير لفرق الوزن في الجلسة: {approval_notes}',
            details={'unexplained_diff_kg': str(session.unexplained_diff_kg)}
        )
        return session

    # ==================== خدمة التبريد اليومية للطبالي الموضبة ====================

    @classmethod
    @transaction.atomic
    def send_packed_pallets_to_cooling(cls, packed_pallets, cold_store, shelf=None, user=None, notes=""):
        """
        إعادة طبالي موضبة إلى برادات المشغل (خدمة التبريد اليومية):
        تُفتح فترة تبريد لكل طبلية، وإن كانت داخل البراد أصلاً تُعدّ نقلاً بين الأسياخ.
        """
        packed_pallets = list(packed_pallets)
        if not packed_pallets:
            raise ValidationError("يجب تحديد طبلية موضبة واحدة على الأقل لإدخالها إلى البراد.")
        if cold_store is None:
            raise ValidationError("يجب تحديد البراد المراد إدخال الطبالي إليه.")
        if shelf is not None and shelf.cold_store_id != cold_store.id:
            raise ValidationError("السيخ المحدد لا يتبع البراد المحدد. اختر سيخاً من داخل نفس البراد.")

        now = timezone.now()
        for p in packed_pallets:
            if p.status == PackedPallet.Status.DELIVERED:
                raise ValidationError(f"الطبلية {p.packed_pallet_code} مسلّمة للتاجر ولا يمكن إعادتها للتبريد.")

            open_log = p.cooling_logs.filter(exited_at__isnull=True).order_by('-entered_at').first()
            movement = PackedPalletCoolingLog.MovementType.IN
            if open_log:
                open_log.close(now)
                movement = PackedPalletCoolingLog.MovementType.MOVE

            p.status = PackedPallet.Status.IN_COOLING
            p.cold_store = cold_store
            p.shelf = shelf
            p.save(update_fields=['status', 'cold_store', 'shelf'])

            PackedPalletCoolingLog.objects.create(
                packed_pallet=p,
                movement_type=movement,
                cold_store=cold_store,
                shelf=shelf,
                entered_at=now,
                moved_by=user,
                notes=notes,
            )

        AuditLog.log(
            user=user,
            action='PACKED_PALLETS_TO_COOLING',
            instance=packed_pallets[0],
            reason=f'إدخال {len(packed_pallets)} طبلية موضبة إلى {cold_store.name}',
            details={
                'count': len(packed_pallets),
                'cold_store': cold_store.name,
                'shelf': str(shelf) if shelf else None,
            }
        )
        return packed_pallets

    @classmethod
    @transaction.atomic
    def checkout_packed_pallets_from_cooling(cls, packed_pallets, user=None, notes=""):
        """
        إخراج طبالي موضبة من البراد (إيقاف احتساب التبريد اليومي) وإعادتها جاهزة للتسليم.
        """
        packed_pallets = list(packed_pallets)
        if not packed_pallets:
            raise ValidationError("يجب تحديد طبلية موضبة واحدة على الأقل لإخراجها من البراد.")

        now = timezone.now()
        for p in packed_pallets:
            for open_log in p.cooling_logs.filter(exited_at__isnull=True):
                open_log.close(now)

            p.status = PackedPallet.Status.PACKED
            p.cold_store = None
            p.shelf = None
            p.save(update_fields=['status', 'cold_store', 'shelf'])

            PackedPalletCoolingLog.objects.create(
                packed_pallet=p,
                movement_type=PackedPalletCoolingLog.MovementType.OUT,
                entered_at=now,
                exited_at=now,
                days_counted=0,
                moved_by=user,
                notes=notes,
            )

        AuditLog.log(
            user=user,
            action='PACKED_PALLETS_OUT_OF_COOLING',
            instance=packed_pallets[0],
            reason=f'إخراج {len(packed_pallets)} طبلية موضبة من البراد',
            details={'count': len(packed_pallets)}
        )
        return packed_pallets

    @classmethod
    def build_cooling_statement(cls, trader=None, only_in_cooling=False):
        """
        كشف خدمة التبريد اليومية:
        لكل طبلية موضبة عدد أيام بقائها في البراد × الأجرة اليومية المعتمدة.
        """
        rate = SystemSetting.get_settings().packed_cooling_daily_rate or Decimal('0.000')
        pallets = PackedPallet.objects.exclude(cooling_logs__isnull=True)
        if trader is not None:
            pallets = pallets.filter(trader=trader)
        if only_in_cooling:
            pallets = pallets.filter(status=PackedPallet.Status.IN_COOLING)
        pallets = pallets.distinct().prefetch_related('cooling_logs').select_related(
            'trader', 'crop', 'box_type', 'cold_store', 'shelf'
        )

        rows = []
        total_days = 0
        total_fee = Decimal('0.000')
        for p in pallets:
            days = p.cooling_days_total
            if days <= 0 and p.status != PackedPallet.Status.IN_COOLING:
                continue
            fee = (Decimal(days) * rate).quantize(Decimal('0.001'), rounding=ROUND_HALF_UP)
            rows.append({'pallet': p, 'days': days, 'fee': fee})
            total_days += days
            total_fee += fee

        return {
            'rows': rows,
            'total_days': total_days,
            'total_fee': total_fee,
            'rate': rate,
            'only_in_cooling': only_in_cooling,
            'trader': trader,
        }

    @classmethod
    @transaction.atomic
    def create_delivery_note(cls, trader, packed_pallets, driver_name="", truck_plate="", receiver_name="", notes="", user=None):
        """
        إنشاء بيان تسليم عند خروج الموضب للتاجر
        """
        if not packed_pallets:
            raise ValidationError("يجب اختيار طبلية موضبة واحدة على الأقل للتسليم.")

        del_number = DeliveryNote.generate_delivery_number()
        total_boxes = sum(p.box_count for p in packed_pallets)
        total_net = sum(p.net_weight_kg for p in packed_pallets)

        delivery_note = DeliveryNote.objects.create(
            delivery_number=del_number,
            trader=trader,
            driver_name=driver_name,
            truck_plate=truck_plate,
            receiver_name=receiver_name,
            total_pallets=len(packed_pallets),
            total_boxes=total_boxes,
            total_net_weight_kg=total_net,
            notes=notes,
            created_by=user
        )
        delivery_note.pallets.set(packed_pallets)

        # تحديث حالة الطبالي إلى مسلّمة وإغلاق أي فترة تبريد مفتوحة
        for p in packed_pallets:
            for open_log in p.cooling_logs.filter(exited_at__isnull=True):
                open_log.close()
            p.status = PackedPallet.Status.DELIVERED
            p.cold_store = None
            p.shelf = None
            p.save(update_fields=['status', 'cold_store', 'shelf'])

        AuditLog.log(
            user=user,
            action='CREATE_DELIVERY_NOTE',
            instance=delivery_note,
            reason=f'تسليم {len(packed_pallets)} طبلية موضبة للتاجر {trader.name}',
            details={'delivery_number': del_number, 'total_boxes': total_boxes, 'total_net': str(total_net)}
        )

        return delivery_note
