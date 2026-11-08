import json
import math
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class TomTomParkingError(Exception):
    pass


SEARCH_URL = "https://api.tomtom.com/search/2/poiSearch/parking.json"
AVAILABILITY_URL = "https://api.tomtom.com/search/2/parkingAvailability.json"
REQUEST_TIMEOUT_SECONDS = 3
MAX_RESULTS = 5


def _get_json(url, params):
    request = Request(
        url + "?" + urlencode(params),
        headers={"Accept": "application/json", "User-Agent": "ParkShare/1.0"},
    )
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        raise TomTomParkingError(
            "TomTom returned HTTP " + str(error.code) + "."
        ) from None
    except (URLError, TimeoutError, OSError):
        raise TomTomParkingError("TomTom could not be reached.") from None
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise TomTomParkingError("TomTom returned an invalid response.") from None


def _valid_coordinate(value, minimum, maximum):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and minimum <= value <= maximum
    )


def search_nearby(latitude, longitude, radius_km, api_key):
    """Return TomTom facilities with provider-reported availability when present."""
    if not api_key:
        return []
    if (not _valid_coordinate(latitude, -90, 90)
            or not _valid_coordinate(longitude, -180, 180)
            or not _valid_coordinate(radius_km, 1, 50)):
        raise ValueError("Invalid coordinates or radius for parking search.")

    response = _get_json(SEARCH_URL, {
        "key": api_key,
        "lat": format(latitude, ".6f"),
        "lon": format(longitude, ".6f"),
        "radius": str(int(radius_km * 1000)),
        "countrySet": "IN",
        "limit": str(MAX_RESULTS),
        "language": "en-GB",
    })
    if not isinstance(response, dict) or not isinstance(response.get("results", []), list):
        raise TomTomParkingError("TomTom returned an invalid parking search response.")

    facilities = []
    for item in response.get("results", [])[:MAX_RESULTS]:
        if not isinstance(item, dict):
            continue
        poi = item.get("poi")
        position = item.get("position")
        if not isinstance(poi, dict) or not isinstance(position, dict):
            continue
        lat = position.get("lat")
        lon = position.get("lon")
        if (not _valid_coordinate(lat, -90, 90)
                or not _valid_coordinate(lon, -180, 180)):
            continue
        name = poi.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        address = item.get("address")
        address = address.get("freeformAddress", "") if isinstance(address, dict) else ""
        facility = {
            "id": str(item.get("id", "")),
            "name": name[:200],
            "address": address[:300] if isinstance(address, str) else "",
            "latitude": lat,
            "longitude": lon,
            "distance_km": (
                round(item["dist"] / 1000, 1)
                if isinstance(item.get("dist"), (int, float))
                and math.isfinite(item["dist"]) and item["dist"] >= 0 else None
            ),
            "availability": None,
            "empty_spots": None,
            "updated_at": "",
        }
        data_sources = item.get("dataSources")
        parking_source = (
            data_sources.get("parkingAvailability")
            if isinstance(data_sources, dict) else None
        )
        availability_id = (
            parking_source.get("id") if isinstance(parking_source, dict) else None
        )
        if isinstance(availability_id, str) and availability_id:
            status_response = _get_json(AVAILABILITY_URL, {
                "key": api_key,
                "parkingAvailability": availability_id,
            })
            statuses = status_response.get("statuses", []) if isinstance(status_response, dict) else []
            if statuses and isinstance(statuses[0], dict):
                current = statuses[0].get("current")
                if isinstance(current, dict):
                    available = current.get("available")
                    if isinstance(available, bool):
                        facility["availability"] = available
                    empty_spots = current.get("emptySpots")
                    if isinstance(empty_spots, int) and not isinstance(empty_spots, bool) and empty_spots >= 0:
                        facility["empty_spots"] = empty_spots
                    updated_at = current.get("updatedAt")
                    if isinstance(updated_at, str):
                        facility["updated_at"] = updated_at[:40]
        facilities.append(facility)
    return facilities
