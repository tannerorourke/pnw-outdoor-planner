#!/usr/bin/env python3
# Offline sun and moon read for a coordinate and date: civil dawn, sunrise,
# sunset, civil dusk, daylight length, moon phase and illuminated fraction.
# Solar times use the NOAA general solar position equations (zenith 90.833
# for sunrise/sunset, 96 for civil twilight); the moon is a mean-synodic-month
# approximation from the 2000-01-06 new moon, good to about a day.
#
#   python3 sun.py LAT LON YYYY-MM-DD [--tz America/Los_Angeles]
import argparse
import math
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from _common import INDENT_PARENT, emit

SYNODIC_DAYS = 29.530588853
NEW_MOON_EPOCH = datetime(2000, 1, 6, 18, 14, tzinfo=timezone.utc)
PHASES = ("new moon", "waxing crescent", "first quarter", "waxing gibbous", "full moon", "waning gibbous", "last quarter", "waning crescent")


def solar_params(d: date):
    """Equation of time (minutes) and declination (radians) at local noon UTC of date d."""
    jd = d.toordinal() + 1721424.5 + 0.5
    t = (jd - 2451545.0) / 36525
    l0 = math.radians((280.46646 + t * (36000.76983 + t * 0.0003032)) % 360)
    m = math.radians(357.52911 + t * (35999.05029 - 0.0001537 * t))
    e = 0.016708634 - t * (0.000042037 + 0.0000001267 * t)
    c = math.radians(
        math.sin(m) * (1.914602 - t * (0.004817 + 0.000014 * t))
        + math.sin(2 * m) * (0.019993 - 0.000101 * t)
        + math.sin(3 * m) * 0.000289
    )
    true_long = l0 + c
    omega = math.radians(125.04 - 1934.136 * t)
    app_long = true_long - math.radians(0.00569 + 0.00478 * math.sin(omega))
    obliq0 = 23 + (26 + (21.448 - t * (46.815 + t * (0.00059 - t * 0.001813))) / 60) / 60
    obliq = math.radians(obliq0 + 0.00256 * math.cos(omega))
    decl = math.asin(math.sin(obliq) * math.sin(app_long))
    y = math.tan(obliq / 2) ** 2
    eq_time = 4 * math.degrees(
        y * math.sin(2 * l0) - 2 * e * math.sin(m) + 4 * e * y * math.sin(m) * math.cos(2 * l0)
        - 0.5 * y * y * math.sin(4 * l0) - 1.25 * e * e * math.sin(2 * m)
    )
    return eq_time, decl


def event_times(lat, lon, d, zenith_deg):
    """(rise, set) as UTC datetimes for the sun crossing the given zenith angle."""
    eq_time, decl = solar_params(d)
    phi = math.radians(lat)
    cos_h = (math.cos(math.radians(zenith_deg)) - math.sin(phi) * math.sin(decl)) / (math.cos(phi) * math.cos(decl))
    if not -1 <= cos_h <= 1:
        return None, None
    ha = math.degrees(math.acos(cos_h))
    midnight = datetime.combine(d, time(0), tzinfo=timezone.utc)
    noon_min = 720 - 4 * lon - eq_time
    return midnight + timedelta(minutes=noon_min - 4 * ha), midnight + timedelta(minutes=noon_min + 4 * ha)


def local(dt, tz):
    return None if dt is None else dt.astimezone(tz).strftime("%H:%M")


def moon(d: date):
    noon = datetime.combine(d, time(12), tzinfo=timezone.utc)
    age = ((noon - NEW_MOON_EPOCH).total_seconds() / 86400) % SYNODIC_DAYS
    illum = (1 - math.cos(2 * math.pi * age / SYNODIC_DAYS)) / 2
    return PHASES[round(age / SYNODIC_DAYS * 8) % 8], round(illum * 100)


def main():
    ap = argparse.ArgumentParser(description=__doc__, parents=[INDENT_PARENT])
    ap.add_argument("lat", type=float)
    ap.add_argument("lon", type=float)
    ap.add_argument("date")
    ap.add_argument("--tz", default="America/Los_Angeles")
    a = ap.parse_args()
    tz, d = ZoneInfo(a.tz), date.fromisoformat(a.date)
    # -- events computed on UTC date d can land on the adjacent local date at western longitudes; shift whole days back onto d
    def on_local_day(dt):
        return None if dt is None else dt + timedelta(days=(d - dt.astimezone(tz).date()).days)

    rise, set_ = map(on_local_day, event_times(a.lat, a.lon, d, 90.833))
    dawn, dusk = map(on_local_day, event_times(a.lat, a.lon, d, 96))
    phase, illum = moon(d)
    emit(
        {
            "date": a.date,
            "civil_dawn": local(dawn, tz),
            "sunrise": local(rise, tz),
            "sunset": local(set_, tz),
            "civil_dusk": local(dusk, tz),
            "daylight_hours": None if rise is None else round((set_ - rise).total_seconds() / 3600, 2),
            "moon_phase": phase,
            "moon_illumination_pct": illum,
        },
        a,
    )


if __name__ == "__main__":
    main()
