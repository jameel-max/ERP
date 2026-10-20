from django.urls import path
from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path('login/', views.user_login, name='login'),
    path('logout/', views.user_logout, name='logout'),
    path('notifications/', views.notification_list, name='notifications'),
    path('notifications/<int:pk>/resolve/', views.notification_resolve, name='notification_resolve'),
    path('settings/', views.system_settings, name='system_settings'),
    path('audit-logs/', views.audit_logs, name='audit_logs'),
    path('database-wipe/', views.database_wipe, name='database_wipe'),
    path('delete/<str:app_label>/<str:model_name>/<int:pk>/', views.generic_delete, name='generic_delete'),
    # نظام إرفاق الصور العام (عدة صور لكل سجل)
    path('attachments/upload/<str:app_label>/<str:model_name>/<int:pk>/', views.attachments_upload, name='attachments_upload'),
    path('attachments/<int:pk>/delete/', views.attachment_delete, name='attachment_delete'),
]
