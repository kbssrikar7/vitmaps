import folium
import json
import os
from django.conf import settings
from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm
from django.views.decorators.csrf import csrf_exempt

def allmaps_view(request):
    # 1. Load Vessel Data
    vessel_files = [
        os.path.join(settings.BASE_DIR, 'static', 'data', 'vesselA.json'),
        os.path.join(settings.BASE_DIR, 'static', 'data', 'vesselB.json'),
        os.path.join(settings.BASE_DIR, 'static', 'data', 'vesselC.json'),
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

    # 2. Base Map Setup (No default tiles)
    m = folium.Map(
        location=[13, 80],
        zoom_start=5,
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

    # 3. Group and Process Routes
    routes = {}
    for v in vessels_raw:
        name = v.get("name") or v.get("VesselName") or "Vessel"
        prefix = ''.join([c for c in name if not c.isdigit()])
        
        # Format the point data for JS
        point = {
            "lat": float(v.get("Latitude") or v.get("lat") or 13.0),
            "lng": float(v.get("Longitude") or v.get("lng") or 80.0),
            "Comments": v.get("Comments", "-"),
            "DateTime": v.get("DateTime", "-"),
            "Speed": v.get("Speed", "-"),
            "IdleTime": v.get("IdleTime", "-"),
            "Battery": v.get("Battery", "-"),
            "Fuel1": v.get("Fuel1", "-"),
            "Fuel2": v.get("Fuel2", "-"),
            "Eng1RunStatus": v.get("Eng1RunStatus", "-"),
            "Eng2RunStatus": v.get("Eng2RunStatus", "-")
        }
        routes.setdefault(prefix, []).append(point)

    vessel_js_array = []
    for name, path in routes.items():
        vessel_js_array.append({
            "name": name,
            "color": "blue", # You can set this per vessel if needed
            "route": path,
            "currentIndex": 0
        })

    # 4. JavaScript logic for Movement + Dynamic Popup
    js_code = f"""
    <script>
    window.onload = function() {{
        var map = {m.get_name()};
        var vessels = {json.dumps(vessel_js_array)};
        var markers = [];

        // Manual Zoom Control at Bottom Left
        L.control.zoom({{ position: 'bottomleft' }}).addTo(map);

        function getPopupHTML(pt) {{
            return `
            <div style="width:200px; font-family:Segoe UI, Arial; font-size:10px; background:#ffffff;">
                <div style="font-weight:600; font-size:12px; border-bottom:1px solid #eee; padding-bottom:6px; margin-bottom:6px;">
                    Fuel2 City : ${{pt.Comments}}
                </div>
                <div style="display:flex; justify-content:space-between; margin-bottom:8px;">
                    <div><div style="color:#777; font-size:11px;">Date Time</div><div>${{pt.DateTime}}</div></div>
                    <div><div style="color:#777; font-size:11px;">Speed</div><div>${{pt.Speed}} KM</div></div>
                    <div><div style="color:#777; font-size:11px;">Idle</div><div>${{pt.IdleTime}} hr</div></div>
                </div>
                <div style="margin-bottom:6px;">🔋 Battery: ${{pt.Battery}} V</div>
                <div style="display:flex; justify-content:space-between;">
                    <div>⛽ Fuel1: ${{pt.Fuel1}} L</div>
                    <div>⛽ Fuel2: ${{pt.Fuel2}} L</div>
                </div>
                <div style="display:flex; justify-content:space-between; margin-top:4px;">
                    <div>Eng1: ${{pt.Eng1RunStatus}}</div>
                    <div>Eng2: ${{pt.Eng2RunStatus}}</div>
                </div>
            </div>`;
        }}

        function createIcon(color) {{
            return L.divIcon({{
                html: `<div style="width:0; height:0; border-left:8px solid transparent; border-right:8px solid transparent; border-bottom:16px solid ${{color}}; transform:rotate(45deg);"></div>`,
                className: "", iconSize: [16, 16], iconAnchor: [8, 8]
            }});
        }}

        vessels.forEach(v => {{
            let start = v.route[0];
            let marker = L.marker([start.lat, start.lng], {{ icon: createIcon(v.color) }}).addTo(map);
            
            // Re-generate content when clicked to show LATEST position data
            marker.on('click', function() {{
                let currentPoint = v.route[v.currentIndex];
                marker.bindPopup(getPopupHTML(currentPoint)).openPopup();
            }});

            markers.push({{ marker: marker, data: v }});
        }});

        // Movement loop
        setInterval(function() {{
            markers.forEach(obj => {{
                let v = obj.data;
                v.currentIndex = (v.currentIndex + 1) % v.route.length;
                let next = v.route[v.currentIndex];
                obj.marker.setLatLng([next.lat, next.lng]);
                
                // If popup is currently open, update its content live
                if (obj.marker.getPopup() && obj.marker.getPopup().isOpen()) {{
                    obj.marker.setPopupContent(getPopupHTML(next));
                }}
            }});
        }}, 1000);
    }};
    </script>
    """

    m.get_root().html.add_child(folium.Element(js_code))
    return render(request, 'allmaps.html', {'map_html': m._repr_html_()})

from django.http import JsonResponse

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
    
    # If GET, and not AJAX, we might still want to render the login page, 
    # but the requirement is popup, so we mostly handle POST here.
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
                # Return form errors as a simple string for the alert/popup
                errors = "\n".join([f"{k}: {v[0]}" for k, v in form.errors.items()])
                return JsonResponse({"success": False, "error": errors})
    else:
        form = UserCreationForm()
    
    # Add bootstrap classes to form fields
    for field in form.fields.values():
        field.widget.attrs.update({'class': 'form-control'})
        
    return render(request, 'register.html', {'form': form})


'''def login_view(request):
    if request.method == "POST":
        username = request.POST['username']
        password = request.POST['password']
        user = authenticate(request, username=username, password=password)
        if user:
            login(request, user)
            return redirect('map')
        else:
            return render(request, 'login.html', {'error': 'Invalid credentials'})
    return render(request, 'login.html')

@login_required
def map_view(request):
    return render(request, 'map.html')'''
