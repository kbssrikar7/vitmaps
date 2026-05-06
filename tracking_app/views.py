import folium
import json
import os
from django.conf import settings
from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login
from django.contrib.auth.decorators import login_required

def allmaps_view(request):
    file_path = os.path.join(settings.BASE_DIR, 'static', 'data', 'vessels.json')

    if not os.path.exists(file_path):
        return render(request, 'allmaps.html', {'map_html': f"File not found: {file_path}"})

    with open(file_path) as f:
        vessels = json.load(f)

       # Base map (NO default tiles)
    m = folium.Map(
        location=[13, 80],
        zoom_start=5,
        control_scale=True,
        zoom_control=False,
        tiles=None
    )

    # ✅ Default layer (Esri - explicitly shown on load)
    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}",
        attr="Tiles © Esri",
        name="Esri World Street Map (English)",
        control=True,
        show=True
    ).add_to(m)

    # Other layers (not shown by default)
    folium.TileLayer(
        tiles="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
        attr="© OpenStreetMap contributors",
        name="OpenStreetMap",
        control=True,
        show=False
    ).add_to(m)

    folium.TileLayer(
        tiles="https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png",
        attr="© OpenStreetMap © Carto",
        name="Carto Light",
        subdomains="abcd",
        show=False
    ).add_to(m)

    folium.TileLayer(
        tiles="https://{s}.basemaps.cartocdn.com/light_nolabels/{z}/{x}/{y}.png",
        attr="© OpenStreetMap © Carto",
        name="Light No Labels",
        subdomains="abcd",
        show=False
    ).add_to(m)

    folium.TileLayer(
        tiles="https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png",
        attr="© OpenStreetMap © Carto",
        name="Carto Voyager",
        subdomains="abcd",
        show=False
    ).add_to(m)


    # Layer control
    folium.LayerControl(position="bottomright").add_to(m)

    # Popup HTML template
    popup_template = """
    <div style="
        width:100%;
        font-family:Segoe UI, Arial;
        font-size:10px;
        border-radius:10px;
        padding:0px;
        box-sizing:border-box;
        background:#ffffff;
    ">
        <div style="
            font-weight:600;
            font-size:12px;
            margin-bottom:0px;
            color:#333;
            border-bottom:1px solid #eee;
            padding-bottom:6px;
        ">
            Fuel2 City : {{Comments}}
        </div>

        <div style="display:flex; justify-content:space-between; margin-bottom:8px;">
            <div>
                <div style="color:#777; font-size:11px;">Date Time</div>
                <div>{{DateTime}}</div>
            </div>
            <div>
                <div style="color:#777; font-size:11px;">Speed</div>
                <div>{{Speed}} KM</div>
            </div>
            <div>
                <div style="color:#777; font-size:11px;">Idle</div>
                <div>{{IdleTime}} hr</div>
            </div>
        </div>

        <div style="margin-bottom:6px;">
            🔋 Battery: {{Battery}} V
        </div>

        <div style="display:flex; justify-content:space-between;">
            <div>⛽ Fuel1: {{Fuel1}} L</div>
            <div>⛽ Fuel2: {{Fuel2}} L</div>
        </div>
        <div style="display:flex; justify-content:space-between;">
            <div>Eng1 Status: {{Eng1RunStatus}}</div>
            <div>Eng2 Status: {{Eng2RunStatus}}</div>
        </div>
    </div>
    """

    # Group vessels by prefix (A, B, C...) to form routes
    routes = {}
    for v in vessels:
        prefix = ''.join([c for c in v["name"] if not c.isdigit()])
        routes.setdefault(prefix, []).append(v)

    vessel_js_array = []
    for prefix, group in routes.items():
        route_points = [{"lat": g["lat"], "lng": g["lng"]} for g in group]
        first = group[0]
        vessel_js_array.append({
            "name": prefix,  # same for all in group
            "lat": first.get("lat", 13),
            "lng": first.get("lng", 80),
            "color": first.get("color", "blue"),
            "popup": popup_template
                .replace("{{Comments}}", str(first.get("Comments", "-")))
                .replace("{{DateTime}}", str(first.get("DateTime", "-")))
                .replace("{{Speed}}", str(first.get("Speed", "-")))
                .replace("{{IdleTime}}", str(first.get("IdleTime", "-")))
                .replace("{{Battery}}", str(first.get("Battery", "-")))
                .replace("{{Fuel1}}", str(first.get("Fuel1", "-")))
                .replace("{{Fuel2}}", str(first.get("Fuel2", "-")))
                .replace("{{Eng1RunStatus}}", str(first.get("Eng1RunStatus", "-")))
                .replace("{{Eng2RunStatus}}", str(first.get("Eng2RunStatus", "-"))),
            "shape": "triangle",
            "route": route_points,
            "currentIndex": 0
        })


    js_code = f"""
    <script>
    window.onload = function() {{
        var map = {m.get_name()};
        var vessels = {json.dumps(vessel_js_array)};
        var markers = [];

        L.control.zoom({{ position: 'bottomleft' }}).addTo(map);

        function createTriangleIcon(color) {{
            return L.divIcon({{
                html: `<div style="
                    width: 0;
                    height: 0;
                    border-left: 8px solid transparent;
                    border-right: 8px solid transparent;
                    border-bottom: 16px solid ${{color}};
                    transform: rotate(45deg);
                "></div>`,
                className: "",
                iconSize: [16, 16],
                iconAnchor: [8, 8]
            }});
        }}

        function createDiamondIcon(color) {{
            return L.divIcon({{
                html: `<div style="
                    width: 16px;
                    height: 16px;
                    background-color: ${{color}};
                    transform: rotate(45deg);
                    border: 2px solid white;
                    box-shadow: 0 0 3px rgba(0,0,0,0.3);
                "></div>`,
                className: "",
                iconSize: [16, 16],
                iconAnchor: [8, 8]
            }});
        }}

        vessels.forEach(v => {{
            var icon = v.shape === "diamond"
                ? createDiamondIcon(v.color)
                : createTriangleIcon(v.color);

            var marker = L.marker([v.lat, v.lng], {{ icon: icon }}).addTo(map);
            marker.bindPopup(v.popup);
            markers.push({{marker: marker, data: v}});
        }});

        // ✅ Move ships along their route arrays
        function moveShips() {{
            markers.forEach(obj => {{
                let v = obj.data;
                if (v.route && v.route.length > 1) {{
                    v.currentIndex = (v.currentIndex + 1) % v.route.length;
                    let nextPoint = v.route[v.currentIndex];
                    obj.marker.setLatLng([nextPoint.lat, nextPoint.lng]);
                }}
            }});
        }}
        setInterval(moveShips, 1000);
    }};
    </script>
    """

    m.get_root().html.add_child(folium.Element(js_code))
    return render(request, 'allmaps.html', {'map_html': m._repr_html_()})

def login_view(request):
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
    return render(request, 'map.html')
