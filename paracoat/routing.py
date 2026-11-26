"""Travel matrix generation with labelled road routing and fallback behavior."""
import math
import requests


def haversine_km(a, b):
    lat1, lon1 = a
    lat2, lon2 = b
    rad = math.pi / 180
    dlat, dlon = (lat2-lat1)*rad, (lon2-lon1)*rad
    h = math.sin(dlat/2)**2 + math.cos(lat1*rad)*math.cos(lat2*rad)*math.sin(dlon/2)**2
    return 6371.0 * 2 * math.asin(math.sqrt(h))


def build_matrix(points, mode="Road estimate (OSRM demo server)", base_url="https://router.project-osrm.org"):
    n = len(points)
    distances = [[0.0]*n for _ in range(n)]
    durations = [[0.0]*n for _ in range(n)]
    if mode.startswith("Road"):
        coords = ";".join(f"{lon},{lat}" for lat, lon in points)
        try:
            response = requests.get(f"{base_url.rstrip('/')}/table/v1/driving/{coords}", params={"annotations":"distance,duration"}, timeout=20)
            response.raise_for_status()
            data = response.json()
            if data.get("code") != "Ok":
                raise ValueError(data.get("message", "Routing service returned an error"))
            if len(data.get("distances",[]))!=n or len(data.get("durations",[]))!=n:
                raise ValueError("Routing service returned an incomplete travel matrix")
            if any(value is None for row in data["distances"] for value in row) or any(value is None for row in data["durations"] for value in row):
                raise ValueError("Some locations have no road connection")
            return [[x/1000 for x in row] for row in data["distances"]], [[x/60 for x in row] for row in data["durations"]], "Road estimates from routing service"
        except Exception as exc:
            warning = f"Road routing unavailable ({exc}); straight-line estimate with 1.3 road factor used."
            factor, speed = 1.3, 28.0
    else:
        warning, factor, speed = "Straight-line estimates only; road distance and traffic are not represented.", 1.0, 30.0
    for i in range(n):
        for j in range(n):
            if i != j:
                km = haversine_km(points[i], points[j]) * factor
                distances[i][j], durations[i][j] = km, km/speed*60
    return distances, durations, warning


def build_route_geometry(points, base_url="https://router.project-osrm.org"):
    """Return ordered road geometry as (latitude, longitude) points, or None."""
    coords=";".join(f"{lon},{lat}" for lat,lon in points)
    response=requests.get(f"{base_url.rstrip('/')}/route/v1/driving/{coords}",params={"overview":"full","geometries":"geojson","steps":"false"},timeout=20)
    response.raise_for_status()
    data=response.json()
    routes=data.get("routes",[])
    if data.get("code")!="Ok" or not routes:return None
    return [(float(lat),float(lon)) for lon,lat in routes[0]["geometry"]["coordinates"]]
