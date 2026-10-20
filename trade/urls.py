from django.urls import path

from . import views

urlpatterns = [
    path('trade/', views.shipment_list, name='trade_shipment_list'),
    path('trade/create/', views.shipment_create, name='trade_shipment_create'),
    path('trade/<int:pk>/', views.shipment_detail, name='trade_shipment_detail'),
    path('trade/<int:pk>/edit/', views.shipment_edit, name='trade_shipment_edit'),
    path('trade/<int:pk>/costs/add/', views.shipment_cost_create, name='trade_cost_create'),
    path('trade/<int:pk>/payments/add/', views.shipment_payment_create, name='trade_payment_create'),
    path('trade/<int:pk>/documents/add/', views.shipment_document_create, name='trade_document_create'),
    path('trade/<int:pk>/status/', views.shipment_status_update, name='trade_status_update'),
]
