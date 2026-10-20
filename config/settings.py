"""
Django settings for config project.
Royal Fruit Packhouse ERP System
"""

import os
import sys
from pathlib import Path
from urllib.parse import urlparse as _urlparse, parse_qs as _parse_qs

BASE_DIR = Path(__file__).resolve().parent.parent

# تحميل بسيط لملف .env في جذر المشروع (بدون مكتبات إضافية).
# لا يتجاوز المتغيرات الموجودة في نظام التشغيل (setdefault).
_env_path = BASE_DIR / '.env'
if _env_path.exists():
    for _line in _env_path.read_text(encoding='utf-8').splitlines():
        _line = _line.strip()
        if _line and not _line.startswith('#') and '=' in _line:
            _key, _value = _line.split('=', 1)
            os.environ.setdefault(_key.strip(), _value.strip().strip('"').strip("'"))

SECRET_KEY = os.environ.get('SECRET_KEY', 'django-insecure-royal-fruit-packhouse-secret-key-2026')

# قابل للضبط للنشر: افتراضياً True للتطوير. للإنتاج اضبط DJANGO_DEBUG=False
DEBUG = os.environ.get('DJANGO_DEBUG', 'True').strip().lower() in ('1', 'true', 'yes', 'on')

# اتركه '*' للتطوير، وفي الإنتاج اضبط DJANGO_ALLOWED_HOSTS=example.com,www.example.com
ALLOWED_HOSTS = [h.strip() for h in os.environ.get('DJANGO_ALLOWED_HOSTS', '*').split(',') if h.strip()]
# إعدادات أمان للإنتاج (تُفعّل تلقائياً عند DEBUG=False)
if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    SECURE_SSL_REDIRECT = os.environ.get('SECURE_SSL_REDIRECT', 'True').strip().lower() in ('1', 'true', 'yes', 'on')
    SECURE_HSTS_SECONDS = int(os.environ.get('SECURE_HSTS_SECONDS', '31536000'))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_REFERRER_POLICY = 'strict-origin-when-cross-origin'
    X_FRAME_OPTIONS = 'DENY'

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    
    # Internal apps
    'core.apps.CoreConfig',
    'operations.apps.OperationsConfig',
    'invoicing.apps.InvoicingConfig',
    'packing.apps.PackingConfig',
    'boxes.apps.BoxesConfig',
    'labor.apps.LaborConfig',
    'finance.apps.FinanceConfig',
    'analytics.apps.AnalyticsConfig',
    'trade.apps.TradeConfig',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'core.context_processors.system_context',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

# Database configuration:
# 1) DATABASE_URL  (Neon / أي PostgreSQL مُدار) - الأفضل والأبسط
# 2) DB_ENGINE=postgresql مع DB_HOST/DB_NAME/...
# 3) SQLite افتراضياً للتطوير المحلي

DATABASE_URL = os.environ.get('DATABASE_URL', '').strip()


def _database_from_url(url):
    """
    يحوّل رابط الاتصال (مثال Neon):
      postgresql://user:pass@ep-xxx.aws.neon.tech/dbname?sslmode=require
    إلى إعدادات Django. يفرض sslmode=require إذا لم يُحدد (Neon يتطلب SSL).
    """
    parsed = _urlparse(url)
    if parsed.scheme not in ('postgres', 'postgresql'):
        raise ValueError(
            'DATABASE_URL غير صالح: يجب أن يبدأ بـ postgresql:// أو postgres://'
        )

    query = _parse_qs(parsed.query)
    sslmode = (query.get('sslmode') or ['require'])[0]

    name = (parsed.path or '').lstrip('/')
    if not name:
        raise ValueError('DATABASE_URL يجب أن يحتوي على اسم قاعدة البيانات بعد اسم المضيف.')

    return {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': name,
        'USER': parsed.username or '',
        'PASSWORD': parsed.password or '',
        'HOST': parsed.hostname or '',
        'PORT': str(parsed.port or 5432),
        'OPTIONS': {'sslmode': sslmode},
        # نترك الاتصالات مفتوحة قليلاً لتقليل كلفة الاتصال بقاعدة Neon
        'CONN_MAX_AGE': int(os.environ.get('DB_CONN_MAX_AGE', '60')),
        'CONN_HEALTH_CHECKS': True,
    }


if DATABASE_URL:
    DATABASES = {'default': _database_from_url(DATABASE_URL)}
else:
    DB_ENGINE = os.environ.get('DB_ENGINE', 'sqlite')
    if DB_ENGINE == 'postgresql':
        DATABASES = {
            'default': {
                'ENGINE': 'django.db.backends.postgresql',
                'NAME': os.environ.get('DB_NAME', 'royal_fruit_db'),
                'USER': os.environ.get('DB_USER', 'postgres'),
                'PASSWORD': os.environ.get('DB_PASSWORD', 'postgres'),
                'HOST': os.environ.get('DB_HOST', 'localhost'),
                'PORT': os.environ.get('DB_PORT', '5432'),
                'CONN_MAX_AGE': int(os.environ.get('DB_CONN_MAX_AGE', '60')),
                'CONN_HEALTH_CHECKS': True,
            }
        }
    else:
        DATABASES = {
            'default': {
                'ENGINE': 'django.db.backends.sqlite3',
                'NAME': BASE_DIR / 'db.sqlite3',
            }
        }

# ---------------------------------------------------------------------------
# أثناء الاختبارات (manage.py test): نستخدم قاعدة محلية معزولة وسريعة
# حتى لا تلمس الاختبارات قاعدة Neon الحقيقية ولا تُبطئها.
# للتشغيل على نفس قاعدة البيانات المُهيّأة (Neon): TEST_ON_CONFIGURED_DB=1
# ---------------------------------------------------------------------------
RUNNING_TESTS = len(sys.argv) > 1 and sys.argv[1] == 'test'
if RUNNING_TESTS and os.environ.get('TEST_ON_CONFIGURED_DB') != '1':
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }

AUTH_USER_MODEL = 'core.User'

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'ar'
TIME_ZONE = 'Asia/Amman'
USE_I18N = True
USE_TZ = True

# ---------------------------------------------------------------------------
# عرض الأرقام (مهم للوضوح المالي):
# لغة «ar» الافتراضية في Django تستخدم الفاصلة «,» كفاصلة عشرية، وهذا يجعل
# المبلغ 80 ديناراً يظهر «80,000» فيُقرأ خطأً كثمانين ألفاً. لذلك نفرض:
#   - الفاصلة العشرية «.»   → 80.000
#   - فاصلة الآلاف «,» مع تجميع كل 3 خانات → 80,000.000
# وحدة التنسيق مُصغّرة في config/formats/ar/formats.py.
# ---------------------------------------------------------------------------
USE_THOUSAND_SEPARATOR = True
NUMBER_GROUPING = 3
FORMAT_MODULE_PATH = ['config.formats']

STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'

# تقديم الملفات الساكنة في الإنتاج عبر WhiteNoise (اختياري - يعمل فقط إذا كانت الحزمة مثبتة)
try:
    import whitenoise  # noqa: F401
    MIDDLEWARE.insert(1, 'whitenoise.middleware.WhiteNoiseMiddleware')
    STORAGES = {
        'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
        'staticfiles': {'BACKEND': 'whitenoise.storage.CompressedStaticFilesStorage'},
    }
except ImportError:
    pass

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

LOGIN_URL = 'login'
LOGIN_REDIRECT_URL = 'home'
LOGOUT_REDIRECT_URL = 'login'
