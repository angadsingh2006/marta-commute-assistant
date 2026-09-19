"""Find the stop_id and route_number to put in the routes table.

Not part of either Lambda — a local helper, because MARTA's realtime feeds
identify stops only by number, and you need a name to find yours.

Run it with the project's virtualenv active (`source .venv/bin/activate`),
otherwise requests and the GTFS bindings won't be importable.

    python tools/find_stop.py "auburn ave"      search stops by name
    python tools/find_stop.py --stop 211751     which routes serve that stop now
"""

import csv
import io
import sys
import time
import zipfile
from pathlib import Path

import requests

GTFS_URL = "https://www.itsmarta.com/google_transit_feed/google_transit.zip"
REALTIME_URL = (
    "https://gtfs-rt.itsmarta.com/TMGTFSRealTimeWebService/tripupdate/tripupdates.pb"
)
# MARTA's web server rejects requests without a browser-like user agent.
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/120"}
CACHE = Path.home() / ".cache" / "marta-commute" / "google_transit.zip"
CACHE_MAX_AGE_SECONDS = 86400


def load_stops():
    """Return every MARTA stop, downloading the schedule feed at most once a day."""
    if not CACHE.exists() or time.time() - CACHE.stat().st_mtime > CACHE_MAX_AGE_SECONDS:
        print("downloading MARTA schedule data (~18 MB, once a day)...", file=sys.stderr)
        response = requests.get(GTFS_URL, headers=HEADERS, timeout=120)
        response.raise_for_status()
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_bytes(response.content)

    with zipfile.ZipFile(CACHE) as archive:
        with archive.open("stops.txt") as handle:
            text = io.TextIOWrapper(handle, encoding="utf-8-sig")
            return list(csv.DictReader(text))


def search(term):
    """Print every stop whose name contains the search term."""
    matches = [s for s in load_stops() if term.upper() in s["stop_name"].upper()]
    if not matches:
        print(f"no stop matching {term!r}")
        return
    print(f"{len(matches)} match(es):\n")
    for stop in matches[:40]:
        print(f"  stop_id {stop['stop_id']:>8}   {stop['stop_name']}")
    print("\nNext: python3 tools/find_stop.py --stop <stop_id>")


def routes_at(stop_id):
    """Print the route numbers with an upcoming arrival at one stop right now."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from google.transit import gtfs_realtime_pb2

    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(requests.get(REALTIME_URL, timeout=60).content)

    now = int(time.time())
    found = {}
    for entity in feed.entity:
        if not entity.HasField("trip_update"):
            continue
        for update in entity.trip_update.stop_time_update:
            if update.stop_id.strip() != str(stop_id):
                continue
            if update.HasField("arrival") and update.arrival.time >= now:
                route = entity.trip_update.trip.route_id
                wait = update.arrival.time - now
                found[route] = min(found.get(route, wait), wait)

    if not found:
        print(f"no upcoming arrivals at stop {stop_id} right now.")
        print("Buses may not be running at this hour — try again during service.")
        return
    print(f"routes serving stop {stop_id} right now:\n")
    for route, wait in sorted(found.items(), key=lambda kv: kv[1]):
        print(f"  route {route:>4}   next arrival in {round(wait / 60)} min")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
    elif sys.argv[1] == "--stop":
        routes_at(sys.argv[2])
    else:
        search(" ".join(sys.argv[1:]))
