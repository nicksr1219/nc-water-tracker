"""
Pulls current groundwater and reservoir/lake levels for North Carolina from
USGS's public Water Data API and saves them to data/stations.json.

Run it with:  py fetch_data.py
No installs needed - only uses Python's built-in libraries.
"""

import json
import time
import urllib.error
import urllib.request
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    EASTERN = ZoneInfo("America/New_York")
except ZoneInfoNotFoundError:
    # Some Windows Python installs don't ship the IANA timezone database.
    # Fall back to whatever timezone this computer is set to - correct as
    # long as it's actually running somewhere in the Eastern time zone (true
    # for a personal machine, not guaranteed for a server/CI runner).
    EASTERN = None

API_BASE = "https://api.waterdata.usgs.gov/ogcapi/v0"
STATE_CODE = "37"  # North Carolina's FIPS state code
ACIS_BASE = "https://data.rcc-acis.org"

# Nearest long-record NOAA weather station to each reservoir, picked by
# straight-line distance, for computing local rainfall vs. normal. Not exact
# on-site precipitation - a proxy for "how has this area's rainfall been."
PRECIP_STATIONS = {
    "02098197": {"sid": "RDU", "name": "Raleigh-Durham International Airport", "distance_mi": 23},
    "0208725090": {"sid": "RDU", "name": "Raleigh-Durham International Airport", "distance_mi": 4},
    "02087182": {"sid": "RDU", "name": "Raleigh-Durham International Airport", "distance_mi": 12},
    "02111391": {"sid": "316256", "name": "North Wilkesboro", "distance_mi": 5},
    "02077280": {"sid": "317516", "name": "Roxboro 7 ESE", "distance_mi": 15},
    "0207730290": {"sid": "317516", "name": "Roxboro 7 ESE", "distance_mi": 14},
}

# USGS parameter codes we care about:
#   72019 = depth to water level, in feet below land surface (groundwater wells)
#   62615 = reservoir/lake surface elevation, ft, modern NAVD88 datum (preferred)
#   62614 = same thing, older NGVD29 datum (fallback if 62615 isn't reported)
#   00062 = reservoir/lake surface elevation, ft, other/local datum (last resort)
GW_PARAM = "72019"
LAKE_PARAMS = ["62615", "62614", "00062"]

# Vertical datum each lake elevation parameter code is referenced to. Used to
# flag when a reading and its full-pool reference number use different datums
# (NAVD88 and NGVD29 differ by roughly 1-1.5 ft in NC).
PARAM_DATUM = {"62615": "NAVD88", "62614": "NGVD29", "00062": "unstated"}

# Percentile classification bands, matching USGS WaterWatch convention. This
# is for water-level PERCENTILE RANK (0-100 = position in the historical
# distribution; 50 = typical). Do not reuse for precipitation - see
# PRECIP_RATIO_LABELS below, which classifies a different kind of number.
NORMAL_LABELS = [
    (10, "Much below normal"),
    (25, "Below normal"),
    (76, "Normal"),
    (91, "Above normal"),
    (101, "Much above normal"),
]

# For any "percent of normal" that's a RATIO (100% = exactly normal) rather
# than a percentile rank - precipitation, and Duke Energy's percent-of-target
# - these bands centered on 100 apply instead of NORMAL_LABELS above. Matches
# the standard NOAA/NWS percent-of-normal-precipitation convention.
RATIO_LABELS = [
    (50, "Much below normal"),
    (75, "Below normal"),
    (125, "Normal"),
    (150, "Above normal"),
    (float("inf"), "Much above normal"),
]


def ratio_label(percent_of_normal):
    for threshold, label in RATIO_LABELS:
        if percent_of_normal < threshold:
            return label
    return "Much above normal"

# Only keep readings from the last 3 days - anything older means the station
# has likely gone offline or stopped reporting.
MAX_AGE = timedelta(days=3)

OUTPUT_PATH = Path(__file__).parent / "data" / "stations.json"
FULL_POOL_REFERENCE_PATH = Path(__file__).parent / "data" / "full_pool_reference.json"
DUKE_COORDINATES_PATH = Path(__file__).parent / "data" / "duke_lake_coordinates.json"

DUKE_API_BASE = "https://api.hydro-derived.duke-energy.app"
DUKE_DETAILS_BASE = "https://lakes.hydro-derived.duke-energy.app/details"

# Hyco Lake and its afterbay are excluded from build_duke_energy_stations()
# (no entry in duke_lake_coordinates.json) even though Duke Energy operates
# them - Duke's own reported *reading* for these two lags by weeks, while
# USGS's live gauge updates every 15-30 min. We keep sourcing the live
# reading from USGS for these two, but use Duke's more-current min/max
# operating range (see data/full_pool_reference.json) for percent-of-full.


RETRYABLE_HTTP_CODES = {429, 500, 502, 503, 504}


def urlopen_with_retry(url_or_request, timeout=30, max_attempts=3):
    """Wraps urllib.request.urlopen with a few retries for transient failures
    (server hiccups, momentary outages) - this now runs unattended every few
    hours in CI, so a single flaky request shouldn't crash the whole run.
    Client errors (bad URL, 404, etc.) fail immediately since retrying won't
    help.
    """
    last_error = None
    for attempt in range(1, max_attempts + 1):
        try:
            return urllib.request.urlopen(url_or_request, timeout=timeout)
        except urllib.error.HTTPError as e:
            if e.code not in RETRYABLE_HTTP_CODES or attempt == max_attempts:
                raise
            last_error = e
        except urllib.error.URLError as e:
            if attempt == max_attempts:
                raise
            last_error = e
        wait_seconds = 2 ** attempt  # 2s, 4s, 8s
        print(f"  ({last_error} - retrying in {wait_seconds}s, attempt {attempt}/{max_attempts})")
        time.sleep(wait_seconds)


def api_get(path, params):
    url = f"{API_BASE}/{path}?{urllib.parse.urlencode(params)}&f=json"
    with urlopen_with_retry(url) as response:
        return json.load(response)


def fetch_monitoring_locations(location_ids):
    """Returns {monitoring_location_id: {name, lat, lon}} for the given site IDs.

    NC has thousands of historical well records, most long since discontinued,
    so we look up only the specific sites we have current readings for rather
    than requesting "all NC groundwater sites" (which would get cut off by the
    API's result limit before reaching the active ones).
    """
    locations = {}
    location_ids = list(location_ids)
    batch_size = 100
    for i in range(0, len(location_ids), batch_size):
        batch = location_ids[i:i + batch_size]
        data = api_get("collections/monitoring-locations/items", {
            "id": ",".join(batch),
            "limit": batch_size,
        })
        for feature in data["features"]:
            props = feature["properties"]
            lon, lat = feature["geometry"]["coordinates"]
            locations[props["id"]] = {
                "name": props["monitoring_location_name"].title(),
                "number": props["monitoring_location_number"],
                "lat": lat,
                "lon": lon,
            }
    return locations


def fetch_latest_readings(site_type_code, parameter_code):
    """Returns latest instantaneous readings for NC sites of one type/parameter."""
    data = api_get("collections/latest-continuous/items", {
        "state_code": STATE_CODE,
        "site_type_code": site_type_code,
        "parameter_code": parameter_code,
        "limit": 1000,
    })
    return data["features"]


def is_recent(iso_time_str):
    reading_time = datetime.fromisoformat(iso_time_str)
    return datetime.now(timezone.utc) - reading_time <= MAX_AGE


def format_time(iso_time_str):
    reading_time = datetime.fromisoformat(iso_time_str)
    local_time = reading_time.astimezone(EASTERN)  # None here means "this computer's local timezone"
    return local_time.strftime("%Y-%m-%d %I:%M %p %Z")


def fetch_daily_percentiles(site_number, parameter_code):
    """Returns {(month, day): {stat_name: value}} built from USGS's historical
    daily statistics for one site/parameter, or None if none exist.

    This is the "period of record" USGS has computed for that day of the year
    across all the years they've been measuring it - e.g. what a typical
    July 21st looks like at this site, based on 15-35+ years of history.
    """
    url = (f"https://waterservices.usgs.gov/nwis/stat/?format=rdb&sites={site_number}"
           f"&statReportType=daily&statTypeCd=all&parameterCd={parameter_code}")
    with urlopen_with_retry(url) as response:
        text = response.read().decode("utf-8")

    lines = [line for line in text.splitlines() if line and not line.startswith("#")]
    if len(lines) < 3:
        return None  # no data for this site/parameter

    header = lines[0].split("\t")
    data_rows = lines[2:]  # line 1 is the header, line 2 is an RDB format-spec row
    col = {name: i for i, name in enumerate(header)}

    stat_fields = ["min_va", "max_va", "p05_va", "p10_va", "p20_va",
                   "p25_va", "p50_va", "p75_va", "p80_va", "p90_va", "p95_va"]
    if "month_nu" not in col or "day_nu" not in col:
        return None

    by_date = {}
    for row in data_rows:
        fields = row.split("\t")
        if len(fields) != len(header):
            continue
        try:
            month = int(fields[col["month_nu"]])
            day = int(fields[col["day_nu"]])
        except ValueError:
            continue

        stats = {}
        for name in stat_fields:
            if name not in col:
                continue
            raw = fields[col[name]].strip()
            stats[name] = float(raw) if raw else None
        by_date[(month, day)] = stats

    return by_date or None


def percentile_rank(value, stats):
    """Where `value` falls in a historical distribution, as a 0-100 percentile."""
    points = []
    for key, pct in [("min_va", 0), ("p05_va", 5), ("p10_va", 10), ("p20_va", 20),
                      ("p25_va", 25), ("p50_va", 50), ("p75_va", 75), ("p80_va", 80),
                      ("p90_va", 90), ("p95_va", 95), ("max_va", 100)]:
        v = stats.get(key)
        if v is not None:
            points.append((v, pct))
    if len(points) < 3:
        return None

    points.sort()
    if value <= points[0][0]:
        return 0.0
    if value >= points[-1][0]:
        return 100.0
    for (v0, p0), (v1, p1) in zip(points, points[1:]):
        if v0 <= value <= v1:
            return p0 if v1 == v0 else round(p0 + (value - v0) / (v1 - v0) * (p1 - p0), 1)
    return None


def normal_label(percent_normal):
    for threshold, label in NORMAL_LABELS:
        if percent_normal < threshold:
            return label
    return "Much above normal"


def compute_percent_normal(site_number, candidate_params, value, station_type):
    """Returns {percent_normal, normal_label} or None if there's not enough history.

    A site's live reading and its historical daily-percentile record aren't
    always filed under the same parameter code - e.g. many reservoirs report
    live elevation as 62615 (NAVD88) but only have percentile history computed
    under the older 62614 or 00062 codes. We try each candidate in turn and use
    whichever one actually has data.
    """
    stats_by_date = None
    stats_param = None
    for param in candidate_params:
        stats_by_date = fetch_daily_percentiles(site_number, param)
        if stats_by_date:
            stats_param = param
            break
    if not stats_by_date:
        return None

    today = datetime.now()
    stats = stats_by_date.get((today.month, today.day)) or stats_by_date.get((today.month, today.day - 1))
    if stats is None:
        return None

    raw_percentile = percentile_rank(float(value), stats)
    if raw_percentile is None:
        return None

    # Groundwater readings are "depth to water" - a *smaller* number means
    # *more* water. Flip the scale so "higher percent = wetter" holds for
    # both station types, matching how the reservoir elevation percentile
    # already works (bigger elevation = more water = higher percentile).
    percent_normal = 100 - raw_percentile if station_type == "groundwater" else raw_percentile
    result = {
        "percent_normal": round(percent_normal, 1),
        "normal_label": normal_label(percent_normal),
    }

    # If the history we found is filed under a different parameter code than
    # the live reading, and the two use different known datums, say so - a
    # small vertical-datum offset could nudge the percentile slightly.
    live_datum = PARAM_DATUM.get(candidate_params[0], "unstated")
    hist_datum = PARAM_DATUM.get(stats_param, "unstated")
    if stats_param != candidate_params[0] and live_datum != "unstated" and hist_datum != "unstated" and live_datum != hist_datum:
        result["normal_datum_note"] = f"History is from {hist_datum} readings, current reading is {live_datum}"
    return result


def load_full_pool_reference():
    if not FULL_POOL_REFERENCE_PATH.exists():
        return {}
    return json.loads(FULL_POOL_REFERENCE_PATH.read_text(encoding="utf-8"))


def compute_percent_full(site_number, parameter_code, value, full_pool_reference):
    ref = full_pool_reference.get(site_number)
    if ref is None or ref.get("full_pool_ft") is None:
        return None

    full_pool_ft = ref["full_pool_ft"]
    min_pool_ft = ref.get("min_pool_ft")
    value = float(value)

    if min_pool_ft is not None:
        # Usable-range method: scale against how much the reservoir actually
        # operates through (full pool down to minimum conservation pool),
        # not against sea level - most of a reservoir's absolute elevation is
        # just "how tall the lake bed is," which never changes.
        usable_range = full_pool_ft - min_pool_ft
        percent_full = round(min(max((value - min_pool_ft) / usable_range * 100, 0.0), 100.0), 1)
        full_pool_method = "usable_range"
    else:
        # Fallback: crude ratio against sea level. Only used when we don't
        # have a minimum-pool reference - stays close to 100% almost always,
        # so treat it as a rough approximation, not a true "how full" measure.
        percent_full = round(min(value / full_pool_ft * 100, 100.0), 1)
        full_pool_method = "elevation_ratio"

    result = {
        "percent_full": percent_full,
        "full_pool_ft": full_pool_ft,
        "full_pool_source": ref.get("source"),
        "full_pool_method": full_pool_method,
    }
    if min_pool_ft is not None:
        result["min_pool_ft"] = min_pool_ft
    if ref.get("confidence") and ref["confidence"] != "HIGH":
        result["full_pool_confidence"] = ref["confidence"]
    reading_datum = PARAM_DATUM.get(parameter_code, "unstated")
    ref_datum = ref.get("datum", "unstated")
    if reading_datum != "unstated" and ref_datum != "unstated" and reading_datum != ref_datum:
        result["datum_mismatch"] = f"Reading is {reading_datum}, full-pool reference is {ref_datum}"
    return result


def parse_acis_value(raw):
    """ACIS uses 'T' for trace precipitation and 'M' (or similar) for missing days."""
    if raw == "T":
        return 0.0
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def compute_precip_percent_normal(site_number):
    """Year-to-date rainfall vs. the historical normal, from the nearest
    long-record NOAA station (NC's ACIS climate data service - free, no key).
    """
    station = PRECIP_STATIONS.get(site_number)
    if station is None:
        return None

    today = datetime.now()
    body = json.dumps({
        "sid": station["sid"],
        "sdate": f"{today.year}-01-01",
        "edate": today.strftime("%Y-%m-%d"),
        "elems": [{"name": "pcpn", "normal": "1"}, {"name": "pcpn"}],
    }).encode("utf-8")
    req = urllib.request.Request(f"{ACIS_BASE}/StnData", data=body, headers={"Content-Type": "application/json"})
    with urlopen_with_retry(req) as response:
        result = json.load(response)

    actual_sum = 0.0
    normal_sum = 0.0
    days_counted = 0
    for _date, normal_raw, actual_raw in result.get("data", []):
        actual_val = parse_acis_value(actual_raw)
        normal_val = parse_acis_value(normal_raw)
        if actual_val is None or normal_val is None:
            continue  # skip days with missing data on either side, to keep the ratio fair
        actual_sum += actual_val
        normal_sum += normal_val
        days_counted += 1

    if normal_sum <= 0 or days_counted < 30:
        return None

    percent = round(actual_sum / normal_sum * 100, 1)
    return {
        "precip_percent_normal": percent,
        "precip_label": ratio_label(percent),
        "precip_period": f"Jan 1 - {today.strftime('%b')} {today.day}, {today.year}",
        "precip_station_name": station["name"],
        "precip_station_distance_mi": station["distance_mi"],
    }


def build_groundwater_stations():
    readings = fetch_latest_readings("GW", GW_PARAM)
    good_readings = []
    for feature in readings:
        props = feature["properties"]
        if props.get("statistic_id") != "00011":  # 00011 = instantaneous value
            continue
        if props.get("qualifier"):  # e.g. "DISCONTINUED"
            continue
        if not is_recent(props["time"]):
            continue
        good_readings.append(props)

    locations = fetch_monitoring_locations(p["monitoring_location_id"] for p in good_readings)

    stations = []
    for i, props in enumerate(good_readings):
        loc = locations.get(props["monitoring_location_id"])
        if loc is None:
            continue

        print(f"  [{i + 1}/{len(good_readings)}] checking history for {loc['number']}...")
        station = {
            "id": loc["number"],
            "name": loc["name"],
            "type": "groundwater",
            "lat": loc["lat"],
            "lon": loc["lon"],
            "value": props["value"],
            "unit": props["unit_of_measure"],
            "label": "Depth to water (below land surface)",
            "time": format_time(props["time"]),
            "usgs_url": f"https://waterdata.usgs.gov/monitoring-location/{loc['number']}/",
        }
        normal = compute_percent_normal(loc["number"], [GW_PARAM], props["value"], "groundwater")
        if normal:
            station.update(normal)
        stations.append(station)
    return stations


DATUM_TO_PARAM = {"NGVD29": "62614", "NAVD88": "62615"}


def build_reservoir_stations():
    full_pool_reference = load_full_pool_reference()

    # A single reservoir can report elevation under more than one datum code.
    # Gather every param's readings per site first, then pick one per site -
    # preferring whichever datum matches that site's full-pool reference (so
    # percent-of-full doesn't need a datum-mismatch approximation), falling
    # back to the usual 62615 > 62614 > 00062 order otherwise.
    readings_by_site = {}
    for param in LAKE_PARAMS:
        for feature in fetch_latest_readings("LK", param):
            props = feature["properties"]
            if props.get("statistic_id") != "00011":
                continue
            if props.get("qualifier"):
                continue
            if not is_recent(props["time"]):
                continue
            readings_by_site.setdefault(props["monitoring_location_id"], {})[param] = props

    best_reading_by_site = {}
    for site_id, readings_by_param in readings_by_site.items():
        plain_number = site_id.split("-", 1)[1]
        ref_datum = full_pool_reference.get(plain_number, {}).get("datum")
        preferred_param = DATUM_TO_PARAM.get(ref_datum)
        param_order = [preferred_param] + LAKE_PARAMS if preferred_param else LAKE_PARAMS
        for param in param_order:
            if param in readings_by_param:
                best_reading_by_site[site_id] = readings_by_param[param]
                break

    locations = fetch_monitoring_locations(best_reading_by_site.keys())

    stations = []
    for i, (site_id, props) in enumerate(best_reading_by_site.items()):
        loc = locations.get(site_id)
        if loc is None:
            continue

        print(f"  [{i + 1}/{len(best_reading_by_site)}] checking history for {loc['number']}...")
        param = props["parameter_code"]
        station = {
            "id": loc["number"],
            "name": loc["name"],
            "type": "reservoir",
            "lat": loc["lat"],
            "lon": loc["lon"],
            "value": props["value"],
            "unit": props["unit_of_measure"],
            "label": "Reservoir/lake surface elevation",
            "time": format_time(props["time"]),
            "usgs_url": f"https://waterdata.usgs.gov/monitoring-location/{loc['number']}/",
        }
        candidate_params = [param] + [p for p in LAKE_PARAMS if p != param]
        normal = compute_percent_normal(loc["number"], candidate_params, props["value"], "reservoir")
        if normal:
            station.update(normal)
        full = compute_percent_full(loc["number"], param, props["value"], full_pool_reference)
        if full:
            station.update(full)
        precip = compute_precip_percent_normal(loc["number"])
        if precip:
            station.update(precip)
        stations.append(station)
    return stations


def duke_key(lake_name):
    """Normalizes Duke's internal lake-name field for matching against our
    hand-curated coordinates file, e.g. 'MTN ISLAND' -> 'MTNISLAND'."""
    return lake_name.upper().replace(" ", "").replace("-", "")


def load_duke_coordinates():
    if not DUKE_COORDINATES_PATH.exists():
        return {}
    return json.loads(DUKE_COORDINATES_PATH.read_text(encoding="utf-8"))


def fetch_duke_lakes():
    req = urllib.request.Request(f"{DUKE_API_BASE}/lakes/current-level", headers={"Accept": "application/json"})
    with urlopen_with_retry(req) as response:
        return json.load(response)


def parse_duke_float(raw):
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    raw = raw.strip()
    if not raw or raw.upper() == "NA":
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def format_duke_time(naive_time_str):
    # Duke's API returns timestamps with no timezone marker. Duke is
    # headquartered in Charlotte, NC, and spot-checking these against when
    # they were fetched suggests they're already Eastern local time, not UTC.
    dt = datetime.fromisoformat(naive_time_str)
    return dt.strftime("%Y-%m-%d %I:%M %p") + " (Duke Energy report, Eastern time)"


def build_duke_energy_stations():
    """Reservoirs Duke Energy operates directly (Catawba-Wateree, Yadkin-Pee
    Dee, etc.) - not covered by USGS's live elevation gauges, so we go
    straight to Duke's own public lake-level feed instead. See README for
    background on why USGS doesn't have these and what this API is.
    """
    coordinates = load_duke_coordinates()
    lakes = fetch_duke_lakes()

    stations = []
    for lake in lakes:
        key = duke_key(lake["LakeName"])
        coord = coordinates.get(key)
        if coord is None or coord.get("in_nc") not in ("yes", "partial"):
            continue  # no confirmed NC coordinate for this lake (yet), or it's not in NC

        actual = parse_duke_float(lake.get("Actual"))
        min_val = parse_duke_float(lake.get("Min"))
        max_val = parse_duke_float(lake.get("Max"))
        target = parse_duke_float(lake.get("Target"))
        if actual is None or min_val is None or max_val is None or max_val == min_val:
            continue

        is_normalized_scale = abs(max_val - 100.0) < 0.01
        if is_normalized_scale:
            # Duke reports this lake on their own 0-100 scale (100 = full
            # pool), so "Actual" already IS a percent-full figure - likely
            # from their own storage-volume curves, more precise than an
            # elevation ratio, so we use it directly instead of re-deriving.
            value_display = f"{actual:.1f}"
            unit_display = "%"
            reading_label = "Percent of full pool (Duke Energy's own live figure)"
            percent_full = round(actual, 1)
            full_pool_method = "duke_normalized"
        else:
            value_display = f"{actual:.2f}"
            unit_display = "ft"
            reading_label = "Reservoir surface elevation (Duke Energy)"
            percent_full = round(min(max((actual - min_val) / (max_val - min_val) * 100, 0.0), 100.0), 1)
            full_pool_method = "usable_range"

        station = {
            "id": f"DUKE-{lake['LakeId']}",
            "name": lake["LakeDisplayName"],
            "type": "reservoir",
            "lat": coord["lat"],
            "lon": coord["lon"],
            "value": value_display,
            "unit": unit_display,
            "label": reading_label,
            "time": format_duke_time(lake["Date"]),
            "usgs_url": f"{DUKE_DETAILS_BASE}/{lake['LocationId']}/{lake['LakeId']}",
            "percent_full": percent_full,
            "full_pool_method": full_pool_method,
            "full_pool_source": "Duke Energy (live operating data)",
            "data_source": "duke_energy",
        }
        if full_pool_method == "usable_range":
            station["full_pool_ft"] = max_val
            station["min_pool_ft"] = min_val
        if lake.get("Elevation"):
            station["full_pool_elevation_note"] = lake["Elevation"].strip()
        if target is not None and target != 0:
            percent_of_target = round(actual / target * 100, 1)
            station["percent_of_target"] = percent_of_target
            station["target_label"] = ratio_label(percent_of_target)
        lip_stage = lake.get("LowInputStage")
        if lip_stage is not None and lip_stage != -1:
            station["drought_stage"] = lip_stage
        messages = lake.get("SpecialMessage") or []
        if messages and messages[0].get("Text"):
            station["official_message"] = messages[0]["Text"]
            station["official_message_date"] = messages[0].get("EventDate")
        stations.append(station)
    return stations


def main():
    print("Fetching NC groundwater wells...")
    groundwater = build_groundwater_stations()
    print(f"  {len(groundwater)} wells with current readings")

    print("Fetching NC reservoir/lake stations...")
    reservoirs = build_reservoir_stations()
    print(f"  {len(reservoirs)} reservoirs/lakes with current readings")

    print("Fetching Duke Energy-operated lakes...")
    duke_lakes = build_duke_energy_stations()
    print(f"  {len(duke_lakes)} Duke Energy lakes with a confirmed NC location")

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "stations": groundwater + reservoirs + duke_lakes,
    }

    OUTPUT_PATH.parent.mkdir(exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(f"Saved {len(output['stations'])} stations to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
