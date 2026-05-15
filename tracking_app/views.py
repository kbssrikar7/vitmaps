from django.urls import reverse
from .models import Vessel

import folium
import json
import logging
import os
import requests
import time
from django.conf import settings
from django.shortcuts import render, redirect
from django.template.loader import render_to_string
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm
from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse
from django.contrib.auth.models import User

from tracking_app.models import Vessel


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
        path_len = len(path)
        vessel_js_array.append({
            "name": name,
            "color": dark_colors[i % len(dark_colors)],
            "route": path,
            "currentIndex": path_len - 1 if path_len > 0 else 0
        })

    # 4. JavaScript logic
    logger.info("allmaps_view: building map JavaScript")
    js_code = render_to_string(
        "folium/allmaps_script.html",
        {
            "map_name": m.get_name(),
            "vessels_json": json.dumps(vessel_js_array),
        },
    )

    m.get_root().html.add_child(folium.Element(js_code))
    logger.info("allmaps_view: rendering template")
    return render(request, 'allmaps.html', {'map_html': m._repr_html_()})


def login_view(request):
    logger.info("login_view: request method=%s", request.method)
    if request.method == "POST":
        username = request.POST.get("username")
        password = request.POST.get("password")
        logger.info("login_view: authenticating username=%s", username)
        # EXTERNAL API LOGIN
        auth_url = "https://shiptrackingapiauth-787201059405.asia-south2.run.app/login"

        payload = {
            "username": username,
            "password": password
        }
        try:
            response = requests.post(
                auth_url,
                json=payload,
                timeout=10
            )
             # API SUCCESS
            if response.status_code == 200:
                data = response.json()

                # API TOKEN
                token = data.get("token")
                userId = data.get("userId")
               # DJANGO USER LOGIN

                user, created = User.objects.get_or_create(
                    username=username
                )
                # backend required for manual login
                user.backend = "django.contrib.auth.backends.ModelBackend"
                logger.info("login_view: authentication success username=%s", username)
                login(request, user)
                # STORE TOKEN IN SESSION
                request.session["bearer_token"] = token
                request.session["api_user_id"] = userId

                return JsonResponse({
                    "success": True,
                    "redirect_url": reverse("user_map_auth")
                })

            else:
                logger.warning("login_view: authentication failed username=%s", username)
                return JsonResponse({
                    "success": False,
                    "error": "Invalid API credentials"
                })

        except Exception as e:
            logger.error("login_view: API connection failed username=%s error=%s", username, e)
            return JsonResponse({
                "success": False,
                "error": "API connection failed"
            })
    logger.info("login_view: rendering login template")
    return render(request, "login.html")


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
    
    user_ID = request.session.get("api_user_id")
    b_token = request.session.get("bearer_token")

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

        err_html = render_to_string(
            "folium/error_banner.html",
            {"error_message": error_message},
        )

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

        # IMPORTANT ----------------------------------------------
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

        path_len = len(route["path"])

        vessel_js_array.append({
            "name": route["name"],
            "color": dark_colors[i % len(dark_colors)],
            "route": route["path"],
            "currentIndex": path_len - 1 if path_len > 0 else 0,
            "visible": True
        })

    # ==========================================================
    # FULL LIVE + REPLAY JS
    # ==========================================================
    js_code = render_to_string(
        "folium/user_map_auth_script.html",
        {
            "map_name": m.get_name(),
            "vessels_json": json.dumps(vessel_js_array),
        },
    )

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

""" def vessel_search_view(request):
    vessels = Vessel.objects.all()
    logger.info(vessels)
    search = request.GET.get('search', '')
    vessel_type = request.GET.get('type','')
    flag = request.GET.get('flag','')

    if search:
        vessels = vessels.filter(name__icontains=search)

    if vessel_type and vessel_type != 'All Types':
        vessels = vessels.filter(vessel_type=vessel_type)

    if flag and flag != 'All Flags':
        vessels = vessels.filter(flag=flag)

    context = {
        'vessels': vessels,
        'count': vessels.count(),
        'search': search,
        'selected_type': vessel_type,
        'selected_flag': flag,
    }
    logger.info("context=%s", context)
    return render(request, 'vessel_search.html', context) """



""" def vessel_search_view(request):

    logger.info("user_map_auth_view: request started user_authenticated=%s", request.user.is_authenticated)

    if not request.user.is_authenticated:
        logger.warning("vessel_search_view: unauthenticated request redirected to login")
        return redirect('login')
    
    #user_ID = request.session.get("api_user_id")
    user_ID ="1"
    b_token = request.session.get("bearer_token")

    api_url = f"https://shiptrackingapiauth-787201059405.asia-south2.run.app/VesselTracking/getbyVesselId/{user_ID}"

    headers = {
    "Authorization": b_token
         }

    try:
        response = requests.get(api_url,headers=headers)
        vessels = response.json()
    except Exception as e:
        vessels = []

    search = request.GET.get('search', '')
    vessel_type = request.GET.get('type','')
    flag = request.GET.get('flag','')

    # Search Filter
    if search:
        vessels = [
            vessel for vessel in vessels
            if search.lower() in vessel['vesselid'].lower()
        ]

    # Vessel Type Filter
    if vessel_type and vessel_type != 'All Types':
        vessels = [
            vessel for vessel in vessels
            if vessel['vessel_type'] == vessel_type
        ]

    # Flag Filter
    if flag and flag != 'All Flags':
        vessels = [
            vessel for vessel in vessels
            if vessel['flag'] == flag
        ]

    context = {
        'vessels': vessels,
        'count': len(vessels),
        'search': search,
        'selected_type': vessel_type,
        'selected_flag': flag,
    }

    return render(request, 'vessel_search.html', context) """

def vessel_search_view(request):

    logger.info(
        "vessel_search_view: user_authenticated=%s",
        request.user.is_authenticated
    )

    if not request.user.is_authenticated:
        return redirect('login')

    vessel_id = request.session.get("api_user_id")
    logger.info("Vessel ID: %s", vessel_id)
    bearer_token = request.session.get("bearer_token")
    logger.info("Bearer token: %s", bearer_token)

    api_url = (
        f"https://shiptrackingapiauth-787201059405.asia-south2.run.app/"
        f"GetAllVessels/{vessel_id}"
    )
    logger.info("api_url: %s", api_url)
    headers = {
        "Authorization": f"Bearer {bearer_token}",
        "Accept": "application/json"
    }

    vessels = []
    total_vessels_count = 0
    try:

        response = requests.get(
            api_url,
            headers=headers,
            timeout=10
        )

        logger.info(
            "API STATUS CODE = %s",
            response.status_code
        )
             
        if response.status_code == 200:

            api_data = response.json()
            total_vessels_count = api_data.get('count', 0) 
            logger.info("API RESPONSE = %s", api_data)

            # API may return dict or list
            # CASE 1 -> API returns LIST
            if isinstance(api_data, list):

                vessels = api_data   

            # CASE 2 -> API returns DICT
            elif isinstance(api_data, dict):

                # If data key exists
                if 'data' in api_data:

                    if isinstance(api_data['data'], list):
                        vessels = api_data['data']

                    elif isinstance(api_data['data'], dict):
                        vessels = [api_data['data']]

                else:
                    vessels = [api_data]



        else:
            logger.warning(
                "API FAILED STATUS=%s",
                response.status_code
            )

    except Exception as e:

        logger.exception("VESSEL API ERROR")

    # Aplly Filters

    search = request.GET.get('search', '').strip()
    vessel_type = request.GET.get('type', '').strip()
    flag = request.GET.get('flag', '').strip()

    # SEARCH FILTER
    if search:

        vessels = [
            vessel for vessel in vessels
            if search.lower() in str(
                vessel.get('VesselName', '')
            ).lower()
        ]
 
    logger.info(vessels)
    
    # TYPE FILTER
    if vessel_type and vessel_type != 'All Types':

        vessels = [
            vessel for vessel in vessels
            if vessel.get('VesselType', '') == vessel_type
        ]

    # FLAG FILTER
    if flag and flag != 'All Flags':

        vessels = [
            vessel for vessel in vessels
            if vessel.get('Flag', '') == flag
        ]

    context = {
        'vessels': vessels,
        'total_vessels_count': total_vessels_count,
        'count': len(vessels),
        'search': search,
        'selected_type': vessel_type,
        'selected_flag': flag,
    }

    logger.info("TOTAL VESSELS = %s", len(vessels))
    return render(
        request,
        'vessel_search.html',
        context
    )