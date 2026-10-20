from django.urls import path
from . import views

urlpatterns = [
    # التجار
    path('traders/', views.trader_list, name='trader_list'),
    path('traders/create/', views.trader_create, name='trader_create'),
    path('traders/<int:pk>/', views.trader_detail, name='trader_detail'),
    path('traders/<int:pk>/add-rate/', views.trader_add_rate, name='trader_add_rate'),

    # السيارات والوصول
    path('trucks/', views.truck_list, name='trucks_list'),
    path('trucks/', views.truck_list, name='truck_list'),
    path('trucks/create/', views.truck_create, name='truck_create'),
    path('trucks/<int:pk>/', views.truck_detail, name='truck_detail'),
    path('trucks/<int:pk>/edit/', views.truck_edit, name='truck_edit'),
    path('trucks/<int:pk>/print-labels/', views.truck_print_all_labels, name='truck_print_all_labels'),

    # الطبالي والـ QR
    path('pallets/<int:pk>/', views.pallet_detail, name='pallet_detail'),
    path('pallets/<int:pk>/print-label/', views.pallet_print_label, name='pallet_print_label'),
    path('pallets/<int:pk>/transfer/', views.pallet_transfer, name='pallet_transfer'),
    path('pallets/<int:pk>/move/', views.pallet_move, name='pallet_move'),
    path('pallet/qr-scan/<str:pallet_code>/', views.pallet_qr_scan, name='pallet_qr_scan'),

    # البرادات والأسياخ
    path('cold-stores/', views.cold_store_list, name='cold_store_list'),
    path('cold-stores/create/', views.cold_store_create, name='cold_store_create'),
    path('cold-stores/<int:store_pk>/add-shelf/', views.shelf_create, name='shelf_create'),
    path('cold-stores/temperature-log/', views.cold_store_temperature_log, name='cold_store_temperature_log'),

    # أنواع الصناديق
    path('box-types/', views.box_type_list, name='box_type_list'),
    path('box-types/create/', views.box_type_create, name='box_type_create'),
    path('box-types/<int:pk>/edit/', views.box_type_edit, name='box_type_edit'),

    # موارد التغليف (مجموعات + أصناف)
    path('packaging-resources/', views.packaging_resources, name='packaging_resources'),
    path('packaging-resources/groups/create/', views.packaging_group_create, name='packaging_group_create'),
    path('packaging-resources/groups/<int:pk>/edit/', views.packaging_group_edit, name='packaging_group_edit'),
    path('packaging-resources/groups/<int:group_pk>/items/create/', views.packaging_item_create, name='packaging_item_create'),
    path('packaging-resources/items/<int:pk>/edit/', views.packaging_item_edit, name='packaging_item_edit'),

    # أنواع البضاعة
    path('crops/', views.crop_list, name='crop_list'),
    path('crops/create/', views.crop_create, name='crop_create'),
    path('crops/<int:pk>/edit/', views.crop_edit, name='crop_edit'),

    # استيراد CSV
    path('import-csv/', views.import_csv_view, name='import_csv'),
]
