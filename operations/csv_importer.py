import csv
import io
from decimal import Decimal
from django.db import transaction
from .models import Trader, BoxType
from core.models import AuditLog


def import_traders_from_csv(file_obj, user=None):
    """
    استيراد بيانات التجار القديمة من ملف CSV
    الترويسة المتوقعة: code,name,phone,notes
    """
    decoded_file = file_obj.read().decode('utf-8-sig')
    io_string = io.StringIO(decoded_file)
    reader = csv.DictReader(io_string)

    imported_count = 0
    updated_count = 0
    errors = []

    with transaction.atomic():
        for row_num, row in enumerate(reader, start=2):
            name = (row.get('name') or row.get('الاسم') or '').strip()
            code = (row.get('code') or row.get('الرمز') or '').strip()
            phone = (row.get('phone') or row.get('الهاتف') or '').strip()
            notes = (row.get('notes') or row.get('ملاحظات') or '').strip()

            if not name:
                errors.append(f"السطر {row_num}: اسم التاجر مطلوب.")
                continue

            if not code:
                code = Trader.generate_next_code()

            trader, created = Trader.objects.update_or_create(
                code=code,
                defaults={
                    'name': name,
                    'phone': phone,
                    'notes': notes,
                }
            )
            if created:
                imported_count += 1
            else:
                updated_count += 1

        if user:
            AuditLog.log(
                user=user,
                action='IMPORT_TRADERS_CSV',
                instance=Trader.objects.first() or Trader(),
                reason=f"استيراد تجار من CSV: {imported_count} جديد، {updated_count} محدث",
                details={'imported': imported_count, 'updated': updated_count}
            )

    return {'imported': imported_count, 'updated': updated_count, 'errors': errors}


def import_boxes_from_csv(file_obj, user=None):
    """
    استيراد بيانات الصناديق القديمة من ملف CSV
    الترويسة: name,color_or_type,tare_weight,discount_price,company_stock
    """
    decoded_file = file_obj.read().decode('utf-8-sig')
    io_string = io.StringIO(decoded_file)
    reader = csv.DictReader(io_string)

    imported_count = 0
    updated_count = 0
    errors = []

    with transaction.atomic():
        for row_num, row in enumerate(reader, start=2):
            name = (row.get('name') or row.get('الاسم') or '').strip()
            color = (row.get('color_or_type') or row.get('اللون') or '').strip()
            tare = row.get('tare_weight') or row.get('الوزن_الفارغ') or '0.800'
            discount = row.get('discount_price') or row.get('سعر_الخصم') or '0.000'
            stock = row.get('company_stock') or row.get('الرصيد') or '0'

            if not name:
                errors.append(f"السطر {row_num}: اسم الصندوق مطلوب.")
                continue

            box, created = BoxType.objects.update_or_create(
                name=name,
                defaults={
                    'color_or_type': color,
                    'tare_weight': Decimal(str(tare)),
                    'discount_price': Decimal(str(discount)),
                    'company_stock': int(stock),
                }
            )
            if created:
                imported_count += 1
            else:
                updated_count += 1

        if user:
            AuditLog.log(
                user=user,
                action='IMPORT_BOXES_CSV',
                instance=BoxType.objects.first() or BoxType(),
                reason=f"استيراد صناديق من CSV: {imported_count} جديد، {updated_count} محدث",
                details={'imported': imported_count, 'updated': updated_count}
            )

    return {'imported': imported_count, 'updated': updated_count, 'errors': errors}
