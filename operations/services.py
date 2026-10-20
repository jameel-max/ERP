from decimal import Decimal
from django.db import transaction
from django.core.exceptions import ValidationError
from django.utils import timezone
from core.models import SystemSetting, Notification, AuditLog
from .models import (
    TruckReceipt, Pallet, PalletMovementLog, ColdStore, Shelf, Trader, Crop,
    ColdStoreTemperatureLog, annotate_shelf_occupancy,
)
from boxes.models import FarmBoxType


def log_temperature(cold_store, temperature, log_type, pallet=None, user=None, notes=""):
    """
    تسجيل درجة حرارة براد عند الإدخال أو الإخراج أو القياس الدوري.
    لا يُنشئ سجلاً إذا لم تكن الحرارة مسجلة (تفادياً لبيانات غير موثوقة).
    """
    if cold_store is None or temperature is None or temperature == '':
        return None
    return ColdStoreTemperatureLog.objects.create(
        cold_store=cold_store,
        temperature=Decimal(str(temperature)),
        log_type=log_type,
        pallet=pallet,
        notes=notes,
        recorded_by=user
    )


def distribute_pallets_over_shelves(cold_store, start_shelf, pallet_count):
    """
    توزيع عدد الطبالي على أسياخ البراد بدءاً من السيخ المختار:
    - يبدأ من السيخ المختار ثم ينتقل إلى السيخ التالي عند امتلائه.
    - يتجاهل الأسياخ الممتلئة (المشغولة ≥ السعة) فلا يُستخدم فيها شيء.
    - الأسياخ بلا سعة محددة (فارغة) تستوعب كل ما تبقّى.
    يُعيد (assignments, overflow) حيث assignments = [(shelf, count), ...].
    """
    shelves = list(cold_store.shelves.order_by('shelf_number')) if cold_store else []
    if not shelves:
        return [], pallet_count

    if start_shelf is not None and start_shelf in shelves:
        idx = shelves.index(start_shelf)
        ordered = shelves[idx:] + shelves[:idx]
    else:
        ordered = shelves

    annotate_shelf_occupancy(ordered)

    assignments = []
    left = pallet_count
    for shelf in ordered:
        if left <= 0:
            break
        # السعة الفعلية: سعة السيخ إن حُدّدت، وإلا سعة البراد ÷ عدد أسياخه
        capacity = shelf.effective_capacity
        if not capacity or capacity <= 0:
            # لا للسيخ ولا للبراد سعة محددة → استوعب الباقي هنا
            assignments.append((shelf, left))
            left = 0
            break
        free = max(0, capacity - shelf.occupied_pallets)
        if free <= 0:
            continue  # سيخ ممتلئ: تجاهله
        take = min(free, left)
        assignments.append((shelf, take))
        left -= take
    return assignments, max(0, left)


class ReceptionService:
    """
    خدمة استقبال السيارات وتوليد الطبالي وحسابات القبان
    """
    @classmethod
    @transaction.atomic
    def process_truck_reception(cls, data, files=None, user=None, custom_boxes_distribution=None):
        settings_obj = SystemSetting.get_settings()

        # 1. التحقق من الحقول الحرجة وفق القائمة المعتمدة في الإعدادات (البند خامساً)
        # «عدد الطبالي» يُحسب تلقائياً من (عدد الصناديق ÷ عدد الصناديق على الطبلية)، فلا يُعدّ ناقصاً.
        critical_fields = [f for f in settings_obj.get_critical_fields() if f != 'pallet_count']
        missing_critical = [
            SystemSetting.ALL_FIELD_LABELS.get(field, field)
            for field in critical_fields
            if not data.get(field)
        ]
        if missing_critical:
            raise ValidationError(f"الحقول الحرجة التالية إلزامية ولا يمكن حفظ السيارة بدونها: {', '.join(missing_critical)}")

        # استخراج الكائنات
        trader = data['trader'] if isinstance(data['trader'], Trader) else Trader.objects.get(id=data['trader'])
        crop = data['crop'] if isinstance(data['crop'], Crop) else Crop.objects.get(id=data['crop'])
        box_type = data['box_type'] if isinstance(data['box_type'], FarmBoxType) else FarmBoxType.objects.get(id=data['box_type'])
        cold_store = data['cold_store'] if isinstance(data['cold_store'], ColdStore) else ColdStore.objects.get(id=data['cold_store'])

        shelf = None
        if data.get('shelf'):
            shelf = data['shelf'] if isinstance(data['shelf'], Shelf) else Shelf.objects.get(id=data['shelf'])
            if shelf.cold_store_id != cold_store.id:
                raise ValidationError("السيخ المختار لا ينتمي إلى هذا البراد.")

        box_count = int(data['box_count'])
        if box_count <= 0:
            raise ValidationError("عدد الصناديق يجب أن يكون أكبر من صفر.")

        on_pallets = bool(data.get('on_pallets', True))

        # «عدد الطبالي» يُحسب تلقائياً = تقريب لأعلى (عدد الصناديق ÷ عدد الصناديق على الطبلية).
        # المعيار 80 صندوقاً للطبلية، ويبقى قابلاً للتعديل من نموذج الاستلام.
        boxes_per_pallet = int(data.get('boxes_per_pallet') or 0)
        pallet_count = int(data.get('pallet_count') or 0)
        if boxes_per_pallet > 0:
            pallet_count = -(-box_count // boxes_per_pallet)
        elif pallet_count > 0:
            # رجوع للتوافق: احتساب عدد الصناديق على الطبلية من عدد الطبالي المُدخل
            boxes_per_pallet = -(-box_count // pallet_count)
        else:
            boxes_per_pallet = 80
            pallet_count = -(-box_count // boxes_per_pallet)
        if pallet_count <= 0:
            raise ValidationError("تعذّر احتساب عدد الطبالي: تحقّق من «عدد الصناديق» و«عدد الصناديق على الطبلية».")

        gross_weight = Decimal(str(data['gross_weight']))
        tare_weight = Decimal(str(data['tare_weight']))
        if gross_weight <= tare_weight:
            raise ValidationError("الوزن القائم يجب أن يكون أكبر من الوزن الفارغ للسيارة.")

        # توليد رقم الدفعة Lot تلقائياً
        lot_number = data.get('lot_number') or TruckReceipt.generate_lot_number()

        # إنشاء السيارة
        truck = TruckReceipt(
            lot_number=lot_number,
            trader=trader,
            crop=crop,
            box_type=box_type,
            box_count=box_count,
            gross_weight=gross_weight,
            tare_weight=tare_weight,
            on_pallets=on_pallets,
            boxes_per_pallet=boxes_per_pallet,
            pallet_count=pallet_count,
            cold_store=cold_store,
            shelf=shelf,
            driver_name=data.get('driver_name', '').strip(),
            truck_plate=data.get('truck_plate', '').strip(),
            arrival_date=data.get('arrival_date') or timezone.now().date(),
            temperature=Decimal(str(data['temperature'])) if data.get('temperature') not in [None, ''] else None,
            notes=data.get('notes', '').strip(),
            farm_name=data.get('farm_name', '').strip() if data.get('farm_name') else '',
            export_certificate_no=data.get('export_certificate_no', '').strip() if data.get('export_certificate_no') else '',
            weight_deduction_applied=bool(data.get('weight_deduction_applied', True)),
            created_by=user,
        )

        if files and files.get('scale_ticket_image'):
            truck.scale_ticket_image = files['scale_ticket_image']

        # حساب الأوزان بالقبان
        empty_pallet_weight = settings_obj.empty_pallet_weight
        truck.calculate_weights(empty_pallet_weight=empty_pallet_weight)

        # فحص الحقول غير الحرجة الناقصة
        missing_non_critical = truck.check_non_critical_missing()
        truck.missing_fields_summary = ", ".join(missing_non_critical)
        truck.save()

        # توليد الطبالي: تُولَّد دائماً عند وجود عدد طبالي (سواء نزلت البضاعة على طبالي
        # أو رتّبها العمال على طبالي في المشغل بعد وصولها بكمات). خصم وزن الطبالي وحده
        # يتوقف على خيار «نزلت البضاعة على طبالي» ويُطبَّق داخل حساب الأوزان.
        if pallet_count > 0:
            cls._generate_pallets_for_truck(truck, custom_boxes_distribution)

        # سجل درجة حرارة البراد عند الإدخال (إن سُجّلت)
        log_temperature(
            cold_store=truck.cold_store,
            temperature=truck.temperature,
            log_type=ColdStoreTemperatureLog.LogType.ENTRY,
            user=user,
            notes=f"إدخال دفعة {truck.lot_number} إلى {truck.cold_store.name}"
        )

        # تسجيل إشعار إذا كانت هناك حقول غير حرجة ناقصة
        if missing_non_critical:
            from django.urls import reverse
            Notification.objects.create(
                title=f"حقول غير حرجة ناقصة في السيارة {truck.lot_number}",
                message=f"التاجر: {truck.trader.name}. الحقول الناقصة: {', '.join(missing_non_critical)}.",
                severity=Notification.Severity.WARNING,
                link=reverse('truck_edit', args=[truck.id]),
                related_model='TruckReceipt',
                related_id=str(truck.id)
            )

        # توثيق في سجل التدقيق
        AuditLog.log(
            user=user,
            action='CREATE_TRUCK_RECEIPT',
            instance=truck,
            reason='وصول سيارة واستلام بضاعة جديدة',
            details={
                'lot_number': truck.lot_number,
                'trader': trader.name,
                'crop': crop.name,
                'box_count': box_count,
                'pallet_count': pallet_count,
                'calculated_net_weight': str(truck.calculated_net_weight),
            }
        )

        return truck

    @classmethod
    def _generate_pallets_for_truck(cls, truck, custom_boxes_distribution=None):
        """
        توليد الطبالي: الدفعة + رقم الطبلية (2026-0143-P01...)
        مع رمز GS1 SSCC وربطها بالبراد والسيخ
        """
        pallet_count = truck.pallet_count
        box_count = truck.box_count
        boxes_per_pallet = truck.boxes_per_pallet or 80

        # توزيع الصناديق: كل طبلية تحمل «عدد الصناديق على الطبلية» (المعيار 80)،
        # والفائض يُوضع على الطبلية الأخيرة.
        if custom_boxes_distribution and len(custom_boxes_distribution) == pallet_count:
            distribution = custom_boxes_distribution
        else:
            full, remainder = divmod(box_count, boxes_per_pallet)
            distribution = [boxes_per_pallet] * full
            if remainder:
                distribution.append(remainder)
            if len(distribution) != pallet_count:
                base_boxes = box_count // pallet_count
                rem = box_count % pallet_count
                distribution = [base_boxes + (1 if i < rem else 0) for i in range(pallet_count)]

        # توزيع الطبالي على أسياخ البراد: يبدأ من السيخ المختار وينتقل للسيخ التالي
        # عند امتلائه، ولا يستخدم الأسياخ الممتلئة أبداً.
        shelf_sequence = [truck.shelf] * pallet_count
        if truck.cold_store_id and truck.shelf_id:
            assignments, overflow = distribute_pallets_over_shelves(
                truck.cold_store, truck.shelf, pallet_count
            )
            if overflow:
                raise ValidationError(
                    f"لا توجد سعة كافية في أسياخ البراد «{truck.cold_store.name}» لاستيعاب "
                    f"{pallet_count} طبلية — تبقّى {overflow} طبلية بلا مكان. "
                    "أضِف أسياخاً أو وسّع سعة الأسياخ ثم أعد المحاولة."
                )
            if assignments:
                shelf_sequence = []
                for shelf, count in assignments:
                    shelf_sequence.extend([shelf] * count)
                while len(shelf_sequence) < pallet_count:
                    shelf_sequence.append(truck.shelf)

        total_net_weight = truck.calculated_net_weight

        for idx, boxes_on_pallet in enumerate(distribution, start=1):
            pallet_code = f"{truck.lot_number}-P{idx:02d}"
            sscc = Pallet.generate_sscc(pallet_code)
            assigned_shelf = shelf_sequence[idx - 1] if idx - 1 < len(shelf_sequence) else truck.shelf

            # وزن تقديري نسبي
            est_weight = Decimal('0.000')
            if box_count > 0:
                est_weight = (total_net_weight * Decimal(boxes_on_pallet) / Decimal(box_count)).quantize(Decimal('0.001'))

            pallet = Pallet.objects.create(
                pallet_code=pallet_code,
                sscc_code=sscc,
                truck_receipt=truck,
                trader=truck.trader,
                crop=truck.crop,
                box_type=truck.box_type,
                box_count=boxes_on_pallet,
                estimated_net_weight=est_weight,
                cold_store=truck.cold_store,
                shelf=assigned_shelf,
                temperature=truck.temperature,
                status=Pallet.Status.IN_COOLING,
                notes=truck.notes
            )

            # سجل حركة التخزين الأولى
            PalletMovementLog.objects.create(
                pallet=pallet,
                to_cold_store=truck.cold_store,
                to_shelf=assigned_shelf,
                movement_type='INITIAL_STORAGE',
                moved_by=truck.created_by,
                notes='إدخال وتخزين أولي عند استلام السيارة'
            )


class StorageService:
    """
    خدمة إدارة البرادات ونقل الطبالي وتحديث المواقع المترابطة
    """
    @classmethod
    @transaction.atomic
    def transfer_pallet(cls, pallet, to_cold_store, to_shelf=None, user=None, reason=""):
        """
        نقل الطبلية بين البرادات أو الأسياخ:
        - يمنع وجودها في موقعين في نفس الوقت
        - يحدّث الإشغال في الموقعين معاً
        - يوثّق حركة في PalletMovementLog وسجل التدقيق
        """
        if to_shelf and to_shelf.cold_store_id != to_cold_store.id:
            raise ValidationError("السيخ المختار لا ينتمي إلى هذا البراد.")

        old_cold_store = pallet.cold_store
        old_shelf = pallet.shelf

        if old_cold_store.id == to_cold_store.id and getattr(old_shelf, 'id', None) == getattr(to_shelf, 'id', None):
            return pallet  # نفس الموقع لا تغيير

        # فحص سعة البراد المستهدف إذا كانت محددة
        if to_cold_store.capacity_pallets:
            current_count = Pallet.objects.filter(cold_store=to_cold_store, status='IN_COOLING').exclude(id=pallet.id).count()
            if current_count >= to_cold_store.capacity_pallets:
                raise ValidationError(f"البراد المستهدف ({to_cold_store.name}) ممتلئ! السعة: {to_cold_store.capacity_pallets} طبلية.")

        # فحص سعة السيخ المستهدف (المحدد أو المشتق من سعة البراد)
        if to_shelf is not None:
            shelf_capacity = to_shelf.effective_capacity
            if shelf_capacity:
                shelf_count = Pallet.objects.filter(shelf=to_shelf, status=Pallet.Status.IN_COOLING).exclude(id=pallet.id).count()
                if shelf_count >= shelf_capacity:
                    raise ValidationError(
                        f"السيخ «{to_shelf.shelf_number}» في {to_cold_store.name} ممتلئ! "
                        f"السعة: {shelf_capacity} طبلية. اختر سيخاً آخر متاحاً."
                    )

        # تحديث الطبلية
        pallet.cold_store = to_cold_store
        pallet.shelf = to_shelf
        if to_cold_store.current_temperature is not None:
            pallet.temperature = to_cold_store.current_temperature
        pallet.save()

        # تسجيل حركة النقل
        PalletMovementLog.objects.create(
            pallet=pallet,
            from_cold_store=old_cold_store,
            from_shelf=old_shelf,
            to_cold_store=to_cold_store,
            to_shelf=to_shelf,
            movement_type='TRANSFER',
            moved_by=user,
            notes=reason or f"نقل من {old_cold_store.name} إلى {to_cold_store.name}"
        )

        # تسجيل حرارة الإخراج من البراد القديم وحرارة الإدخال في البراد الجديد
        log_temperature(
            old_cold_store,
            old_cold_store.current_temperature,
            'EXIT',
            pallet=pallet,
            user=user,
            notes=f"إخراج الطبلية {pallet.pallet_code} — نقل إلى {to_cold_store.name}"
        )
        log_temperature(
            to_cold_store,
            to_cold_store.current_temperature,
            'ENTRY',
            pallet=pallet,
            user=user,
            notes=f"إدخال الطبلية {pallet.pallet_code} — قادمة من {old_cold_store.name}"
        )

        AuditLog.log(
            user=user,
            action='TRANSFER_PALLET',
            instance=pallet,
            reason=reason or f"نقل الطبلية {pallet.pallet_code}",
            details={
                'pallet_code': pallet.pallet_code,
                'from_cold_store': old_cold_store.name,
                'to_cold_store': to_cold_store.name,
                'from_shelf': str(old_shelf) if old_shelf else 'لا يوجد',
                'to_shelf': str(to_shelf) if to_shelf else 'لا يوجد',
            }
        )

        return pallet
