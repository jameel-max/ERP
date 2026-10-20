"""
بيانات مرجعية للاستيراد والتصدير: العملات المشهورة (عربية وأجنبية) ووحدات القياس العالمية.

ملاحظة مهمة: هذه بيانات مرجعية ثابتة يطلبها النظام (وليست بيانات تشغيلية/وهمية).
أسعار الصرف قيم يدوية تقريبية مقابل الدينار الأردني ويجب تحديثها يدوياً من الشاشة عند الحاجة،
لأن النظام يعمل دون إنترنت. الملف آمن لإعادة التشغيل (ignore_conflicts).
"""
from decimal import Decimal

from django.db import migrations


CURRENCIES = [
    # (code, name_ar, name_en, symbol, region, country, decimals, rate_to_jod)
    # ===== عملات عربية =====
    ('JOD', 'الدينار الأردني', 'Jordanian Dinar', 'د.أ', 'ARAB', 'الأردن', 3, '1.000000'),
    ('SAR', 'الريال السعودي', 'Saudi Riyal', 'ر.س', 'ARAB', 'السعودية', 2, '0.189000'),
    ('AED', 'الدرهم الإماراتي', 'UAE Dirham', 'د.إ', 'ARAB', 'الإمارات', 2, '0.193000'),
    ('KWD', 'الدينار الكويتي', 'Kuwaiti Dinar', 'د.ك', 'ARAB', 'الكويت', 3, '2.310000'),
    ('QAR', 'الريال القطري', 'Qatari Riyal', 'ر.ق', 'ARAB', 'قطر', 2, '0.195000'),
    ('BHD', 'الدينار البحريني', 'Bahraini Dinar', 'د.ب', 'ARAB', 'البحرين', 3, '1.880000'),
    ('OMR', 'الريال العماني', 'Omani Rial', 'ر.ع', 'ARAB', 'عُمان', 3, '1.840000'),
    ('EGP', 'الجنيه المصري', 'Egyptian Pound', 'ج.م', 'ARAB', 'مصر', 2, '0.014000'),
    ('IQD', 'الدينار العراقي', 'Iraqi Dinar', 'د.ع', 'ARAB', 'العراق', 3, '0.000540'),
    ('SYP', 'الليرة السورية', 'Syrian Pound', 'ل.س', 'ARAB', 'سوريا', 2, '0.000055'),
    ('LBP', 'الليرة اللبنانية', 'Lebanese Pound', 'ل.ل', 'ARAB', 'لبنان', 2, '0.0000079'),
    ('MAD', 'الدرهم المغربي', 'Moroccan Dirham', 'د.م', 'ARAB', 'المغرب', 2, '0.071000'),
    ('DZD', 'الدينار الجزائري', 'Algerian Dinar', 'د.ج', 'ARAB', 'الجزائر', 2, '0.005300'),
    ('TND', 'الدينار التونسي', 'Tunisian Dinar', 'د.ت', 'ARAB', 'تونس', 3, '0.230000'),
    ('LYD', 'الدينار الليبي', 'Libyan Dinar', 'د.ل', 'ARAB', 'ليبيا', 3, '0.146000'),
    ('SDG', 'الجنيه السوداني', 'Sudanese Pound', 'ج.س', 'ARAB', 'السودان', 2, '0.001180'),
    ('YER', 'الريال اليمني', 'Yemeni Rial', 'ر.ي', 'ARAB', 'اليمن', 2, '0.002800'),
    ('MRU', 'الأوقية الموريتانية', 'Mauritanian Ouguiya', 'أ.م', 'ARAB', 'موريتانيا', 2, '0.018000'),
    ('SOS', 'الشلن الصومالي', 'Somali Shilling', 'ش.ص', 'ARAB', 'الصومال', 2, '0.001240'),
    ('DJF', 'الفرنك الجيبوتي', 'Djiboutian Franc', 'ف.ج', 'ARAB', 'جيبوتي', 2, '0.003990'),
    ('ILS', 'الشيكل', 'Israeli New Shekel', '₪', 'ARAB', 'فلسطين/إسرائيل', 2, '0.190000'),
    # ===== عملات أجنبية =====
    ('USD', 'الدولار الأمريكي', 'US Dollar', '$', 'FOREIGN', 'الولايات المتحدة', 2, '0.709000'),
    ('EUR', 'اليورو', 'Euro', '€', 'FOREIGN', 'منطقة اليورو', 2, '0.770000'),
    ('GBP', 'الجنيه الإسترليني', 'British Pound', '£', 'FOREIGN', 'المملكة المتحدة', 2, '0.900000'),
    ('TRY', 'الليرة التركية', 'Turkish Lira', '₺', 'FOREIGN', 'تركيا', 2, '0.021000'),
    ('CNY', 'اليوان الصيني', 'Chinese Yuan', '¥', 'FOREIGN', 'الصين', 2, '0.098000'),
    ('JPY', 'الين الياباني', 'Japanese Yen', '¥', 'FOREIGN', 'اليابان', 0, '0.004700'),
    ('INR', 'الروبية الهندية', 'Indian Rupee', '₹', 'FOREIGN', 'الهند', 2, '0.008500'),
    ('RUB', 'الروبل الروسي', 'Russian Ruble', '₽', 'FOREIGN', 'روسيا', 2, '0.007300'),
    ('CAD', 'الدولار الكندي', 'Canadian Dollar', 'C$', 'FOREIGN', 'كندا', 2, '0.520000'),
    ('AUD', 'الدولار الأسترالي', 'Australian Dollar', 'A$', 'FOREIGN', 'أستراليا', 2, '0.460000'),
    ('CHF', 'الفرنك السويسري', 'Swiss Franc', 'CHF', 'FOREIGN', 'سويسرا', 2, '0.800000'),
    ('SEK', 'الكرونة السويدية', 'Swedish Krona', 'kr', 'FOREIGN', 'السويد', 2, '0.067000'),
    ('NOK', 'الكرونة النرويجية', 'Norwegian Krone', 'kr', 'FOREIGN', 'النرويج', 2, '0.065000'),
    ('DKK', 'الكرونة الدنماركية', 'Danish Krone', 'kr', 'FOREIGN', 'الدنمارك', 2, '0.103000'),
    ('PLN', 'الزلوتي البولندي', 'Polish Zloty', 'zł', 'FOREIGN', 'بولندا', 2, '0.180000'),
    ('CZK', 'الكرونة التشيكية', 'Czech Koruna', 'Kč', 'FOREIGN', 'التشيك', 2, '0.031000'),
    ('HUF', 'الفورنت الهنغاري', 'Hungarian Forint', 'Ft', 'FOREIGN', 'هنغاريا', 2, '0.002000'),
    ('RON', 'الليو الروماني', 'Romanian Leu', 'lei', 'FOREIGN', 'رومانيا', 2, '0.155000'),
    ('BGN', 'الليف البلغاري', 'Bulgarian Lev', 'лв', 'FOREIGN', 'بلغاريا', 2, '0.394000'),
    ('UAH', 'الهريفنيا الأوكرانية', 'Ukrainian Hryvnia', '₴', 'FOREIGN', 'أوكرانيا', 2, '0.017000'),
    ('ZAR', 'الراند الجنوب أفريقي', 'South African Rand', 'R', 'FOREIGN', 'جنوب أفريقيا', 2, '0.038000'),
    ('BRL', 'الريال البرازيلي', 'Brazilian Real', 'R$', 'FOREIGN', 'البرازيل', 2, '0.130000'),
    ('MXN', 'البيزو المكسيكي', 'Mexican Peso', '$', 'FOREIGN', 'المكسيك', 2, '0.036000'),
    ('ARS', 'البيزو الأرجنتيني', 'Argentine Peso', '$', 'FOREIGN', 'الأرجنتين', 2, '0.000800'),
    ('CLP', 'البيزو التشيلي', 'Chilean Peso', '$', 'FOREIGN', 'تشيلي', 0, '0.000740'),
    ('COP', 'البيزو الكولومبي', 'Colombian Peso', '$', 'FOREIGN', 'كولومبيا', 2, '0.000180'),
    ('PEN', 'السول البيروفي', 'Peruvian Sol', 'S/', 'FOREIGN', 'بيرو', 2, '0.190000'),
    ('NGN', 'النيرة النيجيرية', 'Nigerian Naira', '₦', 'FOREIGN', 'نيجيريا', 2, '0.000450'),
    ('KES', 'الشلن الكيني', 'Kenyan Shilling', 'KSh', 'FOREIGN', 'كينيا', 2, '0.005500'),
    ('ETB', 'البير الإثيوبي', 'Ethiopian Birr', 'Br', 'FOREIGN', 'إثيوبيا', 2, '0.005200'),
    ('GHS', 'السيدي الغاني', 'Ghanaian Cedi', '₵', 'FOREIGN', 'غانا', 2, '0.048000'),
    ('PKR', 'الروبية الباكستانية', 'Pakistani Rupee', '₨', 'FOREIGN', 'باكستان', 2, '0.002500'),
    ('BDT', 'التاكا البنغالية', 'Bangladeshi Taka', '৳', 'FOREIGN', 'بنغلاديش', 2, '0.006000'),
    ('LKR', 'الروبية السريلانكية', 'Sri Lankan Rupee', 'Rs', 'FOREIGN', 'سريلانكا', 2, '0.002300'),
    ('NPR', 'الروبية النيبالية', 'Nepalese Rupee', '₨', 'FOREIGN', 'نيبال', 2, '0.005300'),
    ('IDR', 'الروبية الإندونيسية', 'Indonesian Rupiah', 'Rp', 'FOREIGN', 'إندونيسيا', 2, '0.000044'),
    ('MYR', 'الرينغيت الماليزي', 'Malaysian Ringgit', 'RM', 'FOREIGN', 'ماليزيا', 2, '0.150000'),
    ('THB', 'الباهت التايلاندي', 'Thai Baht', '฿', 'FOREIGN', 'تايلاند', 2, '0.020000'),
    ('SGD', 'الدولار السنغافوري', 'Singapore Dollar', 'S$', 'FOREIGN', 'سنغافورة', 2, '0.530000'),
    ('PHP', 'البيزو الفلبيني', 'Philippine Peso', '₱', 'FOREIGN', 'الفلبين', 2, '0.012500'),
    ('VND', 'الدونغ الفيتنامي', 'Vietnamese Dong', '₫', 'FOREIGN', 'فيتنام', 0, '0.000028'),
    ('KRW', 'الوون الكوري', 'South Korean Won', '₩', 'FOREIGN', 'كوريا الجنوبية', 0, '0.000520'),
    ('HKD', 'دولار هونغ كونغ', 'Hong Kong Dollar', 'HK$', 'FOREIGN', 'هونغ كونغ', 2, '0.091000'),
    ('TWD', 'الدولار التايواني', 'New Taiwan Dollar', 'NT$', 'FOREIGN', 'تايوان', 2, '0.022000'),
    ('NZD', 'الدولار النيوزيلندي', 'New Zealand Dollar', 'NZ$', 'FOREIGN', 'نيوزيلندا', 2, '0.420000'),
    ('IRR', 'الريال الإيراني', 'Iranian Rial', '﷼', 'FOREIGN', 'إيران', 2, '0.0000169'),
    ('AFN', 'الأفغاني', 'Afghan Afghani', '؋', 'FOREIGN', 'أفغانستان', 2, '0.010000'),
    ('KZT', 'التنغي الكازاخي', 'Kazakhstani Tenge', '₸', 'FOREIGN', 'كازاخستان', 2, '0.001400'),
    ('AZN', 'المانات الأذربيجاني', 'Azerbaijani Manat', '₼', 'FOREIGN', 'أذربيجان', 2, '0.417000'),
    ('GEL', 'اللاري الجورجي', 'Georgian Lari', '₾', 'FOREIGN', 'جورجيا', 2, '0.260000'),
    ('AMD', 'الدرام الأرميني', 'Armenian Dram', '֏', 'FOREIGN', 'أرمينيا', 2, '0.001800'),
    # ===== دولية / مرجعية =====
    ('XDR', 'حقوق السحب الخاصة', 'IMF Special Drawing Rights', 'XDR', 'INTL', 'صندوق النقد الدولي', 2, '0.940000'),
    ('XAU', 'أونصة الذهب', 'Gold Ounce', 'XAU', 'INTL', 'عالمي', 2, '1700.000000'),
    ('XAG', 'أونصة الفضة', 'Silver Ounce', 'XAG', 'INTL', 'عالمي', 2, '20.000000'),
]


UNITS = [
    # (code, name_ar, name_en, symbol, category, factor_to_base, base_unit_code)
    # ===== الوزن / الكتلة (الأساس: غرام G) =====
    ('G', 'غرام', 'Gram', 'g', 'WEIGHT', '1', 'G'),
    ('MG', 'مليغرام', 'Milligram', 'mg', 'WEIGHT', '0.001', 'G'),
    ('KG', 'كيلوغرام', 'Kilogram', 'kg', 'WEIGHT', '1000', 'G'),
    ('T', 'طن متري', 'Metric Ton', 't', 'WEIGHT', '1000000', 'G'),
    ('QT_M', 'قنطار متري', 'Metric Quintal', 'q', 'WEIGHT', '100000', 'G'),
    ('LB', 'رطل', 'Pound', 'lb', 'WEIGHT', '453.59237', 'G'),
    ('OZ', 'أونصة', 'Ounce', 'oz', 'WEIGHT', '28.349523125', 'G'),
    # ===== الحجم (الأساس: لتر L) =====
    ('L', 'لتر', 'Liter', 'L', 'VOLUME', '1', 'L'),
    ('ML', 'مليلتر', 'Milliliter', 'mL', 'VOLUME', '0.001', 'L'),
    ('M3', 'متر مكعب', 'Cubic Meter', 'm³', 'VOLUME', '1000', 'L'),
    ('GAL_US', 'غالون أمريكي', 'US Gallon', 'gal', 'VOLUME', '3.785411784', 'L'),
    ('GAL_UK', 'غالون بريطاني', 'Imperial Gallon', 'gal', 'VOLUME', '4.54609', 'L'),
    ('QT_US', 'كوارت أمريكي', 'US Quart', 'qt', 'VOLUME', '0.946352946', 'L'),
    ('PT_US', 'باينت أمريكي', 'US Pint', 'pt', 'VOLUME', '0.473176473', 'L'),
    ('FLOZ_US', 'أونصة سائلة', 'US Fluid Ounce', 'fl oz', 'VOLUME', '0.0295735296', 'L'),
    ('BBL', 'برميل نفط', 'Oil Barrel', 'bbl', 'VOLUME', '158.987294928', 'L'),
    # ===== الطول (الأساس: متر M) =====
    ('M', 'متر', 'Meter', 'm', 'LENGTH', '1', 'M'),
    ('MM', 'مليمتر', 'Millimeter', 'mm', 'LENGTH', '0.001', 'M'),
    ('CM', 'سنتيمتر', 'Centimeter', 'cm', 'LENGTH', '0.01', 'M'),
    ('KM', 'كيلومتر', 'Kilometer', 'km', 'LENGTH', '1000', 'M'),
    ('IN', 'إنش', 'Inch', 'in', 'LENGTH', '0.0254', 'M'),
    ('FT', 'قدم', 'Foot', 'ft', 'LENGTH', '0.3048', 'M'),
    ('YD', 'ياردة', 'Yard', 'yd', 'LENGTH', '0.9144', 'M'),
    ('MI', 'ميل', 'Mile', 'mi', 'LENGTH', '1609.344', 'M'),
    ('NM', 'ميل بحري', 'Nautical Mile', 'nmi', 'LENGTH', '1852', 'M'),
    # ===== المساحة (الأساس: متر مربع M2) =====
    ('M2', 'متر مربع', 'Square Meter', 'm²', 'AREA', '1', 'M2'),
    ('CM2', 'سنتيمتر مربع', 'Square Centimeter', 'cm²', 'AREA', '0.0001', 'M2'),
    ('KM2', 'كيلومتر مربع', 'Square Kilometer', 'km²', 'AREA', '1000000', 'M2'),
    ('HA', 'هكتار', 'Hectare', 'ha', 'AREA', '10000', 'M2'),
    ('DU', 'دونم', 'Dunam', 'dunum', 'AREA', '1000', 'M2'),
    ('ACRE', 'إيكر', 'Acre', 'ac', 'AREA', '4046.8564224', 'M2'),
    ('FT2', 'قدم مربع', 'Square Foot', 'ft²', 'AREA', '0.09290304', 'M2'),
    ('YD2', 'ياردة مربعة', 'Square Yard', 'yd²', 'AREA', '0.83612736', 'M2'),
    # ===== الزمن (الأساس: ثانية S) =====
    ('S', 'ثانية', 'Second', 's', 'TIME', '1', 'S'),
    ('MIN', 'دقيقة', 'Minute', 'min', 'TIME', '60', 'S'),
    ('H', 'ساعة', 'Hour', 'h', 'TIME', '3600', 'S'),
    ('DAY', 'يوم', 'Day', 'd', 'TIME', '86400', 'S'),
    ('WEEK', 'أسبوع', 'Week', 'wk', 'TIME', '604800', 'S'),
    ('MONTH', 'شهر (30 يوم)', 'Month (30 days)', 'mo', 'TIME', '2592000', 'S'),
    ('YEAR', 'سنة (365 يوم)', 'Year (365 days)', 'yr', 'TIME', '31536000', 'S'),
    # ===== درجة الحرارة (الأساس: مئوية C) =====
    ('C', 'درجة مئوية', 'Celsius', '°C', 'TEMPERATURE', '1', 'C'),
    ('F', 'درجة فهرنهايت', 'Fahrenheit', '°F', 'TEMPERATURE', '1', 'C'),
    ('K', 'كلفن', 'Kelvin', 'K', 'TEMPERATURE', '1', 'C'),
    # ===== العدد (الأساس: قطعة PCS) =====
    ('PCS', 'قطعة', 'Piece', 'pc', 'QUANTITY', '1', 'PCS'),
    ('PAIR', 'زوج', 'Pair', 'pr', 'QUANTITY', '2', 'PCS'),
    ('DOZ', 'دستة', 'Dozen', 'doz', 'QUANTITY', '12', 'PCS'),
    ('GROSS', 'غروسة (144)', 'Gross', 'gr', 'QUANTITY', '144', 'PCS'),
    ('HUNDRED', 'مئة', 'Hundred', 'c', 'QUANTITY', '100', 'PCS'),
    ('THOUSAND', 'ألف', 'Thousand', 'M', 'QUANTITY', '1000', 'PCS'),
    # ===== وحدات التغليف (الأساس: قطعة PCS) =====
    ('PACK', 'عبوة', 'Pack', 'pk', 'PACKAGING', '1', 'PCS'),
    ('BOX', 'صندوق', 'Box', 'box', 'PACKAGING', '1', 'PCS'),
    ('CARTON', 'كرتون', 'Carton', 'ctn', 'PACKAGING', '1', 'PCS'),
    ('BAG', 'كيس', 'Bag', 'bag', 'PACKAGING', '1', 'PCS'),
    ('SACK', 'جوال / خيش', 'Sack', 'sack', 'PACKAGING', '1', 'PCS'),
    ('CRATE', 'قفص / تسنيم', 'Crate', 'crate', 'PACKAGING', '1', 'PCS'),
    ('TRAY', 'صينية', 'Tray', 'tray', 'PACKAGING', '1', 'PCS'),
    ('BUNDLE', 'ربطة', 'Bundle', 'bdl', 'PACKAGING', '1', 'PCS'),
    ('PALLET', 'طبلية', 'Pallet', 'plt', 'PACKAGING', '1', 'PCS'),
    ('BIN', 'حاوية / بن', 'Bin', 'bin', 'PACKAGING', '1', 'PCS'),
    # ===== السرعة (الأساس: كم/ساعة KMH) =====
    ('KMH', 'كيلومتر/ساعة', 'Kilometer per Hour', 'km/h', 'SPEED', '1', 'KMH'),
    ('MPH', 'ميل/ساعة', 'Mile per Hour', 'mph', 'SPEED', '1.609344', 'KMH'),
    ('MS', 'متر/ثانية', 'Meter per Second', 'm/s', 'SPEED', '3.6', 'KMH'),
    ('KNOT', 'عقدة', 'Knot', 'kn', 'SPEED', '1.852', 'KMH'),
    # ===== أخرى =====
    ('KWH', 'كيلوواط ساعة', 'Kilowatt Hour', 'kWh', 'OTHER', '1', ''),
    ('HP', 'حصان', 'Horsepower', 'hp', 'OTHER', '1', ''),
    ('BAR', 'بار', 'Bar', 'bar', 'OTHER', '1', ''),
    ('PSI', 'رطل/إنش مربع', 'Pound per Square Inch', 'psi', 'OTHER', '0.0689475729', ''),
    ('CAL', 'سعرة حرارية', 'Calorie', 'cal', 'OTHER', '1', ''),
    ('KCAL', 'كيلو سعرة حرارية', 'Kilocalorie', 'kcal', 'OTHER', '1000', ''),
]


def seed(apps, schema_editor):
    Currency = apps.get_model('core', 'Currency')
    UnitOfMeasure = apps.get_model('core', 'UnitOfMeasure')

    Currency.objects.bulk_create(
        [
            Currency(
                code=code, name_ar=name_ar, name_en=name_en, symbol=symbol,
                region=region, country=country, decimal_places=decimals,
                exchange_rate_to_jod=Decimal(rate), is_active=True,
                notes='سعر الصرف تقديري ويدوي — حدّثه من الشاشة عند الحاجة.',
            )
            for code, name_ar, name_en, symbol, region, country, decimals, rate in CURRENCIES
        ],
        ignore_conflicts=True,
    )

    UnitOfMeasure.objects.bulk_create(
        [
            UnitOfMeasure(
                code=code, name_ar=name_ar, name_en=name_en, symbol=symbol,
                category=category, factor_to_base=Decimal(factor),
                base_unit_code=base, is_active=True,
                notes=(
                    'تحويل غير خطّي: F = C×9/5+32' if code == 'F'
                    else 'تحويل غير خطّي: K = C+273.15' if code == 'K'
                    else ''
                ),
            )
            for code, name_ar, name_en, symbol, category, factor, base in UNITS
        ],
        ignore_conflicts=True,
    )


def unseed(apps, schema_editor):
    Currency = apps.get_model('core', 'Currency')
    UnitOfMeasure = apps.get_model('core', 'UnitOfMeasure')
    Currency.objects.filter(code__in=[c[0] for c in CURRENCIES]).delete()
    UnitOfMeasure.objects.filter(code__in=[u[0] for u in UNITS]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0006_currency_unitofmeasure'),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
