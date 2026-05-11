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
                    <span>Lat: <b>${{pt.lat}}</b></span>
                    <span>Lng: <b>${{pt.lng}}</b></span>
                </div>
                <div style="display:flex; justify-content:space-between; margin-bottom:3px;">
                    <span>Speed: <b>${{pt.Speed}}</b></span>
                    <span>Bat: <b>${{pt.Battery}}V</b></span>
                </div>
                <div style="display:flex; justify-content:space-between; margin-bottom:3px;">
                    <span>Fuel1: <b>${{pt.Fuel1}}L</b></span>
                    <span>Fuel2: <b>${{pt.Fuel2}}L</b></span>
                </div>
                <div style="display:flex; justify-content:space-between; margin-bottom:3px;">
                    <span>RPM1: <b>${{pt.RPM1}}</b></span>
                    <span>RPM2: <b>${{pt.RPM2}}</b></span>
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
                        obj.marker.getPopup().setContent(getPopupHTML(next, v.name));
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

    user_ID =4 

    b_token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1c2VySWQiOjYsInVzZXJuYW1lIjoiQWRtaW4iLCJpYXQiOjE3Nzg0Nzc2OTksImV4cCI6MTc3ODQ3OTQ5OX0.rvsL1474hrN8UzTsXra1ZY9UwEGC1cYGV4UYCO19DHc"

    vessels_raw = request.session.get("auth_vessels_data", [])
    last_fetch = request.session.get("auth_vessels_last_fetch", 0)
    force_refresh = request.GET.get('refresh') == 'true'

    error_message = None

    # ==========================================================
    # FETCH API DATA
    # ==========================================================
    if force_refresh or not vessels_raw or (time.time() - last_fetch > 300):

        bearer_token = getattr(settings, "SHIP_API_BEARER_TOKEN", b_token)

        headers = {
            "Authorization": f"Bearer {bearer_token}",
            "Accept": "application/json"
        }

        vessels_raw = []

        try:

            assoc_url = f"https://shiptrackingapiauth-787201059405.asia-south2.run.app/VesselTracking/UserAssociatedVessels/{user_ID}"

            assoc_res = requests.get(
                assoc_url,
                headers=headers,
                timeout=10
            )

            if assoc_res.status_code == 200:

                assoc_data = assoc_res.json()

                v_ids = []

                if isinstance(assoc_data, list):

                    v_ids = [
                        str(v.get('VesselId'))
                        for v in assoc_data
                        if v.get('VesselId')
                    ]

                elif isinstance(assoc_data, dict):

                    if assoc_data.get('VesselId'):
                        v_ids = [str(assoc_data.get('VesselId'))]

                # ==================================================
                # GET EACH VESSEL TRACKING HISTORY
                # ==================================================
                for vessel_id in v_ids:

                    latest_url = f"https://shiptrackingapiauth-787201059405.asia-south2.run.app/VesselTracking/getbyVesselId/{vessel_id}"

                    latest_res = requests.get(
                        latest_url,
                        headers=headers,
                        timeout=10
                    )

                    if latest_res.status_code == 200:

                        latest_data = latest_res.json()

                        if isinstance(latest_data, list):
                            vessels_raw.extend(latest_data)

                        elif isinstance(latest_data, dict):
                            vessels_raw.append(latest_data)

                    else:
                        logger.warning("Failed vessel_id=%s", vessel_id)

                request.session["auth_vessels_data"] = vessels_raw
                request.session["auth_vessels_last_fetch"] = time.time()

            else:
                error_message = f"Associated Vessel API Error: {assoc_res.status_code}"

        except Exception as e:

            logger.exception("API FAILED")

            error_message = str(e)

    # ==========================================================
    # MAP
    # ==========================================================
    m = folium.Map(
        location=[17.15, 82.4],
        zoom_start=6,
        control_scale=True,
        zoom_control=False,
        tiles=None
    )

    # ==========================================================
    # TILE LAYERS
    # ==========================================================
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

    # ==========================================================
    # ERROR MESSAGE
    # ==========================================================
    if error_message:

        err_html = f"""
        <div style="
            position:fixed;
            top:10px;
            left:50%;
            transform:translateX(-50%);
            z-index:99999;
            background:red;
            color:white;
            padding:10px 20px;
            border-radius:8px;
            font-size:14px;
        ">
            {error_message}
        </div>
        """

        m.get_root().html.add_child(folium.Element(err_html))

    # ==========================================================
    # GROUP ROUTES
    # ==========================================================
    routes = {}

    for v in vessels_raw:

        vessel_name = (
            v.get("VesselName")
            or f"Ship {v.get('VesselId')}"
        )

        vessel_key = (
            v.get("VesselId")
            or v.get("Id")
            or vessel_name
        )

        # IMPORTANT
        # YOUR API LAT/LNG WERE REVERSED
        # FIXED HERE

        lat = float(v.get("Longitude") or v.get("lat") or 17.15)
        lng = float(v.get("Latitude") or v.get("lng") or 82.4)

        point = {
            "lat": lat,
            "lng": lng,
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

            routes[vessel_key] = {
                "name": vessel_name,
                "path": []
            }

        routes[vessel_key]["path"].append(point)

    # SORT BY DATETIME
    for r in routes.values():

        r["path"].sort(
            key=lambda x: x.get("DateTime", "")
        )

    # ==========================================================
    # JS DATA
    # ==========================================================
    dark_colors = [
        "#1a237e",
        "#b71c1c",
        "#1b5e20",
        "#e65100",
        "#4a148c",
        "#004d40",
        "#212121",
        "#3e2723"
    ]

    vessel_js_array = []

    for i, route in enumerate(routes.values()):

        vessel_js_array.append({
            "name": route["name"],
            "color": dark_colors[i % len(dark_colors)],
            "route": route["path"],
            "currentIndex": 0,
            "visible": True
        })

    # ==========================================================
    # FULL LIVE + REPLAY JS
    # ==========================================================
    js_code = f"""
    <script>

    window.onload = function() {{

        var map = {m.get_name()};

        var vessels = {json.dumps(vessel_js_array)};

        var vesselObjects = [];

        var animationSpeed = 1000;

        var moveTimer = null;

        // ======================================================
        // ZOOM CONTROL
        // ======================================================
        L.control.zoom({{
            position:'bottomleft'
        }}).addTo(map);

        // ======================================================
        // POPUP
        // ======================================================
        function getPopupHTML(pt, vName) {{

            let dt = pt.DateTime.includes('T')
                ? pt.DateTime.replace('T',' ').split('.')[0]
                : pt.DateTime;

            return `
            <div style="width:180px; font-family:Arial, sans-serif; font-size:11px; color:#333;">
                <div style="font-weight:bold; border-bottom:1px solid #ccc; padding-bottom:4px; margin-bottom:6px; color:#2f4f8f; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">
                    ${{vName}}
                </div>
                <div style="margin-bottom:5px; font-size:10px; color:#c53030; font-weight:bold;">🕒 ${{dt}}</div>
                <div style="display:flex; justify-content:space-between; margin-bottom:3px;">
                    <span>Lat: <b>${{pt.lat}}</b></span>
                    <span>Lng: <b>${{pt.lng}}</b></span>
                </div>
                <div style="display:flex; justify-content:space-between; margin-bottom:3px;">
                    <span>Speed: <b>${{pt.Speed}}</b></span>
                    <span>Bat: <b>${{pt.Battery}}V</b></span>
                </div>
                <div style="display:flex; justify-content:space-between; margin-bottom:3px;">
                    <span>Fuel1: <b>${{pt.Fuel1}}L</b></span>
                    <span>Fuel2: <b>${{pt.Fuel2}}L</b></span>
                </div>
                <div style="display:flex; justify-content:space-between; margin-bottom:3px;">
                    <span>RPM1: <b>${{pt.RPM1}}</b></span>
                    <span>RPM2: <b>${{pt.RPM2}}</b></span>
                </div>
                <div style="font-size:10px; margin-top:5px; border-top:1px dotted #ccc; padding-top:4px;">
                    E1: <span style="color:${{pt.Eng1RunStatus==='Running'?'green':'red'}}">${{pt.Eng1RunStatus}}</span> |
                    E2: <span style="color:${{pt.Eng2RunStatus==='Running'?'green':'red'}}">${{pt.Eng2RunStatus}}</span>
                </div>
            </div>`;
        }}

        // ======================================================
        // YOUR ORIGINAL SHIP ICON
        // ======================================================
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
                        background:linear-gradient(
                            135deg,
                            rgba(255,255,255,0.28),
                            ${{color}} 38%,
                            rgba(0,0,0,0.2)
                        );
                        border:2px solid #ffffff;
                        border-radius:18px 5px 5px 18px;
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
                    "></div>

                </div>
                `,
                iconSize:[34,22],
                iconAnchor:[17,11]
            }});
        }}

        // ======================================================
        // TOGGLEABLE CONTROL PANEL
        // ======================================================
        var controlPanel = L.control({{position:'bottomleft'}});

        controlPanel.onAdd = function() {{

            var container = L.DomUtil.create('div', 'control-panel-container');

            // 1. THE TOGGLE BUTTON (Movable effect via absolute positioning in control corner)
            var toggleBtn = L.DomUtil.create('div', '', container);
            toggleBtn.innerHTML = `
                <div id="panelToggleBtn" style="
                    background: #1565c0;
                    color: white;
                    width: 45px;
                    height: 45px;
                    border-radius: 50%;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    cursor: pointer;
                    box-shadow: 0 4px 12px rgba(0,0,0,0.3);
                    font-size: 20px;
                    transition: transform 0.2s;
                " title="Toggle Controls">
                    ⚙️
                </div>
            `;

            // 2. THE ACTUAL PANEL (Hidden by default)
            var div = L.DomUtil.create('div', '', container);
            div.id = "vesselControlPanel";
            div.style.display = "none"; // Auto hide
            div.style.background = "white";
            div.style.padding = "15px";
            div.style.width = "240px";
            div.style.borderRadius = "12px";
            div.style.boxShadow = "0 8px 24px rgba(0,0,0,0.2)";
            div.style.fontFamily = "Arial, sans-serif";
            div.style.marginTop = "10px";

            div.innerHTML = `
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px;">
                    <span style="font-size:16px; font-weight:bold;">Vessel Controls</span>
                    <span id="closePanelBtn" style="cursor:pointer; font-size:18px;">&times;</span>
                </div>

                <div style="display:grid; grid-template-columns: 1fr 1fr; gap:8px; margin-bottom:10px;">
                    <button onclick="startReplay()" style="padding:8px; border:none; background:#1565c0; color:white; border-radius:6px; cursor:pointer;">▶ Play</button>
                    <button onclick="pauseReplay()" style="padding:8px; border:none; background:#d32f2f; color:white; border-radius:6px; cursor:pointer;">⏸ Pause</button>
                </div>

                <button onclick="resetReplay()" style="width:100%; margin-bottom:12px; padding:8px; border:none; background:#2e7d32; color:white; border-radius:6px; cursor:pointer;">⟳ Reset Replay</button>

                <label style="font-size:12px; color:#666;">Animation Speed</label>
                <select onchange="changeSpeed(this.value)" style="width:100%; padding:8px; margin-top:5px; border-radius:4px; border:1px solid #ccc;">
                    <option value="2000">🐌 Slow</option>
                    <option value="1000" selected>Normal</option>
                    <option value="400">⚡ Fast</option>
                    <option value="150">🚀 Very Fast</option>
                </select>

                <hr style="margin:15px 0; border:0; border-top:1px solid #eee;">
                <div style="font-size:13px; font-weight:bold; margin-bottom:8px;">Vessels</div>
                <div id="vesselList" style="max-height:150px; overflow-y:auto;"></div>
            `;

            // DRAGGABLE & TOGGLE LOGIC
            L.DomEvent.disableClickPropagation(container);

            var isDragging = false;
            var startX, startY;
            var currentX = 0, currentY = 0;
            var initialX, initialY;
            var xOffset = 0, yOffset = 0;

            function dragStart(e) {{
                if (e.type === "touchstart") {{
                    initialX = e.touches[0].clientX - xOffset;
                    initialY = e.touches[0].clientY - yOffset;
                }} else {{
                    initialX = e.clientX - xOffset;
                    initialY = e.clientY - yOffset;
                }}
                
                if (e.target === toggleBtn || toggleBtn.contains(e.target)) {{
                    isDragging = true;
                    startX = (e.type === "touchstart") ? e.touches[0].clientX : e.clientX;
                    startY = (e.type === "touchstart") ? e.touches[0].clientY : e.clientY;
                }}
            }}

            function dragEnd(e) {{
                initialX = currentX;
                initialY = currentY;
                isDragging = false;

                // If it was a click (not a drag), toggle the panel
                var endX = (e.type === "touchend") ? e.changedTouches[0].clientX : e.clientX;
                var endY = (e.type === "touchend") ? e.changedTouches[0].clientY : e.clientY;
                
                if (Math.abs(endX - startX) < 5 && Math.abs(endY - startY) < 5) {{
                    var panel = document.getElementById('vesselControlPanel');
                    panel.style.display = (panel.style.display === "none") ? "block" : "none";
                }}
            }}

            function drag(e) {{
                if (isDragging) {{
                    e.preventDefault();
                    if (e.type === "touchmove") {{
                        currentX = e.touches[0].clientX - initialX;
                        currentY = e.touches[0].clientY - initialY;
                    }} else {{
                        currentX = e.clientX - initialX;
                        currentY = e.clientY - initialY;
                    }}

                    xOffset = currentX;
                    yOffset = currentY;

                    setTranslate(currentX, currentY, container);
                }}
            }}

            function setTranslate(xPos, yPos, el) {{
                el.style.transform = "translate3d(" + xPos + "px, " + yPos + "px, 0)";
            }}

            toggleBtn.addEventListener("touchstart", dragStart, false);
            document.addEventListener("touchend", dragEnd, false);
            document.addEventListener("touchmove", drag, false);

            toggleBtn.addEventListener("mousedown", dragStart, false);
            document.addEventListener("mouseup", dragEnd, false);
            document.addEventListener("mousemove", drag, false);

            // Close button logic
            setTimeout(() => {{
                var closeBtn = document.getElementById('closePanelBtn');
                if(closeBtn) {{
                    L.DomEvent.on(closeBtn, 'click', function() {{
                        document.getElementById('vesselControlPanel').style.display = "none";
                    }});
                }}
            }}, 100);

            return container;
        }};

        controlPanel.addTo(map);

        // ======================================================
        // CREATE VESSELS
        // ======================================================
        var allPoints = [];

        vessels.forEach((v, index) => {{

            if(v.route.length === 0) return;

            let start = v.route[0];

            let marker = L.marker(
                [start.lat, start.lng],
                {{
                    icon:createIcon(v.color)
                }}
            ).addTo(map);

            marker.bindPopup(
                getPopupHTML(start, v.name)
            );

            // FULL ROUTE POLYLINE
            let fullPolyline = L.polyline(
                v.route.map(p => [p.lat, p.lng]),
                {{
                    color:v.color,
                    weight:2,
                    opacity:0.25,
                    dashArray:'6,6'
                }}
            ).addTo(map);

            // LIVE REPLAY LINE
            let replayPolyline = L.polyline(
                [[start.lat, start.lng]],
                {{
                    color:v.color,
                    weight:5,
                    opacity:0.9
                }}
            ).addTo(map);

            vesselObjects.push({{
                marker:marker,
                fullPolyline:fullPolyline,
                replayPolyline:replayPolyline,
                data:v
            }});

            // VESSEL TOGGLE
            let vesselList = document.getElementById('vesselList');

            vesselList.innerHTML += `
                <div style="margin-bottom:6px;">
                    <input
                        type="checkbox"
                        checked
                        onchange="toggleVessel(${{index}}, this.checked)"
                    >
                    <span style="color:${{v.color}};font-weight:bold;">
                        ${{v.name}}
                    </span>
                </div>
            `;

            v.route.forEach(p => {{
                allPoints.push([p.lat, p.lng]);
            }});

        }});

        // ======================================================
        // FIT MAP
        // ======================================================
        if(allPoints.length > 0) {{
            map.fitBounds(allPoints, {{
                padding:[40,40]
            }});
        }}

        // ======================================================
        // MOVE
        // ======================================================
        function moveVessels() {{

            vesselObjects.forEach(obj => {{

                let v = obj.data;

                if(!v.visible) return;

                if(v.route.length <= 1) return;

                if(v.currentIndex >= v.route.length - 1)
                    return;

                v.currentIndex++;

                let next = v.route[v.currentIndex];

                obj.marker.setLatLng([
                    next.lat,
                    next.lng
                ]);

                // REPLAY LINE
                let replayPath = v.route
                    .slice(0, v.currentIndex + 1)
                    .map(p => [p.lat, p.lng]);

                obj.replayPolyline.setLatLngs(replayPath);

                if(obj.marker.isPopupOpen()) {{

                    obj.marker.setPopupContent(
                        getPopupHTML(next, v.name)
                    );

                }}

            }});

        }}

        // ======================================================
        // REPLAY FUNCTIONS
        // ======================================================
        window.startReplay = function() {{

            if(moveTimer)
                clearInterval(moveTimer);

            moveTimer = setInterval(
                moveVessels,
                animationSpeed
            );

        }}

        window.pauseReplay = function() {{

            clearInterval(moveTimer);

        }}

        window.resetReplay = function() {{

            clearInterval(moveTimer);

            vesselObjects.forEach(obj => {{

                let v = obj.data;

                v.currentIndex = 0;

                let first = v.route[0];

                obj.marker.setLatLng([
                    first.lat,
                    first.lng
                ]);

                obj.replayPolyline.setLatLngs([
                    [first.lat, first.lng]
                ]);

            }});

        }}

        // ======================================================
        // SPEED
        // ======================================================
        window.changeSpeed = function(speed) {{

            animationSpeed = parseInt(speed);

            startReplay();

        }}

        // ======================================================
        // SHOW/HIDE VESSEL
        // ======================================================
        window.toggleVessel = function(index, checked) {{

            let obj = vesselObjects[index];

            obj.data.visible = checked;

            if(checked) {{

                map.addLayer(obj.marker);
                map.addLayer(obj.fullPolyline);
                map.addLayer(obj.replayPolyline);

            }}
            else {{

                map.removeLayer(obj.marker);
                map.removeLayer(obj.fullPolyline);
                map.removeLayer(obj.replayPolyline);

            }}

        }}

        // ======================================================
        // AUTO START
        // ======================================================
        startReplay();

    }};

    </script>
    """

    m.get_root().html.add_child(
        folium.Element(js_code)
    )

    logger.info("Rendering template")

    return render(
        request,
        'user_map_auth.html',
        {
            'map_auth_html': m._repr_html_()
        }
    )