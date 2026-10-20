from django.urls import path
from . import views

urlpatterns = [
    path('analytics/dashboard/', views.analytics_dashboard, name='analytics_dashboard'),
    path('analytics/daily-report/', views.manager_daily_report, name='manager_daily_report'),
    path('analytics/daily-report/pdf/', views.manager_daily_pdf, name='manager_daily_pdf'),
    path('analytics/export-excel/', views.export_manager_daily_excel, name='export_manager_daily_excel'),
]
