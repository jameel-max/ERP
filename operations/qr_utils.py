import io
import base64
import json
import qrcode
from qrcode.image.pil import PilImage
from django.urls import reverse


def build_scan_url(request, url_name, *args):
    """
    الرابط المطلق الذي يُشفَّر داخل رمز QR ليفتح صفحة بيانات الطبلية عند المسح.

    الأولوية: (عنوان السيرفر لروابط مسح QR) من الإعدادات المركزية إن كان مُدخلاً،
    وإلا عنوان الطلب الحالي (المضيف والمنفذ الذي تفتح منه الصفحة).
    """
    from core.models import SystemSetting

    base = (SystemSetting.get_settings().scan_base_url or '').strip().rstrip('/')
    path = reverse(url_name, args=args)
    if base:
        return f"{base}{path}"
    return request.build_absolute_uri(path)


def generate_qr_base64(data_dict_or_str):
    """
    توليد صورة QR بصيغة Base64 للعرض المباشر في قوالب HTML وملصقات الطبالي
    """
    if isinstance(data_dict_or_str, dict):
        # نص منسق أو JSON
        payload = json.dumps(data_dict_or_str, ensure_ascii=False, indent=2)
    else:
        payload = str(data_dict_or_str)

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=2,
    )
    qr.add_data(payload)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    b64_str = base64.b64encode(buffer.getvalue()).decode('utf-8')
    return f"data:image/png;base64,{b64_str}"
