import requests
import pandas as pd
import pydeck as pdk
import streamlit as st

st.set_page_config(page_title="NZ Transport Cost + CO₂", page_icon="🚉", layout="wide")

API_KEY = st.secrets.get("GOOGLE_MAPS_API_KEY", "")

EMISSION = {
    "Car": 0.128,
    "Transit": 0.05,
    "Bike": 0.0,
    "E-bike": 0.0006,
}

ROUTE_COLORS = {
    "Car": [220, 53, 69],
    "Transit": [13, 110, 253],
    "Bike": [40, 167, 69],
    "E-bike": [255, 153, 0],
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
        "origin": {
            "location": {
                "latLng": origin
            }
        },
        "destination": {
            "location": {
                "latLng": dest
            }
        },
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
        width_min_pixels=4,
        pickable=True,
    )


def make_marker_layer(marker_df: pd.DataFrame):
    return pdk.Layer(
        "ScatterplotLayer",
        data=marker_df,
        get_position="[lon, lat]",
        get_radius=90,
        get_fill_color="[0, 0, 0, 180]",
        pickable=True,
    )


st.title("🚗 NZ Transport Cost + CO₂")
st.write("Compare **cost, travel time, CO₂, and route map** for a trip in New Zealand.")

if not API_KEY:
    st.error("Google API key not found. Add GOOGLE_MAPS_API_KEY to Streamlit secrets.")
    st.stop()

left, right = st.columns(2)

with left:
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

with right:
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

if st.button("Compare routes", type="primary"):
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

    c1, c2 = st.columns([1, 1.15])

    with c1:
        st.subheader("Results")
        st.dataframe(df, use_container_width=True, hide_index=True)

        best = df.iloc[0]
        st.success(
            f"Best low-carbon option: **{best['Mode']}** "
            f"({best['CO₂ (kg)']} kg CO₂, {best['Time (min)']} min, ${best['Cost ($)']})"
        )

        car_rows = df[df["Mode"] == "Car"]
        if not car_rows.empty:
            saved = round(float(car_rows.iloc[0]["CO₂ (kg)"]) - float(best["CO₂ (kg)"]), 3)
            if saved > 0:
                st.info(f"Compared with Car, this saves about **{saved} kg CO₂** per trip.")

        st.caption("Distance/time come from Google. Cost and CO₂ are MVP estimates.")

    with c2:
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

st.markdown("---")
st.caption("Next upgrade: exact fares, better transit split, and cleaner autocomplete UX.")
