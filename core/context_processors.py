from django.db.models import Count, Q

from .models import SystemSetting, Notification

# مدة الكاش الثانية — الإشعارات لا تتغير كل ثانية، والاستعلام يُنفَّذ في كل صفحة
_NOTIFICATION_CACHE_TTL = 20


def system_context(request):
    """
    سياق عام يمرر بيانات الشركة واللون الملكي الأخضر الغامق والإشعارات لجميع القوالب
    استُبدل عدّادات الإشعارات الثلاثة باستعلام واحد، ثم كُرّرت لتجنب استعلامين إضافيين
    في كل صفحة (قاعدة البيانات خارجية ومسافة الشبكة كبيرة ≈170ms لكل استعلام).
    """
    from django.core.cache import cache
    from django.db import transaction

    settings_obj = SystemSetting.get_settings()

    cached = cache.get(Notification._CONTEXT_CACHE_KEY)
    if cached is not None:
        unread_notifications, unread_count, critical_count = cached
    else:
        unread_notifications = list(
            Notification.objects.filter(is_resolved=False).order_by('-created_at')[:10]
        )
        stats = Notification.objects.filter(is_resolved=False).aggregate(
            total=Count('id'),
            critical=Count('id', filter=Q(severity='CRITICAL')),
        )
        unread_count = stats['total'] or 0
        critical_count = stats['critical'] or 0
        # on_commit يمنع تسرّب الكاش بين اختبارات TestCase (تُلغي معاملاتها)
        transaction.on_commit(
            lambda: cache.set(
                Notification._CONTEXT_CACHE_KEY,
                (unread_notifications, unread_count, critical_count),
                _NOTIFICATION_CACHE_TTL,
            )
        )

    return {
        'company_name_en': settings_obj.company_name_en,
        'company_name_ar': settings_obj.company_name_ar,
        'brand_color': settings_obj.brand_color,
        'system_settings': settings_obj,
        'unread_notifications': unread_notifications,
        'unread_notifications_count': unread_count,
        'critical_notifications_count': critical_count,
    }
