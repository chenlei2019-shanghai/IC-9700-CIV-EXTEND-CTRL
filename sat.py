"""
Satellite tracking and Doppler correction for IC-9700.
Uses skyfield for TLE-based orbit prediction.
"""

import logging
from skyfield.api import load, EarthSatellite, wgs84
from skyfield.positionlib import ICRF

logger = logging.getLogger(__name__)

# In-memory state
_tle_data: dict[str, tuple[str, str]] = {}  # name -> (line1, line2)
_observer = {"lat": 31.23, "lon": 121.47, "alt_m": 50}  # default: Shanghai
_ts = load.timescale()

# Known satellite presets with default frequencies
SATELLITE_PRESETS = {
    "SO-50": {
        "uplink": 145850000,   # 145.850 MHz FM (with 67Hz tone)
        "downlink": 436795000,  # 436.795 MHz FM
        "uplink_mode": "FM",
        "downlink_mode": "FM",
    },
    "AO-91": {
        "uplink": 435250000,
        "downlink": 145960000,
        "uplink_mode": "USB",
        "downlink_mode": "USB",
    },
    "AO-92": {
        "uplink": 435240000,
        "downlink": 145880000,
        "uplink_mode": "USB",
        "downlink_mode": "USB",
    },
    "ISS": {
        "uplink": 145825000,   # APRS
        "downlink": 145825000,
        "uplink_mode": "FM",
        "downlink_mode": "FM",
    },
    "AO-73": {
        "uplink": 435300000,
        "downlink": 145825000,
        "uplink_mode": "USB",
        "downlink_mode": "USB",
    },
    "PO-101": {
        "uplink": 435280000,
        "downlink": 145840000,
        "uplink_mode": "USB",
        "downlink_mode": "USB",
    },
    "CAS-4A": {
        "uplink": 435070000,
        "downlink": 145975000,
        "uplink_mode": "USB",
        "downlink_mode": "USB",
    },
    "CAS-4B": {
        "uplink": 435180000,
        "downlink": 145915000,
        "uplink_mode": "USB",
        "downlink_mode": "USB",
    },
    "AO-7": {
        "uplink": 432150000,   # Mode B: 70cm up, 2m down
        "downlink": 145950000,
        "uplink_mode": "USB",
        "downlink_mode": "USB",
    },
    "FO-29": {
        "uplink": 145950000,   # Mode J: 2m up, 70cm down
        "downlink": 435850000,
        "uplink_mode": "USB",
        "downlink_mode": "USB",
    },
    "ISS_FM": {
        "uplink": 145990000,   # Voice
        "downlink": 437800000,
        "uplink_mode": "FM",
        "downlink_mode": "FM",
    },
    "AO-85": {
        "uplink": 435170000,
        "downlink": 145980000,
        "uplink_mode": "FM",
        "downlink_mode": "FM",
    },
    "AO-95": {
        "uplink": 435300000,
        "downlink": 145920000,
        "uplink_mode": "USB",
        "downlink_mode": "USB",
    },
    "RS-44": {
        "uplink": 145965000,
        "downlink": 435640000,
        "uplink_mode": "USB",
        "downlink_mode": "USB",
    },
    "XW-2A": {
        "uplink": 435660000,
        "downlink": 145640000,
        "uplink_mode": "USB",
        "downlink_mode": "USB",
    },
}


def set_tle(name: str, line1: str, line2: str) -> bool:
    """Store TLE data for a satellite."""
    try:
        _tle_data[name] = (line1.strip(), line2.strip())
        return True
    except Exception:
        return False


def set_observer(lat: float, lon: float, alt_m: float = 50) -> None:
    """Set observer location."""
    _observer["lat"] = lat
    _observer["lon"] = lon
    _observer["alt_m"] = alt_m


def get_tle_names() -> list[str]:
    """Get list of satellites with TLE data."""
    return list(_tle_data.keys())


def get_presets() -> dict:
    """Get satellite presets with default frequencies."""
    return SATELLITE_PRESETS


def calc_doppler(name: str) -> dict | None:
    """
    Calculate current Doppler shift for a satellite.
    Returns dict with corrected uplink/downlink frequencies.
    """
    if name not in _tle_data:
        return None
    if name not in SATELLITE_PRESETS:
        return None

    try:
        line1, line2 = _tle_data[name]
        sat = EarthSatellite(line1, line2, name, _ts)
        t = _ts.now()
        obs = wgs84.latlon(_observer["lat"], _observer["lon"], _observer["alt_m"])

        # Position relative to observer
        difference = sat - obs
        topocentric = difference.at(t)

        # Range rate (m/s, positive = moving away)
        range_rate = topocentric.range_rate.km_per_s * 1000  # m/s

        preset = SATELLITE_PRESETS[name]
        ul_freq = preset["uplink"]
        dl_freq = preset["downlink"]

        c = 299792458.0  # speed of light m/s

        # Doppler formula: f_obs = f_src * (c - v) / c  (v positive = moving away)
        ul_corrected = int(ul_freq * (c - range_rate) / c)
        dl_corrected = int(dl_freq * (c - range_rate) / c)

        # Also calculate Az/El for reference
        alt, az, _ = topocentric.altaz()
        elevation = alt.degrees
        azimuth = az.degrees

        return {
            "name": name,
            "uplink_nominal": ul_freq,
            "downlink_nominal": dl_freq,
            "uplink_doppler": ul_corrected,
            "downlink_doppler": dl_corrected,
            "uplink_shift_hz": ul_corrected - ul_freq,
            "downlink_shift_hz": dl_corrected - dl_freq,
            "range_rate_ms": round(range_rate, 1),
            "elevation": round(elevation, 1),
            "azimuth": round(azimuth, 1),
        }
    except Exception as e:
        logger.error("Doppler calc error: %s", e)
        return None
