"""
سجل «الحذف المنفرد»: يحدّد الأنواع المسموح حذفها من الشاشات.

لماذا سجل صريح بدل الحذف العشوائي لأي موديل؟
- الأمان: لا يمكن للمستخدم حذف موديلات حسّاسة (المستخدمون، إعدادات النظام، سجل التدقيق).
- الوضوح: كل نوع مسجّل هنا يظهر له زر حذف منفرد في واجهته.
"""

DELETE_ALLOWED = {
    'operations': {
        'trader',
        'crop',
        'packaginggroup',
        'boxtype',
        'traderpackingrate',
        'coldstore',
        'shelf',
        'truckreceipt',
        'pallet',
        'palletmovementlog',
        'coldstoretemperaturelog',
    },
    'boxes': {
        'traderboxbalance',
        'farmboxtype',
        'farmboxrecord',
        'emptyboxrecipient',
        'emptyboxdelivery',
        'farmemptyboxconsumption',
    },
    'invoicing': {
        'invoice',
        'invoiceitem',
    },
    'packing': {
        'packingsession',
        'packingoutput',
        'packingwaste',
        'packedpallet',
        'packedpalletcoolinglog',
        'deliverynote',
        'packingoutputpackagingitem',
        'packingsessionpackagingcharge',
    },
    'labor': {
        'workshop',
        'dailylaborlog',
        'employee',
        'employeesalarypayment',
    },
    'finance': {
        'treasuryaccount',
        'expensecategory',
        'expense',
        'check',
        'payment',
    },
    'core': {
        'notification',
        'attachment',
    },
    'trade': {
        'tradeshipment',
        'shipmentcost',
        'shipmentpayment',
        'shipmentdocument',
    },
}


def is_deletable(app_label, model_name):
    return model_name in DELETE_ALLOWED.get(app_label, set())
