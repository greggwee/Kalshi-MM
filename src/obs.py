"""Station observations for the temperature markets.

Source: the Iowa Environmental Mesonet ASOS archive (public, keyless), which
keeps the hourly METARs, the specials and the 5-minute readings.

What the reports carry:

* 5-minute reading: temperature in whole degrees C.
* hourly METAR (about :51-:54): temperature to 0.1 C in the T group.
* every six hours (00, 06, 12, 18 UTC): the 6-hour minimum (group 2sTTT) and
  maximum (group 1sTTT) to 0.1 C, taken from the 1-minute data. These are the
  only public numbers that see between the readings.

The climate day runs midnight to midnight local STANDARD time all year.
"""
import csv
import io
import re
import sqlite3
from datetime import datetime, timedelta, timezone

IEM_URL = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"

# series -> (IEM station id, hours behind UTC in standard time)
STATIONS = {
    "KXLOWTATL": ("ATL", 5), "KXLOWTAUS": ("AUS", 6), "KXLOWTBOS": ("BOS", 5),
    "KXLOWTCHI": ("MDW", 6), "KXLOWTDAL": ("DFW", 6), "KXLOWTDC": ("DCA", 5),
    "KXLOWTDEN": ("DEN", 7), "KXLOWTHOU": ("HOU", 6), "KXLOWTLAX": ("LAX", 8),
    "KXLOWTMIA": ("MIA", 5), "KXLOWTMIN": ("MSP", 6), "KXLOWTNYC": ("NYC", 5),
    "KXLOWTPHIL": ("PHL", 5), "KXLOWTPHX": ("PHX", 7), "KXLOWTSEA": ("SEA", 8),
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS obs (
    station TEXT NOT NULL,
    ts INTEGER NOT NULL,          -- observation time, epoch seconds UTC
    temp_c REAL,                  -- air temperature
    precise INTEGER NOT NULL,     -- 1 = 0.1 C from an hourly/special METAR, 0 = whole C 5-minute reading
    min6_c REAL,                  -- 6-hour minimum group, when present
    max6_c REAL,                  -- 6-hour maximum group, when present
    PRIMARY KEY (station, ts, precise)
);
"""

_T_GROUP = re.compile(r"\bT([01])(\d{3})([01])(\d{3})\b")
_BODY_TEMP = re.compile(r"\s(M?\d{2})/(M?\d{2})?\s")
_MIN6 = re.compile(r"\s2([01])(\d{3})(?=\s|$)")
_MAX6 = re.compile(r"\s1([01])(\d{3})(?=\s|$)")


def c_to_f(c):
    return c * 9.0 / 5.0 + 32.0


def _signed(sign, digits):
    return (-1.0 if sign == "1" else 1.0) * int(digits) / 10.0


def parse_metar(metar):
    """Return dict(temp_c, precise, min6_c, max6_c) from one raw report.

    A 5-minute reading (marked MADISHF) repeats its whole-degree temperature in
    a T group, so it is never treated as precise.
    """
    out = {"temp_c": None, "precise": 0, "min6_c": None, "max6_c": None}
    hf = "MADISHF" in metar
    body, _, remarks = metar.partition(" RMK")
    m = _T_GROUP.search(remarks)
    if m:
        out["temp_c"] = _signed(m.group(1), m.group(2))
        out["precise"] = 0 if hf else 1
    else:
        b = _BODY_TEMP.search(body + " ")
        if b:
            t = b.group(1)
            out["temp_c"] = -float(t[1:]) if t.startswith("M") else float(t)
    if not hf:
        m = _MIN6.search(remarks)
        if m:
            out["min6_c"] = _signed(m.group(1), m.group(2))
        m = _MAX6.search(remarks)
        if m:
            out["max6_c"] = _signed(m.group(1), m.group(2))
    return out


def parse_iem_csv(text):
    """Rows (station, ts, temp_c, precise, min6_c, max6_c) from an IEM download."""
    rows = []
    for rec in csv.DictReader(io.StringIO(text)):
        metar = rec.get("metar") or ""
        try:
            ts = int(datetime.strptime(rec["valid"], "%Y-%m-%d %H:%M")
                     .replace(tzinfo=timezone.utc).timestamp())
        except (KeyError, ValueError):
            continue
        p = parse_metar(metar)
        if p["temp_c"] is None and p["min6_c"] is None:
            continue
        rows.append((rec["station"], ts, p["temp_c"], p["precise"], p["min6_c"], p["max6_c"]))
    return rows


def connect(path):
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    return conn


def store(conn, rows):
    conn.executemany("INSERT OR REPLACE INTO obs VALUES (?,?,?,?,?,?)", rows)
    conn.commit()


def fetch_station(session, station, start, end, timeout=300):
    """Download every report for one station between two dates (UTC, end exclusive)."""
    params = [("station", station), ("data", "metar"), ("tz", "Etc/UTC"),
              ("format", "onlycomma"), ("latlon", "no"), ("missing", "M"), ("trace", "T"),
              ("report_type", "1"), ("report_type", "3"), ("report_type", "4"),
              ("year1", start.year), ("month1", start.month), ("day1", start.day),
              ("year2", end.year), ("month2", end.month), ("day2", end.day)]
    r = session.get(IEM_URL, params=params, timeout=timeout)
    r.raise_for_status()
    return parse_iem_csv(r.text)


def climate_day_bounds(date, utc_offset_h):
    """Epoch seconds [start, end) of the local-standard-time day `date` (a datetime.date)."""
    start = datetime(date.year, date.month, date.day, tzinfo=timezone.utc) \
        + timedelta(hours=utc_offset_h)
    return int(start.timestamp()), int(start.timestamp()) + 86400
