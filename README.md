# t.in.ecmwf

GRASS GIS addon that downloads ECMWF open-data real-time forecasts and
imports them as space-time raster datasets (STRDS).

- IFS forecasts at 0.25 degree (default) or 0.1 degree (published by
  ECMWF since 20 October 2026), and AIFS forecasts at 0.25 degree.
- `resolution=best` takes 0.1 degree when the run is already published
  at 0.1 degree (2 hours after 0.25), and 0.25 degree otherwise.
- `-b` downloads every step of the run, up to 15 days (360 h).
- Several datasets (`ifs_oper`, `ifs_enfo`, `ifs_enfo_ep`, `ifs_wave`,
  `ifs_waef`, `ifs_waef_ep`, `aifs_single`, `aifs_ens`, `aifs_ens_cf`)
  can be given; they are downloaded and imported one after the other.
- Only the requested GRIB messages are fetched, with HTTP range
  requests driven by the JSON index of each file.

See [t.in.ecmwf.md](t.in.ecmwf.md) for the manual.

## Install

```sh
g.extension t.in.ecmwf url=/path/to/t.in.ecmwf
```

or build it inside a grass-addons tree:

```sh
make -C grass-addons/src/temporal/t.in.ecmwf MODULE_TOPDIR=/path/to/grass
```

It needs only the GDAL Python bindings (with the GRIB driver) and
numpy.

## Example

```sh
g.region n=31 s=20 w=80 e=93 res=0.1
t.in.ecmwf -b -d datasets=ifs_oper,aifs_single resolution=best \
    parameters=2t,tp,ro cache_dir=$HOME/RSDATA/ecmwf_cache
```

## Tests

```sh
python3 -m pytest tests
```

## Licence

Unlicense (public domain), see [LICENSE](LICENSE). ECMWF open data is
distributed under CC-BY-4.0.
