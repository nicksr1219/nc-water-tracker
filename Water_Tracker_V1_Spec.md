# NC Water Table & Reservoir Tracker — V1 Spec

## Purpose
A web app that shows current groundwater (water table) and reservoir/lake water
levels on an interactive map, starting with North Carolina. Built to expand to
all 50 states in later versions without a rebuild.

## Who it's for
Personal/public-facing tool for anyone curious about current water levels in
their area — hobbyists, landowners, anglers, or anyone tracking drought
conditions.

## V1 Scope (North Carolina only)

### Data
- **Primary source:** USGS WaterServices API — https://waterservices.usgs.gov/
  Free, public, structured JSON/XML. No login or scraping required.
  - Groundwater levels endpoint
  - Reservoir/lake level endpoint (via USGS site type filtering)
- **Secondary source (if gaps exist):** NC DEQ Division of Water Resources
  groundwater data — https://www.ncwater.org/?page=20
- Pull data for NC stations only in V1 (state code filter: NC)

### Core features
1. **Map view** of North Carolina showing every monitored station as a point
2. **Click a point** to see a popup/panel with:
   - Station name and ID
   - Current reading (water level / elevation) and units
   - Date and time of the reading
   - A link out to the station's full USGS page for historical charts
3. **Zoom and pan** on the map (standard map interaction)
4. **Basic legend** distinguishing groundwater wells vs. reservoir/lake stations
   (e.g., different marker colors or icons)

### Out of scope for V1 (save for later)
- Other states (V2)
- Historical trend charts inside the app itself (V1 just links out to USGS)
- User accounts, saved favorites, alerts/notifications
- Mobile app (V1 is a web app, works fine in a mobile browser)

## Suggested technical approach
*(Claude Code should confirm/adjust this — these are starting assumptions, not
fixed requirements)*
- A small backend script (Python or Node) to fetch data from the USGS API and
  cache it locally, refreshed on a schedule (e.g., every few hours)
- A simple frontend using a map library (e.g., Leaflet) to render station
  points and handle click/zoom interactions
- Data stored in a lightweight local format (JSON or SQLite) — no need for a
  full database at this scale

## Success criteria for V1
- Opening the app shows a map of NC with live station points plotted
- Clicking any point shows accurate, current data and a working link to USGS
- The whole thing runs locally on a Windows laptop without extra paid services

## Notes for future versions
- V2: add a state selector/parameter so the same pipeline pulls data for any
  state, one at a time
- Consider adding simple color-coding for "above normal / normal / below
  normal" once a baseline comparison approach is worked out
