import streamlit as st
import pandas as pd
import requests

st.set_page_config(page_title="NZ Transport CO₂ App", page_icon="🚉")

API_KEY = st.secrets.get("GOOGLE_MAPS_API_KEY", "")

# --- NZ Emission factors ---
EMISSION = {
    "Car": 0.128,
    "Transit": 0.05,   # simplified average (train/bus mix)
    "Bike": 0.0,
    "E-bike": 0.0006,
}

# --- Cost models ---
def cost(distance, mode):
    if mode == "Car":
        return round(distance * 0.25, 2)
    if mode == "Transit":
        return round(distance * 0.35, 2)
    if mode == "Bike":
        return 0
    if mode == "E-bike":
        return round(distance * 0.003, 2)

# --- CO2 ---
def co2(distance, mode):
    return round(distance * EMISSION[mode], 3)

# --- Geocode ---
def geocode(place):
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    r = requests.get(url, params={"address": place, "key": API_KEY})
    data = r.json()
    if data["status"] != "OK":
        return None
    loc = data["results"][0]["geometry"]["location"]
    return {"lat": loc["lat"], "lng": loc["lng"]}

# --- Route ---
def route(origin, dest, mode):
    url = "https://routes.googleapis.com/directions/v2:computeRoutes"
    headers = {
        "X-Goog-Api-Key": API_KEY,
        "X-Goog-FieldMask": "routes.distanceMeters,routes.duration"
    }
    body = {
        "origin": {"location": {"latLng": origin}},
        "destination": {"location": {"latLng": dest}},
        "travelMode": mode
    }
    r = requests.post(url, headers=headers, json=body)
    data = r.json()
    if "routes" not in data:
        return None
    dist = data["routes"][0]["distanceMeters"] / 1000
    dur = int(data["routes"][0]["duration"].replace("s", "")) / 60
    return dist, round(dur)

# --- UI ---
st.title("🚗 NZ Transport Cost + CO₂")

start = st.text_input("Start", "Waterloo Station, Lower Hutt")
end = st.text_input("End", "Wellington Station")

if st.button("Compare"):
    if not API_KEY:
        st.error("Add Google API key in secrets")
        st.stop()

    o = geocode(start)
    d = geocode(end)

    if not o or not d:
        st.error("Location error")
        st.stop()

    results = []

    for label, mode in {
        "Car": "DRIVE",
        "Transit": "TRANSIT",
        "Bike": "BICYCLE"
    }.items():

        res = route(o, d, mode)
        if res:
            dist, time = res
            results.append({
                "Mode": label,
                "Time (min)": time,
                "Cost ($)": cost(dist, label),
                "CO₂ (kg)": co2(dist, label)
            })

            if label == "Bike":
                results.append({
                    "Mode": "E-bike",
                    "Time (min)": int(time * 0.75),
                    "Cost ($)": cost(dist, "E-bike"),
                    "CO₂ (kg)": co2(dist, "E-bike")
                })

    df = pd.DataFrame(results).sort_values("CO₂ (kg)")
    st.dataframe(df, use_container_width=True)

    best = df.iloc[0]
    st.success(f"Best option: {best['Mode']} (lowest CO₂)")
