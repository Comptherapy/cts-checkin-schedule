import streamlit as st
import pandas as pd
from datetime import datetime
import io

st.set_page_config(
    page_title="Clinic Check-In Schedule",
    page_icon="🏥",
    layout="wide"
)

st.markdown("""
<style>
    .main { background-color: #f0f4f8; }
    .stApp { font-family: Arial, sans-serif; }
    h1 { color: #2F5496; }
    div[data-testid="metric-container"] {
        background-color: #2F5496;
        border-radius: 8px;
        padding: 10px;
    }
    div[data-testid="metric-container"] label { color: #ccd9f0 !important; }
    div[data-testid="metric-container"] div { color: white !important; }
</style>
""", unsafe_allow_html=True)

def time_to_minutes(t):
    if pd.isna(t):
        return 9999
    t = str(t).strip()
    for fmt in ["%I:%M %p", "%H:%M", "%I:%M%p"]:
        try:
            dt = datetime.strptime(t, fmt)
            return dt.hour * 60 + dt.minute
        except:
            continue
    return 9999

def strip_status(name):
    if pd.isna(name):
        return ""
    name = str(name).strip()
    for s in ["-Open", "-Arrived", "-Cancelled", "-No Show", "-Rescheduled", "-No-Show"]:
        if name.endswith(s):
            return name[:-len(s)].strip()
    if "-" in name:
        parts = name.rsplit("-", 1)
        if parts[1].strip() in ["Open","Arrived","Cancelled","No Show","Rescheduled"]:
            return parts[0].strip()
    return name

def strip_credentials(therapist):
    if pd.isna(therapist):
        return ""
    therapist = str(therapist).strip()
    if "," in therapist:
        therapist = therapist[:therapist.index(",")].strip()
    for marker in [" Lic", " OTD/", " MS-", " DPT"]:
        if marker in therapist:
            therapist = therapist[:therapist.index(marker)].strip()
    return therapist.strip()

def get_initials(name):
    try:
        parts = name.strip().split()
        first = parts[0][0].upper() if parts else ""
        last  = parts[-1][0].upper() if len(parts) > 1 else ""
        return f"{first}.{last}." if first and last else first or last
    except:
        return ""

def process_schedule(df):
    required = ["FacilityCode","TherapistDisplayName","AppointmentStartTime2",
                "PatientName2","CaseDescription"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        return None, None, f"Could not find expected columns: {missing}."

    data = df[df["FacilityCode"].astype(str).str.strip() == "CTS"].copy()
    if data.empty:
        return None, None, "No patient data found."

    # ── Build Zapier CSV (one row per therapist per patient) ──────────────────
    zapier_rows = []
    for _, row in data.iterrows():
        therapist  = str(row["TherapistDisplayName"])
        appt_time  = str(row["AppointmentStartTime2"]).strip()
        pat_name   = str(row["PatientName2"])
        discipline = str(row["CaseDescription"]).strip()

        if not pat_name or pat_name == "nan":
            continue

        clean_name      = strip_status(pat_name)
        clean_therapist = strip_credentials(therapist)
        initials        = get_initials(clean_name)

        zapier_rows.append({
            "Patient Name": clean_name,
            "Initials":     initials,
            "Appt Time":    appt_time,
            "Therapist":    clean_therapist,
            "Discipline":   discipline,
        })

    if not zapier_rows:
        return None, None, "No valid patient records found."

    zapier_df = pd.DataFrame(zapier_rows)
    zapier_df["_min"] = zapier_df["Appt Time"].apply(time_to_minutes)
    zapier_df = zapier_df.sort_values(["Patient Name", "_min"]).drop(columns=["_min"]).reset_index(drop=True)

    # ── Build display table (collapsed, one row per patient) ──────────────────
    patients = {}
    for r in zapier_rows:
        name = r["Patient Name"]
        mins = time_to_minutes(r["Appt Time"])
        if name not in patients:
            patients[name] = {
                "time":          r["Appt Time"],
                "therapist":     r["Therapist"],
                "all_therapists": {r["Therapist"]},
                "discipline":    r["Discipline"],
                "minutes":       mins,
                "initials":      r["Initials"],
            }
        else:
            patients[name]["all_therapists"].add(r["Therapist"])
            if mins < patients[name]["minutes"]:
                patients[name].update({
                    "time":       r["Appt Time"],
                    "therapist":  r["Therapist"],
                    "discipline": r["Discipline"],
                    "minutes":    mins,
                })

    display_df = pd.DataFrame([
        {
            "Patient Name":    name,
            "Initials":        d["initials"],
            "First Appt":      d["time"],
            "First Therapist": d["therapist"],
            "All Therapists":  ", ".join(sorted(d["all_therapists"])),
            "Discipline":      d["discipline"],
        }
        for name, d in patients.items()
    ])

    display_df["_min"] = display_df["First Appt"].apply(time_to_minutes)
    display_df = display_df.sort_values("_min").drop(columns=["_min"]).reset_index(drop=True)
    display_df.index = display_df.index + 1

    return display_df, zapier_df, None


def save_to_dropbox(zapier_df):
    """Save the per-therapist schedule to Dropbox for Zapier lookup."""
    try:
        import dropbox
        dbx = dropbox.Dropbox(
            oauth2_refresh_token=st.secrets["DROPBOX_REFRESH_TOKEN"],
            app_key=st.secrets["DROPBOX_APP_KEY"],
            app_secret=st.secrets["DROPBOX_APP_SECRET"]
        )

        csv_bytes = zapier_df.to_csv(index=False).encode("utf-8")

        dbx.files_upload(
            csv_bytes,
            "/Apps/CTS Schedule Sync/Apps/CTS Schedule Sync/daily_schedule.csv",
            mode=dropbox.files.WriteMode.overwrite
        )
        return True, None
    except Exception as e:
        return False, str(e)

DISC_COLORS = {
    "Physical Therapy":    "#d0e8ff",
    "Occupational Therapy":"#d4f0d4",
    "Speech Therapy":      "#fff3cd",
}

def color_row(row):
    disc = str(row.get("Discipline", ""))
    for key, color in DISC_COLORS.items():
        if key in disc:
            return [f"background-color: {color}"] * len(row)
    return [""] * len(row)

# ── UI ─────────────────────────────────────────────────────────────────────────
st.title("🏥 Daily Check-In Schedule")
st.markdown("Upload your PT Practice Pro daily export to generate today's check-in lookup.")
st.markdown("---")

uploaded_file = st.file_uploader(
    "Drop your PT Practice Pro CSV export here",
    type=["csv", "txt"],
    help="Export your daily patient list from PT Practice Pro as CSV, then upload it here."
)

if uploaded_file:
    try:
        df = pd.read_csv(
            uploaded_file,
            encoding="utf-8-sig",
            dtype=str,
            on_bad_lines="skip"
        )
    except Exception as e:
        st.error(f"Could not read file: {e}")
        st.stop()

    result_df, zapier_df, error = process_schedule(df)

    if error:
        st.error(f"⚠️ {error}")
        st.write("**Column names found in your file:**", list(df.columns))
    else:
        with st.spinner("Saving schedule to Dropbox..."):
            success, err = save_to_dropbox(zapier_df)
            if success:
                st.success("✅ Schedule saved to Dropbox — Zapier is ready to route check-ins!")
            else:
                st.warning(f"⚠️ Could not save to Dropbox: {err}")

        total        = len(result_df)
        pt_count     = result_df["Discipline"].str.contains("Physical",     na=False).sum()
        ot_count     = result_df["Discipline"].str.contains("Occupational", na=False).sum()
        st_count     = result_df["Discipline"].str.contains("Speech",       na=False).sum()
        n_therapists = result_df["First Therapist"].nunique()

        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Total Patients",       total)
        c2.metric("Physical Therapy",     pt_count)
        c3.metric("Occupational Therapy", ot_count)
        c4.metric("Speech Therapy",       st_count)
        c5.metric("Therapists Today",     n_therapists)

        st.markdown("---")
        st.markdown("""
        <div style='display:flex;gap:20px;margin-bottom:10px;font-size:13px;'>
            <span style='background:#d0e8ff;padding:3px 12px;border-radius:4px;'>■ Physical Therapy</span>
            <span style='background:#d4f0d4;padding:3px 12px;border-radius:4px;'>■ Occupational Therapy</span>
            <span style='background:#fff3cd;padding:3px 12px;border-radius:4px;'>■ Speech Therapy</span>
        </div>
        """, unsafe_allow_html=True)

        options = ["All Therapists"] + sorted(result_df["First Therapist"].unique().tolist())
        selected = st.selectbox("Filter by therapist:", options)
        display_df = result_df if selected == "All Therapists" else \
                     result_df[result_df["First Therapist"] == selected]

        st.dataframe(
            display_df.style.apply(color_row, axis=1),
            use_container_width=True,
            height=min(600, 50 + len(display_df) * 38),
        )
        st.markdown(f"*Showing {len(display_df)} of {total} patients — sorted by first appointment time*")

        st.markdown("---")
        st.download_button(
            label="⬇️ Download Lookup as CSV",
            data=result_df.to_csv(index=False),
            file_name="checkin_lookup.csv",
            mime="text/csv"
        )

else:
    st.markdown("""
    <div style='background:white;padding:30px;border-radius:12px;
                border:2px dashed #2F5496;text-align:center;color:#555;'>
        <h3 style='color:#2F5496;'>How to use this app</h3>
        <ol style='text-align:left;display:inline-block;'>
            <li>Open PT Practice Pro and export your <b>daily patient list</b> as CSV</li>
            <li>Click <b>Browse files</b> above (or drag and drop the CSV)</li>
            <li>Schedule saves to Dropbox automatically</li>
            <li>Re-upload anytime the schedule changes throughout the day</li>
        </ol>
        <p style='margin-top:20px;color:#888;'>No data is stored beyond your secure Dropbox folder.</p>
    </div>
    """, unsafe_allow_html=True)

st.markdown("---")
st.caption("Clinic Check-In Schedule Tool • Built for CTS Pediatric Therapy")
