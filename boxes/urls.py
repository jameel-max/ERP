from django.urls import path
from . import views

urlpatterns = [
    path('boxes/dashboard/', views.boxes_dashboard, name='boxes_dashboard'),
    path('boxes/traders/<int:trader_id>/', views.trader_boxes_statement, name='trader_boxes_statement'),
    path('boxes/record-movement/', views.record_box_movement, name='record_box_movement'),
    path('boxes/movements/<int:pk>/reverse/', views.reverse_box_movement, name='reverse_box_movement'),

    # القسم المستقل: الصناديق الواردة من المزرعة
    path('farm-boxes/', views.farm_boxes_dashboard, name='farm_boxes_dashboard'),
    path('farm-boxes/types/create/', views.farm_box_type_create, name='farm_box_type_create'),
    path('farm-boxes/types/<int:pk>/edit/', views.farm_box_type_edit, name='farm_box_type_edit'),
    path('farm-boxes/records/create/', views.farm_box_record_create, name='farm_box_record_create'),
    path('farm-boxes/records/create/<int:truck_pk>/', views.farm_box_record_create, name='farm_box_record_create_for_truck'),
    path('farm-boxes/records/<int:pk>/edit/', views.farm_box_record_edit, name='farm_box_record_edit'),

    # قسم تسليم الفارغ: إرجاع إلى المزرعة أو بيعه لمنظمة/جهة
    path('empty-boxes/', views.empty_box_delivery_list, name='empty_box_delivery_list'),
    path('empty-boxes/recipients/create/', views.empty_box_recipient_create, name='empty_box_recipient_create'),
    path('empty-boxes/recipients/<int:pk>/edit/', views.empty_box_recipient_edit, name='empty_box_recipient_edit'),
    path('empty-boxes/deliveries/create/', views.empty_box_delivery_create, name='empty_box_delivery_create'),
    path('empty-boxes/deliveries/<int:pk>/', views.empty_box_delivery_detail, name='empty_box_delivery_detail'),
    path('empty-boxes/deliveries/<int:pk>/edit/', views.empty_box_delivery_edit, name='empty_box_delivery_edit'),
    path('empty-boxes/deliveries/<int:pk>/confirm/', views.empty_box_delivery_confirm, name='empty_box_delivery_confirm'),
]
