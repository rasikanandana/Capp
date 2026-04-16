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
    "Car": 0.128,        # Toyota Aqua hybrid approx
    "Transit": 0.05,     # bus + train combined estimate for MVP
    "Train": 0.0148,     # electric rail NZ
    "Bus": 0.155,        # average NZ bus
    "Bicycle": 0.0,
    "E-bike": 0.0006,
}

ROUTE_COLORS = {
    "Car": [220, 53, 69],
    "Transit": [13, 110, 253],
    "Bicycle": [40, 167, 69],
    "E-bike": [255, 153, 0],
}

MODE_ICONS = {
    "Car": "🚗",
    "Transit": "🚆",
    "Bicycle": "🚲",
    "E-bike": "⚡",
}


def cost(distance_km: float, mode: str) -> float:
    if mode == "Car":
        return round(distance_km * 0.25, 2)
    if mode == "Transit":
        return round(distance_km * 0.35, 2)
    if mode == "Bicycle":
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


st.markdown("## 🚗 NZ Transport Cost + CO₂")
st.caption("Compare cost, travel time, CO₂, route map, and savings for a trip in New Zealand.")
st.caption("Transit means public transport combined (bus + train).")

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
        "Bicycle": "BICYCLE",
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
                "Mode": f"{MODE_ICONS.get(label, '')} {label}",
                "ModeKey": label,
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

            if label == "Bicycle":
                results.append({
                    "Mode": f"{MODE_ICONS.get('E-bike', '')} E-bike",
                    "ModeKey": "E-bike",
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
    car_rows = df[df["ModeKey"] == "Car"]
    bicycle_rows = df[df["ModeKey"] == "Bicycle"]

    car_row = car_rows.iloc[0] if not car_rows.empty else None
    bicycle_row = bicycle_rows.iloc[0] if not bicycle_rows.empty else None

    daily_co2_saved = 0.0
    daily_cost_diff = 0.0
    monthly_co2_saved = 0.0
    monthly_cost_diff = 0.0

    bicycle_daily_co2_saved = 0.0
    bicycle_daily_cost_diff = 0.0
    bicycle_monthly_co2_saved = 0.0
    bicycle_monthly_cost_diff = 0.0

    if car_row is not None:
        daily_co2_saved = max(0, car_row["CO₂ (kg)"] - best["CO₂ (kg)"])
        daily_cost_diff = car_row["Cost ($)"] - best["Cost ($)"]
        monthly_co2_saved = daily_co2_saved * trips_per_month
        monthly_cost_diff = daily_cost_diff * trips_per_month

    if car_row is not None and bicycle_row is not None:
        bicycle_daily_co2_saved = max(0, car_row["CO₂ (kg)"] - bicycle_row["CO₂ (kg)"])
        bicycle_daily_cost_diff = car_row["Cost ($)"] - bicycle_row["Cost ($)"]
        bicycle_monthly_co2_saved = bicycle_daily_co2_saved * trips_per_month
        bicycle_monthly_cost_diff = bicycle_daily_cost_diff * trips_per_month

    left_col, right_col = st.columns([1.05, 1])

    with left_col:
        st.subheader("📅 Daily savings")
        st.markdown(
            f"""
Compared with driving:

- 🌱 **CO₂ reduction:** {daily_co2_saved:.3f} kg/trip  
- 💰 **Cost difference:** ${abs(daily_cost_diff):.2f} ({'saving' if daily_cost_diff > 0 else 'extra cost'})  
"""
        )

        st.subheader("🚲 Compared bicycle with driving")
        st.markdown(
            f"""
- 🌱 **CO₂ reduction:** {bicycle_daily_co2_saved:.3f} kg/trip  
- 💰 **Cost difference:** ${abs(bicycle_daily_cost_diff):.2f} ({'saving' if bicycle_daily_cost_diff > 0 else 'extra cost'})  
"""
        )

        st.subheader("📊 Monthly savings")
        st.markdown(
            f"""
Compared with driving:

- 🌱 **CO₂ reduction:** {monthly_co2_saved:.1f} kg/month  
- 💰 **Cost difference:** ${abs(monthly_cost_diff):.2f} ({'saving' if monthly_cost_diff > 0 else 'extra cost'})  
"""
        )

        st.subheader("🚲 Monthly bicycle vs driving")
        st.markdown(
            f"""
- 🌱 **CO₂ reduction:** {bicycle_monthly_co2_saved:.1f} kg/month  
- 💰 **Cost difference:** ${abs(bicycle_monthly_cost_diff):.2f} ({'saving' if bicycle_monthly_cost_diff > 0 else 'extra cost'})  
"""
        )

        st.info("You can also add how you travel today in this app.")

        st.subheader("Detailed comparison")
        display_df = df.drop(columns=["ModeKey"])
        st.dataframe(display_df, use_container_width=True, hide_index=True)

    with right_col:
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
- Blue: Transit (bus + train)  
- Green: Bicycle  
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
- **Transit means public transport combined (bus + train)**

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

st.markdown("---")
st.caption("Rasika Nandana © | ver1.0 | 2026.04.16")
