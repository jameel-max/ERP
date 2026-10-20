from django.urls import path
from . import views

urlpatterns = [
    path('workshops/', views.workshop_list, name='workshop_list'),
    path('workshops/create/', views.workshop_create, name='workshop_create'),
    path('workshops/<int:pk>/', views.workshop_detail, name='workshop_detail'),
    path('workshops/record-daily/', views.record_daily_labor, name='record_daily_labor'),
    path('workshops/cost-report/', views.labor_cost_report, name='labor_cost_report'),

    # الموظفون (رواتب شهرية) — قسم مستقل عن الورش
    path('employees/', views.employee_list, name='employee_list'),
    path('employees/create/', views.employee_create, name='employee_create'),
    path('employees/salary-report/', views.salary_report, name='salary_report'),
    path('employees/salary-pay/', views.employee_salary_pay, name='employee_salary_pay'),
    path('employees/salary/<int:pk>/delete/', views.employee_salary_delete, name='employee_salary_delete'),
    path('employees/<int:pk>/', views.employee_detail, name='employee_detail'),
    path('employees/<int:pk>/edit/', views.employee_edit, name='employee_edit'),
]
