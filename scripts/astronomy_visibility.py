"""Astronomical target visibility calculator.

Given target coordinates (RA/Dec), observer location, and a UTC time range,
this module computes altitude/azimuth samples and derives a best observation
window above a configurable minimum altitude.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Iterable

_LOGGER = logging.getLogger("astronomy.visibility")


def configure_logging(log_file: str | Path = "logs/astronomy_visibility.log") -> None:
    """Configure console and file logging once."""
    if _LOGGER.handlers:
        return

    _LOGGER.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    _LOGGER.addHandler(stream_handler)

    path = Path(log_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    _LOGGER.addHandler(file_handler)


@dataclass(frozen=True)
class ObservationTarget:
    name: str
    ra_hours: float
    dec_deg: float


@dataclass(frozen=True)
class VisibilitySample:
    timestamp_utc: str
    altitude_deg: float
    azimuth_deg: float
    is_visible: bool


@dataclass(frozen=True)
class ObservationWindow:
    start_utc: str
    end_utc: str
    duration_minutes: int
    peak_altitude_deg: float


def _normalize_angle_deg(value: float) -> float:
    return value % 360.0


def _normalize_hours(value: float) -> float:
    return value % 24.0


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def julian_date(dt_utc: datetime) -> float:
    """Convert datetime (UTC) to Julian Date."""
    dt = _as_utc(dt_utc)
    year = dt.year
    month = dt.month
    day = dt.day + (
        dt.hour / 24.0
        + dt.minute / 1440.0
        + dt.second / 86400.0
        + dt.microsecond / 86400_000000.0
    )

    if month <= 2:
        year -= 1
        month += 12

    a = year // 100
    b = 2 - a + (a // 4)

    return (
        math.floor(365.25 * (year + 4716))
        + math.floor(30.6001 * (month + 1))
        + day
        + b
        - 1524.5
    )


def greenwich_mean_sidereal_time_hours(dt_utc: datetime) -> float:
    """Compute GMST in sidereal hours using IAU-style approximation."""
    jd = julian_date(dt_utc)
    t = (jd - 2451545.0) / 36525.0

    gmst_deg = (
        280.46061837
        + 360.98564736629 * (jd - 2451545.0)
        + 0.000387933 * (t * t)
        - (t * t * t) / 38710000.0
    )
    return _normalize_angle_deg(gmst_deg) / 15.0


def local_sidereal_time_hours(dt_utc: datetime, longitude_deg: float) -> float:
    """Compute local sidereal time in hours.

    East longitudes are positive, west longitudes are negative.
    """
    gmst_h = greenwich_mean_sidereal_time_hours(dt_utc)
    return _normalize_hours(gmst_h + longitude_deg / 15.0)


def equatorial_to_horizontal(
    ra_hours: float,
    dec_deg: float,
    latitude_deg: float,
    longitude_deg: float,
    dt_utc: datetime,
) -> tuple[float, float]:
    """Convert equatorial coordinates to horizontal coordinates (Alt/Az)."""
    lst_h = local_sidereal_time_hours(dt_utc, longitude_deg)
    ha_h = (lst_h - ra_hours) % 24.0
    if ha_h > 12.0:
        ha_h -= 24.0

    ha_rad = math.radians(ha_h * 15.0)
    dec_rad = math.radians(dec_deg)
    lat_rad = math.radians(latitude_deg)

    sin_alt = (
        math.sin(dec_rad) * math.sin(lat_rad)
        + math.cos(dec_rad) * math.cos(lat_rad) * math.cos(ha_rad)
    )
    sin_alt = max(-1.0, min(1.0, sin_alt))
    alt_rad = math.asin(sin_alt)

    cos_alt = max(1e-12, math.cos(alt_rad))
    sin_az = -math.sin(ha_rad) * math.cos(dec_rad) / cos_alt
    cos_az = (
        math.sin(dec_rad) - math.sin(alt_rad) * math.sin(lat_rad)
    ) / (cos_alt * max(1e-12, math.cos(lat_rad)))

    az_rad = math.atan2(sin_az, cos_az)

    altitude_deg = math.degrees(alt_rad)
    azimuth_deg = _normalize_angle_deg(math.degrees(az_rad))
    return altitude_deg, azimuth_deg


def sample_visibility(
    target: ObservationTarget,
    latitude_deg: float,
    longitude_deg: float,
    start_utc: datetime,
    duration_hours: float,
    step_minutes: int,
    min_altitude_deg: float = 20.0,
) -> list[VisibilitySample]:
    """Create altitude/azimuth samples over the requested time range."""
    if step_minutes <= 0:
        raise ValueError("step_minutes must be > 0")
    if duration_hours <= 0:
        raise ValueError("duration_hours must be > 0")

    start = _as_utc(start_utc)
    end = start + timedelta(hours=duration_hours)
    step = timedelta(minutes=step_minutes)

    samples: list[VisibilitySample] = []
    current = start
    while current <= end:
        alt_deg, az_deg = equatorial_to_horizontal(
            ra_hours=target.ra_hours,
            dec_deg=target.dec_deg,
            latitude_deg=latitude_deg,
            longitude_deg=longitude_deg,
            dt_utc=current,
        )
        samples.append(
            VisibilitySample(
                timestamp_utc=current.isoformat(),
                altitude_deg=round(alt_deg, 3),
                azimuth_deg=round(az_deg, 3),
                is_visible=alt_deg >= min_altitude_deg,
            )
        )
        current += step

    return samples


def find_best_window(samples: Iterable[VisibilitySample]) -> ObservationWindow | None:
    """Find the longest consecutive visible observation interval."""
    best: ObservationWindow | None = None
    current_start: VisibilitySample | None = None
    current_end: VisibilitySample | None = None
    current_peak = -90.0

    for sample in samples:
        if sample.is_visible:
            if current_start is None:
                current_start = sample
            current_end = sample
            current_peak = max(current_peak, sample.altitude_deg)
            continue

        if current_start is not None and current_end is not None:
            candidate = _build_window(current_start, current_end, current_peak)
            if best is None or candidate.duration_minutes > best.duration_minutes:
                best = candidate
        current_start = None
        current_end = None
        current_peak = -90.0

    if current_start is not None and current_end is not None:
        candidate = _build_window(current_start, current_end, current_peak)
        if best is None or candidate.duration_minutes > best.duration_minutes:
            best = candidate

    return best


def _build_window(start: VisibilitySample, end: VisibilitySample, peak_alt: float) -> ObservationWindow:
    start_dt = datetime.fromisoformat(start.timestamp_utc)
    end_dt = datetime.fromisoformat(end.timestamp_utc)
    duration = int((end_dt - start_dt).total_seconds() / 60)
    return ObservationWindow(
        start_utc=start.timestamp_utc,
        end_utc=end.timestamp_utc,
        duration_minutes=max(duration, 0),
        peak_altitude_deg=round(peak_alt, 3),
    )


def run_analysis(
    target: ObservationTarget,
    latitude_deg: float,
    longitude_deg: float,
    start_utc: datetime,
    duration_hours: float,
    step_minutes: int,
    min_altitude_deg: float,
) -> dict:
    """Run full visibility analysis and return a JSON-serializable payload."""
    _LOGGER.info(
        "Starting analysis target=%s ra=%.4fh dec=%.4fdeg lat=%.4f lon=%.4f",
        target.name,
        target.ra_hours,
        target.dec_deg,
        latitude_deg,
        longitude_deg,
    )

    samples = sample_visibility(
        target=target,
        latitude_deg=latitude_deg,
        longitude_deg=longitude_deg,
        start_utc=start_utc,
        duration_hours=duration_hours,
        step_minutes=step_minutes,
        min_altitude_deg=min_altitude_deg,
    )
    window = find_best_window(samples)

    visible_count = sum(1 for item in samples if item.is_visible)
    payload = {
        "target": asdict(target),
        "observer": {
            "latitude_deg": latitude_deg,
            "longitude_deg": longitude_deg,
        },
        "start_utc": _as_utc(start_utc).isoformat(),
        "duration_hours": duration_hours,
        "step_minutes": step_minutes,
        "min_altitude_deg": min_altitude_deg,
        "visible_samples": visible_count,
        "total_samples": len(samples),
        "best_window": asdict(window) if window else None,
        "samples": [asdict(item) for item in samples],
    }

    _LOGGER.info(
        "Finished analysis target=%s visible_samples=%d total_samples=%d best_window=%s",
        target.name,
        visible_count,
        len(samples),
        "yes" if window else "no",
    )
    return payload


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Astronomical visibility calculator")
    parser.add_argument("--target-name", default="Vega")
    parser.add_argument("--ra-hours", type=float, default=18.6156, help="Right Ascension in hours")
    parser.add_argument("--dec-deg", type=float, default=38.7837, help="Declination in degrees")
    parser.add_argument("--lat-deg", type=float, required=True, help="Observer latitude in degrees")
    parser.add_argument("--lon-deg", type=float, required=True, help="Observer longitude in degrees")
    parser.add_argument("--start-utc", default="", help="ISO timestamp, defaults to now UTC")
    parser.add_argument("--duration-hours", type=float, default=12.0)
    parser.add_argument("--step-minutes", type=int, default=15)
    parser.add_argument("--min-altitude-deg", type=float, default=20.0)
    parser.add_argument("--json-out", default="", help="Optional output JSON file")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    configure_logging()

    start_utc = (
        datetime.fromisoformat(args.start_utc).astimezone(UTC)
        if args.start_utc
        else datetime.now(tz=UTC)
    )

    target = ObservationTarget(
        name=args.target_name,
        ra_hours=args.ra_hours,
        dec_deg=args.dec_deg,
    )

    result = run_analysis(
        target=target,
        latitude_deg=args.lat_deg,
        longitude_deg=args.lon_deg,
        start_utc=start_utc,
        duration_hours=args.duration_hours,
        step_minutes=args.step_minutes,
        min_altitude_deg=args.min_altitude_deg,
    )

    if args.json_out:
        out_path = Path(args.json_out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(result, indent=2, ensure_ascii=True), encoding="utf-8")
        _LOGGER.info("Wrote JSON report to %s", out_path)

    print(json.dumps(result, indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
