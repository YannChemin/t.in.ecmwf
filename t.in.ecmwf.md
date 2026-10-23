## DESCRIPTION

*t.in.ecmwf* downloads real-time forecasts from the
[ECMWF open data](https://www.ecmwf.int/en/forecasts/datasets/open-data)
service and imports each requested parameter as a space-time raster
dataset (STRDS). It covers the IFS forecasts at 0.25 degree and, since
20 October 2026, at 0.1 degree, and the AIFS (machine learning)
forecasts at 0.25 degree.

Only the requested fields are transferred. Each forecast file comes
with a JSON index giving the byte offset and length of every GRIB
message, and the module fetches just those messages with HTTP range
requests. They are cropped to the area (by default the current region,
plus one grid cell on each side), imported with *r.import*, and
registered with their valid time.

### Datasets

The **datasets** option takes one or more of:

| Dataset | Model | Stream/type | Content | 0.1 degree |
|---|---|---|---|---|
| ifs_oper | IFS | oper/fc | Control forecast (formerly HRES) | yes |
| ifs_enfo | IFS | enfo/ef | Ensemble, 50 perturbed members | yes |
| ifs_enfo_ep | IFS | enfo/ep | Ensemble probabilities | yes |
| ifs_wave | IFS | wave/fc | Wave control forecast | yes |
| ifs_waef | IFS | waef/ef | Wave ensemble, 50 members | yes |
| ifs_waef_ep | IFS | waef/ep | Wave ensemble probabilities | yes |
| aifs_single | AIFS Single | oper/fc | ML deterministic forecast | no |
| aifs_ens | AIFS ENS | enfo/pf | ML ensemble perturbed members | no |
| aifs_ens_cf | AIFS ENS | enfo/cf | ML ensemble control member | no |

With several datasets, they are queued: each dataset is downloaded and
imported completely before the next one starts. A parameter missing
from one dataset (for example *swh* in *ifs_oper*, or *tp* in
*ifs_wave*) is skipped there with a warning. The module fails when a
dataset offers none of the parameters, or when a parameter is in none
of the datasets.

### Resolution

**resolution=0.25** (the default) uses the 0.25 degree grid, available
for every dataset. **resolution=0.1** uses the 0.1 degree grid (IFS
only). The module fails for an AIFS dataset at that resolution.
**resolution=best** takes 0.1 degree when the run is already published
at 0.1 degree, and 0.25 degree otherwise. ECMWF publishes the 0.1
degree files 2 hours after the 0.25 degree ones, so right after a new
run the 0.25 degree grid of the newest run is preferred to the 0.1
degree grid of the previous run. AIFS datasets always use 0.25 degree
under *best*.

At 0.1 degree, ECMWF splits each step into a surface file (`-sfc`)
and a pressure-level file (`-pl`). The module reads the surface index
first and the pressure-level index only for parameters not found
there.

### Run and steps

By default the latest complete run is used. A run counts as complete
when its last step is on the server. **date** and **time** select a
given run, and both must be given. The ECMWF server keeps about four
days of runs. The mirrors (**source=aws** or **source=google**) keep
more.

**steps** selects forecast steps in hours, as numbers and ranges
(`0-72,96`). Steps that a run does not publish are ignored. The IFS
publishes 0-144 h every 3 hours and 150-360 h every 6 hours for the 00
and 12 UTC runs, and 0-144 h every 3 hours for the 06 and 18 UTC runs.
AIFS publishes 0-360 h every 6 hours. Probabilities (*_ep* datasets)
come as intervals (such as 0-24 h) in two files (240 h and 360 h), and
**steps** selects the intervals by their end.

The **-b** flag (bundle) downloads every available step of the run, up
to 15 days (360 h), in place of **steps**. Combined with several
datasets, the bundles are downloaded one after the other.

### Parameters, levels and members

**parameters** takes ECMWF short names (2t, tp, 10u, 10v, msl, ro,
swh, ...). The **-l** flag prints, for each dataset, the chosen
resolution and run, the published steps, and every parameter with its
level type, levels and size per step, then exits without downloading.

For multi-level parameters (pressure levels, soil layers),
**levels** restricts the levels; by default all levels of the
parameter are kept. For ensembles, **members** restricts the members
(`1-10,25`); by default all members are kept. An ensemble at 0.1
degree is large (about 1.7 MB per member, parameter and step before
cropping), and the module reports the download size before starting.

### Output

One STRDS is created per parameter and level, named
`<output_prefix>_<dataset>_<parameter>[_<level>]`, for example
*ecmwf_ifs_oper_2t* or *ecmwf_ifs_oper_t_850*. Maps are named
`<strds>_<run YYYYMMDDHH>_<step>` for points in time and
`<strds>_<run>_<start>_<end>` for intervals. Ensemble members go into
the same STRDS and carry the semantic label `mNN`, so that
`ecmwf_ifs_enfo_2t.m07` selects member 7 in the temporal modules.

Values are in the native ECMWF units (K, m, Pa, J m-2, ...), and the
STRDS description gives the unit. The time stamps follow the
meaning of each field:

- instantaneous fields (2t, msl, swh, ...) get a point in time at the
  valid time;
- fields accumulated since the start of the forecast (tp, cp, sf, ro,
  ssrd, ssr, str, strd, ttr, ewss, nsss, ...) get the interval from
  the run time to the valid time. Step 0 is skipped, as it is zero;
- with **-d**, accumulated fields are de-accumulated into the amount
  of each interval between two consecutive downloaded steps. Packing
  noise makes small negative differences, which are clipped to zero
  for fields that cannot be negative (precipitation, snowfall, runoff,
  downward radiation);
- *mx2t3*, *mn2t3* (and the 6 hour versions) get their 3 or 6 hour
  window, and *10fg* the interval since the previous published step;
- probabilities get their interval.

The STRDS semantic type is *sum* for accumulated fields, *max* or *min*
for extremes, and *mean* otherwise. An existing STRDS is replaced, with
its maps, only with **--overwrite**.

### Cache

Downloaded GRIB messages are stored under **cache_dir**, one file per
message, by resolution, run, dataset and step. A later call with the
same **cache_dir** reuses them, for example to import another area or
to add de-accumulation. Without **cache_dir**, a temporary directory is
used and removed at the end.

## NOTES

The module needs the GDAL Python bindings with the GRIB driver, and no
other extra Python package. Downloads run in parallel within a dataset
(**nprocs**, default 4). Please keep this number small: the ECMWF
server is shared.

An area crossing the antimeridian is not supported. Import the east
and west parts separately.

ECMWF open data is distributed under the CC-BY-4.0 licence. Credit
ECMWF in products derived from it.

## EXAMPLES

List what the 0.1 degree control forecast offers:

```sh
t.in.ecmwf -l datasets=ifs_oper resolution=0.1
```

Download the 15-day forecast of temperature, precipitation and runoff
for Nepal and Bangladesh at the best resolution, with precipitation and
runoff per step, for the control forecast and then the AIFS:

```sh
g.region n=31 s=20 w=80 e=93 res=0.1
t.in.ecmwf -b -d datasets=ifs_oper,aifs_single resolution=best \
    parameters=2t,tp,ro cache_dir=$HOME/RSDATA/ecmwf_cache
t.rast.list ecmwf_ifs_oper_tp columns=name,start_time,end_time
```

Ensemble members 1 to 10 of the 2 m temperature for the next 3 days,
and the probability of more than 10 mm and 25 mm of rain in 24 hours:

```sh
t.in.ecmwf datasets=ifs_enfo,ifs_enfo_ep resolution=0.1 \
    parameters=2t,tpg10,tpg25 members=1-10 steps=0-72
t.rast.list ecmwf_ifs_enfo_2t.m03
```

Temperature at 850 and 500 hPa, at 0.1 degree:

```sh
t.in.ecmwf datasets=ifs_oper resolution=0.1 parameters=t levels=850,500
```

## SEE ALSO

*[r.import](https://grass.osgeo.org/grass-stable/manuals/r.import.html),
[t.create](https://grass.osgeo.org/grass-stable/manuals/t.create.html),
[t.in.era5](t.in.era5.md),
[t.rast.list](https://grass.osgeo.org/grass-stable/manuals/t.rast.list.html),
[t.register](https://grass.osgeo.org/grass-stable/manuals/t.register.html)*

[ECMWF open data: real-time forecasts from IFS and
AIFS](https://confluence.ecmwf.int/spaces/DAC/pages/272310539/ECMWF+open+data+real-time+forecasts+from+IFS+and+AIFS),
[What's new at
0.1°](https://confluence.ecmwf.int/spaces/DAC/pages/738041087/ECMWF+open+data+what+s+new+at+0.1%C2%B0)

## AUTHORS

Yann Chemin
