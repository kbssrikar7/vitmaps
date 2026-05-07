# urls.py
from django.urls import path
from . import views, api_views

urlpatterns = [
    path('', views.allmaps_view, name='allmaps'),
    path('user_map_auth/', views.user_map_auth_view, name='user_map_auth'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('register/', views.register_view, name='register'),
   # path('map/', views.map_view, name='map'),

    # APIs
    path('api/update/', api_views.update_location),
    path('api/latest/<str:device_id>/', api_views.latest_location),
]