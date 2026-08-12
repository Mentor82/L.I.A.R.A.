from __future__ import annotations

from datetime import UTC, datetime

from scripts.astronomy_visibility import (
    ObservationTarget,
    VisibilitySample,
    equatorial_to_horizontal,
    find_best_window,
    greenwich_mean_sidereal_time_hours,
    julian_date,
    local_sidereal_time_hours,
    sample_visibility,
)


def test_julian_date_j2000_reference():
    dt = datetime(2000, 1, 1, 12, 0, 0, tzinfo=UTC)
    assert abs(julian_date(dt) - 2451545.0) < 1e-9


def test_gmst_j2000_reference():
    dt = datetime(2000, 1, 1, 12, 0, 0, tzinfo=UTC)
    gmst = greenwich_mean_sidereal_time_hours(dt)
    # Reference close to 18.697374558h
    assert abs(gmst - 18.697374558) < 1e-3


def test_target_at_local_meridian_is_near_zenith_when_dec_equals_latitude():
    dt = datetime(2026, 4, 27, 0, 0, 0, tzinfo=UTC)
    lat_deg = 20.0
    lon_deg = 10.0
    ra_hours = local_sidereal_time_hours(dt, lon_deg)

    altitude_deg, azimuth_deg = equatorial_to_horizontal(
        ra_hours=ra_hours,
        dec_deg=lat_deg,
        latitude_deg=lat_deg,
        longitude_deg=lon_deg,
        dt_utc=dt,
    )

    assert altitude_deg > 89.0
    assert 0.0 <= azimuth_deg < 360.0


def test_sample_visibility_returns_expected_count_and_shape():
    target = ObservationTarget(name="Vega", ra_hours=18.6156, dec_deg=38.7837)
    samples = sample_visibility(
        target=target,
        latitude_deg=48.1372,
        longitude_deg=11.5756,
        start_utc=datetime(2026, 4, 27, 0, 0, 0, tzinfo=UTC),
        duration_hours=2.0,
        step_minutes=30,
        min_altitude_deg=20.0,
    )

    # t=0,30,60,90,120 minutes => 5 samples
    assert len(samples) == 5
    assert all(isinstance(item.altitude_deg, float) for item in samples)
    assert all(0.0 <= item.azimuth_deg < 360.0 for item in samples)


def test_find_best_window_returns_longest_visible_segment():
    samples = [
        VisibilitySample("2026-04-27T00:00:00+00:00", 10.0, 120.0, False),
        VisibilitySample("2026-04-27T00:15:00+00:00", 25.0, 121.0, True),
        VisibilitySample("2026-04-27T00:30:00+00:00", 27.0, 122.0, True),
        VisibilitySample("2026-04-27T00:45:00+00:00", 19.0, 123.0, False),
        VisibilitySample("2026-04-27T01:00:00+00:00", 21.0, 124.0, True),
        VisibilitySample("2026-04-27T01:15:00+00:00", 24.0, 125.0, True),
        VisibilitySample("2026-04-27T01:30:00+00:00", 28.0, 126.0, True),
    ]

    window = find_best_window(samples)
    assert window is not None
    assert window.start_utc == "2026-04-27T01:00:00+00:00"
    assert window.end_utc == "2026-04-27T01:30:00+00:00"
    assert window.duration_minutes == 30
    assert window.peak_altitude_deg == 28.0
