import requests
import pandas as pd
import pydeck as pdk
import streamlit as st

st.set_page_config(
    page_title="NZ Transport Cost + CO₂",
    page_icon="🚉",
    layout="wide",
)

API_KEY = st.secrets.get("GOOGLE_MAPS_API_KEY", "")

# NZ-based / MVP emission factors
EMISSION = {
    "Car": 0.128,      # Toyota Aqua hybrid approx
    "Transit": 0.05,   # blended transit estimate for MVP
    "Train": 0.0148,   # electric rail NZ
    "Bus": 0.155,      # average NZ bus
    "Bike": 0.0,
    "E-bike": 0.0006,
}

ROUTE_COLORS = {
    "Car": [220, 53, 69],
    "Transit": [13, 110, 253],
    "Bike": [40, 167, 69],
    "E-bike": [255, 153, 0],
}

MODE_ICONS = {
    "Car": "🚗",
    "Transit": "🚆",
    "Bike": "🚲",
    "E-bike": "⚡",
}


def cost(distance_km: float, mode: str) -> float:
    if mode == "Car":
        return round(distance_km * 0.25, 2)
    if mode == "Transit":
        return round(distance_km * 0.35, 2)
    if mode == "Bike":
        return 0.0
    if mode == "E-bike":
        return round(distance_km * 0.003, 2)
    return 0.0


def co2(distance_km: float, mode: str) -> float:
    return round(distance_km * EMISSION[mode], 3)


def autocomplete(query: str):
    if not query or len(query.strip()) < 2:
        return []

    url = "https://maps.googleapis.com/maps/api/place/autocomplete/json"
    params = {
        "input": query,
        "key": API_KEY,
        "components": "country:nz",
    }

    try:
        r = requests.get(url, params=params, timeout=20)
        r.raise_for_status()
        data = r.json()
    except Exception:
        return []

    if data.get("status") not in ("OK", "ZERO_RESULTS"):
        return []

    return [item["description"] for item in data.get("predictions", [])]


def geocode(place: str):
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    params = {
        "address": place,
        "key": API_KEY,
        "region": "nz",
    }

    try:
        r = requests.get(url, params=params, timeout=20)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        st.error(f"Geocoding failed: {e}")
        return None

    if data.get("status") != "OK" or not data.get("results"):
        return None

    loc = data["results"][0]["geometry"]["location"]
    return {
        "latitude": loc["lat"],
        "longitude": loc["lng"],
    }


def decode_polyline(encoded: str):
    points = []
    index = 0
    lat = 0
    lng = 0

    while index < len(encoded):
        shift = 0
        result = 0
        while True:
            b = ord(encoded[index]) - 63
            index += 1
            result |= (b & 0x1F) << shift
            shift += 5
            if b < 0x20:
                break
        dlat = ~(result >> 1) if (result & 1) else (result >> 1)
        lat += dlat

        shift = 0
        result = 0
        while True:
            b = ord(encoded[index]) - 63
            index += 1
            result |= (b & 0x1F) << shift
            shift += 5
            if b < 0x20:
                break
        dlng = ~(result >> 1) if (result & 1) else (result >> 1)
        lng += dlng

        points.append({"lat": lat / 1e5, "lon": lng / 1e5})

    return points


def compute_route(origin: dict, dest: dict, mode: str):
    url = "https://routes.googleapis.com/directions/v2:computeRoutes"
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": API_KEY,
        "X-Goog-FieldMask": "routes.distanceMeters,routes.duration,routes.polyline.encodedPolyline",
    }

    body = {
        "origin": {"location": {"latLng": origin}},
        "destination": {"location": {"latLng": dest}},
        "travelMode": mode,
        "units": "METRIC",
    }

    if mode == "DRIVE":
        body["routingPreference"] = "TRAFFIC_AWARE"

    try:
        r = requests.post(url, headers=headers, json=body, timeout=30)
        data = r.json()
    except Exception as e:
        return {"error": f"Request failed: {e}"}

    if r.status_code != 200:
        return {"error": f"API error: {data}"}

    routes = data.get("routes", [])
    if not routes:
        return {"error": "No routes returned"}

    first = routes[0]
    distance_meters = first.get("distanceMeters")
    duration_str = first.get("duration")
    encoded_polyline = first.get("polyline", {}).get("encodedPolyline")

    if distance_meters is None or duration_str is None:
        return {"error": "Missing route data"}

    try:
        distance_km = distance_meters / 1000
        duration_min = round(int(duration_str.replace("s", "")) / 60)
    except Exception:
        return {"error": "Could not parse duration"}

    polyline_points = []
    if encoded_polyline:
        try:
            polyline_points = decode_polyline(encoded_polyline)
        except Exception:
            polyline_points = []

    return {
        "distance_km": distance_km,
        "duration_min": duration_min,
        "polyline_points": polyline_points,
    }


def make_route_layer(route_df: pd.DataFrame):
    return pdk.Layer(
        "PathLayer",
        data=route_df,
        get_path="path",
        get_color="color",
        width_scale=1,
        width_min_pixels=5,
        pickable=True,
    )


def make_marker_layer(marker_df: pd.DataFrame):
    return pdk.Layer(
        "ScatterplotLayer",
        data=marker_df,
        get_position="[lon, lat]",
        get_radius=100,
        get_fill_color="[0, 0, 0, 180]",
        pickable=True,
    )


def render_mode_card(row, best_mode, car_row, trips_per_month):
    mode = row["Mode"]
    icon = MODE_ICONS.get(mode, "🚉")

    monthly_cost = row["Cost ($)"] * trips_per_month
    monthly_co2 = row["CO₂ (kg)"] * trips_per_month

    if car_row is not None and mode != "Car":
        monthly_co2_saved = max(0, (car_row["CO₂ (kg)"] - row["CO₂ (kg)"]) * trips_per_month)
        monthly_cost_diff = (car_row["Cost ($)"] - row["Cost ($)"]) * trips_per_month
    else:
        monthly_co2_saved = 0
        monthly_cost_diff = 0

    badge = ""
    if mode == best_mode:
        badge = '<div class="mode-badge">Lowest CO₂</div>'

    savings_text = ""
    if mode != "Car" and car_row is not None:
        cost_word = "save" if monthly_cost_diff > 0 else "spend"
        savings_text = f"""
        <div class="small-note">
            Monthly vs car: <b>{monthly_co2_saved:.1f} kg CO₂ less</b><br>
            You {cost_word} about <b>${abs(monthly_cost_diff):.2f}</b> per month
        </div>
        """

    return f"""
    <div class="mode-card">
        {badge}
        <div class="mode-title">{icon} {mode}</div>
        <div class="metric-row">
            <div class="metric-box">
                <div class="metric-label">Time</div>
                <div class="metric-value">{int(row["Time (min)"])} min</div>
            </div>
            <div class="metric-box">
                <div class="metric-label">Cost / trip</div>
                <div class="metric-value">${row["Cost ($)"]:.2f}</div>
            </div>
        </div>
        <div class="metric-row">
            <div class="metric-box">
                <div class="metric-label">CO₂ / trip</div>
                <div class="metric-value">{row["CO₂ (kg)"]:.3f} kg</div>
            </div>
            <div class="metric-box">
                <div class="metric-label">Monthly CO₂</div>
                <div class="metric-value">{monthly_co2:.1f} kg</div>
            </div>
        </div>
        <div class="small-note">
            Monthly cost: <b>${monthly_cost:.2f}</b>
        </div>
        {savings_text}
    </div>
    """


st.markdown(
    """
    <style>
    .main-title {
        font-size: 2.2rem;
        font-weight: 800;
        margin-bottom: 0.2rem;
    }
    .sub-title {
        color: #666;
        margin-bottom: 1.2rem;
    }
    .mode-card {
        background: white;
        border-radius: 18px;
        padding: 18px 18px 16px 18px;
        box-shadow: 0 4px 18px rgba(0,0,0,0.08);
        border: 1px solid rgba(0,0,0,0.06);
        margin-bottom: 16px;
        position: relative;
    }
    .mode-badge {
        position: absolute;
        top: 14px;
        right: 14px;
        background: #e8f7ec;
        color: #167a33;
        font-size: 0.75rem;
        font-weight: 700;
        padding: 5px 10px;
        border-radius: 999px;
    }
    .mode-title {
        font-size: 1.2rem;
        font-weight: 700;
        margin-bottom: 14px;
    }
    .metric-row {
        display: flex;
        gap: 10px;
        margin-bottom: 10px;
    }
    .metric-box {
        flex: 1;
        background: #f8f9fb;
        border-radius: 14px;
        padding: 12px;
    }
    .metric-label {
        font-size: 0.78rem;
        color: #666;
        margin-bottom: 4px;
    }
    .metric-value {
        font-size: 1.1rem;
        font-weight: 700;
    }
    .small-note {
        font-size: 0.88rem;
        color: #444;
        margin-top: 8px;
        line-height: 1.5;
    }
    .summary-card {
        background: linear-gradient(135deg, #f3f8ff, #eefcf2);
        border-radius: 20px;
        padding: 20px;
        margin-bottom: 18px;
        border: 1px solid rgba(0,0,0,0.05);
    }
    .summary-title {
        font-size: 1.1rem;
        font-weight: 800;
        margin-bottom: 8px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown('<div class="main-title">🚗 NZ Transport Cost + CO₂</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="sub-title">Compare cost, travel time, CO₂, route map, and monthly savings for a trip in New Zealand.</div>',
    unsafe_allow_html=True,
)

if not API_KEY:
    st.error("Google API key not found. Add GOOGLE_MAPS_API_KEY to Streamlit secrets.")
    st.stop()

top_left, top_right = st.columns([2, 1])

with top_left:
    col1, col2 = st.columns(2)

    with col1:
        start_query = st.text_input("Start location", "Waterloo Station, Lower Hutt")
        start = start_query
        if start_query:
            start_suggestions = autocomplete(start_query)
            if start_suggestions:
                start = st.selectbox(
                    "Start suggestions",
                    start_suggestions,
                    label_visibility="collapsed",
                    key="start_select",
                )

    with col2:
        end_query = st.text_input("Destination", "Wellington Station")
        end = end_query
        if end_query:
            end_suggestions = autocomplete(end_query)
            if end_suggestions:
                end = st.selectbox(
                    "Destination suggestions",
                    end_suggestions,
                    label_visibility="collapsed",
                    key="end_select",
                )

with top_right:
    trips_per_month = st.number_input(
        "Trips per month",
        min_value=1,
        max_value=100,
        value=20,
        step=1,
        help="Use 20 for one-way commute days, or 40 for return trips on 20 workdays.",
    )

compare_clicked = st.button("Compare routes", type="primary", use_container_width=True)

if compare_clicked:
    with st.spinner("Finding places..."):
        origin = geocode(start)
        destination = geocode(end)

    if not origin or not destination:
        st.error("Could not find one or both locations.")
        st.stop()

    mode_map = {
        "Car": "DRIVE",
        "Transit": "TRANSIT",
        "Bike": "BICYCLE",
    }

    results = []
    path_rows = []

    with st.spinner("Getting routes..."):
        for label, google_mode in mode_map.items():
            route_data = compute_route(origin, destination, google_mode)

            if "error" in route_data:
                continue

            distance_km = route_data["distance_km"]
            duration_min = route_data["duration_min"]
            polyline_points = route_data["polyline_points"]

            results.append({
                "Mode": label,
                "Distance (km)": round(distance_km, 1),
                "Time (min)": duration_min,
                "Cost ($)": cost(distance_km, label),
                "CO₂ (kg)": co2(distance_km, label),
            })

            if polyline_points:
                path_rows.append({
                    "mode": label,
                    "path": [[p["lon"], p["lat"]] for p in polyline_points],
                    "color": ROUTE_COLORS[label],
                })

            if label == "Bike":
                results.append({
                    "Mode": "E-bike",
                    "Distance (km)": round(distance_km, 1),
                    "Time (min)": max(1, int(duration_min * 0.75)),
                    "Cost ($)": cost(distance_km, "E-bike"),
                    "CO₂ (kg)": co2(distance_km, "E-bike"),
                })

                if polyline_points:
                    path_rows.append({
                        "mode": "E-bike",
                        "path": [[p["lon"], p["lat"]] for p in polyline_points],
                        "color": ROUTE_COLORS["E-bike"],
                    })

    if not results:
        st.error("No routes found. Check your API key, enabled APIs, or the place names.")
        st.stop()

    df = pd.DataFrame(results).sort_values(by=["CO₂ (kg)", "Time (min)"]).reset_index(drop=True)

    best = df.iloc[0]
    best_mode = best["Mode"]

    car_rows = df[df["Mode"] == "Car"]
    car_row = car_rows.iloc[0] if not car_rows.empty else None

    if car_row is not None:
        best_monthly_co2_saved = max(0, (car_row["CO₂ (kg)"] - best["CO₂ (kg)"]) * trips_per_month)
        best_monthly_cost_diff = (car_row["Cost ($)"] - best["Cost ($)"]) * trips_per_month
    else:
        best_monthly_co2_saved = 0
        best_monthly_cost_diff = 0

    main_left, main_right = st.columns([1.15, 1])

    with main_left:
        st.markdown(
            f"""
            <div class="summary-card">
                <div class="summary-title">Best low-carbon option</div>
                <div style="font-size:1.35rem; font-weight:800; margin-bottom:8px;">{MODE_ICONS.get(best_mode, "🚉")} {best_mode}</div>
                <div style="line-height:1.7;">
                    Trip: <b>{best['Time (min)']} min</b>, <b>${best['Cost ($)']:.2f}</b>, <b>{best['CO₂ (kg)']:.3f} kg CO₂</b><br>
                    Monthly: <b>${best['Cost ($)'] * trips_per_month:.2f}</b>, <b>{best['CO₂ (kg)'] * trips_per_month:.1f} kg CO₂</b><br>
                    CO₂ reduction vs car: <b>{best_monthly_co2_saved:.1f} kg/month</b>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        st.subheader("Options")

        card_col1, card_col2 = st.columns(2)
        for i, (_, row) in enumerate(df.iterrows()):
            html = render_mode_card(row, best_mode, car_row, trips_per_month)
            with card_col1 if i % 2 == 0 else card_col2:
                st.markdown(html, unsafe_allow_html=True)

        st.subheader("Detailed comparison")
        st.dataframe(df, use_container_width=True, hide_index=True)

    with main_right:
        st.subheader("Map")

        if path_rows:
            path_df = pd.DataFrame(path_rows)
            marker_df = pd.DataFrame([
                {"name": "Start", "lat": origin["latitude"], "lon": origin["longitude"]},
                {"name": "End", "lat": destination["latitude"], "lon": destination["longitude"]},
            ])

            all_lats = [origin["latitude"], destination["latitude"]]
            all_lons = [origin["longitude"], destination["longitude"]]

            for row in path_rows:
                for lon, lat in row["path"]:
                    all_lats.append(lat)
                    all_lons.append(lon)

            view_state = pdk.ViewState(
                latitude=sum(all_lats) / len(all_lats),
                longitude=sum(all_lons) / len(all_lons),
                zoom=10,
                pitch=0,
            )

            deck = pdk.Deck(
                initial_view_state=view_state,
                layers=[
                    make_route_layer(path_df),
                    make_marker_layer(marker_df),
                ],
                tooltip={"text": "{mode}"},
            )

            st.pydeck_chart(deck, use_container_width=True)

            st.markdown(
                """
**Route colors**
- Red: Car  
- Blue: Transit  
- Green: Bike  
- Orange: E-bike
"""
            )
        else:
            st.warning("Map route not available for this trip.")

        if car_row is not None:
            st.subheader("Monthly savings snapshot")

            monthly_rows = []
            for _, row in df.iterrows():
                monthly_rows.append({
                    "Mode": row["Mode"],
                    "Monthly Cost ($)": round(row["Cost ($)"] * trips_per_month, 2),
                    "Monthly CO₂ (kg)": round(row["CO₂ (kg)"] * trips_per_month, 1),
                    "CO₂ Reduction vs Car (kg)": round(
                        max(0, (car_row["CO₂ (kg)"] - row["CO₂ (kg)"]) * trips_per_month), 1
                    ),
                })

            monthly_df = pd.DataFrame(monthly_rows)
            st.dataframe(monthly_df, use_container_width=True, hide_index=True)

st.markdown("---")

st.markdown(
    """
### 🧪 About this app

This is a **simple MVP** for quick comparison.

- Uses Google Maps for distance and time
- Cost and CO₂ are **estimated using NZ averages**

### 📊 Emission assumptions (NZ-based)

- 🚗 Car (Hybrid Aqua): ~0.128 kg CO₂/km
- 🚆 Train (Electric): ~0.0148 kg CO₂/km
- 🚌 Bus (Average NZ): ~0.155 kg CO₂/km
- ⚡ E-bike: ~0.0006 kg CO₂/km

### ⚠️ Limitations

- Transit is simplified in this MVP
- Costs are approximate, not real ticket fares
- Results are for comparison only, not exact trip planning

### 🚀 Next upgrade ideas

- More accurate **train vs bus separation**
- Metlink or Auckland Transport real fares
- Better bus/train travel times
- Monthly savings dashboard with charts
- Route optimisation: fastest vs lowest carbon
"""
)
