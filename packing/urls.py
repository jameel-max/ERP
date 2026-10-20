from django.urls import path
from . import views

urlpatterns = [
    path('packing/', views.packing_list, name='packing_list'),
    path('packing/start/', views.packing_start, name='packing_start'),
    path('packing/sessions/<int:pk>/', views.packing_detail, name='packing_detail'),
    path('packing/sessions/<int:pk>/finalize/', views.packing_finalize, name='packing_finalize'),
    path('packing/sessions/<int:pk>/approve/', views.packing_manager_approve, name='packing_manager_approve'),

    # الطبالي الموضبة
    path('packing/pallets/', views.packed_pallets_list, name='packed_pallets_list'),
    path('packing/pallets/<int:pk>/print/', views.packed_pallet_print_label, name='packed_pallet_print_label'),
    path('packing/pallets/qr/<str:packed_pallet_code>/', views.packed_pallet_qr_scan, name='packed_pallet_qr_scan'),

    # خدمة التبريد اليومية للطبالي الموضبة
    path('packing/pallets/cooling-in/', views.packed_pallet_cooling_in, name='packed_pallet_cooling_in'),
    path('packing/pallets/cooling-out/', views.packed_pallet_cooling_out, name='packed_pallet_cooling_out'),
    path('packing/pallets/<int:pk>/cooling-history/', views.packed_pallet_cooling_history, name='packed_pallet_cooling_history'),
    path('packing/pallets/<int:pk>/move/', views.packed_pallet_move, name='packed_pallet_move'),
    path('packing/cooling-statement/', views.cooling_statement, name='cooling_statement'),

    # بيانات التسليم
    path('delivery-notes/', views.delivery_notes_list, name='delivery_notes_list'),
    path('delivery-notes/create/', views.delivery_note_create, name='delivery_note_create'),
    path('delivery-notes/<int:pk>/', views.delivery_note_detail, name='delivery_note_detail'),
]
