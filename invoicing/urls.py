from django.urls import path
from . import views

urlpatterns = [
    path('invoices/', views.invoice_list, name='invoice_list'),
    path('invoices/create/', views.invoice_create, name='invoice_create'),
    path('invoices/<int:pk>/', views.invoice_detail, name='invoice_detail'),
    path('invoices/<int:pk>/link-trucks/', views.invoice_link_trucks, name='invoice_link_trucks'),
    path('invoices/<int:pk>/approve/', views.invoice_approve, name='invoice_approve'),
    path('invoices/<int:pk>/sync/', views.invoice_sync, name='invoice_sync'),
    path('invoices/<int:pk>/pdf/', views.invoice_pdf, name='invoice_pdf'),
]
