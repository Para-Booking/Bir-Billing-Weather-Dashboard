"""Small weather calculations, kept pure so they're easy to test."""
import math
from datetime import date, datetime, time as dtime
from typing import Iterable, Optional, Tuple
from zoneinfo import ZoneInfo


def dew_point_c(temp_c: float, rh: float) -> Optional[float]:
    """Magnus formula (Sonntag constants); good to ±0.35 °C for -45..60 °C."""
    if rh is None or temp_c is None or rh <= 0:
        return None
    a, b = 17.62, 243.12
    gamma = math.log(rh / 100.0) + a * temp_c / (b + temp_c)
    return b * gamma / (a - gamma)


def cloud_base_agl_m(temp_c: float, dew_c: float) -> float:
    """Estimated convective cloud base above the station: ~125 m per °C of
    temperature/dew-point spread. A rule of thumb, not a measurement."""
    return max(0.0, temp_c - dew_c) * 125.0


def sea_level_hpa(pressure_pa: float, temp_c: float, elevation_m: float) -> float:
    """Reduce station pressure to sea level (standard barometric formula,
    using the station temperature). At 1,431 m this reads a few hPa lower
    than the Pi's Weather Underground figure, whose formula treats the
    station temperature as the sea-level temperature."""
    p_hpa = pressure_pa / 100.0
    return p_hpa / (1.0 - 0.0065 * elevation_m / (temp_c + 0.0065 * elevation_m + 273.15)) ** 5.257


def circular_mean_deg(angles: Iterable[float]) -> Optional[float]:
    s = c = 0.0
    n = 0
    for a in angles:
        r = math.radians(a)
        s += math.sin(r)
        c += math.cos(r)
        n += 1
    if n == 0 or (abs(s) < 1e-9 and abs(c) < 1e-9):
        return None
    return math.degrees(math.atan2(s, c)) % 360.0


def sun_times(lat: float, lon: float, day: date, tz: str) -> Tuple[Optional[int], Optional[int]]:
    """Sunrise and sunset (unix seconds) for a local date, using the standard
    sunrise equation. Accurate to about a minute; None during polar day/night."""
    noon_local = datetime.combine(day, dtime(12, 0), ZoneInfo(tz))
    j_date = noon_local.timestamp() / 86400.0 + 2440587.5
    n = math.ceil(j_date - 2451545.0 + 0.0008)
    j_star = n - lon / 360.0
    m = math.radians((357.5291 + 0.98560028 * j_star) % 360.0)
    c = 1.9148 * math.sin(m) + 0.02 * math.sin(2 * m) + 0.0003 * math.sin(3 * m)
    lam = math.radians((math.degrees(m) + c + 180.0 + 102.9372) % 360.0)
    j_transit = 2451545.0 + j_star + 0.0053 * math.sin(m) - 0.0069 * math.sin(2 * lam)
    decl = math.asin(math.sin(lam) * math.sin(math.radians(23.4397)))
    phi = math.radians(lat)
    cos_w = (math.sin(math.radians(-0.833)) - math.sin(phi) * math.sin(decl)) / (
        math.cos(phi) * math.cos(decl)
    )
    if not -1.0 <= cos_w <= 1.0:
        return None, None
    w = math.degrees(math.acos(cos_w)) / 360.0
    to_unix = lambda j: int(round((j - 2440587.5) * 86400.0))  # noqa: E731
    return to_unix(j_transit - w), to_unix(j_transit + w)
