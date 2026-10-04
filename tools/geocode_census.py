"""OPTIONAL. Run on your own laptop (needs internet). Sends the 500 sample addresses to the free
U.S. Census batch geocoder (no key) and writes data/geocoded.csv; the pipeline then uses the
Census 'incorporated place' as the authoritative legal city. Standard library only.

  python3 tools/geocode_census.py
"""
import csv
import io
import os
import urllib.request
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDR = os.path.join(ROOT, "starter", "data", "sample_addresses.csv")
OUT = os.path.join(ROOT, "data", "geocoded.csv")
URL = "https://geocoding.geo.census.gov/geocoder/geographies/addressbatch"

rows = list(csv.DictReader(open(ADDR, newline="", encoding="utf-8")))
buf = io.StringIO()
w = csv.writer(buf)
for r in rows:
    w.writerow([r["address_id"], r["street_address"], r["postal_city"], r["state"], r["zip"]])
boundary = uuid.uuid4().hex
parts = []
for name, val in (("benchmark", "Public_AR_Current"), ("vintage", "Current_Current")):
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{val}\r\n')
parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="addressFile"; filename="a.csv"\r\nContent-Type: text/csv\r\n\r\n{buf.getvalue()}\r\n--{boundary}--\r\n')
req = urllib.request.Request(URL, data="".join(parts).encode(), headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
print("Sending 500 addresses to the Census geocoder (takes ~1-3 minutes)...")
raw = urllib.request.urlopen(req, timeout=600).read().decode("utf-8", errors="replace")
out = []
for rec in csv.reader(io.StringIO(raw)):
    if not rec:
        continue
    aid, match = rec[0], rec[2] if len(rec) > 2 else ""
    # geographies columns: ..., state FIPS, county FIPS, tract, block  (place name is not returned in batch);
    out.append({"address_id": aid, "match": match, "matched_address": rec[4] if len(rec) > 4 else "",
                "county": rec[9] if len(rec) > 9 else "", "place": ""})
with open(OUT, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["address_id", "match", "matched_address", "county", "place"])
    w.writeheader(); w.writerows(out)
m = sum(1 for x in out if x["match"] == "Match")
print(f"{m}/{len(out)} matched. Saved {OUT}. (City comes from the matched address; review unmatched rows.)")
