import streamlit as st
import pandas as pd
import requests

st.set_page_config(page_title="NZ Transport Cost + CO₂", page_icon="🚉", layout="centered")

API_KEY = st.secrets.get("GOOGLE_MAPS_API_KEY", "")

# NZ-based/simple MVP emission factors
EMISSION = {
    "Car": 0.128,
    "Transit": 0.05,   # simple blended transit estimate for MVP
    "Bike": 0.0,
    "E-bike": 0.0006,
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
        st.error(f"Geocoding request failed: {e}")
        return None

    if data.get("status") != "OK" or not data.get("results"):
        st.warning(f"Could not find location: {place}")
        return None

    loc = data["results"][0]["geometry"]["location"]
    return {
        "latitude": loc["lat"],
        "longitude": loc["lng"],
    }

def route(origin: dict, dest: dict, mode: str):
    url = "https://routes.googleapis.com/directions/v2:computeRoutes"
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": API_KEY,
        "X-Goog-FieldMask": "routes.distanceMeters,routes.duration",
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
        st.warning(f"{mode}: request failed: {e}")
        return None

    if r.status_code != 200:
        st.warning(f"{mode}: API error: {data}")
        return None

    routes = data.get("routes", [])
    if not routes:
        return None

    first = routes[0]

    distance_meters = first.get("distanceMeters")
    duration_str = first.get("duration")

    if distance_meters is None or duration_str is None:
        return None

    try:
        distance_km = distance_meters / 1000
        duration_min = round(int(duration_str.replace("s", "")) / 60)
    except Exception:
        return None

    return distance_km, duration_min

st.title("🚗 NZ Transport Cost + CO₂")
st.write("Compare **cost, travel time, and CO₂** for a trip in New Zealand.")

start = st.text_input("Start", "Waterloo Station, Lower Hutt")
end = st.text_input("End", "Wellington Station")

if st.button("Compare"):
    if not API_KEY:
        st.error("Google API key not found. Add GOOGLE_MAPS_API_KEY to Streamlit secrets.")
        st.stop()

    with st.spinner("Finding locations..."):
        origin = geocode(start)
        destination = geocode(end)

    if not origin or not destination:
        st.stop()

    results = []

    mode_map = {
        "Car": "DRIVE",
        "Transit": "TRANSIT",
        "Bike": "BICYCLE",
    }

    with st.spinner("Getting routes..."):
        for label, google_mode in mode_map.items():
            res = route(origin, destination, google_mode)
            if res is None:
                continue

            distance_km, time_min = res

            results.append({
                "Mode": label,
                "Distance (km)": round(distance_km, 1),
                "Time (min)": time_min,
                "Cost ($)": cost(distance_km, label),
                "CO₂ (kg)": co2(distance_km, label),
            })

            if label == "Bike":
                results.append({
                    "Mode": "E-bike",
                    "Distance (km)": round(distance_km, 1),
                    "Time (min)": max(1, int(time_min * 0.75)),
                    "Cost ($)": cost(distance_km, "E-bike"),
                    "CO₂ (kg)": co2(distance_km, "E-bike"),
                })

    if not results:
        st.error("No routes found. Check your API key, enabled APIs, or route locations.")
        st.stop()

    df = pd.DataFrame(results)

    required_cols = ["Mode", "Distance (km)", "Time (min)", "Cost ($)", "CO₂ (kg)"]
    missing_cols = [c for c in required_cols if c not in df.columns]
    if missing_cols:
        st.error(f"Missing expected columns: {missing_cols}")
        st.write(df)
        st.stop()

    df = df.sort_values(by=["CO₂ (kg)", "Time (min)"]).reset_index(drop=True)

    st.subheader("Results")
    st.dataframe(df, use_container_width=True, hide_index=True)

    best = df.iloc[0]
    st.success(
        f"Best low-carbon option: **{best['Mode']}** "
        f"({best['CO₂ (kg)']} kg CO₂, {best['Time (min)']} min, ${best['Cost ($)']})"
    )

    car_rows = df[df["Mode"] == "Car"]
    if not car_rows.empty:
        car_co2 = float(car_rows.iloc[0]["CO₂ (kg)"])
        saved = round(car_co2 - float(best["CO₂ (kg)"]), 3)
        if saved > 0:
            st.info(f"Compared with Car, this option saves about **{saved} kg CO₂** per trip.")

st.markdown("---")
st.caption("This MVP uses Google for routing and simple estimates for cost and CO₂.")
st.caption("Rasika Nandana 2026.04.16.")
