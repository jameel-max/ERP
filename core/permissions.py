from functools import wraps
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.contrib import messages


def _login_url(request):
    from django.urls import reverse
    return reverse('login') + '?next=' + request.path


def _deny(request, message, hint):
    """
    يعرض رسالة خطأ واضحة + الخطوة التالية المطلوبة، ثم يمنع العملية.
    القاعدة: لا نترك المستخدم تائهاً — نقول له دائماً ماذا يفعل لاحقاً.
    """
    messages.error(request, f"{message} | الخطوة التالية: {hint}")
    raise PermissionDenied(message)


def owner_required(view_func):
    """للمالك فقط: كل الإجراءات والموافقات النهائية."""
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(_login_url(request))
        if not request.user.is_owner:
            _deny(
                request,
                'هذا الإجراء من صلاحيات مالك النظام (شخص واحد مسؤول عن كل الإجراءات والموافقات).',
                'سجّل الدخول بحساب المالك، أو اطلب من المالك تنفيذ هذا الإجراء والموافقة عليه.',
            )
        return view_func(request, *args, **kwargs)
    return _wrapped_view


def manager_required(view_func):
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(_login_url(request))
        if not request.user.is_manager:
            _deny(
                request,
                'عذراً، هذه العملية تتطلب صلاحية مدير للنظام.',
                'سجّل الدخول بحساب المالك أو المدير، أو اطلب من المالك اعتماد هذا الإجراء.',
            )
        return view_func(request, *args, **kwargs)
    return _wrapped_view


def accountant_required(view_func):
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(_login_url(request))
        if not request.user.is_accountant:
            _deny(
                request,
                'عذراً، هذه العملية تتطلب صلاحية محاسب أو مدير.',
                'سجّل الدخول بحساب المالك أو المدير أو المحاسب لتنفيذ هذا الإجراء.',
            )
        return view_func(request, *args, **kwargs)
    return _wrapped_view


def write_required(view_func):
    """
    منع المستخدم بصلاحية 'مشاهد' من إجراء أي إضافة أو تعديل أو حذف
    """
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(_login_url(request))
        if request.user.role == 'VIEWER' and request.method in ['POST', 'PUT', 'DELETE']:
            _deny(
                request,
                'صلاحية المشاهد للعرض فقط ولا يمكن إجراء تعديلات.',
                'سجّل الدخول بحساب المالك أو المدير أو المحاسب لتنفيذ التعديلات.',
            )
        return view_func(request, *args, **kwargs)
    return _wrapped_view
