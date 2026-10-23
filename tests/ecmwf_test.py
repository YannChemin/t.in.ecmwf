"""Tests of the URL layout, step schedules and time stamps."""

import datetime as dt

RUN = dt.datetime(2026, 10, 22, 0, tzinfo=dt.timezone.utc)


def test_file_url_0p25(tin):
    url = tin.file_url(
        "https://data.ecmwf.int/forecasts",
        RUN,
        tin.DATASETS["ifs_oper"],
        "0p25",
        24,
        "",
        "index",
    )
    assert url == (
        "https://data.ecmwf.int/forecasts/20261022/00z/ifs/0p25/oper/"
        "20261022000000-24h-oper-fc.index"
    )


def test_file_url_0p1_split(tin):
    assert tin.file_parts("0p1", ("sfc", "pl")) == ["-sfc", "-pl"]
    assert tin.file_parts("0p25", ("sfc", "pl")) == [""]
    url = tin.file_url(
        "root", RUN, tin.DATASETS["ifs_enfo_ep"], "0p1", 240, "-sfc", "grib2"
    )
    assert url == "root/20261022/00z/ifs/0p1/enfo/20261022000000-240h-enfo-ep-sfc.grib2"


def test_file_url_aifs(tin):
    url = tin.file_url("root", RUN, tin.DATASETS["aifs_ens_cf"], "0p25", 6, "", "index")
    assert url == "root/20261022/00z/aifs-ens/0p25/enfo/20261022000000-6h-enfo-cf.index"


def test_aifs_has_no_0p1(tin):
    assert not tin.supports(tin.DATASETS["aifs_single"], "0p1")
    assert tin.supports(tin.DATASETS["ifs_waef"], "0p1")


def test_schedules(tin):
    long_run = tin.schedule_steps("ifs", 0)
    assert long_run[:3] == [0, 3, 6]
    assert 141 in long_run
    assert 147 not in long_run
    assert 150 in long_run
    assert long_run[-1] == 360
    assert len(long_run) == 85
    assert tin.schedule_steps("ifs", 6)[-1] == 144
    assert tin.schedule_steps("aifs", 18) == list(range(0, 361, 6))
    assert tin.schedule_steps("ep", 12) == [240, 360]


def test_ranges(tin):
    ranges = tin.parse_ranges(["0-72", " 96"], "step")
    assert ranges == [(0, 72), (96, 96)]
    assert tin.in_ranges(72, ranges)
    assert tin.in_ranges(96, ranges)
    assert not tin.in_ranges(75, ranges)
    assert tin.in_ranges(5, None)


def test_step_item(tin):
    assert tin.step_item({"step": "24"}) == 24
    assert tin.step_item({"step": "0-24"}) == (0, 24)


def test_select_entries(tin):
    entries = [
        ("g", {"param": "t", "levelist": "850", "step": "24"}),
        ("g", {"param": "t", "levelist": "500", "step": "24"}),
        ("g", {"param": "2t", "number": "3", "step": "24"}),
        ("g", {"param": "2t", "number": "30", "step": "24"}),
        ("g", {"param": "2t", "number": "3", "step": "48"}),
    ]
    sel = tin.select_entries(entries, {"t", "2t"}, {"850"}, [(1, 10)], [(0, 24)])
    assert [e for _, e in sel] == [entries[0][1], entries[2][1]]


def test_spans_instantaneous(tin):
    assert tin.time_spans("2t", [0, 3], [0, 3], False) == [
        (0, 0, None, None),
        (3, 3, None, None),
    ]


def test_spans_accumulated(tin):
    steps = [0, 3, 6, 12]
    assert tin.time_spans("tp", steps, steps, False) == [
        (3, 0, 3, None),
        (6, 0, 6, None),
        (12, 0, 12, None),
    ]
    # De-accumulation subtracts the previous downloaded step, step 0
    # included even though it gives no map.
    assert tin.time_spans("tp", steps, steps, True) == [
        (3, 0, 3, 0),
        (6, 3, 6, 3),
        (12, 6, 12, 6),
    ]
    # Without step 0 the first step is the amount since the run start.
    assert tin.time_spans("tp", [24, 48], steps, True) == [
        (24, 0, 24, None),
        (48, 24, 48, 24),
    ]


def test_spans_windows(tin):
    all_steps = [0, 3, 6, 144, 150]
    assert tin.time_spans("mx2t3", [0, 3], all_steps, False) == [(3, 0, 3, None)]
    assert tin.time_spans("mn2t6", [150], all_steps, False) == [(150, 144, 150, None)]
    assert tin.time_spans("10fg3", [3], all_steps, False) == [(3, 0, 3, None)]
    assert tin.time_spans("10fg", [0, 150], all_steps, False) == [(150, 144, 150, None)]


def test_spans_probability(tin):
    assert tin.time_spans("tpg10", [(0, 24), (24, 48)], [240, 360], False) == [
        ((0, 24), 0, 24, None),
        ((24, 48), 24, 48, None),
    ]


def test_semantic_type(tin):
    assert tin.semantic_type("tp") == "sum"
    assert tin.semantic_type("mx2t3") == "max"
    assert tin.semantic_type("mn2t6") == "min"
    assert tin.semantic_type("10fg") == "max"
    assert tin.semantic_type("10fg3") == "max"
    assert tin.semantic_type("2t") == "mean"


def test_crop_window(tin):
    gt = (-180.05, 0.1, 0.0, 90.05, 0.0, -0.1)
    xoff, yoff, cols, rows = tin.crop_window(gt, 3600, 1801, [31, 80, 26, 89])
    assert (xoff, yoff) == (2599, 589)
    west = gt[0] + xoff * gt[1]
    north = gt[3] + yoff * gt[5]
    assert west < 80
    assert north > 31
    assert west + cols * gt[1] > 89
    assert north + rows * gt[5] < 26
    # Clipped at the grid edges.
    assert tin.crop_window(gt, 3600, 1801, [90, -180, -90, 180]) == (0, 0, 3600, 1801)


def test_normalize_area(tin):
    assert tin.normalize_area([10, 190, 0, 200]) == [10, -170, 0, -160]
    assert tin.normalize_area([90, 0, -90, 360]) == [90, -180.0, -90, 180.0]
    assert tin.normalize_area([10, 170, 0, 180]) == [10, 170, 0, 180.0]


def test_cache_path(tin):
    e = {
        "param": "t",
        "levtype": "pl",
        "levelist": "850",
        "number": "7",
        "step": "0-24",
    }
    path = tin.cache_path("/c", "ifs_enfo", "0p1", RUN, e)
    assert path == "/c/0p1/2026102200/ifs_enfo/0_24/t_pl_850_m07.grib2"


def test_parameter_units(tin):
    assert tin.parameter_info("tp", "ifs", "", "-") == ("Total precipitation", "m")
    assert tin.parameter_info("tp", "aifs-single", "", "") == (
        "Total precipitation",
        "kg m-2",
    )
    assert tin.parameter_info("tpg10", "ifs", "Prob [%]", "%") == ("Prob [%]", "%")
