from django.urls import reverse

import folium
import json
import logging
import os
import requests
import time
from django.conf import settings
from django.shortcuts import render, redirect
from django.template.loader import render_to_string
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm
from django.http import JsonResponse
from django.contrib.auth.models import User

# Standard logger for tracking app events
logger = logging.getLogger(__name__)

# --- GLOBAL MAP CONFIGURATION ---
# Injecting local Bootstrap CSS/JS into Folium's default resource list
# This ensures the map UI components use our project's styling.
folium.Map.default_css = [
    (name, "/static/css/bootstrap.min.css" if name == "bootstrap_css" else url)
    for name, url in folium.Map.default_css
]
folium.Map.default_js = [
    (name, "/static/js/bootstrap.bundle.min.js" if name == "bootstrap" else url)
    for name, url in folium.Map.default_js
]


def chrome_devtools_config(request):
    """
    Endpoint for Chrome DevTools protocol configuration if needed.
    """
    return JsonResponse({})


def allmaps_view(request):
    """
    PUBLIC VIEW: Displays all vessels from the public API on a map.
    Does not require login.
    """
    logger.info("allmaps_view: request started")

    # Step 1: Check session for cached data, otherwise fetch from Public API
    vessels_raw = request.session.get("vessels_raw", [])
    if not vessels_raw:
        api_url = "https://shiptrackingapi-787201059405.asia-south2.run.app/VesselTracking/GetAll"
        try:
            response = requests.get(api_url, timeout=10)
            if response.status_code == 200:
                vessels_raw = response.json()
        except Exception:
            logger.exception("allmaps_view: API fetch failed")
        
        request.session["vessels_raw"] = vessels_raw

    # Step 2: Handle empty data case
    if not vessels_raw:
        return render(request, 'allmaps.html', {'map_html': "No vessel data available."})

    # Step 3: Initialize Folium Map centered on India/Bay of Bengal
    m = folium.Map(location=[17.15, 82.4], zoom_start=6, control_scale=True, zoom_control=False, tiles=None)

    # Step 4: Add multiple Tile Layers (Base Maps)
    folium.TileLayer(tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}", attr="Tiles © Esri", name="Esri World Street Map (English)", show=True).add_to(m)
    folium.TileLayer(tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", attr="Tiles © Esri", name="Esri World Imagery (Satellite)", show=False).add_to(m)
    folium.TileLayer(tiles="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", attr="© OpenStreetMap contributors", name="OpenStreetMap", show=False).add_to(m)
    folium.TileLayer(tiles="https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png", attr="© Carto", name="Carto Light", show=False).add_to(m)
    folium.TileLayer(tiles="https://{s}.basemaps.cartocdn.com/light_nolabels/{z}/{x}/{y}.png", attr="© Carto", name="Light No Labels", show=False).add_to(m)
    folium.TileLayer(tiles="https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png", attr="© Carto", name="Carto Voyager", show=False).add_to(m)

    # Step 5: Add Layer Control for user to switch base maps
    folium.LayerControl(position="bottomright").add_to(m)

    # Step 6: Group raw data into routes by vessel name
    routes = {}
    for v in vessels_raw:
        v_name = v.get("VesselName") or f"Ship {v.get('VesselId') or v.get('Id') or 'Unknown'}"
        # COORDINATE FIX: API provides reversed Lat/Lng. Longitude -> lat, Latitude -> lng.
        lat = float(v.get("Longitude") or v.get("lat") or 17.15)
        lng = float(v.get("Latitude") or v.get("lng") or 82.4)
        
        point = {
            "lat": lat, "lng": lng,
            "Comments": v.get("Comments", "-"), "DateTime": v.get("DateTime", "-"),
            "Speed": v.get("Speed", "-"), "Battery": v.get("Battery", "-"),
            "Fuel1": v.get("Fuel1", "-"), "Fuel2": v.get("Fuel2", "-"),
            "RPM1": v.get("RPM1", "-"), "RPM2": v.get("RPM2", "-"),
            "Eng1RunStatus": "Running" if v.get("Eng1RunStatus") in [1, "1", "Running"] else "Idle",
            "Eng2RunStatus": "Running" if v.get("Eng2RunStatus") in [1, "1", "Running"] else "Idle"
        }
        routes.setdefault(v_name, []).append(point)

    # Step 7: Format data for JavaScript
    vessel_js_array = []
    dark_colors = ["#1a237e", "#b71c1c", "#1b5e20", "#e65100", "#4a148c", "#004d40", "#212121", "#3e2723"]
    for i, (name, path) in enumerate(routes.items()):
        vessel_js_array.append({
            "name": name, "color": dark_colors[i % len(dark_colors)],
            "route": path, "currentIndex": len(path) - 1
        })

    # Step 8: Render JS script template and add to map
    js_code = render_to_string("folium/allmaps_script.html", {"map_name": m.get_name(), "vessels_json": json.dumps(vessel_js_array)})
    m.get_root().html.add_child(folium.Element(js_code))

    # Step 9: Return HTML response
    return render(request, 'allmaps.html', {'map_html': m._repr_html_()})


def login_view(request):
    """
    AUTHENTICATION VIEW: Connects to external API for user validation.
    Stores Bearer Token and UserID in Django Session.
    """
    if request.method == "POST":
        username = request.POST.get("username")
        password = request.POST.get("password")
        auth_url = "https://shiptrackingapiauth-787201059405.asia-south2.run.app/login"
        try:
            # Authenticate with External API
            response = requests.post(auth_url, json={"username": username, "password": password}, timeout=10)
            if response.status_code == 200:
                data = response.json()
                # Mirror user in local Django DB for session management
                user, _ = User.objects.get_or_create(username=username)
                user.backend = "django.contrib.auth.backends.ModelBackend"
                login(request, user)
                
                # Step 2: Store API credentials in Session
                request.session["bearer_token"] = data.get("token")
                request.session["api_user_id"] = data.get("userId")
                
                # Step 3: Clear old tracking data to force fresh reload
                request.session.pop("auth_vessels_data", None)
                return JsonResponse({"success": True, "redirect_url": reverse("user_map_auth")})
        except Exception:
            logger.exception("login_view failed")
        return JsonResponse({"success": False, "error": "Invalid credentials or API error"})
    return render(request, "login.html")


def logout_view(request):
    """
    LOGOUT VIEW: Clears session and redirects to public map.
    """
    logout(request)
    return redirect('allmaps')


def register_view(request):
    """
    REGISTRATION VIEW: Standard Django user creation.
    """
    if request.method == 'POST':
        form = UserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            return JsonResponse({"success": True, "redirect_url": "/"})
        return JsonResponse({"success": False, "error": form.errors.as_json()})
    return render(request, 'register.html', {'form': UserCreationForm()})


def get_auth_vessels_data(request):
    """
    DATA HELPER: Fetches tracking points for user-associated vessels.
    State 1 (First Load): Fetches full history for all vessels.
    State 2 (Polling): Fetches only latest 'top 1' record and appends to session.
    """
    user_ID = request.session.get("api_user_id")
    b_token = request.session.get("bearer_token")
    vessels_raw = request.session.get("auth_vessels_data", [])
    
    bearer_token = getattr(settings, "SHIP_API_BEARER_TOKEN", b_token)
    headers = {"Authorization": f"Bearer {bearer_token}", "Accept": "application/json"}
    
    new_records = []
    try:
        # Step 1: Identify all vessels associated with this UserID
        assoc_url = f"https://shiptrackingapiauth-787201059405.asia-south2.run.app/VesselTracking/UserAssociatedVessels/{user_ID}"
        assoc_res = requests.get(assoc_url, headers=headers, timeout=10)
        
        if assoc_res.status_code == 200:
            assoc_data = assoc_res.json()
            if not isinstance(assoc_data, list):
                assoc_data = [assoc_data] if assoc_data else []

            if not vessels_raw:
                # --- FIRST TIME LOAD: FULL HISTORY ---
                for v in assoc_data:
                    vid = v.get('VesselId')
                    if not vid: continue
                    hist_url = f"https://shiptrackingapiauth-787201059405.asia-south2.run.app/VesselTracking/getbyVesselId/{vid}"
                    hist_res = requests.get(hist_url, headers=headers, timeout=10)
                    if hist_res.status_code == 200:
                        data = hist_res.json()
                        points = data if isinstance(data, list) else ([data] if data else [])
                        vessels_raw.extend(points)
                new_records = vessels_raw
            else:
                # --- INCREMENTAL POLL: APPEND NEWEST ONLY ---
                # assoc_data contains latest top 1 record for each ship
                for new_v in assoc_data:
                    vid = new_v.get('VesselId')
                    dt = new_v.get('DateTime')
                    # Deduplicate: Only append if this specific timestamp isn't in history yet
                    exists = any(old.get('VesselId') == vid and old.get('DateTime') == dt for old in vessels_raw)
                    if not exists:
                        vessels_raw.append(new_v)
                        new_records.append(new_v)

            # Save updated history back to Session
            request.session["auth_vessels_data"] = vessels_raw
            request.session["auth_vessels_last_fetch"] = time.time()
    except Exception:
        logger.exception("API fetch failed")
    
    return vessels_raw, new_records


def process_auth_vessels_to_js(vessels_raw, is_incremental=False):
    """
    JS FORMATTER: Converts raw API dictionaries into a structured JSON array for the map.
    Handles coordinate swapping and status mapping.
    """
    routes = {}
    for v in vessels_raw:
        vname = v.get("VesselName") or f"Ship {v.get('VesselId')}"
        vkey = v.get("VesselId") or v.get("Id") or vname
        
        # COORDINATE FIX: API Lat/Lng are reversed.
        lat = float(v.get("Longitude") or v.get("lat") or 17.15)
        lng = float(v.get("Latitude") or v.get("lng") or 82.4)

        point = {
            "lat": lat, "lng": lng,
            "Comments": v.get("Comments", "-"), "DateTime": v.get("DateTime", "-"),
            "Speed": v.get("Speed", "-"), "Battery": v.get("Battery", "-"),
            "Fuel1": v.get("Fuel1", "-"), "Fuel2": v.get("Fuel2", "-"),
            "RPM1": v.get("RPM1", "-"), "RPM2": v.get("RPM2", "-"),
            "Eng1RunStatus": "Running" if v.get("Eng1RunStatus") in [1, "1", "Running"] else "Idle",
            "Eng2RunStatus": "Running" if v.get("Eng2RunStatus") in [1, "1", "Running"] else "Idle"
        }
        if vkey not in routes:
            routes[vkey] = {"name": vname, "path": []}
        routes[vkey]["path"].append(point)

    # Historical data should be sorted for correct track drawing
    if not is_incremental:
        for r in routes.values():
            r["path"].sort(key=lambda x: x.get("DateTime", ""))
    
    vessel_js_array = []
    dark_colors = ["#1a237e", "#b71c1c", "#1b5e20", "#e65100", "#4a148c", "#004d40", "#212121", "#3e2723"]
    for i, (vkey, route) in enumerate(routes.items()):
        vessel_js_array.append({
            "name": route["name"], "vkey": vkey,
            "color": dark_colors[i % len(dark_colors)],
            "route": route["path"], "currentIndex": len(route["path"]) - 1,
            "visible": True
        })
    return vessel_js_array


@login_required
def user_map_auth_view(request):
    """
    PRIVATE VIEW: Displays user's specific fleet.
    Requires authentication and Bearer Token.
    """
    # Step 1: Initial fetch of tracking data
    vessels_raw, _ = get_auth_vessels_data(request)
    vessel_js_array = process_auth_vessels_to_js(vessels_raw)
    
    # Step 2: Initialize Map
    m = folium.Map(location=[17.15, 82.4], zoom_start=6, control_scale=True, zoom_control=False, tiles=None)

    # Step 3: Add Tile Layers
    folium.TileLayer(tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}", attr="Tiles © Esri", name="Esri World Street Map (English)", show=True).add_to(m)
    folium.TileLayer(tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", attr="Tiles © Esri", name="Esri World Imagery (Satellite)", show=False).add_to(m)
    folium.TileLayer(tiles="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", attr="© OpenStreetMap contributors", name="OpenStreetMap", show=False).add_to(m)
    folium.TileLayer(tiles="https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png", attr="© Carto", name="Carto Light", show=False).add_to(m)
    folium.TileLayer(tiles="https://{s}.basemaps.cartocdn.com/light_nolabels/{z}/{x}/{y}.png", attr="© Carto", name="Light No Labels", show=False).add_to(m)
    folium.TileLayer(tiles="https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png", attr="© Carto", name="Carto Voyager", show=False).add_to(m)

    folium.LayerControl(position="bottomright").add_to(m)

    # Step 4: Inject Replay/Live Tracking Logic JS
    js_code = render_to_string("folium/user_map_auth_script.html", {"map_name": m.get_name(), "vessels_json": json.dumps(vessel_js_array)})
    m.get_root().html.add_child(folium.Element(js_code))

    return render(request, 'user_map_auth.html', {'map_auth_html': m._repr_html_()})


@login_required
def vessel_data_json(request):
    """
    POLLING ENDPOINT: Called by frontend every 30s.
    Returns ONLY the newest points found in the latest fetch to minimize payload.
    """
    _, new_records = get_auth_vessels_data(request)
    vessel_js_array = process_auth_vessels_to_js(new_records, is_incremental=True)
    return JsonResponse({"vessels": vessel_js_array})
