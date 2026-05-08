from django.urls import reverse

import folium
import json
import logging
import os
import requests
import time
from django.conf import settings
from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm
from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse


logger = logging.getLogger(__name__)


folium.Map.default_css = [
    (name, "/static/css/bootstrap.min.css" if name == "bootstrap_css" else url)
    for name, url in folium.Map.default_css
]
folium.Map.default_js = [
    (name, "/static/js/bootstrap.bundle.min.js" if name == "bootstrap" else url)
    for name, url in folium.Map.default_js
]


def chrome_devtools_config(request):
    return JsonResponse({})


def allmaps_view(request):
    logger.info("allmaps_view: request started")

    # 1. Fetch live data from API URL
    # Check if vessels are already in session
    vessels_raw = request.session.get("vessels_raw", [])
    logger.info("allmaps_view: loaded %s records from session", len(vessels_raw) if isinstance(vessels_raw, list) else 1)

    if not vessels_raw:
        api_url = "https://shiptrackingapi-787201059405.asia-south2.run.app/VesselTracking/GetAll"
        try:
            logger.info("allmaps_view: fetching vessel data from API url=%s", api_url)
            response = requests.get(api_url, timeout=10)
            logger.info("allmaps_view: API response status_code=%s", response.status_code)
            if response.status_code == 200:
                vessels_raw = response.json()
                logger.info("allmaps_view: API records loaded count=%s", len(vessels_raw) if isinstance(vessels_raw, list) else 1)
            else:
                logger.warning("allmaps_view: API returned non-success status_code=%s", response.status_code)
        except Exception as e:
            logger.exception("allmaps_view: API fetch failed, trying local fallback")
            # Fallback to local files if API fails
            data_dir = os.path.join(settings.BASE_DIR, 'static', 'data')
            logger.info("allmaps_view: fallback data directory path=%s", data_dir)
            if os.path.exists(data_dir):
                for filename in os.listdir(data_dir):
                    if filename.startswith('vessel') and filename.endswith('.json'):
                        file_path = os.path.join(data_dir, filename)
                        logger.info("allmaps_view: reading fallback file path=%s", file_path)
                        with open(file_path) as f:
                            try:
                                data = json.load(f)
                                if isinstance(data, list):
                                    vessels_raw.extend(data)
                                    logger.info("allmaps_view: fallback list loaded file=%s count=%s", filename, len(data))
                                elif isinstance(data, dict):
                                    vessels_raw.append(data)
                                    logger.info("allmaps_view: fallback object loaded file=%s", filename)
                            except Exception:
                                logger.exception("allmaps_view: failed reading fallback file path=%s", file_path)
                                continue
            else:
                logger.warning("allmaps_view: fallback data directory not found path=%s", data_dir)

        # Store the fetched data in session for reuse
        request.session["vessels_raw"] = vessels_raw
        logger.info("allmaps_view: stored records in session count=%s", len(vessels_raw) if isinstance(vessels_raw, list) else 1)

    if not vessels_raw:
        logger.warning("allmaps_view: no vessel data available")
        return render(request, 'allmaps.html', {'map_html': "No vessel data available."})

    # 2. Base Map Setup
    logger.info("allmaps_view: creating base map")
    m = folium.Map(
        location=[17.15, 82.4],
        zoom_start=6,
        control_scale=True,
        zoom_control=False,
        tiles=None
    )

    # --- ADD ALL LAYERS ---
    logger.info("allmaps_view: adding tile layers")
    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}",
        attr="Tiles © Esri",
        name="Esri World Street Map (English)",
        show=True
    ).add_to(m)

    folium.TileLayer(
        tiles="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
        attr="© OpenStreetMap contributors",
        name="OpenStreetMap",
        show=False
    ).add_to(m)

    folium.TileLayer(
        tiles="https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png",
        attr="© Carto",
        name="Carto Light",
        show=False
    ).add_to(m)

    folium.TileLayer(
        tiles="https://{s}.basemaps.cartocdn.com/light_nolabels/{z}/{x}/{y}.png",
        attr="© Carto",
        name="Light No Labels",
        show=False
    ).add_to(m)

    folium.TileLayer(
        tiles="https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png",
        attr="© Carto",
        name="Carto Voyager",
        show=False
    ).add_to(m)

    folium.LayerControl(position="bottomright").add_to(m)

    # 3. Group and Process Routes by VesselName or ID
    logger.info("allmaps_view: grouping vessel records into routes")
    routes = {}
    for v in vessels_raw:
        # Prioritize VesselName for dynamic allocation, fallback to ID
        v_name = v.get(
            "VesselName") or f"Ship {v.get('VesselId') or v.get('Id') or 'Unknown'}"

        point = {
            "lat": float(v.get("Longitude") or v.get("lat") or 16.93),
            "lng": float(v.get("Latitude") or v.get("lng") or 82.26),
            "Comments": v.get("Comments", "-"),
            "DateTime": v.get("DateTime", "-"),
            "Speed": v.get("Speed", "-"),
            "IdleTime": v.get("IdleTime", "-"),
            "Battery": v.get("Battery", "-"),
            "Fuel1": v.get("Fuel1", "-"),
            "Fuel2": v.get("Fuel2", "-"),
            "RPM1": v.get("RPM1", "-"),
            "RPM2": v.get("RPM2", "-"),
            "Eng1RunStatus": "Running" if v.get("Eng1RunStatus") in [1, "1", "Running"] else "Idle",
            "Eng2RunStatus": "Running" if v.get("Eng2RunStatus") in [1, "1", "Running"] else "Idle"
        }
        routes.setdefault(v_name, []).append(point)
    logger.info("allmaps_view: route grouping complete route_count=%s", len(routes))

    vessel_js_array = []
    # Darker color palette for markers
    dark_colors = ["#1a237e", "#b71c1c", "#1b5e20",
                   "#e65100", "#4a148c", "#004d40", "#212121", "#3e2723"]

    logger.info("allmaps_view: preparing vessel JavaScript data")
    for i, (name, path) in enumerate(routes.items()):
        vessel_js_array.append({
            "name": name,
            "color": dark_colors[i % len(dark_colors)],
            "route": path,
            "currentIndex": 0
        })

    # 4. JavaScript logic
    logger.info("allmaps_view: building map JavaScript")
    js_code = f"""
    <script>
    window.onload = function() {{
        var map = {m.get_name()};
        var vessels = {json.dumps(vessel_js_array)};
        var markers = [];

        L.control.zoom({{ position: 'bottomleft' }}).addTo(map);

        function getPopupHTML(pt, vName) {{
            let dt = pt.DateTime.includes('T') ? pt.DateTime.replace('T', ' ').split('.')[0] : pt.DateTime;
            return `
            <div style="width:180px; font-family:Arial, sans-serif; font-size:11px; color:#333;">
                <div style="font-weight:bold; border-bottom:1px solid #ccc; padding-bottom:4px; margin-bottom:6px; color:#2f4f8f; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">
                    ${{vName}}
                </div>
                <div style="margin-bottom:5px; font-size:10px; color:#c53030; font-weight:bold;">🕒 ${{dt}}</div>
                <div style="display:flex; justify-content:space-between; margin-bottom:3px;">
                    <span>Speed: <b>${{pt.Speed}}</b></span>
                    <span>Bat: <b>${{pt.Battery}}V</b></span>
                </div>
                <div style="display:flex; justify-content:space-between; margin-bottom:3px;">
                    <span>F1: <b>${{pt.Fuel1}}L</b></span>
                    <span>F2: <b>${{pt.Fuel2}}L</b></span>
                </div>
                <div style="display:flex; justify-content:space-between; margin-bottom:3px;">
                    <span>R1: <b>${{pt.RPM1}}</b></span>
                    <span>R2: <b>${{pt.RPM2}}</b></span>
                </div>
                <div style="font-size:10px; margin-top:5px; border-top:1px dotted #ccc; padding-top:4px;">
                    E1: <span style="color:${{pt.Eng1RunStatus==='Running'?'green':'red'}}">${{pt.Eng1RunStatus}}</span> |
                    E2: <span style="color:${{pt.Eng2RunStatus==='Running'?'green':'red'}}">${{pt.Eng2RunStatus}}</span>
                </div>
            </div>`;
        }}

        function createIcon(color) {{
            return L.divIcon({{
                className: "",
                html: `
                <div style="
                    position:relative;
                    width:30px;
                    height:18px;
                    transform:rotate(-90deg);
                    filter:drop-shadow(0 4px 6px rgba(0,0,0,0.45));
                ">
                    <div style="
                        position:absolute;
                        inset:2px 1px 2px 0;
                        background:linear-gradient(135deg, rgba(255,255,255,0.28), ${{color}} 38%, rgba(0,0,0,0.2));
                        border:2px solid #ffffff;
                        border-radius:18px 5px 5px 18px;
                        box-shadow:inset 0 1px 2px rgba(255,255,255,0.45), inset -3px 0 3px rgba(0,0,0,0.18);
                    "></div>
                    <div style="
                        position:absolute;
                        right:-1px;
                        top:5px;
                        width:0;
                        height:0;
                        border-top:4px solid transparent;
                        border-bottom:4px solid transparent;
                        border-left:8px solid #ffffff;
                    "></div>
                    <div style="
                        position:absolute;
                        left:8px;
                        top:5px;
                        width:8px;
                        height:6px;
                        background:rgba(255,255,255,0.9);
                        border-radius:3px;
                        box-shadow:8px 0 0 rgba(255,255,255,0.55);
                    "></div>
                </div>`,
                iconSize: [34, 22],
                iconAnchor: [17, 11]
            }});
        }}

        vessels.forEach(v => {{
            if (v.route.length === 0) return;
            let start = v.route[0];
            let marker = L.marker([start.lat, start.lng], {{ icon: createIcon(v.color) }}).addTo(map);

            marker.bindPopup(getPopupHTML(start, v.name), {{
                maxWidth: 190,
                minWidth: 180,
                autoPan: true
            }});

            markers.push({{ marker: marker, data: v }});
        }});

        var moveInterval = 1000;
        var moveTimer = null;

        window.setMapSpeed = function(ms) {{
            moveInterval = parseInt(ms);
            if (moveTimer) clearInterval(moveTimer);

            moveTimer = setInterval(function() {{
                markers.forEach(obj => {{
                    let v = obj.data;
                    v.currentIndex = (v.currentIndex + 1) % v.route.length;
                    let next = v.route[v.currentIndex];
                    obj.marker.setLatLng([next.lat, next.lng]);

                    if (obj.marker.getPopup()) {{
                        obj.marker.setPopupContent(getPopupHTML(next, v.name));
                    }}
                }});
            }}, moveInterval);
        }};

        window.setMapSpeed(1000);
    }};
    </script>
    """

    m.get_root().html.add_child(folium.Element(js_code))
    logger.info("allmaps_view: rendering template")
    return render(request, 'allmaps.html', {'map_html': m._repr_html_()})


def login_view(request):
    logger.info("login_view: request method=%s", request.method)
    if request.method == "POST":
        username = request.POST.get('username')
        password = request.POST.get('password')
        logger.info("login_view: authenticating username=%s", username)
        user = authenticate(request, username=username, password=password)

        if user:
            logger.info("login_view: authentication success username=%s", username)
            login(request, user)
            return JsonResponse({"success": True, "redirect_url": reverse("user_map_auth")})
        else:
            logger.warning("login_view: authentication failed username=%s", username)
            return JsonResponse({"success": False, "error": "Invalid credentials"})
    logger.info("login_view: rendering login template")
    return render(request, 'login.html')


def logout_view(request):
    logger.info("logout_view: logging out user_id=%s", getattr(request.user, "id", None))
    logout(request)
    logger.info("logout_view: redirecting to allmaps")
    return redirect('allmaps')


def register_view(request):
    logger.info("register_view: request method=%s", request.method)
    if request.method == 'POST':
        logger.info("register_view: validating registration form")
        form = UserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            logger.info("register_view: registration success user_id=%s username=%s", user.id, user.username)
            login(request, user)
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                logger.info("register_view: returning AJAX success")
                return JsonResponse({"success": True, "redirect_url": "/"})
            logger.info("register_view: redirecting to allmaps")
            return redirect('allmaps')
        else:
            logger.warning("register_view: form invalid errors=%s", form.errors.as_json())
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                errors = "\n".join(
                    [f"{k}: {v[0]}" for k, v in form.errors.items()])
                logger.info("register_view: returning AJAX errors")
                return JsonResponse({"success": False, "error": errors})
    else:
        logger.info("register_view: creating empty form")
        form = UserCreationForm()

    logger.info("register_view: applying form field classes")
    for field in form.fields.values():
        field.widget.attrs.update({'class': 'form-control'})

    logger.info("register_view: rendering register template")
    return render(request, 'register.html', {'form': form})


def user_map_auth_view(request):
    logger.info("user_map_auth_view: request started user_authenticated=%s", request.user.is_authenticated)
    if not request.user.is_authenticated:
        logger.warning("user_map_auth_view: unauthenticated request redirected to login")
        return redirect('login')
    user_ID = 1
    b_token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1c2VySWQiOjYsInVzZXJuYW1lIjoiQWRtaW4iLCJpYXQiOjE3NzgyNDkwMDUsImV4cCI6MTc3ODI1MDgwNX0.wW4kWOBAYcceaGlHGgv4TGeOcK-TFlllRaNQtbXT1rA"
    
    # Check session for cached data and timestamp
    vessels_raw = request.session.get("auth_vessels_data", [])
    last_fetch = request.session.get("auth_vessels_last_fetch", 0)
    force_refresh = request.GET.get('refresh') == 'true'
    error_message = None
    logger.info(
        "user_map_auth_view: cache status cached_count=%s last_fetch=%s force_refresh=%s",
        len(vessels_raw) if isinstance(vessels_raw, list) else 1,
        last_fetch,
        force_refresh,
    )

    # Fetch if cache is empty, expired (5 mins), or forced
    if force_refresh or not vessels_raw or (time.time() - last_fetch > 300):
        bearer_token = getattr(settings, "SHIP_API_BEARER_TOKEN", f"{b_token}")
        headers = {"Authorization": f"Bearer {bearer_token}", "Accept": "application/json"}
        vessels_raw = []
        logger.info("user_map_auth_view: fetching fresh auth vessel data")
        
        try:
            # Step 1: Get Associated Vessels
            assoc_url = f"https://shiptrackingapiauth-787201059405.asia-south2.run.app/VesselTracking/UserAssociatedVessels/{user_ID}"
            logger.info("user_map_auth_view: fetching associated vessels url=%s", assoc_url)
            assoc_res = requests.get(assoc_url, headers=headers, timeout=10)
            logger.info("user_map_auth_view: associated vessels status_code=%s", assoc_res.status_code)
            
            if assoc_res.status_code == 200:
                assoc_data = assoc_res.json()
                # Extract Vessel IDs
                v_ids = []
                if isinstance(assoc_data, list):
                    v_ids = [str(v.get('VesselId')) for v in assoc_data if v.get('VesselId')]
                elif isinstance(assoc_data, dict) and assoc_data.get('VesselId'):
                    v_ids = [str(assoc_data['VesselId'])]
                logger.info("user_map_auth_view: associated vessel ids count=%s", len(v_ids))
                
                if v_ids:
                    # Step 2: Get Detailed Records for these IDs
                    for vessel_id in v_ids:
                        latest_url = f"https://shiptrackingapiauth-787201059405.asia-south2.run.app/VesselTracking/getbyVesselId/{vessel_id}"
                        logger.info("user_map_auth_view: fetching latest vessel records url=%s", latest_url)
                        latest_res = requests.get(latest_url, headers=headers, timeout=10)
                        logger.info(
                            "user_map_auth_view: latest records status_code=%s vessel_id=%s",
                            latest_res.status_code,
                            vessel_id,
                        )

                        if latest_res.status_code == 200:
                            latest_data = latest_res.json()
                            if isinstance(latest_data, list):
                                vessels_raw.extend(latest_data)
                            elif isinstance(latest_data, dict):
                                vessels_raw.append(latest_data)
                        else:
                            error_message = f"Detailed Records Error: {latest_res.status_code}"
                            logger.warning("user_map_auth_view: %s vessel_id=%s", error_message, vessel_id)

                    if vessels_raw:
                        request.session["auth_vessels_data"] = vessels_raw
                        request.session["auth_vessels_last_fetch"] = time.time()
                        logger.info("user_map_auth_view: latest records cached count=%s", len(vessels_raw))
                else:
                    error_message = "No vessels associated with this user."
                    logger.warning("user_map_auth_view: %s", error_message)
            else:
                error_message = f"Associated Vessels Error: {assoc_res.status_code}"
                logger.warning("user_map_auth_view: %s", error_message)
                
        except Exception as e:
            error_message = f"Connection Error: {str(e)}"
            logger.exception("user_map_auth_view: auth API fetch failed, trying local fallback")
            # Fallback to local files if API fails
            data_dir = os.path.join(settings.BASE_DIR, 'static', 'data')
            logger.info("user_map_auth_view: fallback data directory path=%s", data_dir)
            if os.path.exists(data_dir):
                for filename in os.listdir(data_dir):
                    if filename.startswith('vessel') and filename.endswith('.json'):
                        file_path = os.path.join(data_dir, filename)
                        logger.info("user_map_auth_view: reading fallback file path=%s", file_path)
                        with open(file_path) as f:
                            try:
                                data = json.load(f)
                                if isinstance(data, list):
                                    vessels_raw.extend(data)
                                    logger.info("user_map_auth_view: fallback list loaded file=%s count=%s", filename, len(data))
                                elif isinstance(data, dict):
                                    vessels_raw.append(data)
                                    logger.info("user_map_auth_view: fallback object loaded file=%s", filename)
                            except Exception:
                                logger.exception("user_map_auth_view: failed reading fallback file path=%s", file_path)
                                continue
            else:
                logger.warning("user_map_auth_view: fallback data directory not found path=%s", data_dir)
    else:
        logger.info("user_map_auth_view: using cached auth vessel data")

    # 2. Base Map Setup
    logger.info("user_map_auth_view: creating base map")
    m = folium.Map(
        location=[17.15, 82.4],
        zoom_start=6,
        control_scale=True,
        zoom_control=False,
        tiles=None
    )

    # Show error message if something went wrong
    if error_message:
        logger.warning("user_map_auth_view: showing map error message=%s", error_message)
        err_html = f"""
        <div style="position: fixed; top: 10px; left: 50%; transform: translateX(-50%); z-index: 9999; 
                    background: rgba(220, 38, 38, 0.9); color: white; padding: 12px 24px; border-radius: 8px; 
                    font-family: 'Segoe UI', Arial, sans-serif; box-shadow: 0 4px 15px rgba(0,0,0,0.3); font-size: 14px; border: 1px solid white;">
            <b>⚠️ Alert:</b> {error_message}
        </div>
        """
        m.get_root().html.add_child(folium.Element(err_html))

    # --- ADD ALL LAYERS ---
    logger.info("user_map_auth_view: adding tile layers")
    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}",
        attr="Tiles © Esri",
        name="Esri World Street Map (English)",
        show=True
    ).add_to(m)

    folium.TileLayer(
        tiles="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
        attr="© OpenStreetMap contributors",
        name="OpenStreetMap",
        show=False
    ).add_to(m)

    folium.TileLayer(
        tiles="https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png",
        attr="© Carto",
        name="Carto Light",
        show=False
    ).add_to(m)

    folium.TileLayer(
        tiles="https://{s}.basemaps.cartocdn.com/light_nolabels/{z}/{x}/{y}.png",
        attr="© Carto",
        name="Light No Labels",
        show=False
    ).add_to(m)

    folium.TileLayer(
        tiles="https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png",
        attr="© Carto",
        name="Carto Voyager",
        show=False
    ).add_to(m)

    folium.LayerControl(position="bottomright").add_to(m)

    # 3. Group and Process Routes by VesselName or ID
    logger.info("user_map_auth_view: grouping vessel records into routes")
    routes = {}
    for v in vessels_raw:
        # Prioritize VesselName for dynamic allocation, fallback to ID
        v_name = v.get(
            "VesselName") or f"Ship {v.get('VesselId') or v.get('Id') or 'Unknown'}"
        vessel_key = v.get("VesselId") or v.get("Id") or v_name

        point = {
            "lat": float(v.get("Longitude") or v.get("lat") or 16.93),
            "lng": float(v.get("Latitude") or v.get("lng") or 82.26),
            "Comments": v.get("Comments", "-"),
            "DateTime": v.get("DateTime", "-"),
            "Speed": v.get("Speed", "-"),
            "IdleTime": v.get("IdleTime", "-"),
            "Battery": v.get("Battery", "-"),
            "Fuel1": v.get("Fuel1", "-"),
            "Fuel2": v.get("Fuel2", "-"),
            "RPM1": v.get("RPM1", "-"),
            "RPM2": v.get("RPM2", "-"),
            "Eng1RunStatus": "Running" if v.get("Eng1RunStatus") in [1, "1", "Running"] else "Idle",
            "Eng2RunStatus": "Running" if v.get("Eng2RunStatus") in [1, "1", "Running"] else "Idle"
        }
        if vessel_key not in routes:
            routes[vessel_key] = {"name": v_name, "path": []}
        routes[vessel_key]["path"].append(point)
    for vessel_route in routes.values():
        vessel_route["path"].sort(key=lambda p: (p.get("DateTime") or "", p.get("Comments") or ""))
    logger.info("user_map_auth_view: route grouping complete route_count=%s", len(routes))

    vessel_js_array = []
    # Darker color palette for markers
    dark_colors = ["#1a237e", "#b71c1c", "#1b5e20",
                   "#e65100", "#4a148c", "#004d40", "#212121", "#3e2723"]

    logger.info("user_map_auth_view: preparing vessel JavaScript data")
    for i, vessel_route in enumerate(routes.values()):
        vessel_js_array.append({
            "name": vessel_route["name"],
            "color": dark_colors[i % len(dark_colors)],
            "route": vessel_route["path"],
            "currentIndex": 0
        })

    # 4. JavaScript logic
    logger.info("user_map_auth_view: building map JavaScript")
    js_code = f"""
    <script>
    window.onload = function() {{
        var map = {m.get_name()};
        var vessels = {json.dumps(vessel_js_array)};
        var markers = [];
        var allRoutePoints = [];

        L.control.zoom({{ position: 'bottomleft' }}).addTo(map);

        function getPopupHTML(pt, vName) {{
            let dt = pt.DateTime.includes('T') ? pt.DateTime.replace('T', ' ').split('.')[0] : pt.DateTime;
            return `
            <div style="width:180px; font-family:Arial, sans-serif; font-size:11px; color:#333;">
                <div style="font-weight:bold; border-bottom:1px solid #ccc; padding-bottom:4px; margin-bottom:6px; color:#2f4f8f; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">
                    ${{vName}}
                </div>
                <div style="margin-bottom:5px; font-size:10px; color:#c53030; font-weight:bold;">🕒 ${{dt}}</div>
                <div style="display:flex; justify-content:space-between; margin-bottom:3px;">
                    <span>Speed: <b>${{pt.Speed}}</b></span>
                    <span>Bat: <b>${{pt.Battery}}V</b></span>
                </div>
                <div style="display:flex; justify-content:space-between; margin-bottom:3px;">
                    <span>F1: <b>${{pt.Fuel1}}L</b></span>
                    <span>F2: <b>${{pt.Fuel2}}L</b></span>
                </div>
                <div style="display:flex; justify-content:space-between; margin-bottom:3px;">
                    <span>R1: <b>${{pt.RPM1}}</b></span>
                    <span>R2: <b>${{pt.RPM2}}</b></span>
                </div>
                <div style="font-size:10px; margin-top:5px; border-top:1px dotted #ccc; padding-top:4px;">
                    E1: <span style="color:${{pt.Eng1RunStatus==='Running'?'green':'red'}}">${{pt.Eng1RunStatus}}</span> | 
                    E2: <span style="color:${{pt.Eng2RunStatus==='Running'?'green':'red'}}">${{pt.Eng2RunStatus}}</span>
                </div>
            </div>`;
        }}

        function createIcon(color) {{
            return L.divIcon({{
                className: '',
                html: `
                <div style="
                    position:relative;
                    width:30px;
                    height:18px;
                    transform:rotate(-90deg);
                    filter:drop-shadow(0 4px 6px rgba(0,0,0,0.45));
                ">
                    <div style="
                        position:absolute;
                        inset:2px 1px 2px 0;
                        background:linear-gradient(135deg, rgba(255,255,255,0.28), ${{color}} 38%, rgba(0,0,0,0.2));
                        border:2px solid #ffffff;
                        border-radius:18px 5px 5px 18px;
                        box-shadow:inset 0 1px 2px rgba(255,255,255,0.45), inset -3px 0 3px rgba(0,0,0,0.18);
                    "></div>
                    <div style="
                        position:absolute;
                        right:-1px;
                        top:5px;
                        width:0;
                        height:0;
                        border-top:4px solid transparent;
                        border-bottom:4px solid transparent;
                        border-left:8px solid #ffffff;
                    "></div>
                    <div style="
                        position:absolute;
                        left:8px;
                        top:5px;
                        width:8px;
                        height:6px;
                        background:rgba(255,255,255,0.9);
                        border-radius:3px;
                        box-shadow:8px 0 0 rgba(255,255,255,0.55);
                    "></div>
                </div>`,
                iconSize:[34,22],
                iconAnchor:[17,11]
            }});
        }}

        vessels.forEach(v => {{
            if (v.route.length === 0) return;
            let start = v.route[0];
            
            let marker = L.marker([start.lat, start.lng], {{
                icon: L.divIcon({{
                    className: '',
                    html: `<div style="background:${{v.color}}; width:12px; height:12px; border-radius:50%; border:2px solid white;"></div>`,
                    iconSize: [12, 12]
                }})
            }}).addTo(map);

            marker.bindPopup(getPopupHTML(start, v.name));

            // Polyline shows the history/path
            let polyline = L.polyline([[start.lat, start.lng]], {{
                color: v.color, weight: 3, opacity: 0.7
            }}).addTo(map);

            v.route.forEach(p => allRoutePoints.push([p.lat, p.lng]));
            markers.push({{ marker: marker, polyline: polyline, data: v }});
        }});

        if (allRoutePoints.length > 0) {{
            map.fitBounds(allRoutePoints, {{ padding: [35, 35] }});
        }}

      if (allRoutePoints.length > 0) map.fitBounds(allRoutePoints, {{padding:[30,30]}});

        function moveVessels() {{
            markers.forEach(obj => {{
                let v = obj.data;
                if (v.route.length <= 1) return;

                // Step by step increase with restart logic
                v.currentIndex = (v.currentIndex + 1) % v.route.length;
                let next = v.route[v.currentIndex];

                // Update Marker
                obj.marker.setLatLng([next.lat, next.lng]);
                
                // Update Polyline (Trail effect: from start up to current point)
                let currentPath = v.route.slice(0, v.currentIndex + 1).map(p => [p.lat, p.lng]);
                obj.polyline.setLatLngs(currentPath);

                if (obj.marker.getPopup() && obj.marker.isPopupOpen()) {{
                    obj.marker.setPopupContent(getPopupHTML(next, v.name));
                }}
            }});
        }}
        var moveTimer = setInterval(moveVessels, 1000);

        window.setMapSpeed = function(ms) {{
            clearInterval(moveTimer);
            moveTimer = setInterval(moveVessels, parseInt(ms));
        }};
    }};
    </script>
    """

    m.get_root().html.add_child(folium.Element(js_code))
    logger.info("user_map_auth_view: rendering template")
    return render(request, 'user_map_auth.html', {'map_auth_html': m._repr_html_()})
