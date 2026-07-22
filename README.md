# NC Water Table & Reservoir Tracker

**Live at:** https://nicksr1219.github.io/nc-water-tracker/ — data refreshes
automatically every 4 hours via GitHub Actions (see `.github/workflows/refresh-and-deploy.yml`).
No need to run anything locally just to view the map; the instructions below
are for making changes to the project.

## What's here
- `fetch_data.py` — pulls current groundwater/reservoir readings for North
  Carolina from USGS and Duke Energy, computes percent-of-normal and
  percent-of-full where possible, and saves it all to `data/stations.json`.
- `index.html` — the map. Loads `data/stations.json` and lets you click
  stations to see their reading and gauges.
- `data/stations.json` — the cached data. This is what the map actually
  displays; it doesn't call any API live in the browser, so you need to
  re-run `fetch_data.py` to get fresh numbers.
- `data/full_pool_reference.json` — hand-curated full-pool elevations for
  USGS-tracked reservoirs, with a source cited for each one.
- `data/duke_lake_coordinates.json` — hand-curated coordinates and
  NC/not-NC status for Duke Energy's lakes, since their API doesn't include
  location data. Both of these reference files are the only parts of the
  project not pulled live from an API — see below for how they were built.
- `refresh_data.bat` / `run_server.bat` — double-click shortcuts for the two
  commands below, so you don't have to type anything.
- `.github/workflows/refresh-and-deploy.yml` — runs `fetch_data.py` every 4
  hours on GitHub's servers, commits the refreshed `data/stations.json`, and
  publishes the site to GitHub Pages. This is what keeps the live URL above
  current without you needing to run anything yourself.

## How to run it locally (for making changes)

**1. Get fresh data (do this whenever you want current numbers):**
```bash
py fetch_data.py
```
Or just double-click `refresh_data.bat`.

**2. View the map:**
```bash
py -m http.server 8000
```
Then open `http://localhost:8000/index.html` in your browser.

Or just double-click `run_server.bat` — it starts the server and opens the
page for you. Leave that window open while you're using the map; closing it
stops the server. Press Ctrl+C in that window (or just close it) when done.

*(Why a server and not just double-clicking index.html? Browsers block a
page from reading local files like `data/stations.json` unless it's served
over `http://`. This is a browser security rule, not something specific to
this app.)*

## Where the data comes from
Two sources, both free and requiring no login or API key:

- **USGS's public Water Data API** (`api.waterdata.usgs.gov`) for all
  groundwater wells and 18 reservoirs/lakes that USGS gauges directly.
  Groundwater: depth to water below land surface (parameter 72019).
  Reservoirs: water surface elevation in feet (parameter 62615, or a
  fallback code if a site doesn't report that one).
- **Duke Energy's own live lake-level feed** (`api.hydro-derived.duke-energy.app`)
  for 22 more reservoirs that Duke operates directly and USGS doesn't gauge
  at all - see "Duke Energy lakes" below.

Only stations that reported a reading in the last 3 days are included (USGS
side), so the map only shows currently-active stations.

### Percent of normal
Computed from USGS's own historical daily statistics for each station -
essentially "how does today's reading compare to every reading USGS has on
record for this exact date, across all the years they've measured it here."
A reading at the historical median for that date is 50%; a record low is 0%;
a record high is 100%. This needs a reasonably long period of record, so not
every station gets one (currently: all groundwater wells, and 8 of 18
reservoirs — mostly the small stormwater ponds don't have enough history).

For groundwater, the scale is flipped so "higher % = more water" for both
station types: raw USGS data is "depth to water," where a *bigger* number
means the water is *farther* down.

### Percent of full pool
Only shown for reservoirs listed in `data/full_pool_reference.json`, each
with a cited source. Two calculation methods, depending on what's available:

- **Usable-range method** (preferred): (current elevation − minimum
  conservation-pool elevation) ÷ (full pool − minimum), scaled 0-100%. This
  compares against the reservoir's actual operating band, which is what
  people intuitively mean by "how full is it."
- **Elevation-ratio fallback**: current elevation ÷ full-pool elevation,
  used only when we don't have a minimum-pool reference. This one is a rough
  approximation - a reservoir's absolute elevation is mostly just "height
  above sea level," which never changes, so this ratio stays close to 100%
  almost regardless of drought severity. The panel labels which method was
  used for each reservoir and explains the difference inline.

If a reservoir's live reading and its full-pool reference use different
vertical datums (NAVD88 vs. NGVD29, roughly a 1 ft difference in NC), the app
says so in the panel; where possible, `fetch_data.py` instead just fetches
the live reading in the *matching* datum so no approximation is needed.

Researched full-pool elevations for all 18 reservoirs; found solid, cited
numbers for 6, and minimum-pool elevations (enabling the better usable-range
method) for 4 of those 6:

| Reservoir | Full pool | Minimum pool | Method | Confidence |
|---|---|---|---|---|
| W. Kerr Scott Reservoir | 1030.0 ft | 1000.0 ft | Usable range | High |
| Lake Crabtree | 276.0 ft | not found | Elevation ratio | Medium |
| B. Everett Jordan Lake | 216.0 ft | 202.0 ft | Usable range | High |
| Falls Lake | 251.5 ft | 236.5 ft | Usable range | High |
| Hyco Lake | 410.5 ft | 406.0 ft | Usable range | High |
| Afterbay Reservoir (Roxboro) | 399.0 ft | 375.0 ft | Usable range | High |

Lake Crabtree is a flood-control/sediment structure without a published
operating range anywhere findable, so it keeps the cruder elevation-ratio
approximation. Hyco Lake and its Afterbay originally used the same fallback
(from a 1970s USGS report with no minimum-pool figure at all), but as of
2026-07-22 both use Duke Energy's own live operating range instead - see
"Duke Energy lakes" below for why, and for what we learned in the process
(the Afterbay reservoir is currently reading *below* Duke's own stated
minimum operating elevation - a genuinely severe condition, not a data
error, cross-checked between both USGS's and Duke's independent readings).

The other 12 reservoirs (mostly small Raleigh/Cary/Morrisville stormwater
ponds, plus both Lake Mattamuskeet gauges) have no publicly documented
full-pool elevation at all - most aren't large enough to fall under NC's
regulated dam inventory, and Mattamuskeet is a natural, passively-managed
lake with no engineered "full" to reference. These stations still show
percent-of-normal when they have enough history; they just don't get a
percent-of-full gauge. We didn't want to guess a number rather than show
nothing.

### Local rainfall vs. normal
Shown alongside the other two gauges for the same 6 reservoirs, as causal
context for *why* a reservoir might be low - separate from, and not a fix
for, the percent-of-full/percent-of-normal comparison above. Computed as
year-to-date actual precipitation ÷ year-to-date normal precipitation, from
the nearest long-record NOAA weather station (4-23 miles from each
reservoir, all picked by straight-line distance) via NOAA's free ACIS
climate data API (`data.rcc-acis.org`, no key required). 100% = received
exactly the normal amount of rain so far this year; below 100% is a
rainfall deficit.

Note this uses different classification bands than the other two gauges:
"percent of normal" for water levels is a *percentile rank* (50% = typical
day), while "percent of normal" for rainfall is a *ratio* (100% = typical
amount) - the two numbers aren't on the same scale even though both use the
same "much below normal → much above normal" labels.

### Duke Energy lakes
Major NC lakes like Lake Norman, Lake Wylie, Lake James, and Lake Hickory
have **no live USGS data at all** - USGS has old historical station records
for some, but Duke Energy (who operates these reservoirs directly) publishes
their own water levels rather than feeding them into USGS's public system.
Duke's own site (`lakes.duke-energy.com`) turned out to be a modern web app
backed by a clean, public JSON API with no login required - the same data
they show on their own site and mobile app - so `fetch_data.py` pulls
directly from that instead (`api.hydro-derived.duke-energy.app`). This is an
unofficial API, found by inspecting the site's network traffic rather than a
documented partner integration - worth knowing if this ever breaks without
warning, since Duke could change it without notice.

Duke's feed covers 34 lakes total, some in South Carolina. We identified
which are actually (or partially) in North Carolina and found coordinates
for them - Duke's API gives levels but no location data - and currently
show **22 of the 34** on the map (`data/duke_lake_coordinates.json` has the
full research, including the ones confirmed *not* in NC, like Lake Wateree
and Lake Keowee, and the ones still missing a confident coordinate, like
Tuckasegee Lake).

Duke reports each lake one of two ways:
- **Normalized 0-100 scale** (most of the larger lakes): "Actual" already
  *is* a percent-of-full-pool figure, likely from Duke's own storage-volume
  curves - more precise than anything we could derive ourselves, so we use
  it directly rather than recalculating.
- **Raw feet**: same usable-range calculation as the USGS-tracked reservoirs
  above, just using Duke's own min/max instead of separately-researched
  numbers.

Duke also provides two things nothing else in this app has:
- **A seasonal target** (their own operating guide-curve for right now) -
  shown as "percent of target," a ratio-based cousin of the percentile-based
  "percent of normal" used for USGS stations, with its own explanatory note
  since the two aren't measuring the same kind of "normal."
- **Official drought-stage and narrative text** ("Low Inflow Protocol" stage
  and dated update messages, written by Duke's own Drought Management
  Advisory Group) - shown directly in the panel. This is the real version of
  the illustrative "Background" context block from the original demo mockup.

## Current status vs. the spec
Done:
- Live NC map with 98 real stations: groundwater wells + USGS reservoirs +
  Duke Energy lakes (including Lake Norman, Lake Wylie, Lake James, and
  other major lakes USGS doesn't cover at all)
- Click a station for its current reading, reading time, gauges, and a link
  to its full record (USGS or Duke Energy, whichever sourced that station)
- Percent-of-normal: percentile-based for USGS stations (all groundwater
  wells, 8/18 USGS reservoirs), target-based for Duke lakes that report one
- Percent-of-full: usable-range or Duke's own live figure for 26 reservoirs
  total, elevation-ratio fallback for 1 (Lake Crabtree)
- Local rainfall vs. normal for the 6 USGS reservoirs with full-pool data
- Duke Energy's official drought stage and narrative update text, where
  Duke provides it
- Map markers colored by drought status with shape for station type, plus
  filter checkboxes to show wells only / reservoirs only / both
- Zoom/pan, legend explaining shape and color

Not yet built:
- 12 of Duke's 34 lakes still aren't shown - confirmed not in NC (mostly
  South Carolina lakes like Lake Wateree and Lake Keowee), except Tuckasegee
  Lake, which is in NC but still needs a confidently-sourced coordinate.
- The written "drought background" paragraph from the demo, for stations
  that don't have Duke's official narrative text - USGS-only stations still
  don't have anything like this.
