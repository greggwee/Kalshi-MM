"""At which minute of the hour do the settlement stations report? (public NWS API)"""
import collections
import time
import requests

STATIONS = {"KNYC": "NYC Central Park", "KMDW": "Chicago Midway", "KMIA": "Miami",
            "KLAX": "Los Angeles", "KAUS": "Austin", "KDEN": "Denver", "KPHL": "Philadelphia",
            "KSEA": "Seattle", "KEWR": "Newark", "KBOS": "Boston"}
s = requests.Session()
s.headers["User-Agent"] = "weather-research-script"
for st, name in STATIONS.items():
    try:
        r = s.get(f"https://api.weather.gov/stations/{st}/observations", params={"limit": 200}, timeout=30)
        r.raise_for_status()
        feats = r.json().get("features", [])
    except Exception as e:  # noqa: BLE001
        print(st, name, "error", e)
        continue
    minutes = collections.Counter()
    for f in feats:
        ts = f["properties"]["timestamp"]            # e.g. 2026-11-13T04:51:00+00:00
        minutes[int(ts[14:16])] += 1
    top = ", ".join(f":{m:02d} x{n}" for m, n in minutes.most_common(6))
    span = f"{feats[-1]['properties']['timestamp'][:16]} .. {feats[0]['properties']['timestamp'][:16]}" if feats else ""
    print(f"{st} {name:18s} n={len(feats):3d} {span}  minutes: {top}")
    time.sleep(0.5)
