from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('core.urls')),
    path('', include('operations.urls')),
    path('', include('invoicing.urls')),
    path('', include('packing.urls')),
    path('', include('boxes.urls')),
    path('', include('labor.urls')),
    path('', include('finance.urls')),
    path('', include('analytics.urls')),
    path('', include('trade.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
