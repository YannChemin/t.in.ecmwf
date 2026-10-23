#!/usr/bin/env python3

############################################################################
#
# MODULE:       t.in.ecmwf
# AUTHOR(S):    Yann Chemin
# PURPOSE:      Downloads ECMWF open-data real-time forecasts (IFS at 0.25
#               or 0.1 degree, AIFS at 0.25 degree) and imports each
#               requested parameter as a space-time raster dataset.
#               Only the requested GRIB messages are fetched, through
#               byte-range requests driven by the per-file JSON index.
#
# SPDX-License-Identifier: Unlicense
#
############################################################################

# %module
# % description: Downloads ECMWF open-data forecasts (IFS 0.25 or 0.1 degree, AIFS 0.25 degree) and imports them as space-time raster datasets.
# % keyword: temporal
# % keyword: import
# % keyword: forecast
# % keyword: weather
# % keyword: ECMWF
# %end
# %option
# % key: datasets
# % type: string
# % multiple: yes
# % required: yes
# % options: ifs_oper,ifs_enfo,ifs_enfo_ep,ifs_wave,ifs_waef,ifs_waef_ep,aifs_single,aifs_ens,aifs_ens_cf
# % answer: ifs_oper
# % description: Forecast datasets to download, processed one after the other in the given order
# % descriptions: ifs_oper;IFS control forecast (oper/fc, formerly HRES);ifs_enfo;IFS ensemble perturbed members (enfo/ef);ifs_enfo_ep;IFS ensemble probabilities (enfo/ep);ifs_wave;IFS wave control forecast (wave/fc);ifs_waef;IFS wave ensemble members (waef/ef);ifs_waef_ep;IFS wave ensemble probabilities (waef/ep);aifs_single;AIFS Single forecast (0.25 degree only);aifs_ens;AIFS ENS perturbed members (0.25 degree only);aifs_ens_cf;AIFS ENS control member (0.25 degree only)
# %end
# %option
# % key: resolution
# % type: string
# % required: no
# % options: 0.25,0.1,best
# % answer: 0.25
# % description: Grid spacing in degrees
# % descriptions: 0.25;0.25 degree grid (all datasets);0.1;0.1 degree grid (IFS datasets only, published 2 hours after 0.25);best;0.1 degree where the run is already published at 0.1, 0.25 degree otherwise
# %end
# %option
# % key: parameters
# % type: string
# % multiple: yes
# % required: no
# % answer: 2t,tp
# % description: ECMWF parameter short names (for example 2t,tp,10u,10v,msl,ro,swh); use -l to list what a dataset offers
# %end
# %option
# % key: levels
# % type: string
# % multiple: yes
# % required: no
# % description: Levels to keep for multi-level parameters (pressure levels in hPa, soil layers 1-4); default all levels
# %end
# %option
# % key: members
# % type: string
# % multiple: yes
# % required: no
# % description: Ensemble members to keep, as numbers or ranges (for example 1-10,25); default all members
# %end
# %option
# % key: steps
# % type: string
# % multiple: yes
# % required: no
# % answer: 0-24
# % description: Forecast steps in hours to download, as numbers or ranges (for example 0-72,96); ignored with -b
# %end
# %option
# % key: date
# % type: string
# % required: no
# % description: Forecast run date, YYYY-MM-DD (default the latest complete run)
# %end
# %option
# % key: time
# % type: string
# % required: no
# % options: 00,06,12,18
# % description: Forecast run hour in UTC (default the latest complete run)
# %end
# %option
# % key: area
# % type: double
# % multiple: yes
# % required: no
# % key_desc: north,west,south,east
# % description: Bounding box in WGS84 degrees (default the current region)
# %end
# %option
# % key: source
# % type: string
# % required: no
# % options: ecmwf,aws,google
# % answer: ecmwf
# % description: Server to download from
# % descriptions: ecmwf;data.ecmwf.int (rolling archive of about 4 days);aws;Amazon S3 mirror;google;Google Cloud Storage mirror
# %end
# %option
# % key: output_prefix
# % type: string
# % required: no
# % answer: ecmwf
# % description: Prefix of the output STRDS names, <output_prefix>_<dataset>_<parameter>[_<level>]
# %end
# %option
# % key: resample
# % type: string
# % required: no
# % options: nearest,bilinear,bicubic,lanczos,bilinear_f,bicubic_f,lanczos_f
# % answer: nearest
# % description: Resampling method used by r.import when the project is not in WGS84
# %end
# %option G_OPT_M_DIR
# % key: cache_dir
# % required: no
# % description: Directory to keep downloaded GRIB fields in, reused by later runs (default a temporary directory)
# %end
# %option G_OPT_M_NPROCS
# % answer: 4
# % description: Number of parallel downloads within a dataset
# %end
# %flag
# % key: b
# % description: Bundle: download every available step of the run, up to 15 days (360 h)
# %end
# %flag
# % key: d
# % description: De-accumulate accumulated fields (tp, cp, sf, ro, ssrd, ...) into amounts per step interval
# %end
# %flag
# % key: l
# % description: List the run, steps, parameters, levels and members of each dataset, then exit
# %end
# %rules
# % requires: date,time
# % requires: time,date
# %end

import atexit
import datetime as dt
import json
import math
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import grass.script as gs

TMP_RASTERS = []
TMP_DIR = None

SOURCES = {
    "ecmwf": "https://data.ecmwf.int/forecasts",
    "aws": "https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com",
    "google": "https://storage.googleapis.com/ecmwf-open-data",
}

RESOLUTIONS = {"0.25": "0p25", "0.1": "0p1"}
RESOLUTION_DEG = {"0p25": 0.25, "0p1": 0.1}

# Step lists are the published schedules; "ifs" runs 00/12 go to 360 h,
# runs 06/18 stop at 144 h (checked on data.ecmwf.int, October 2026).
DATASETS = {
    "ifs_oper": dict(model="ifs", stream="oper", type="fc", schedule="ifs"),
    "ifs_enfo": dict(model="ifs", stream="enfo", type="ef", schedule="ifs"),
    "ifs_enfo_ep": dict(model="ifs", stream="enfo", type="ep", schedule="ep"),
    "ifs_wave": dict(model="ifs", stream="wave", type="fc", schedule="ifs"),
    "ifs_waef": dict(model="ifs", stream="waef", type="ef", schedule="ifs"),
    "ifs_waef_ep": dict(model="ifs", stream="waef", type="ep", schedule="ep"),
    "aifs_single": dict(model="aifs-single", stream="oper", type="fc", schedule="aifs"),
    "aifs_ens": dict(model="aifs-ens", stream="enfo", type="pf", schedule="aifs"),
    "aifs_ens_cf": dict(model="aifs-ens", stream="enfo", type="cf", schedule="aifs"),
}

BUNDLE_HOURS = 360

# Fields accumulated from the start of the forecast.
ACCUMULATED = {
    "tp",
    "cp",
    "lsp",
    "sf",
    "ro",
    "sro",
    "ssro",
    "ssr",
    "ssrd",
    "str",
    "strd",
    "ttr",
    "tsr",
    "tisr",
    "ewss",
    "nsss",
    "slhf",
    "sshf",
    "e",
    "pev",
    "es",
    "smlt",
    "fdir",
    "cdir",
}
# De-accumulated amounts that cannot be negative; differences of packed
# values give small negative noise (a few 1e-5 m for 12-bit tp), clipped
# to 0.
NON_NEGATIVE = {"tp", "cp", "lsp", "sf", "ro", "sro", "ssro", "ssrd", "strd", "smlt"}

# Names and native units of the usual open-data parameters, for the STRDS
# metadata (GDAL only names the WMO-standard ones).
PARAMETERS = {
    "10fg": ("10 m wind gust since previous post-processing", "m s-1"),
    "100u": ("100 m U wind component", "m s-1"),
    "100v": ("100 m V wind component", "m s-1"),
    "10u": ("10 m U wind component", "m s-1"),
    "10v": ("10 m V wind component", "m s-1"),
    "2d": ("2 m dewpoint temperature", "K"),
    "2t": ("2 m temperature", "K"),
    "asn": ("Snow albedo", "0-1"),
    "cp": ("Convective precipitation", "m"),
    "d": ("Divergence", "s-1"),
    "ewss": ("Eastward turbulent surface stress", "N m-2 s"),
    "gh": ("Geopotential height", "gpm"),
    "hcc": ("High cloud cover", "0-1"),
    "lcc": ("Low cloud cover", "0-1"),
    "lsm": ("Land-sea mask", "0-1"),
    "mcc": ("Medium cloud cover", "0-1"),
    "mn2t3": ("Minimum 2 m temperature in the last 3 hours", "K"),
    "mn2t6": ("Minimum 2 m temperature in the last 6 hours", "K"),
    "mp2": ("Mean zero-crossing wave period", "s"),
    "msl": ("Mean sea level pressure", "Pa"),
    "mucape": ("Most-unstable CAPE", "J kg-1"),
    "mwd": ("Mean wave direction", "degree true"),
    "mwp": ("Mean wave period", "s"),
    "mx2t3": ("Maximum 2 m temperature in the last 3 hours", "K"),
    "mx2t6": ("Maximum 2 m temperature in the last 6 hours", "K"),
    "nsss": ("Northward turbulent surface stress", "N m-2 s"),
    "pp1d": ("Peak wave period", "s"),
    "ptype": ("Precipitation type", "code table 4.201"),
    "q": ("Specific humidity", "kg kg-1"),
    "r": ("Relative humidity", "%"),
    "ro": ("Runoff", "m"),
    "rsn": ("Snow density", "kg m-3"),
    "sd": ("Snow depth", "m of water equivalent"),
    "sf": ("Snowfall", "m of water equivalent"),
    "sithick": ("Sea ice thickness", "m"),
    "skt": ("Skin temperature", "K"),
    "sot": ("Soil temperature", "K"),
    "sp": ("Surface pressure", "Pa"),
    "ssr": ("Surface net short-wave radiation", "J m-2"),
    "ssrd": ("Surface short-wave radiation downwards", "J m-2"),
    "str": ("Surface net long-wave radiation", "J m-2"),
    "strd": ("Surface long-wave radiation downwards", "J m-2"),
    "sve": ("Eastward surface sea water velocity", "m s-1"),
    "svn": ("Northward surface sea water velocity", "m s-1"),
    "swh": ("Significant height of combined wind waves and swell", "m"),
    "t": ("Temperature", "K"),
    "tcc": ("Total cloud cover", "0-1"),
    "tciw": ("Total column cloud ice water", "kg m-2"),
    "tclw": ("Total column cloud liquid water", "kg m-2"),
    "tcrw": ("Total column rain water", "kg m-2"),
    "tcw": ("Total column water", "kg m-2"),
    "tcwv": ("Total column water vapour", "kg m-2"),
    "tp": ("Total precipitation", "m"),
    "tprate": ("Total precipitation rate", "kg m-2 s-1"),
    "ttr": ("Top net long-wave radiation", "J m-2"),
    "u": ("U wind component", "m s-1"),
    "v": ("V wind component", "m s-1"),
    "vo": ("Vorticity", "s-1"),
    "vsw": ("Volumetric soil water", "m3 m-3"),
    "w": ("Vertical velocity", "Pa s-1"),
    "z": ("Geopotential", "m2 s-2"),
    "zos": ("Sea surface height", "m"),
}

# AIFS encodes precipitation with the WMO codes, in kg m-2 (mm), where
# the IFS uses the ECMWF local codes in m.
AIFS_UNITS = {"tp": "kg m-2", "cp": "kg m-2", "sf": "kg m-2"}

# Extremes over the interval since the previous post-processing step.
SINCE_PREVIOUS = {"10fg": "max"}
# Extremes over a fixed window, the window length in hours in the name.
WINDOW_RE = re.compile(r"^m([xn])2t(\d+)$")

HTTP_RETRIES = 4


def cleanup():
    if TMP_RASTERS:
        gs.run_command(
            "g.remove",
            flags="f",
            type="raster",
            name=TMP_RASTERS,
            quiet=True,
            errors="ignore",
        )
    if TMP_DIR and os.path.isdir(TMP_DIR):
        shutil.rmtree(TMP_DIR, ignore_errors=True)


class NotFound(Exception):
    """The server answered 404 (or 403, as the S3 mirrors do)."""


def http_get(url, offset=None, length=None):
    """Returns the body of url, or of the byte range [offset, offset +
    length) when offset is given; retries transient failures with a
    growing delay and raises NotFound for a missing file."""
    headers = {"User-Agent": "t.in.ecmwf (GRASS GIS addon)"}
    if offset is not None:
        headers["Range"] = "bytes={}-{}".format(offset, offset + length - 1)
    delay = 5
    for attempt in range(HTTP_RETRIES):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = resp.read()
            if length is not None and len(data) != length:
                raise OSError("got {} bytes instead of {}".format(len(data), length))
            return data
        except urllib.error.HTTPError as e:
            if e.code in (403, 404):
                raise NotFound(url) from e
            error = e
        except (OSError, TimeoutError) as e:
            error = e
        if attempt < HTTP_RETRIES - 1:
            gs.verbose("Retrying {} in {} s ({})...".format(url, delay, error))
            time.sleep(delay)
            delay *= 3
    raise OSError("Download of {} failed: {}".format(url, error))


def http_exists(url):
    req = urllib.request.Request(
        url, method="HEAD", headers={"User-Agent": "t.in.ecmwf (GRASS GIS addon)"}
    )
    try:
        with urllib.request.urlopen(req, timeout=60):
            return True
    except urllib.error.HTTPError as e:
        if e.code in (403, 404):
            return False
        raise


def schedule_steps(schedule, hour):
    """Every step published for a run starting at `hour` UTC."""
    if schedule == "ep":
        return [240, 360]
    if schedule == "aifs":
        return list(range(0, 361, 6))
    final = 360 if hour in (0, 12) else 144
    steps = list(range(0, 145, 3)) + list(range(150, 361, 6))
    return [s for s in steps if s <= final]


def file_parts(resol, levtypes=("sfc",)):
    """File name suffixes: one file per step at 0.25 degree, surface and
    pressure-level files split at 0.1 degree."""
    if resol == "0p25":
        return [""]
    return ["-sfc" if lt == "sfc" else "-pl" for lt in levtypes]


def file_url(root, run, ds, resol, step, part, ext):
    return (
        "{root}/{run:%Y%m%d}/{run:%H}z/{model}/{resol}/{stream}/"
        "{run:%Y%m%d%H}0000-{step}h-{stream}-{type}{part}.{ext}"
    ).format(
        root=root,
        run=run,
        model=ds["model"],
        resol=resol,
        stream=ds["stream"],
        type=ds["type"],
        step=step,
        part=part,
        ext=ext,
    )


def supports(ds, resol):
    return resol == "0p25" or ds["model"] == "ifs"


def run_is_published(root, ds, resol, run):
    """A run counts as published once the index of its last step exists:
    ECMWF publishes the steps in order."""
    final = schedule_steps(ds["schedule"], run.hour)[-1]
    part = file_parts(resol)[0]
    return http_exists(file_url(root, run, ds, resol, final, part, "index"))


def latest_run(root, ds, resol, days_back=5):
    now = dt.datetime.now(dt.timezone.utc)
    cand = now.replace(hour=now.hour - now.hour % 6, minute=0, second=0, microsecond=0)
    for _ in range(days_back * 4):
        if run_is_published(root, ds, resol, cand):
            return cand
        cand -= dt.timedelta(hours=6)
    return None


def choose_run_and_resolution(root, name, ds, resolution, run):
    """Returns (run, resol) for one dataset. With resolution=best the 0.1
    degree grid is taken when it already carries the run that 0.25 degree
    offers, since 0.1 degree is published 2 hours later."""
    if resolution == "best":
        candidates = ["0p1", "0p25"] if supports(ds, "0p1") else ["0p25"]
    else:
        resol = RESOLUTIONS[resolution]
        if not supports(ds, resol):
            gs.fatal(
                "Dataset <{}> is not published at {} degree".format(name, resolution)
            )
        candidates = [resol]

    if run is not None:
        for resol in candidates:
            part = file_parts(resol)[0]
            if http_exists(
                file_url(
                    root,
                    run,
                    ds,
                    resol,
                    schedule_steps(ds["schedule"], run.hour)[0],
                    part,
                    "index",
                )
            ):
                return run, resol
        gs.fatal(
            "Run {:%Y-%m-%d %H} UTC of <{}> is not available at {} degree".format(
                run, name, " or ".join(str(RESOLUTION_DEG[r]) for r in candidates)
            )
        )

    found = {r: latest_run(root, ds, r) for r in candidates}
    if resolution == "best" and len(candidates) == 2:
        fine, coarse = found["0p1"], found["0p25"]
        if fine is not None and (coarse is None or fine >= coarse):
            return fine, "0p1"
        if coarse is not None:
            gs.message(
                (
                    "Run {:%Y-%m-%d %H} UTC of <{}> is not yet published at 0.1 "
                    "degree, using 0.25 degree"
                ).format(coarse, name)
            )
            return coarse, "0p25"
    elif found[candidates[0]] is not None:
        return found[candidates[0]], candidates[0]
    gs.fatal("No published run of <{}> found on the server".format(name))


def parse_ranges(values, what):
    """Parses ["0-72", "96"] into a list of (low, high) integer pairs."""
    ranges = []
    for item in values:
        item = item.strip()
        m = re.fullmatch(r"(\d+)(?:-(\d+))?", item)
        if not m:
            gs.fatal(
                "Invalid {} <{}>, expected a number or a range a-b".format(what, item)
            )
        low = int(m.group(1))
        high = int(m.group(2)) if m.group(2) else low
        if high < low:
            gs.fatal("Invalid {} range <{}>".format(what, item))
        ranges.append((low, high))
    return ranges


def in_ranges(value, ranges):
    return ranges is None or any(low <= value <= high for low, high in ranges)


def step_item(entry):
    """The end step in hours, or (start, end) for an interval step."""
    start, end = step_range(entry)
    return end if start == end else (start, end)


def step_range(entry):
    """(start, end) of an index entry's step; "0-24" for probabilities."""
    parts = entry["step"].split("-")
    return int(parts[0]), int(parts[-1])


def read_indexes(root, ds, resol, run, steps, levtypes, nprocs):
    """Reads the JSON index of every (step, file part); returns a list of
    (grib_url, entry) and the steps whose files are missing."""
    jobs = []
    for step in steps:
        for part in file_parts(resol, levtypes):
            jobs.append((step, file_url(root, run, ds, resol, step, part, "index")))
    entries = []
    missing = set()

    def fetch(job):
        step, url = job
        try:
            text = http_get(url).decode()
        except NotFound:
            return step, url, None
        return (
            step,
            url,
            [json.loads(line) for line in text.splitlines() if line.strip()],
        )

    with ThreadPoolExecutor(max_workers=nprocs) as pool:
        for step, url, lines in pool.map(fetch, jobs):
            if lines is None:
                missing.add(step)
                continue
            grib = url[: -len("index")] + "grib2"
            entries.extend((grib, e) for e in lines)
    return entries, sorted(missing)


def select_entries(entries, params, levels, members, step_ranges):
    selected = []
    for grib, e in entries:
        if e["param"] not in params:
            continue
        if "levelist" in e and levels and e["levelist"] not in levels:
            continue
        if "number" in e and not in_ranges(int(e["number"]), members):
            continue
        if not in_ranges(step_range(e)[1], step_ranges):
            continue
        selected.append((grib, e))
    return selected


def series_key(e):
    return e["param"], e.get("levelist"), e.get("number")


def strds_name(prefix, dataset, param, level):
    name = "{}_{}_{}".format(prefix, dataset, param)
    if level is not None:
        name += "_" + level
    return name


def parameter_info(param, model, comment, unit):
    """(name, unit) of a parameter, from the table when it is known,
    else from the GRIB metadata."""
    if param not in PARAMETERS:
        return comment or param, unit
    name, unit = PARAMETERS[param]
    if model.startswith("aifs"):
        unit = AIFS_UNITS.get(param, unit)
    return name, unit


def semantic_type(param):
    if param in ACCUMULATED:
        return "sum"
    if param in SINCE_PREVIOUS:
        return SINCE_PREVIOUS[param]
    m = WINDOW_RE.match(param)
    if m:
        return "max" if m.group(1) == "x" else "min"
    return "mean"


def time_spans(param, items, all_steps, deaccumulate):
    """Turns the sorted steps of one series into map time stamps. `items`
    holds end steps in hours, or (start, end) pairs for probabilities.
    Returns a list of (item, start, end, previous) in hours after the run:
    end None marks a point in time, previous is the step to subtract
    (de-accumulation) or None. Steps that would give an empty interval,
    such as step 0 of an accumulated field, are dropped."""
    spans = []
    prev_selected = None
    for item in items:
        if isinstance(item, tuple):
            spans.append((item, item[0], item[1], None))
            continue
        step = item
        window = WINDOW_RE.match(param)
        if param in ACCUMULATED:
            if step > 0:
                if deaccumulate:
                    start = prev_selected if prev_selected is not None else 0
                    spans.append((item, start, step, prev_selected))
                else:
                    spans.append((item, 0, step, None))
        elif param in SINCE_PREVIOUS:
            earlier = [s for s in all_steps if s < step]
            if earlier:
                spans.append((item, earlier[-1], step, None))
        elif window:
            hours = int(window.group(2))
            if step >= hours:
                spans.append((item, step - hours, step, None))
        else:
            spans.append((item, step, None, None))
        prev_selected = step
    return spans


def cache_path(cache_dir, dataset, resol, run, e):
    level = "_" + e["levelist"] if "levelist" in e else ""
    member = "_m{:02d}".format(int(e["number"])) if "number" in e else ""
    return os.path.join(
        cache_dir,
        resol,
        "{:%Y%m%d%H}".format(run),
        dataset,
        e["step"].replace("-", "_"),
        "{}_{}{}{}.grib2".format(e["param"], e["levtype"], level, member),
    )


def download(job):
    url, e, path = job
    if os.path.exists(path) and os.path.getsize(path) == e["_length"]:
        return path
    data = http_get(url, e["_offset"], e["_length"])
    if data[:4] != b"GRIB" or data[-4:] != b"7777":
        raise OSError("{} bytes {} are not a GRIB message".format(url, e["_offset"]))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = os.path.join(os.path.dirname(path), "." + os.path.basename(path))
    with open(tmp, "wb") as f:
        f.write(data)
    Path(tmp).replace(path)
    return path


def default_area():
    info = gs.parse_command("g.region", flags="bg")
    return [float(info[k]) for k in ("ll_n", "ll_w", "ll_s", "ll_e")]


def normalize_area(area):
    north, west, south, east = area
    if north <= south:
        gs.fatal("Area north <{}> must be greater than south <{}>".format(north, south))
    if east - west >= 360:
        return [north, -180.0, south, 180.0]
    west = (west + 180.0) % 360.0 - 180.0
    east = (east + 180.0) % 360.0 - 180.0
    if east == -180.0:
        east = 180.0
    if west >= east:
        gs.fatal(
            "Areas crossing the antimeridian are not supported; import the east "
            "and west parts separately"
        )
    return [north, west, south, east]


def crop_window(geotransform, xsize, ysize, area):
    """Pixel window (xoff, yoff, cols, rows) covering area plus one cell
    on each side, clipped to the grid."""
    x0, dx, _, y0, _, dy = geotransform
    north, west, south, east = area
    col0 = max(0, int(math.floor((west - x0) / dx)) - 1)
    col1 = min(xsize, int(math.ceil((east - x0) / dx)) + 1)
    row0 = max(0, int(math.floor((north - y0) / dy)) - 1)
    row1 = min(ysize, int(math.ceil((south - y0) / dy)) + 1)
    if col1 <= col0 or row1 <= row0:
        gs.fatal("Area {} does not overlap the forecast grid".format(area))
    return col0, row0, col1 - col0, row1 - row0


def read_field(path, area):
    """Reads one GRIB message cropped to area, in native ECMWF units.
    Returns (array, geotransform, unit, comment)."""
    import numpy as np
    from osgeo import gdal

    gdal.UseExceptions()
    gdal.SetConfigOption("GRIB_NORMALIZE_UNITS", "NO")
    src = gdal.Open(path)
    band = src.GetRasterBand(1)
    gt = src.GetGeoTransform()
    xoff, yoff, cols, rows = crop_window(gt, src.RasterXSize, src.RasterYSize, area)
    arr = band.ReadAsArray(xoff, yoff, cols, rows).astype(np.float64)
    nodata = band.GetNoDataValue()
    if nodata is not None:
        arr[arr == nodata] = np.nan
    crop_gt = (gt[0] + xoff * gt[1], gt[1], 0.0, gt[3] + yoff * gt[5], 0.0, gt[5])
    meta = band.GetMetadata()
    unit = meta.get("GRIB_UNIT", "").strip("[]")
    comment = meta.get("GRIB_COMMENT", "")
    src = None
    return arr, crop_gt, unit, comment


def import_series(param, label, series, area, tmp_dir, tmp_name, resample, overwrite):
    """Writes the fields of one series into a multiband GeoTIFF, imports
    it with r.import and renames the bands to their final names.
    `series` is a list of (final_name, path, previous_path). Returns the
    unit and description of the field."""
    import numpy as np
    from osgeo import gdal, osr

    nodata = -9999.0
    tif = os.path.join(tmp_dir, tmp_name + ".tif")
    dst = None
    unit = comment = ""
    # The raw (still accumulated) field of the last step, reused as the
    # subtrahend of the next one.
    last_path, last_raw = None, None
    for i, (_, path, prev_path) in enumerate(series):
        raw, gt, unit, comment = read_field(path, area)
        arr = raw
        if prev_path is not None:
            prev = (
                last_raw if prev_path == last_path else read_field(prev_path, area)[0]
            )
            arr = raw - prev
            if param in NON_NEGATIVE:
                arr = np.maximum(arr, 0.0)
        last_path, last_raw = path, raw
        if dst is None:
            rows, cols = arr.shape
            dst = gdal.GetDriverByName("GTiff").Create(
                tif,
                cols,
                rows,
                len(series),
                gdal.GDT_Float32,
                options=["BIGTIFF=IF_SAFER", "INTERLEAVE=BAND"],
            )
            dst.SetGeoTransform(gt)
            srs = osr.SpatialReference()
            srs.ImportFromEPSG(4326)
            dst.SetProjection(srs.ExportToWkt())
        band = dst.GetRasterBand(i + 1)
        # SetNoDataValue() before WriteArray(), or GDAL may refill blocks.
        band.SetNoDataValue(nodata)
        band.WriteArray(np.where(np.isnan(arr), nodata, arr).astype(np.float32))
    dst.FlushCache()
    dst = None

    # r.import adds a .N band suffix only to multiband files.
    if len(series) == 1:
        imported = [tmp_name]
    else:
        imported = ["{}.{}".format(tmp_name, i + 1) for i in range(len(series))]
    TMP_RASTERS.extend(imported)
    gs.run_command(
        "r.import",
        input=tif,
        output=tmp_name,
        resample=resample,
        extent="input",
        quiet=True,
        overwrite=True,
    )
    os.remove(tif)
    for tmp, (name, _, _) in zip(imported, series):
        if not gs.find_file(tmp, element="cell")["name"]:
            gs.fatal("r.import did not create <{}>".format(tmp))
        gs.run_command("g.rename", raster=(tmp, name), quiet=True, overwrite=overwrite)
        TMP_RASTERS.remove(tmp)
        if label:
            # Set on the map: t.register drops a label given as the third
            # column of a point-in-time line (GRASS 8.6.0dev).
            gs.run_command("r.support", map=name, semantic_label=label)
        gs.raster_history(name)
    return unit, comment


def list_dataset(root, name, ds, resol, run, nprocs):
    steps = schedule_steps(ds["schedule"], run.hour)
    levtypes = ("sfc",) if resol == "0p25" else ("sfc", "pl")
    entries, missing = read_indexes(root, ds, resol, run, steps[:1], levtypes, nprocs)
    params = {}
    members = set()
    for _, e in entries:
        info = params.setdefault(
            e["param"], dict(levtype=e["levtype"], levels=set(), size=0)
        )
        if "levelist" in e:
            info["levels"].add(e["levelist"])
        if "number" in e:
            members.add(int(e["number"]))
        info["size"] += e["_length"]
    print("dataset={}".format(name))
    print("resolution={}".format(RESOLUTION_DEG[resol]))
    print("run={:%Y-%m-%d %H}:00 UTC".format(run))
    print("steps={}".format(",".join(str(s) for s in steps)))
    if members:
        print("members={}-{}".format(min(members), max(members)))
    for param in sorted(params):
        info = params[param]
        levels = sorted(info["levels"], key=lambda v: float(v))
        print(
            "parameter={}|levtype={}|levels={}|MB_per_step={:.1f}".format(
                param, info["levtype"], ",".join(levels), info["size"] / 1e6
            )
        )
    print("")


def process_dataset(
    root,
    name,
    ds,
    resol,
    run,
    options,
    flags,
    area,
    cache_dir,
    nprocs,
    params,
    levels,
    members,
):
    import grass.temporal as tgis

    all_steps = schedule_steps(ds["schedule"], run.hour)
    bundle = flags["b"]
    if bundle:
        step_ranges = [(0, BUNDLE_HOURS)]
    else:
        step_ranges = parse_ranges(options["steps"].split(","), "step")
    if ds["type"] == "ep":
        # Probabilities sit in the 240 h and 360 h files; filter on the
        # end of each interval.
        file_steps = all_steps
    else:
        file_steps = [s for s in all_steps if in_ranges(s, step_ranges)]
    if not file_steps:
        gs.fatal(
            "No published step of <{}> matches steps={}".format(name, options["steps"])
        )

    gs.message(
        "Reading the index of {} {} degree run {:%Y-%m-%d %H} UTC ({} steps)...".format(
            name, RESOLUTION_DEG[resol], run, len(file_steps)
        )
    )
    entries, missing = read_indexes(root, ds, resol, run, file_steps, ("sfc",), nprocs)
    selected = select_entries(entries, params, levels, members, step_ranges)
    absent = params - {e["param"] for _, e in selected}
    if absent and resol == "0p1":
        # At 0.1 degree, upper-air fields are in separate -pl files.
        pl_entries, _ = read_indexes(root, ds, resol, run, file_steps, ("pl",), nprocs)
        selected += select_entries(pl_entries, absent, levels, members, step_ranges)
        entries += pl_entries
        absent = params - {e["param"] for _, e in selected}
    if missing:
        if len(missing) == len(file_steps):
            gs.fatal(
                "None of the requested steps of <{}> is on the server".format(name)
            )
        gs.warning(
            "Steps {} of <{}> are missing on the server and skipped".format(
                ",".join(str(s) for s in missing), name
            )
        )
    if absent:
        # With several datasets queued, a parameter may belong to another
        # one (swh to ifs_wave, tp to ifs_oper); main() fails on
        # parameters that no dataset offers.
        available = sorted({e["param"] for _, e in entries})
        report = gs.warning if selected else gs.fatal
        report(
            "Parameters <{}> not found in <{}> at {} degree; available: {}".format(
                ",".join(sorted(absent)),
                name,
                RESOLUTION_DEG[resol],
                ",".join(available),
            )
        )

    # Group into series, one STRDS per (parameter, level), members as
    # semantic labels.
    groups = {}
    for grib, e in selected:
        groups.setdefault(series_key(e), []).append((grib, e))
    mapset = gs.gisenv()["MAPSET"]
    overwrite = gs.overwrite()
    strds_of = {}
    for param, level, _ in groups:
        strds = strds_name(options["output_prefix"], name, param, level)
        if not gs.legal_name(strds):
            gs.fatal("Output name <{}> is not a legal GRASS name".format(strds))
        strds_of[(param, level)] = strds
    for strds in set(strds_of.values()):
        sp = tgis.dataset_factory("strds", "{}@{}".format(strds, mapset))
        if sp.is_in_db() and not overwrite:
            gs.fatal(
                "Space-time raster dataset <{}> already exists, use --overwrite".format(
                    strds
                )
            )

    jobs = [(g, e, cache_path(cache_dir, name, resol, run, e)) for g, e in selected]
    total = sum(e["_length"] for _, e in selected)
    gs.message(
        "Downloading {} fields ({:.1f} MB) of {}...".format(
            len(jobs), total / 1e6, name
        )
    )
    done = 0
    with ThreadPoolExecutor(max_workers=nprocs) as pool:
        futures = [pool.submit(download, job) for job in jobs]
        for future in as_completed(futures):
            future.result()
            done += 1
            gs.percent(done, len(jobs), 1)

    # With --overwrite, the old datasets and their maps go before the
    # import, which reuses the map names.
    for strds in set(strds_of.values()):
        sp = tgis.dataset_factory("strds", "{}@{}".format(strds, mapset))
        if sp.is_in_db():
            gs.run_command("t.remove", flags="df", inputs=strds, quiet=True)

    path_of = {id(e): p for _, e, p in jobs}
    run_naive = run.replace(tzinfo=None)
    gs.message("Importing {} series of {}...".format(len(groups), name))
    created = set()
    register = {}
    for n, (key, items) in enumerate(sorted(groups.items(), key=lambda kv: str(kv[0]))):
        param, level, number = key
        strds = strds_of[(param, level)]
        by_step = {step_item(e): e for _, e in items}
        ends = sorted(by_step, key=lambda v: v if isinstance(v, tuple) else (v, v))
        spans = time_spans(param, ends, all_steps, flags["d"])
        label = "m{:02d}".format(int(number)) if number is not None else None
        series = []
        rows = []
        for item, start, end, prev in spans:
            e = by_step[item]
            suffix = (
                "{:03d}".format(start)
                if end is None
                else "{:03d}_{:03d}".format(start, end)
            )
            map_name = "{}_{:%Y%m%d%H}_{}".format(strds, run, suffix)
            if label:
                map_name += "_" + label
            prev_path = path_of[id(by_step[prev])] if prev is not None else None
            series.append((map_name, path_of[id(e)], prev_path))
            t0 = run_naive + dt.timedelta(hours=start)
            row = [map_name, "{:%Y-%m-%d %H:%M:%S}".format(t0)]
            if end is not None:
                row.append(
                    "{:%Y-%m-%d %H:%M:%S}".format(run_naive + dt.timedelta(hours=end))
                )
            rows.append("|".join(row))
        if not series:
            continue
        unit, comment = import_series(
            param,
            label,
            series,
            area,
            TMP_DIR or cache_dir,
            "tmp_t_in_ecmwf_{}_{}".format(os.getpid(), n),
            options["resample"],
            overwrite,
        )
        if strds not in created:
            mode = (
                "de-accumulated per step" if flags["d"] and param in ACCUMULATED else ""
            )
            gs.run_command(
                "t.create",
                output=strds,
                type="strds",
                temporaltype="absolute",
                semantictype=semantic_type(param),
                title="ECMWF {} {}/{} {} {} degree, run {:%Y-%m-%d %H} UTC".format(
                    ds["model"].upper(),
                    ds["stream"],
                    ds["type"],
                    param,
                    RESOLUTION_DEG[resol],
                    run,
                ),
                description="{} [{}]{}{}".format(
                    *parameter_info(param, ds["model"], comment, unit),
                    ", level " + level if level else "",
                    ", " + mode if mode else "",
                ),
                quiet=True,
            )
            created.add(strds)
        register.setdefault(strds, []).extend(rows)
        gs.percent(n + 1, len(groups), 1)

    for strds, rows in register.items():
        maps_file = os.path.join(TMP_DIR or cache_dir, "{}_register.txt".format(strds))
        with open(maps_file, "w") as f:
            f.write("\n".join(rows) + "\n")
        gs.run_command("t.register", input=strds, file=maps_file, quiet=True)
        os.remove(maps_file)
        gs.message(
            "Created space-time raster dataset <{}> with {} maps".format(
                strds, len(rows)
            )
        )
    return {e["param"] for _, e in selected}


def main():
    global TMP_DIR
    options, flags = gs.parser()

    import grass.temporal as tgis

    tgis.init()

    root = SOURCES[options["source"]]
    names = options["datasets"].split(",")
    params = set(p.strip() for p in options["parameters"].split(",") if p.strip())
    levels = (
        set(v.strip() for v in options["levels"].split(","))
        if options["levels"]
        else None
    )
    members = (
        parse_ranges(options["members"].split(","), "member")
        if options["members"]
        else None
    )
    nprocs = max(1, int(options["nprocs"]))

    run = None
    if options["date"]:
        try:
            day = dt.datetime.strptime(options["date"], "%Y-%m-%d")
        except ValueError:
            gs.fatal("Invalid date <{}>, expected YYYY-MM-DD".format(options["date"]))
        run = day.replace(hour=int(options["time"]), tzinfo=dt.timezone.utc)

    if flags["l"]:
        for name in names:
            ds = DATASETS[name]
            run_ds, resol = choose_run_and_resolution(
                root, name, ds, options["resolution"], run
            )
            list_dataset(root, name, ds, resol, run_ds, nprocs)
        return 0

    if not params:
        gs.fatal("No parameter given")
    if options["area"]:
        area = [float(v) for v in options["area"].split(",")]
        if len(area) != 4:
            gs.fatal("Area must be given as north,west,south,east")
    else:
        area = default_area()
    area = normalize_area(area)

    cache_dir = options["cache_dir"]
    TMP_DIR = gs.tempdir()
    if not cache_dir:
        cache_dir = TMP_DIR
    os.makedirs(cache_dir, exist_ok=True)

    # The datasets are queued: each one is downloaded and imported before
    # the next one starts.
    found = set()
    for i, name in enumerate(names):
        ds = DATASETS[name]
        run_ds, resol = choose_run_and_resolution(
            root, name, ds, options["resolution"], run
        )
        gs.message(
            "Dataset {}/{}: <{}>, {} degree, run {:%Y-%m-%d %H} UTC".format(
                i + 1, len(names), name, RESOLUTION_DEG[resol], run_ds
            )
        )
        found |= process_dataset(
            root,
            name,
            ds,
            resol,
            run_ds,
            options,
            flags,
            area,
            cache_dir,
            nprocs,
            params,
            levels,
            members,
        )
    if params - found:
        gs.fatal(
            "Parameters <{}> were not found in any of the datasets".format(
                ",".join(sorted(params - found))
            )
        )
    return 0


if __name__ == "__main__":
    atexit.register(cleanup)
    sys.exit(main())
