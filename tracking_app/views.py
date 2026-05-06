import folium
import json
import os
from django.conf import settings
from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm
from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse

def allmaps_view(request):
    # 1. Load Vessel Data
    vessel_files = [
        os.path.join(settings.BASE_DIR, 'static', 'data', 'vesselA.json'),
        #os.path.join(settings.BASE_DIR, 'static', 'data', 'vesselB.json'),
        #os.path.join(settings.BASE_DIR, 'static', 'data', 'vesselC.json'),
    ]

    vessels_raw = []
    for vf in vessel_files:
        if os.path.exists(vf):
            with open(vf) as f:
                try:
                    data = json.load(f)
                    if isinstance(data, list):
                        vessels_raw.extend(data)
                except Exception:
                    continue

    if not vessels_raw:
        return render(request, 'allmaps.html', {'map_html': "No vessel data found."})

    # 2. Base Map Setup
    m = folium.Map(
        location=[17.15, 82.4], # Improved center for Kakinada cluster
        zoom_start=11,
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

    # 3. Group and Process Routes by VesselId
    routes = {}
    for v in vessels_raw:
        v_id = v.get("VesselId") or v.get("Id") or "Vessel"
        v_name = f"Vessel {v_id}"
        
        point = {
            "lat": float(v.get("Longitude") or 16.93), # Note: In provided JSON Longitude and Latitude values seem swapped or specific to area
            "lng": float(v.get("Latitude") or 82.26),  # Adjusting based on typical KKD coordinates
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
    colors = ["blue", "red", "green", "orange", "purple"]
    for i, (name, path) in enumerate(routes.items()):
        vessel_js_array.append({
            "name": name,
            "color": colors[i % len(colors)],
            "route": path,
            "currentIndex": 0
        })

    # 4. JavaScript logic for Movement + Dynamic Popup + Speed Control
    js_code = f"""
    <script>
    window.onload = function() {{
        var map = {m.get_name()};
        var vessels = {json.dumps(vessel_js_array)};
        var markers = [];

        L.control.zoom({{ position: 'bottomleft' }}).addTo(map);

        function getPopupHTML(pt) {{
            return `
            <div style="width:180px; font-family:Arial, sans-serif; font-size:11px; color:#333;">
                <div style="font-weight:bold; border-bottom:1px solid #ccc; padding-bottom:4px; margin-bottom:6px; color:#2f4f8f; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">
                    ${{pt.Comments}}
                </div>
                <div style="margin-bottom:5px; font-size:10px; color:#666;">🕒 ${{pt.DateTime.replace('T', ' ').split('.')[0]}}</div>
                <div style="display:flex; justify-content:space-between; margin-bottom:3px;">
                    <span>Speed: <b>${{pt.Speed}}</b></span>
                    <span>Battery: <b>${{pt.Battery}}V</b></span>
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
                html: `<div style="width: 0; height: 0; border-left: 7px solid transparent; border-right: 7px solid transparent; border-bottom: 14px solid ${{color}}; filter: drop-shadow(0 2px 3px rgba(0,0,0,0.4));"></div>`,
                className: "", iconSize: [14, 14], iconAnchor: [7, 7]
            }});
        }}

        vessels.forEach(v => {{
            if (v.route.length === 0) return;
            let start = v.route[0];
            let marker = L.marker([start.lat, start.lng], {{ icon: createIcon(v.color) }}).addTo(map);
            
            // Bind small popup with minimal padding
            marker.bindPopup(getPopupHTML(start), {{
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
                    
                    // Always update popup content so it's fresh when opened
                    obj.marker.setPopupContent(getPopupHTML(next));
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
            return JsonResponse({"success": True, "redirect_url": "/"})
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
                errors = "\n".join([f"{k}: {v[0]}" for k, v in form.errors.items()])
                return JsonResponse({"success": False, "error": errors})
    else:
        form = UserCreationForm()
    
    for field in form.fields.values():
        field.widget.attrs.update({'class': 'form-control'})
        
    return render(request, 'register.html', {'form': form})
