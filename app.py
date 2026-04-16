import streamlit as st
import pandas as pd

st.set_page_config(page_title="NZ Transport Cost + CO₂ Calculator", page_icon="🚉", layout="centered")

# ---------- Simple constants ----------
# These are MVP values. Later you can replace them with live route APIs and real fare data.

EMISSION_FACTORS_KG_PER_KM = {
    "Car (Hybrid Aqua)": 0.128,   # kg CO2e / km
    "Train (Electric)": 0.0148,
    "Bus (Average NZ)": 0.155,
    "Bike": 0.0,
    "E-bike": 0.0006,
}

# Simple default speeds for rough time estimates
SPEED_KMH = {
    "Car (Hybrid Aqua)": 48,
    "Train (Electric)": 38,
    "Bus (Average NZ)": 28,
    "Bike": 16,
    "E-bike": 24,
}

# Simple cost model for MVP
# You can later replace train/bus with fare tables or APIs.
def calculate_cost(distance_km: float):
    return {
        "Car (Hybrid Aqua)": distance_km * 0.25,  # includes fuel + rough running cost
        "Train (Electric)": distance_km * 0.35,
        "Bus (Average NZ)": distance_km * 0.22,
        "Bike": 0.0,
        "E-bike": distance_km * 0.003,  # rough electricity-only estimate
    }

def calculate_time_minutes(distance_km: float):
    times = {}
    for mode, speed in SPEED_KMH.items():
        hours = distance_km / speed if speed > 0 else 0
        times[mode] = round(hours * 60)
    return times

def calculate_emissions(distance_km: float):
    return {
        mode: round(distance_km * factor, 3)
        for mode, factor in EMISSION_FACTORS_KG_PER_KM.items()
    }

# ---------- UI ----------
st.title("🚗 NZ Transport Cost + CO₂ Calculator")
st.write("Compare **cost, travel time, and CO₂** for a route in New Zealand.")

col1, col2 = st.columns(2)

with col1:
    start_location = st.text_input("Start", value="Waterloo")
with col2:
    end_location = st.text_input("End", value="Wellington")

distance_km = st.number_input(
    "Distance (km, one way)",
    min_value=0.1,
    value=16.0,
    step=0.5
)

return_trip = st.checkbox("Return trip", value=True)

trip_distance = distance_km * 2 if return_trip else distance_km

if st.button("Calculate"):
    costs = calculate_cost(trip_distance)
    emissions = calculate_emissions(trip_distance)
    times = calculate_time_minutes(trip_distance)

    df = pd.DataFrame({
        "Mode": list(EMISSION_FACTORS_KG_PER_KM.keys()),
        "Time (min)": [times[m] for m in EMISSION_FACTORS_KG_PER_KM.keys()],
        "Cost (NZD)": [round(costs[m], 2) for m in EMISSION_FACTORS_KG_PER_KM.keys()],
        "CO₂ (kg)": [emissions[m] for m in EMISSION_FACTORS_KG_PER_KM.keys()],
    })

    df = df.sort_values(by=["CO₂ (kg)", "Cost (NZD)"]).reset_index(drop=True)

    st.subheader("Results")
    st.dataframe(df, use_container_width=True, hide_index=True)

    # Best low-carbon option
    best_mode = df.iloc[0]["Mode"]
    best_co2 = df.iloc[0]["CO₂ (kg)"]

    car_row = df[df["Mode"] == "Car (Hybrid Aqua)"].iloc[0]
    savings_vs_car = round(car_row["CO₂ (kg)"] - best_co2, 3)

    st.success(
        f"Lowest-carbon option: **{best_mode}**. "
        f"Compared with **Car (Hybrid Aqua)**, this saves about **{savings_vs_car} kg CO₂** per trip."
    )

    st.caption(
        "This MVP uses simple NZ-based assumptions for emissions and rough cost/time models. "
        "It is for comparison only, not exact fare or route planning."
    )
else:
    st.info("Enter a route and click **Calculate**.")

st.markdown("---")
st.markdown("### Notes")
st.markdown(
    """
- This is a **simple MVP** for quick comparison.
- Distance is currently **manual**.
- Next upgrade ideas:
  - Google Maps API / routing API
  - Metlink or Auckland Transport fares
  - Better bus/train travel times
  - Monthly savings calculator
"""
)