from django.urls import path
from . import views

urlpatterns = [
    # الخزينة
    path('treasury/', views.treasury_dashboard, name='treasury_dashboard'),
    path('treasury/create-account/', views.treasury_account_create, name='treasury_account_create'),
    path('treasury/transfer/', views.internal_transfer_create, name='internal_transfer_create'),

    # المقبوضات
    path('payments/', views.payment_list, name='payment_list'),
    path('payments/create/', views.payment_create, name='payment_create'),

    # المصاريف
    path('expenses/', views.expense_list, name='expense_list'),
    path('expenses/create/', views.expense_create, name='expense_create'),
    path('expenses/<int:pk>/approve/', views.expense_approve, name='expense_approve'),
    path('expenses/<int:pk>/cancel/', views.expense_cancel, name='expense_cancel'),

    # تصنيفات المصاريف
    path('expense-categories/', views.expense_category_list, name='expense_category_list'),
    path('expense-categories/create/', views.expense_category_create, name='expense_category_create'),
    path('expense-categories/quick-add/', views.expense_category_quick_add, name='expense_category_quick_add'),
    path('expense-categories/<int:pk>/edit/', views.expense_category_edit, name='expense_category_edit'),

    # الشيكات
    path('checks/', views.check_list, name='check_list'),
    path('checks/create/', views.check_create, name='check_create'),
    path('checks/<int:pk>/collect/', views.check_collect, name='check_collect'),
    path('checks/<int:pk>/return/', views.check_return, name='check_return'),

    # كشف حساب التاجر
    path('traders/<int:trader_id>/statement/', views.trader_financial_statement, name='trader_financial_statement'),
]
