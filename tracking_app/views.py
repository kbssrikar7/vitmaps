from django.urls import reverse

import folium
import json
import os
import requests
from django.conf import settings
from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm
from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse


def allmaps_view(request):
    # 1. Fetch live data from API URL
    # Check if vessels are already in session
    vessels_raw = request.session.get("vessels_raw", [])

    if not vessels_raw:
        api_url = "https://shiptrackingapi-787201059405.asia-south2.run.app/VesselTracking/GetAll"
        try:
            response = requests.get(api_url, timeout=10)
            if response.status_code == 200:
                vessels_raw = response.json()
        except Exception as e:
            print(f"Error fetching API data: {e}")
            # Fallback to local files if API fails
            data_dir = os.path.join(settings.BASE_DIR, 'static', 'data')
            if os.path.exists(data_dir):
                for filename in os.listdir(data_dir):
                    if filename.startswith('vessel') and filename.endswith('.json'):
                        with open(os.path.join(data_dir, filename)) as f:
                            try:
                                data = json.load(f)
                                if isinstance(data, list):
                                    vessels_raw.extend(data)
                                elif isinstance(data, dict):
                                    vessels_raw.append(data)
                            except:
                                continue

        # Store the fetched data in session for reuse
        request.session["vessels_raw"] = vessels_raw

    if not vessels_raw:
        return render(request, 'allmaps.html', {'map_html': "No vessel data available."})

    # 2. Base Map Setup
    m = folium.Map(
        location=[17.15, 82.4],
        zoom_start=6,
        control_scale=True,
        zoom_control=False,
        tiles=None
    )

    # --- ADD ALL LAYERS ---
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

    vessel_js_array = []
    # Darker color palette for markers
    dark_colors = ["#1a237e", "#b71c1c", "#1b5e20",
                   "#e65100", "#4a148c", "#004d40", "#212121", "#3e2723"]

    for i, (name, path) in enumerate(routes.items()):
        vessel_js_array.append({
            "name": name,
            "color": dark_colors[i % len(dark_colors)],
            "route": path,
            "currentIndex": 0
        })

    # 4. JavaScript logic
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
    return render(request, 'allmaps.html', {'map_html': m._repr_html_()})


def login_view(request):
    if request.method == "POST":
        username = request.POST.get('username')
        password = request.POST.get('password')
        user = authenticate(request, username=username, password=password)

        if user:
            login(request, user)
            return JsonResponse({"success": True, "redirect_url": reverse("user_map_auth")})
        else:
            return JsonResponse({"success": False, "error": "Invalid credentials"})
    return render(request, 'login.html')


def logout_view(request):
    logout(request)
    return redirect('allmaps')


def register_view(request):
    if request.method == 'POST':
        form = UserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({"success": True, "redirect_url": "/"})
            return redirect('allmaps')
        else:
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                errors = "\n".join(
                    [f"{k}: {v[0]}" for k, v in form.errors.items()])
                return JsonResponse({"success": False, "error": errors})
    else:
        form = UserCreationForm()

    for field in form.fields.values():
        field.widget.attrs.update({'class': 'form-control'})

    return render(request, 'register.html', {'form': form})


def user_map_auth_view(request):
    if not request.user.is_authenticated:
        return redirect('login')
    user_ID = 4
    b_token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1c2VySWQiOjYsInVzZXJuYW1lIjoiQWRtaW4iLCJpYXQiOjE3NzgyMjMzMTAsImV4cCI6MTc3ODIyNTExMH0.5Kzng4iRoMKN84FNQamXmzaB_Qgaz8rVw21bmlwu5lo"
    print(f"user ID : {user_ID}")
    print(f"BToken : {b_token}")

    # 1. Fetch live data from AUTH API URL (GET with Bearer token)
    api_url = f"https://shiptrackingapiauth-787201059405.asia-south2.run.app/VesselTracking/UserAssociatedVessels/{user_ID}"
    vessels_raw = request.session.get("vessels_raw", [])
    bearer_token = getattr(settings, "SHIP_API_BEARER_TOKEN", f"{b_token}")
    print(f"Bearer Token : {bearer_token}")
    headers = {
        "Authorization": f"Bearer {bearer_token}",
        "Accept": "application/json"
    }

    try:
        response = requests.get(api_url, headers=headers, timeout=10)
        if response.status_code == 200:
            vessels_raw = response.json()
    except Exception as e:
        print(f"Error fetching API data: {e}")
        # Fallback to local files if API fails
        data_dir = os.path.join(settings.BASE_DIR, 'static', 'data')
        if os.path.exists(data_dir):
            for filename in os.listdir(data_dir):
                if filename.startswith('vessel') and filename.endswith('.json'):
                    with open(os.path.join(data_dir, filename)) as f:
                        try:
                            data = json.load(f)
                            if isinstance(data, list):
                                vessels_raw.extend(data)
                            elif isinstance(data, dict):
                                vessels_raw.append(data)
                        except:
                            continue

        # Store the fetched data in session for reuse
        request.session["vessels_raw"] = vessels_raw

    if not vessels_raw:
        return render(request, 'allmaps.html', {'map_html': "No vessel data available."})

    # 2. Base Map Setup
    m = folium.Map(
        location=[17.15, 82.4],
        zoom_start=6,
        control_scale=True,
        zoom_control=False,
        tiles=None
    )

    # --- ADD ALL LAYERS ---
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

    vessel_js_array = []
    # Darker color palette for markers
    dark_colors = ["#1a237e", "#b71c1c", "#1b5e20",
                   "#e65100", "#4a148c", "#004d40", "#212121", "#3e2723"]

    for i, (name, path) in enumerate(routes.items()):
        vessel_js_array.append({
            "name": name,
            "color": dark_colors[i % len(dark_colors)],
            "route": path,
            "currentIndex": 0
        })

    # 4. JavaScript logic
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
            let marker = L.marker([start.lat, start.lng], {{ icon: createIcon(v.color) }}).addTo(map);
            
            marker.bindPopup(getPopupHTML(start, v.name), {{
                maxWidth: 190,
                minWidth: 180,
                autoPan: true
            }});

            // Add dynamic polyline for this vessel
            let polyline = L.polyline([[start.lat, start.lng]], {{
                color: v.color,
                weight: 3,
                opacity: 0.8
            }}).addTo(map);

            markers.push({{ marker: marker, polyline: polyline, data: v }});
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
                    
                    // Update Marker
                    obj.marker.setLatLng([next.lat, next.lng]);
                    if (obj.marker.getPopup()) {{
                        obj.marker.setPopupContent(getPopupHTML(next, v.name));
                    }}

                    // Update Polyline
                    if (v.currentIndex === 0) {{
                        obj.polyline.setLatLngs([[next.lat, next.lng]]);
                    }} else {{
                        obj.polyline.addLatLng([next.lat, next.lng]);
                    }}
                }});
            }}, moveInterval);
        }};

        window.setMapSpeed(1000);
    }};
    </script>
    """

    m.get_root().html.add_child(folium.Element(js_code))
    return render(request, 'user_map_auth.html', {'map_auth_html': m._repr_html_()})

    return allmaps_view(request)
