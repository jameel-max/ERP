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
    PackingRawConsumption, PackingLeftoverSettlement,
)
from boxes.services import BoxService, EmptyBoxService
from boxes.models import FarmBoxType, FarmEmptyBoxConsumption


class PackingService:
    """
    خدمة التوضيب وخطوط الإنتاج والرندمان والهالك
    """
    @classmethod
    @transaction.atomic
    def reverse_packing_session(cls, session, user=None, reason=''):
        """
        عكس أثر عملية توضيب بالكامل قبل حذفها (يُستدعى من PackingSession.delete):

        - إرجاع **كل** الطبالي المصدر (المستهلكة والمرتجعة) إلى البراد في نفس السيخ.
        - حذف الطبالي الموضّبة الناتجة عن الجلسة (ومعها سجلات تبريدها).
        - تحرير رصيد صناديق الفارغ (من المزرعة) المستهلك في هذه الجلسة.
        - تسجيل حركات إرجاع وسجل تدقيق واضح.

        ملاحظة: لا نلمس أي قيود فواتير/مالية — إن كانت الجلسة مرتبطة بفاتورة محميّة
        فسيُرفض الحذف بـ PROTECT قبل الوصول إلى هنا.
        """
        code = session.session_code
        source_ids = list(session.source_pallets.values_list('id', flat=True))

        # 0. عكس سحوبات الكميات الخام: إعادة الصناديق إلى رصيد طبالي مصدرها
        #    (حتى تبقى أرصدة الطبالي صحيحة بعد حذف العملية).
        restored_boxes = 0
        for c in list(session.raw_consumptions.select_related('source_pallet')):
            pallet = Pallet.objects.select_for_update().filter(id=c.source_pallet_id).first()
            if pallet is None:
                continue
            pallet.consumed_box_count = max(0, pallet.consumed_box_count - c.box_count)
            pallet.remaining_box_count = pallet.remaining_box_count + c.box_count
            if pallet.remaining_box_count > pallet.box_count:
                pallet.remaining_box_count = pallet.box_count
            pallet.save(update_fields=['consumed_box_count', 'remaining_box_count'])
            restored_boxes += c.box_count
        session.raw_consumptions.all().delete()

        # 1. إرجاع الطبالي المصدر إلى البراد (نفس البراد ونفس السيخ)
        if source_ids:
            now = timezone.now()
            for p in Pallet.objects.select_for_update().filter(id__in=source_ids):
                p.status = Pallet.Status.IN_COOLING
                p.exited_cooling_at = None
                p.save(update_fields=['status', 'exited_cooling_at'])
                PalletMovementLog.objects.create(
                    pallet=p,
                    from_cold_store=None,
                    from_shelf=None,
                    to_cold_store=p.cold_store,
                    to_shelf=p.shelf,
                    movement_type='RETURN_TO_COOLING',
                    moved_by=user,
                    notes=f"إرجاع طبلية إلى البراد بعد إلغاء/حذف جلسة التوضيب {code}",
                )

        # 2. حذف الطبالي الموضّبة الناتجة (CASCADE يحذف سجلات التبريد المرتبطة)
        packed_count = session.packed_pallets.count()
        session.packed_pallets.all().delete()

        # 3. تحرير الفارغ المستهلك في هذه الجلسة
        released_empty = 0
        for fc in list(session.farm_empty_consumptions.all()):
            released_empty += fc.quantity
            fc.delete()

        AuditLog.log(
            user=user,
            action='REVERSE_PACKING_SESSION',
            instance=session,
            reason=reason or f'عكس أثر جلسة التوضيب {code} قبل حذفها',
            details={
                'session_code': code,
                'source_pallets_returned': len(source_ids),
                'packed_pallets_deleted': packed_count,
                'farm_empty_released': released_empty,
                'raw_boxes_restored': restored_boxes,
            },
        )
        return {
            'source_pallets_returned': len(source_ids),
            'packed_pallets_deleted': packed_count,
            'farm_empty_released': released_empty,
            'raw_boxes_restored': restored_boxes,
        }

    @classmethod
    @transaction.atomic
    def update_packing_session(cls, session, trader, crop, packing_method,
                               source_pallets, workshop_name="", notes=None, user=None):
        """
        تعديل البيانات الأساسية لعملية توضيب (قبل الإنهاء فقط):
        التاجر، البضاعة، طريقة التوضيب، الورشة، والطبالي المصدر.

        عند تغيير الطبالي المصدر: تُعاد الطبالي القديمة إلى البراد، وتُخرج الجديدة
        منه (مع تسجيل الحركتين)، ويُعاد احتساب وزن الداخل.
        """
        if session.status != PackingSession.Status.DRAFT:
            raise ValidationError('لا يمكن تعديل جلسة توضيب بدأت أو اكتملت؛ التعديل متاح للمسودة فقط.')
        if not source_pallets:
            raise ValidationError('يجب اختيار طبلية واحدة على الأقل من البراد.')

        new_ids = {p.id for p in source_pallets}
        old_ids = set(session.source_pallets.values_list('id', flat=True))

        # إرجاع الطبالي التي أُزيلت من الجلسة إلى البراد
        removed_ids = old_ids - new_ids
        if removed_ids:
            now = timezone.now()
            for p in Pallet.objects.select_for_update().filter(id__in=removed_ids):
                p.status = Pallet.Status.IN_COOLING
                p.exited_cooling_at = None
                p.save(update_fields=['status', 'exited_cooling_at'])
                PalletMovementLog.objects.create(
                    pallet=p, from_cold_store=None, from_shelf=None,
                    to_cold_store=p.cold_store, to_shelf=p.shelf,
                    movement_type='RETURN_TO_COOLING', moved_by=user,
                    notes=f"إرجاع طبلية إلى البراد بعد تعديل جلسة التوضيب {session.session_code}",
                )

        # إخراج الطبالي المضافة حديثاً من البراد
        added = [p for p in source_pallets if p.id not in old_ids]
        if added:
            now = timezone.now()
            for p in Pallet.objects.select_for_update().filter(id__in=[p.id for p in added]):
                p.status = Pallet.Status.OUT_FOR_PACKING
                p.exited_cooling_at = now
                p.save(update_fields=['status', 'exited_cooling_at'])
                PalletMovementLog.objects.create(
                    pallet=p, from_cold_store=p.cold_store, from_shelf=p.shelf,
                    to_cold_store=None, to_shelf=None,
                    movement_type='TO_PACKING', moved_by=user,
                    notes=f"إخراج من البراد للتوضيب في الجلسة {session.session_code}",
                )

        session.trader = trader
        session.crop = crop
        session.packing_method = packing_method
        session.workshop_name = workshop_name or ''
        if notes is not None:
            session.notes = notes
        session.source_pallets.set(source_pallets)
        session.input_weight_kg = sum(
            (p.estimated_net_weight for p in source_pallets), Decimal('0.000')
        ).quantize(Decimal('0.001'))
        session.save()

        AuditLog.log(
            user=user,
            action='UPDATE_PACKING_SESSION',
            instance=session,
            reason=f'تعديل بيانات جلسة التوضيب {session.session_code}',
            details={
                'session_code': session.session_code,
                'source_pallets': [p.pallet_code for p in source_pallets],
                'removed_pallets': len(removed_ids),
                'added_pallets': len(added),
            },
        )
        return session

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
    def record_outputs_and_finalize(cls, session, outputs_data, wastes_data=None,
                                    farm_empty_items=None, allocations=None,
                                    all_packed=None, user=None):
        """
        تسجيل نواتج المقاسات والهالك وتوليد الطبالي الموضبة وحساب الرندمان.

        **منطق الكميات بعد التصحيح:**
        - عدد طبالي الخام ≠ عدد طبالي المنتج الموضب، ولا يُفترض تطابقهما إطلاقاً.
        - الأساس في احتساب الاستهلاك هو الكمية الفعلية (صناديق المزرعة المسحوبة)،
          وتُبيّن `allocations` مصدر كل كمية: رقم الطبلية الخام المصدر وعدد الصناديق.
        - يمكن أن تُغذّى الطبلية الموضبة الواحدة من أكثر من طبلية خام، بما فيها
          طبلية موجودة في البراد لم تبدأ في هذه العملية.
        - إن لم تُمرَّر `allocations` (استدعاءات قديمة/متوافقة) يعمل المسار
          التاريخي كما كان دون تغيير سلوكه.

        `all_packed` — إجابة المحاسب الإلزامية عند إنهاء العملية:
          - True  : «نعم، تم توضيب جميع الكميات» → يُتحقّق من خلوّ العملية من خام
                    غير محسوم، ثم تُغلق.
          - False : «لا، ما زالت هناك كميات خام متبقية» → تبقى مفتوحة (INCOMPLETE).
          - None  : المسار التاريخي (بلا سؤال).
        """
        if not outputs_data:
            raise ValidationError("يجب إدخال ناتج توضيب واحد على الأقل.")

        if session.is_closed:
            raise ValidationError(
                "عملية التوضيب مغلقة ولا يمكن إدخال كميات جديدة إليها. "
                "افتح دفعة توضيب جديدة لإضافة خام جديد."
            )

        settings_obj = SystemSetting.get_settings()

        # وضع الكميات الجديدة (مفعّل) مقابل المسار التاريخي المتوافق
        use_allocations = allocations is not None
        draw_plan = cls._prepare_draw_plan(session, outputs_data, allocations) if use_allocations else None

        packaging_charge_total = Decimal('0.000')
        packaging_items_count = 0

        # 1. تسجيل النواتج
        legacy_primary = session.source_pallets.order_by('pallet_code').first()
        for out_index, out in enumerate(outputs_data):
            box_type = out['box_type'] if isinstance(out['box_type'], BoxType) else BoxType.objects.get(id=out['box_type'])
            box_count = int(out['box_count'])
            # الوزن المُدخل من المشغّل هو وزن الطبلية الموضبة كما تُوزن فعلياً
            # (بضاعة + صناديق). نطرح وزن الصناديق الفارغة تلقائياً
            # (عدد الصناديق × الوزن الفارغ المعرَّف في نوع الصندوق) حتى لا يُحاسَب
            # التاجر على وزن الصناديق كأنه بضاعة موضبة، ولا يتضخم الرندمان.
            entered_weight = Decimal(str(out['net_weight_kg']))
            tare_per_box = Decimal(str(box_type.tare_weight or 0))
            box_tare_total = (tare_per_box * box_count).quantize(Decimal('0.001'))
            net_weight = (entered_weight - box_tare_total).quantize(Decimal('0.001'))
            if net_weight < 0:
                raise ValidationError(
                    f"الوزن المُدخل للطبلية ({entered_weight} كغ) أقل من وزن صناديقها الفارغة "
                    f"({box_tare_total} كغ = {box_count} صندوق × {tare_per_box} كغ). "
                    f"تحقّق من الوزن المُدخل أو من «الوزن الفارغ» المعرَّف في نوع الصندوق «{box_type.name}»."
                )
            pallet_count = int(out.get('pallet_count', 1) or 1)

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
            if use_allocations:
                row_sources = draw_plan[out_index]
                primary_pallet = row_sources[0][0]
            else:
                primary_pallet = legacy_primary
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

                packed_pallet = PackedPallet.objects.create(
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

                if use_allocations:
                    # تسجيل مصادر الكميات الخام الفعلية التي غذّت هذه الطبلية الموضبة
                    sub_sources = cls._split_sources(row_sources, pallet_count)[i]
                    cls._register_raw_consumptions(
                        session=session,
                        packed_pallet=packed_pallet,
                        sources=sub_sources,
                        net_weight=p_net,
                        user=user,
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

        # 2-أ. صناديق الفارغ (من المزرعة) المستهلكة في البرارة والتالف:
        # قسم مستقل تماماً عن نواتج المقاسات، يُخصم من رصيد الفارغ المتاح
        # حتى لا يُسلَّم نفس الفارغ مرتين (مرة هنا ومرة عند تسليم الفارغ).
        for fe in (farm_empty_items or []):
            fb_type = fe['farm_box_type']
            if not isinstance(fb_type, FarmBoxType):
                fb_type = FarmBoxType.objects.get(id=fb_type)
            fe_qty = int(fe.get('quantity') or 0)
            if fe_qty <= 0:
                continue
            EmptyBoxService.record_packing_consumption(
                farm_box_type=fb_type,
                quantity=fe_qty,
                trader=session.trader,
                packing_session=session,
                notes=f'استهلاك فارغ مزرعة في برارة/تالف الجلسة {session.session_code}',
                user=user,
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

        if use_allocations:
            # 3. وضع الكميات الجديد:
            #    - وزن الداخل يُحسب من السحوبات الموثّقة فقط.
            #    - الطبالي التي بقي فيها خام تبقى «مفتوحة» ولا تُعتبر مستهلكة.
            #    - لا يُستنتج عدد الطبالي المستهلكة من الفرق بين عدد الطبالي.
            cls._recalculate_input_weight_from_consumptions(session)

            leftover_pallets = cls._leftover_pallets(session)
            if all_packed is True:
                # تحقق: كل الكميات المسجلة كمستخدمة تم صرفها فعلياً بحركة موثقة
                unmoved = [
                    c for c in session.raw_consumptions.all()
                    if not c.movement_id or c.box_count <= 0
                ]
                if unmoved:
                    raise ValidationError(
                        "لا يمكن إغلاق العملية: توجد كميات مسجّلة كمستخدمة بلا حركة صرف موثّقة "
                        f"({len(unmoved)} سطر). راجع سجلات السحب قبل الإغلاق."
                    )
                if leftover_pallets:
                    details = '، '.join(
                        f"{p.pallet_code} ({p.remaining_box_count} صندوق)" for p in leftover_pallets
                    )
                    raise ValidationError(
                        "لا يمكن إغلاق العملية: ما زالت هناك كميات خام متبقية غير محسومة — "
                        f"{details}. استكمل التوضيب من الكميات المتاحة، أو سجّل تسوية معتمدة "
                        "لمصير الكميات المتبقية قبل الإغلاق."
                    )
                session.all_packed_confirmed = True
                session.all_packed_confirmed_by = user
                session.all_packed_confirmed_at = timezone.now()
            elif all_packed is False:
                if not leftover_pallets:
                    raise ValidationError(
                        "لا توجد كميات خام متبقية في هذه العملية. "
                        "اختر «نعم، تم توضيب جميع الكميات» أو أضف كميات خام جديدة أولاً."
                    )
                session.all_packed_confirmed = False
                session.all_packed_confirmed_by = None
                session.all_packed_confirmed_at = None
        else:
            # 3. المسار التاريخي (متوافق مع الإصدارات السابقة):
            #    فصل الطبالي **المستهلكة فعلاً** عن **غير المستهلكة** بالترتيب.
            source_pallets = list(session.source_pallets.order_by('pallet_code'))
            used_count = session.packed_pallets.count()
            used_pallets = source_pallets[:used_count]
            returned_pallets = source_pallets[used_count:]

            session.input_weight_kg = sum(
                (p.estimated_net_weight for p in used_pallets), Decimal('0.000')
            ).quantize(Decimal('0.001'))

            used_ids = [p.id for p in used_pallets]
            if used_ids:
                Pallet.objects.filter(id__in=used_ids).update(status=Pallet.Status.PACKED)

            if returned_pallets:
                now = timezone.now()
                for p in returned_pallets:
                    p.status = Pallet.Status.IN_COOLING
                    p.exited_cooling_at = None
                    p.save(update_fields=['status', 'exited_cooling_at'])

                    PalletMovementLog.objects.create(
                        pallet=p,
                        from_cold_store=None,
                        from_shelf=None,
                        to_cold_store=p.cold_store,
                        to_shelf=p.shelf,
                        movement_type='RETURN_TO_COOLING',
                        moved_by=user,
                        notes=f"إرجاع طبلية غير موضّبة إلى البراد من جلسة {session.session_code}",
                    )

                AuditLog.log(
                    user=user,
                    action='RETURN_UNUSED_PALLETS',
                    instance=session,
                    reason=(
                        f'إرجاع {len(returned_pallets)} طبلية غير موضّبة إلى البراد '
                        f'من الجلسة {session.session_code}'
                    ),
                    details={
                        'session_code': session.session_code,
                        'used_pallets': len(used_pallets),
                        'returned_pallets': len(returned_pallets),
                        'returned_codes': [p.pallet_code for p in returned_pallets],
                    },
                )

        # 4. حساب المقاييس وفحص حد السماح
        session.calculate_metrics(tolerance_pct=settings_obj.weight_tolerance_pct)

        # 5. تحديد حالة الجلسة حسب إجابة المحاسب وفرق الوزن
        if use_allocations and all_packed is False:
            session.status = PackingSession.Status.INCOMPLETE
            session.completed_at = None
        else:
            session.completed_at = timezone.now()
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
                'all_packed_confirmed': session.all_packed_confirmed,
                'raw_pallets': session.source_pallets.count(),
                'packed_pallets': session.packed_pallets.count(),
            }
        )

        return session

    # ==========================================================
    # منطق الكميات الجديد: خريطة السحب ومصادر الطبلية الخام
    # ==========================================================

    @classmethod
    def _prepare_draw_plan(cls, session, outputs_data, allocations):
        """
        التحقق من صحة خريطة السحب **قبل** أي كتابة في قاعدة البيانات:

        - لكل سطر ناتج يجب تحديد مصدر الكميات (الطبلية الخام + عدد الصناديق).
        - الطبلية المصدر يجب أن تكون للتاجر والبضاعة نفسيهما، وأن تكون متاحة للصرف.
        - مجموع المطلوب من كل طبلية يجب ألا يتجاوز رصيدها المتاح (منع الصرف المزدوج
          أو تجاوز الرصيد)، وتُقفل الطبالي أثناء الفحص لمنع التزامن.
        """
        allocations = list(allocations or [])
        if len(allocations) != len(outputs_data):
            raise ValidationError("عدد خرائط سحب الكميات لا يطابق عدد أسطر النواتج.")

        plan = []
        demand = {}          # pallet_id -> عدد الصناديق المطلوبة
        pallet_cache = {}

        for idx, out in enumerate(outputs_data):
            rows = allocations[idx] or []
            per_row = []
            row_total = 0
            for item in rows:
                pallet_id = item.get('source_pallet')
                try:
                    boxes = int(item.get('box_count') or 0)
                except (TypeError, ValueError):
                    boxes = 0
                if boxes <= 0 or not pallet_id:
                    continue

                try:
                    pallet_key = int(pallet_id)
                except (TypeError, ValueError):
                    raise ValidationError("معرّف الطبلية الخام المصدر غير صحيح.")

                if pallet_key not in pallet_cache:
                    pallet_cache[pallet_key] = Pallet.objects.select_for_update().filter(id=pallet_key).first()
                pallet = pallet_cache[pallet_key]
                if pallet is None:
                    raise ValidationError("إحدى الطبالي الخام المصدر غير موجودة في السجلات.")
                if pallet.trader_id != session.trader_id:
                    raise ValidationError(
                        f"الطبلية {pallet.pallet_code} تخصّ تاجراً آخر غير تاجر هذه العملية."
                    )
                if pallet.crop_id != session.crop_id:
                    raise ValidationError(
                        f"الطبلية {pallet.pallet_code} تحمل بضاعة ({pallet.crop.name}) "
                        f"مختلفة عن بضاعة العملية ({session.crop.name})."
                    )
                if pallet.status not in (
                    Pallet.Status.IN_COOLING,
                    Pallet.Status.OUT_FOR_PACKING,
                    Pallet.Status.OPEN_PARTIAL,
                ):
                    raise ValidationError(
                        f"الطبلية {pallet.pallet_code} غير متاحة للصرف حالياً (حالتها: {pallet.get_status_display()})."
                    )

                per_row.append([pallet, boxes])
                row_total += boxes
                demand[pallet.id] = demand.get(pallet.id, 0) + boxes
            if row_total <= 0:
                raise ValidationError(
                    "يجب تحديد مصدر الكميات الخام لكل سطر ناتج: اختر الطبلية الخام "
                    "المصدر وعدد الصناديق المسحوبة منها."
                )
            plan.append(per_row)

        # فحص الرصيد المتاح لكل طبلية (بعد جمع كل الطلبات عليها)
        for pallet_id, needed in demand.items():
            pallet = pallet_cache[pallet_id]
            if needed > pallet.remaining_box_count:
                raise ValidationError(
                    f"الكمية المطلوبة من الطبلية {pallet.pallet_code} ({needed} صندوق) "
                    f"تتجاوز الرصيد المتاح فيها ({pallet.remaining_box_count} صندوق). "
                    "لا يجوز صرف الصناديق نفسها مرتين أو تجاوز الرصيد المتاح."
                )
        return plan

    @classmethod
    def _split_sources(cls, sources, parts):
        """
        تقسيم مصادر سطر واحد على عدة طبالي موضبة (تقسيم تناسبي صحيح بلا ضياع).
        """
        if parts <= 1:
            return [list(sources)]
        buckets = [[] for _ in range(parts)]
        for pallet, boxes in sources:
            remaining = boxes
            for i in range(parts):
                if remaining <= 0:
                    break
                if i == parts - 1:
                    take = remaining
                else:
                    take = (boxes * (i + 1)) // parts - (boxes * i) // parts
                if take > remaining:
                    take = remaining
                if take > 0:
                    buckets[i].append([pallet, take])
                    remaining -= take
        return buckets

    @classmethod
    def _register_raw_consumptions(cls, session, packed_pallet, sources, net_weight, user=None):
        """
        تسجيل سحب الكميات الخام من مصادرها الفعلية:

        - يخصم الكمية من رصيد الطبلية المصدر (باستخدام رصيد محدَّث ومقفل).
        - يسجّل حركة صرف من موقع التخزين (البراد/السيخ) إن كانت الطبلية في البراد.
        - يوزّع الوزن على المصادر بنسبة الصناديق المسحوبة.
        - يحدّث حالة الطبلية: مستهلكة بالكامل (PACKED) أو مفتوحة (OPEN_PARTIAL).
        """
        total_boxes = sum(count for _, count in sources)
        for pallet, boxes in sources:
            locked = Pallet.objects.select_for_update().get(id=pallet.id)

            if boxes > locked.remaining_box_count:
                raise ValidationError(
                    f"الكمية المطلوبة من الطبلية {locked.pallet_code} ({boxes} صندوق) "
                    f"تتجاوز الرصيد المتاح ({locked.remaining_box_count} صندوق)."
                )

            # نصيب الطبلية من وزنها التقديري (بحسب نسبة الصناديق المسحوبة)
            if locked.box_count:
                est_ratio = Decimal(boxes) / Decimal(locked.box_count)
            else:
                est_ratio = Decimal('0')
            estimated_weight = (Decimal(locked.estimated_net_weight) * est_ratio).quantize(Decimal('0.001'))

            # صافي وزن البضاعة المنسوبة إلى هذه الكمية (نسبةً إلى مجموع سطر الناتج)
            if total_boxes:
                net_ratio = Decimal(boxes) / Decimal(total_boxes)
            else:
                net_ratio = Decimal('0')
            net = (Decimal(net_weight) * net_ratio).quantize(Decimal('0.001'))

            # إن كانت الطبلية ما زالت في البراد فهذه حركة صرف من موقع تخزين موثّقة
            movement = None
            if locked.status == Pallet.Status.IN_COOLING:
                movement = PalletMovementLog.objects.create(
                    pallet=locked,
                    from_cold_store=locked.cold_store,
                    from_shelf=locked.shelf,
                    to_cold_store=None,
                    to_shelf=None,
                    movement_type='TO_PACKING',
                    moved_by=user,
                    notes=(
                        f'صرف {boxes} صندوق من البراد لتغذية الطبلية الموضبة '
                        f'{packed_pallet.packed_pallet_code} في الجلسة {session.session_code}'
                    ),
                )
                locked.status = Pallet.Status.OUT_FOR_PACKING
                locked.exited_cooling_at = timezone.now()
                # تُضاف الطبلية إلى طبالي مصدر العملية لأنها أصبحت جزءاً منها
                session.source_pallets.add(locked)
            elif locked.status in (Pallet.Status.OPEN_PARTIAL, Pallet.Status.OUT_FOR_PACKING):
                # الطبلية مخرجة من البراد ومخصّصة للتوضيب: نسجّل سحباً جزئياً منها
                # مع موقعها الأصلي، فكل سحب له حركة موثّقة.
                movement = PalletMovementLog.objects.create(
                    pallet=locked,
                    from_cold_store=locked.cold_store,
                    from_shelf=locked.shelf,
                    to_cold_store=None,
                    to_shelf=None,
                    movement_type='PARTIAL_DRAW',
                    moved_by=user,
                    notes=(
                        f'سحب {boxes} صندوق من طبلية مفتوحة لتغذية الطبلية الموضبة '
                        f'{packed_pallet.packed_pallet_code} في الجلسة {session.session_code}'
                    ),
                )

            locked.consumed_box_count += boxes
            locked.remaining_box_count = max(0, locked.remaining_box_count - boxes)
            locked.status = (
                Pallet.Status.PACKED if locked.remaining_box_count == 0
                else Pallet.Status.OPEN_PARTIAL
            )
            locked.save(update_fields=[
                'consumed_box_count', 'remaining_box_count', 'status', 'exited_cooling_at',
            ])

            PackingRawConsumption.objects.create(
                session=session,
                packed_pallet=packed_pallet,
                source_pallet=locked,
                trader=session.trader,
                crop=session.crop,
                box_count=boxes,
                estimated_weight_kg=estimated_weight,
                net_weight_kg=net,
                remaining_box_count=locked.remaining_box_count,
                cold_store=locked.cold_store,
                shelf=locked.shelf,
                movement=movement,
                created_by=user,
            )

    @classmethod
    def _recalculate_input_weight_from_consumptions(cls, session):
        """
        وزن الداخل = مجموع نصيب كل سحب موثّق من وزن طبلية مصدره التقديري.
        لا يُستنتج من عدد الطبالي إطلاقاً.
        """
        total = sum(
            (c.estimated_weight_kg for c in session.raw_consumptions.all()),
            Decimal('0.000'),
        )
        session.input_weight_kg = total.quantize(Decimal('0.001'))
        return session.input_weight_kg

    @classmethod
    def _leftover_pallets(cls, session):
        """
        الطبالي المرتبطة بالعملية التي بقي فيها خام لم يُستهلك أو تُسوَّى.
        """
        pallets = list(session.source_pallets.all())
        return [p for p in pallets if p.remaining_box_count > 0]

    # ==========================================================
    # إضافة خام جديد إلى عملية مفتوحة / تسوية الكميات المتبقية
    # ==========================================================

    @classmethod
    @transaction.atomic
    def add_raw_pallets_to_session(cls, session, pallets, user=None):
        """
        إضافة طبالي خام جديدة إلى عملية توضيب مفتوحة:

        - لا تُقبل إلا في عملية مفتوحة (قيد العمل أو توضيب غير مكتمل).
        - الطبالي يجب أن تكون في البراد، وأن تكون للتاجر والبضاعة نفسيهما.
        - تُسجَّل حركة إخراج من البراد، وتُضاف إلى طبالي مصدر العملية.
        """
        if session.is_closed:
            raise ValidationError(
                "عملية التوضيب مغلقة ولا يمكن إضافة خام جديد إليها. "
                "افتح دفعة توضيب جديدة لإضافة كميات خام جديدة."
            )

        pallets = list(pallets)
        if not pallets:
            raise ValidationError("يجب اختيار طبلية خام واحدة على الأقل لإضافتها إلى العملية.")

        existing_ids = set(session.source_pallets.values_list('id', flat=True))
        now = timezone.now()
        added = []

        for p in pallets:
            locked = Pallet.objects.select_for_update().get(id=p.id)
            if locked.id in existing_ids:
                continue
            if locked.trader_id != session.trader_id:
                raise ValidationError(
                    f"الطبلية {locked.pallet_code} تخصّ تاجراً آخر غير تاجر هذه العملية."
                )
            if locked.crop_id != session.crop_id:
                raise ValidationError(
                    f"الطبلية {locked.pallet_code} تحمل بضاعة ({locked.crop.name}) "
                    f"مختلفة عن بضاعة العملية ({session.crop.name})."
                )
            if locked.status not in (Pallet.Status.IN_COOLING, Pallet.Status.OPEN_PARTIAL):
                raise ValidationError(
                    f"الطبلية {locked.pallet_code} غير موجودة في البراد (حالتها: {locked.get_status_display()})."
                )

            locked.status = Pallet.Status.OUT_FOR_PACKING
            locked.exited_cooling_at = now
            locked.save(update_fields=['status', 'exited_cooling_at'])

            PalletMovementLog.objects.create(
                pallet=locked,
                from_cold_store=locked.cold_store,
                from_shelf=locked.shelf,
                to_cold_store=None,
                to_shelf=None,
                movement_type='TO_PACKING',
                moved_by=user,
                notes=f"إخراج من البراد للتوضيب في الجلسة {session.session_code}",
            )
            added.append(locked)
            existing_ids.add(locked.id)

        if added:
            session.source_pallets.add(*added)

        AuditLog.log(
            user=user,
            action='ADD_RAW_PALLETS_TO_SESSION',
            instance=session,
            reason=f'إضافة {len(added)} طبلية خام إلى الجلسة {session.session_code}',
            details={
                'session_code': session.session_code,
                'added_codes': [p.pallet_code for p in added],
            },
        )
        return added

    @classmethod
    @transaction.atomic
    def settle_leftovers(cls, session, items, user=None):
        """
        تسوية الكميات الخام المتبقية قبل إغلاق العملية:

        `items` = [{'pallet': id, 'resolution': 'RETURN_TO_STORE' | 'WRITE_OFF', 'notes': str}]

        - RETURN_TO_STORE: تُعاد الطبلية إلى البراد في نفس السيخ ويبقى رصيدها مخزوناً.
        - WRITE_OFF: تسوية/إتلاف معتمد — يُصفَّر رصيد الطبلية ويُوثَّق ذلك،
          **دون إنشاء أي قيود مالية تلقائية** (يبقى الأثر محاسبياً بيد المحاسب).
        """
        if session.is_closed:
            raise ValidationError("عملية التوضيب مغلقة ولا يمكن تسوية كميات عليها.")

        items = list(items or [])
        if not items:
            raise ValidationError("يجب اختيار كمية خام متبقية واحدة على الأقل لتسويتها.")

        session_pallet_ids = set(session.source_pallets.values_list('id', flat=True))
        created = []

        for item in items:
            pallet_id = item.get('pallet')
            resolution = item.get('resolution')
            notes = (item.get('notes') or '').strip()

            if pallet_id not in session_pallet_ids:
                raise ValidationError("إحدى الطبالي المطلوب تسويتها ليست مرتبطة بهذه العملية.")
            if resolution not in (
                PackingLeftoverSettlement.Resolution.RETURN_TO_STORE,
                PackingLeftoverSettlement.Resolution.WRITE_OFF,
            ):
                raise ValidationError("نوع التسوية غير معروف. اختر «إعادة الباقي للبراد» أو «تسوية/إتلاف».")
            if resolution == PackingLeftoverSettlement.Resolution.WRITE_OFF and not notes:
                raise ValidationError("تسوية/الإتلاف تتطلب ذكر المبرر في الملاحظات.")

            locked = Pallet.objects.select_for_update().get(id=pallet_id)
            if locked.remaining_box_count <= 0:
                continue
            remaining_before = locked.remaining_box_count

            if resolution == PackingLeftoverSettlement.Resolution.RETURN_TO_STORE:
                # الباقي يعود مخزوناً إلى البراد في نفس السيخ
                locked.status = Pallet.Status.IN_COOLING
                locked.exited_cooling_at = None
                locked.save(update_fields=['status', 'exited_cooling_at'])
                PalletMovementLog.objects.create(
                    pallet=locked,
                    from_cold_store=None,
                    from_shelf=None,
                    to_cold_store=locked.cold_store,
                    to_shelf=locked.shelf,
                    movement_type='RETURN_TO_COOLING',
                    moved_by=user,
                    notes=f"إرجاع باقي الخام إلى البراد بعد التسوية في الجلسة {session.session_code}",
                )
                # الطبلية عادت مخزوناً إلى البراد، فتخرج من طبالٍ مصدر هذه العملية
                # (يبقى أثرها محفوظاً في سجل السحب وسجل التسوية).
                session.source_pallets.remove(locked)
            else:
                # تسوية/إتلاف معتمد: يُصفَّر الرصيد وتُغلق الطبلية (بلا قيود مالية تلقائية)
                locked.remaining_box_count = 0
                locked.status = Pallet.Status.PACKED
                locked.save(update_fields=['remaining_box_count', 'status'])
                PalletMovementLog.objects.create(
                    pallet=locked,
                    from_cold_store=locked.cold_store,
                    from_shelf=locked.shelf,
                    to_cold_store=None,
                    to_shelf=None,
                    movement_type='WRITE_OFF',
                    moved_by=user,
                    notes=f"تسوية/إتلاف باقي الخام في الجلسة {session.session_code}: {notes}",
                )

            settlement = PackingLeftoverSettlement.objects.create(
                session=session,
                pallet=locked,
                box_count=remaining_before,
                resolution=resolution,
                notes=notes,
                settled_by=user,
            )
            created.append(settlement)

        AuditLog.log(
            user=user,
            action='SETTLE_PACKING_LEFTOVERS',
            instance=session,
            reason=f'تسوية {len(created)} كمية خام متبقية في الجلسة {session.session_code}',
            details={
                'session_code': session.session_code,
                'settlements': [
                    {
                        'pallet': s.pallet.pallet_code,
                        'resolution': s.resolution,
                        'box_count': s.box_count,
                    }
                    for s in created
                ],
            },
        )
        return created

    @classmethod
    @transaction.atomic
    def close_session(cls, session, user=None, notes=""):
        """
        إغلاق عملية توضيب بعد معالجة كل الكميات الخام المتبقية:

        - يُتحقّق من عدم بقاء أي طبلية خام مرتبطة بالعملية عليها رصيد متاح.
        - يُسجَّل تأكيد المحاسب ووقت التأكيد.
        - تُغلق العملية (أو تُحال المدير لو تجاوز فرق الوزن حد السماح).
        """
        if session.is_closed:
            raise ValidationError("عملية التوضيب مغلقة مسبقاً.")

        leftover = cls._leftover_pallets(session)
        if leftover:
            details = '، '.join(f"{p.pallet_code} ({p.remaining_box_count} صندوق)" for p in leftover)
            raise ValidationError(
                "لا يمكن إغلاق العملية: ما زالت هناك كميات خام متبقية غير محسومة — "
                f"{details}. سجّل تسوية معتمدة لمصيرها أولاً."
            )

        settings_obj = SystemSetting.get_settings()
        session.calculate_metrics(tolerance_pct=settings_obj.weight_tolerance_pct)
        session.all_packed_confirmed = True
        session.all_packed_confirmed_by = user
        session.all_packed_confirmed_at = timezone.now()
        session.leftover_note = notes or session.leftover_note

        if session.requires_manager_approval:
            session.status = PackingSession.Status.PENDING_APPROVAL
            Notification.objects.create(
                title=f"توضيب بانتظار اعتماد المدير: {session.session_code}",
                message=(
                    f"التاجر: {session.trader.name}. فرق وزن غير مفسر: "
                    f"{session.unexplained_diff_kg} كغ يتطلب تدقيق واعتماد المدير."
                ),
                severity=Notification.Severity.CRITICAL,
                link=f"/packing/sessions/{session.id}/",
                related_model='PackingSession',
                related_id=str(session.id)
            )
        else:
            session.status = PackingSession.Status.COMPLETED
        session.completed_at = timezone.now()
        session.save()

        AuditLog.log(
            user=user,
            action='CLOSE_PACKING_SESSION',
            instance=session,
            reason=f'إغلاق جلسة التوضيب {session.session_code} بعد معالجة الكميات المتبقية',
            details={
                'session_code': session.session_code,
                'status': session.status,
                'notes': notes,
            },
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
