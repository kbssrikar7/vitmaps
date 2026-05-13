# tracking_app/urls.py
from django.urls import path
from . import views, api_views

# Defining URL patterns for the tracking application
urlpatterns = [
    # PUBLIC ROUTES
    # Home page displaying public vessel data
    path('', views.allmaps_view, name='allmaps'),
    
    # Chrome DevTools specific protocol configuration
    path('.well-known/appspecific/com.chrome.devtools.json', views.chrome_devtools_config),
    
    # PRIVATE ROUTES (Requires Login)
    # Main authenticated map view for private fleets
    path('user_map_auth/', views.user_map_auth_view, name='user_map_auth'),
    
    # Incremental JSON endpoint for live 30s updates
    path('user_map_auth/vessel_data_json/', views.vessel_data_json, name='vessel_data_json'),
    
    # AUTHENTICATION ROUTES
    # API-based login system
    path('login/', views.login_view, name='login'),
    
    # Standard logout
    path('logout/', views.logout_view, name='logout'),
    
    # User registration
    path('register/', views.register_view, name='register'),

    # REST API ENDPOINTS (For mobile/device updates)
    # Update current location for a device
    path('api/update/', api_views.update_location),
    
    # Fetch the most recent location for a specific device
    path('api/latest/<str:device_id>/', api_views.latest_location),
]
