from wsgiref import headers

from django.urls import reverse

import folium
import json
import logging
import os
import requests
import time
from datetime import datetime, time as dt_time
from django.conf import settings
from django.core.cache import cache
from django.shortcuts import render, redirect
from django.template.loader import render_to_string
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm
from django.http import JsonResponse
from django.contrib.auth.models import User

# Standard logger for tracking app events
logger = logging.getLogger(__name__)
PUBLIC_MAP_CACHE_KEY = "public_allmaps_html"
PUBLIC_MAP_CACHE_TTL_SECONDS = 60

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

    cached_map_html = cache.get(PUBLIC_MAP_CACHE_KEY)
    if cached_map_html:
        return render(request, 'allmaps.html', {'map_html': cached_map_html})

    # Step 1: Fetch public vessel data without storing the full payload in session.
    vessels_raw = []
    api_url = "https://shiptrackingapi-787201059405.asia-south2.run.app/VesselTracking/GetAll"
    try:
        response = requests.get(api_url, timeout=10)
        if response.status_code == 200:
            vessels_raw = response.json()
    except Exception:
        logger.exception("allmaps_view: API fetch failed")

    # Fallback to local JSON if API fails or returns no data.
    if not vessels_raw:
        try:
            local_path = os.path.join(settings.BASE_DIR, 'static', 'data', 'vessels.json')
            if os.path.exists(local_path):
                with open(local_path, 'r') as f:
                    vessels_raw = json.load(f)
        except Exception:
            logger.exception("allmaps_view: Local fallback failed")

    if vessels_raw and not isinstance(vessels_raw, list):
        vessels_raw = [vessels_raw]

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
        v_name = v.get("VesselName") or v.get("name") or f"Ship {v.get('VesselId') or v.get('Id') or 'Unknown'}"
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
    map_html = m._repr_html_()
    cache.set(PUBLIC_MAP_CACHE_KEY, map_html, PUBLIC_MAP_CACHE_TTL_SECONDS)
    return render(request, 'allmaps.html', {'map_html': map_html})


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

def get_all_user_vessels_data(request, start_date=None, end_date=None, target_vessel_id=None):
    user_ID = request.session.get("api_user_id")
    b_token = request.session.get("bearer_token")
    bearer_token = getattr(settings, "SHIP_API_BEARER_TOKEN", b_token)
    headers = {"Authorization": f"Bearer {bearer_token}", "Accept": "application/json"}
    vessels_raw = request.session.get("auth_vessels_data", []) 

    try:
        assoc_url = f"https://shiptrackingapiauth-787201059405.asia-south2.run.app/VesselTracking/UserAssociatedVessels/{user_ID}"
        logger.info(f"===>API GET URL: {assoc_url}")
        assoc_res = requests.get(assoc_url, headers=headers, timeout=30)
        logger.info(f"===>API response status: {assoc_res.status_code}")
        vessels_raw, new_records = get_auth_vessels_data(assoc_res, request, start_date, end_date)        
        return vessels_raw, new_records
    except Exception:
        logger.exception("API fetch failed")
        return [], []


def get_vessels_data_filtering(request, start_date=None, end_date=None, target_vessel_id=None):
    user_ID = request.session.get("api_user_id")
    b_token = request.session.get("bearer_token")
    bearer_token = getattr(settings, "SHIP_API_BEARER_TOKEN", b_token)
    headers = {"Authorization": f"Bearer {bearer_token}", "Accept": "application/json"}
    vessels_raw = request.session.get("auth_vessels_data", []) 

    try:
        s_date = start_date.replace("T", " ") if start_date else None
        e_date = end_date.replace("T", " ") if end_date else None
        assoc_url = "https://shiptrackingapiauth-787201059405.asia-south2.run.app/VesselTracking/GetDataWithUserIdAndDateRange"
        payload = {"userId": user_ID, "startDate": s_date, "endDate": e_date}
        if target_vessel_id:
            payload["vesselId"] = target_vessel_id

        logger.info(f"===>API POST URL: {assoc_url} Payload: {payload}")
        assoc_res = requests.post(assoc_url, headers=headers, json=payload, timeout=30)
        vessels_raw, new_records = get_auth_vessels_data(assoc_res, request, start_date, end_date)
        return vessels_raw, new_records

    except Exception:
        logger.exception("API fetch failed")
        return [], []
        return [], []

def get_auth_vessels_data_LiveData(request, start_date=None, end_date=None, target_vessel_id=None):
    user_ID = request.session.get("api_user_id")
    b_token = request.session.get("bearer_token")
    bearer_token = getattr(settings, "SHIP_API_BEARER_TOKEN", b_token)
    headers = {"Authorization": f"Bearer {bearer_token}", "Accept": "application/json"}
    vessels_raw = request.session.get("auth_vessels_data", []) 

    try:
        assoc_url = f"https://shiptrackingapiauth-787201059405.asia-south2.run.app/VesselTracking/UserAssociatedVessels/{user_ID}"
        logger.info(f"===>API GET URL: {assoc_url}")
        assoc_res = requests.get(assoc_url, headers=headers, timeout=30)
        logger.info(f"===>API response status: {assoc_res.status_code}")
        vessels_raw, new_records = get_auth_vessels_data(assoc_res, request, start_date, end_date)       
        return vessels_raw, new_records
    except Exception:
        logger.exception("API fetch failed")
        return [], []


def get_auth_vessels_data(assoc_res,request, start_date=None, end_date=None):
    """
    DATA HELPER: Fetches tracking points for user-associated vessels.
    Can be filtered by date range and specific vessel ID.
    """   
    vessels_raw = request.session.get("auth_vessels_data", []) 
    new_records = []
    try:
        if assoc_res.status_code == 200:
            assoc_data = assoc_res.json()
            if not isinstance(assoc_data, list):
                 assoc_data = [assoc_data] if assoc_data else []

            logger.info(f"===>Performing incremental update check against {len(vessels_raw)} records in session")
            #logger.info(f"=============================>count {len(assoc_data)}")

            for new_v in assoc_data:
                vid = new_v.get("VesselId")
                dt = new_v.get("DateTime")
                
                #logger.info(f"======>Processing new vessel record: {vid} at {dt}")
                # Check if this record already exists in session 
                exists = any(
                    old.get("VesselId") == vid and old.get("DateTime") == dt
                    for old in vessels_raw
                )

                if not exists:
                    vessels_raw.append(new_v)
                    new_records.append(new_v)

                    if new_records:
                            request.session["auth_vessels_data"] = vessels_raw
                            request.session["auth_vessels_last_fetch"] = time.time()
                    #logger.info(f"Session updated with {len(new_records)} new records")

        # --- Filter by Date Range before returning ---
        if start_date or end_date:
            s_cmp = start_date.replace("T", " ") if (start_date and start_date != "null") else "0000-00-00 00:00:00"
            e_cmp = end_date.replace("T", " ") if (end_date and end_date != "null") else "9999-99-99 99:99:99"

            if len(s_cmp) == 16: s_cmp += ":00"
            if len(e_cmp) == 16: e_cmp += ":59"

            vessels_raw = [v for v in vessels_raw if s_cmp <= v.get("DateTime", "") <= e_cmp]
            new_records = [v for v in new_records if s_cmp <= v.get("DateTime", "") <= e_cmp]
            logger.info(f"Filtered results to {len(vessels_raw)} total, {len(new_records)} new")

    except Exception:
        logger.exception("API fetch failed")
        return [], []

    logger.info(
        f"get_auth_vessels_data: returning {len(vessels_raw)} total records, {len(new_records)} new records"
    )

    return vessels_raw, new_records



def process_auth_vessels_to_js(vessels_raw, is_incremental=False):
    """
    JS FORMATTER: Converts raw API dictionaries into a structured JSON array for the map.
    Handles coordinate swapping and status mapping.
    """
    logger.info(f"Processing {len(vessels_raw)} raw vessel records into JS format (is_incremental={is_incremental})")
    routes = {}
    for v in vessels_raw:
        logger.debug(f"Processing vessel record: {v}")
        vname = v.get("VesselName") or f"Ship {v.get('VesselId')}"
        vkey = v.get("VesselId") or v.get("Id") or vname
        logger.debug(f"Vessel key: {vkey}, name: {vname}")
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
    logger.info("user_map_auth_view: request started for user_id: " + str(request.session.get("api_user_id")))
    # Step 1: Check if we have data, otherwise load defaults (Today 00:00 to Now)
    if not request.session.get("auth_vessels_data"):
        # This will populate session with today's data
        logger.info("user_map_auth_view: No session data found, performing initial fetch for today's data")
        today_start = datetime.combine(datetime.now().date(), dt_time.min).strftime('%Y-%m-%dT%H:%M')
        logger.info(f"user_map_auth_view: Fetching data from {today_start} to now")
        now_str = "" #datetime.now().strftime('%Y-%m-%dT%H:%M')
        logger.info(f"user_map_auth_view: Current time for fetch end: {now_str}")
        vessels_raw, _ = get_vessels_data_filtering(request, start_date=today_start, end_date=now_str)
        vessels_raw, _ =get_all_user_vessels_data(request)
        logger.info(f"user_map_auth_view: Initial fetch returned {len(vessels_raw)} records")
    else:
        vessels_raw = request.session.get("auth_vessels_data", [])
        logger.info(f"user_map_auth_view: Using session data with {len(vessels_raw)} records")
        
    vessel_js_array = process_auth_vessels_to_js(vessels_raw)
    logger.info(f"user_map_auth_view: Processed {len(vessel_js_array)} vessels for JS rendering")
    
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
    start_date = request.GET.get('start_date')
    end_date = request.GET.get('end_date')
    vessel_id = request.GET.get('vessel_id')
    logger.info("vessel_data_json============>startdate : " + str(start_date))
    logger.info("vessel_data_json============>enddate : " + str(end_date))
    logger.info("vessel_data_json============>vessel_id : " + str(vessel_id))
    if end_date == "null" or end_date == "":
        live_flg=True
        logger.info("++++++++++++++++++++++++++++>vessel_data_json: Fetching live data updates<---------------")
        _, new_records = get_auth_vessels_data_LiveData(request, start_date=start_date, end_date=end_date, target_vessel_id=vessel_id)
        logger.info(f"vessel_data_json: Found {len(new_records)} new records since last fetch")
        vessel_js_array = process_auth_vessels_to_js(new_records, is_incremental=True)
        logger.info(f"vessel_data_json: Returning {len(vessel_js_array)} new records in JSON response")
        return JsonResponse({"vessels": vessel_js_array})       
    else:
        logger.info("------------>not fetching for live data") 
        return JsonResponse({"vessels": []})        


@login_required
def vessel_filter_json(request):
    """
    FILTER ENDPOINT: Fetches historical data based on date range and optional vessel ID.
    """
    start_date = request.GET.get('start_date')
    end_date = request.GET.get('end_date')
    vessel_id = request.GET.get('vessel_id')
    logger.info("vessel_filter_json============>startdate : " + str(start_date))
    logger.info("vessel_filter_json============>enddate : " + str(end_date))
    logger.info("vessel_filter_json============>vessel_id : " + str(vessel_id))
    logger.info("vessel_filter_json: Initiating filtered fetch")
    vessels_raw, _ = get_vessels_data_filtering(request, start_date=start_date, end_date=end_date, target_vessel_id=vessel_id)
    logger.info(f"vessel_filter_json: Filtered fetch returned {len(vessels_raw)} records")
    vessel_js_array = process_auth_vessels_to_js(vessels_raw)
    logger.info(f"vessel_filter_json: Processed {len(vessel_js_array)} vessels for JS response")
    #logger.info(f"vessel_filter_json: Sample vessel data for debugging: {vessel_js_array[0] if vessel_js_array else 'No vessels'}")
    #logger.info("vessel_filter_json: request completed: returning JSON response :"+JsonResponse({"vessels": vessel_js_array}).content.decode())
    return JsonResponse({"vessels": vessel_js_array})
