# Astronomy Visibility Tool

## Purpose

`scripts/astronomy_visibility.py` computes observation windows for astronomical targets.
Given target RA/Dec and observer location, it samples altitude/azimuth over time and
returns the best continuous visibility window above a minimum altitude.

## Features

- Julian Date conversion
- Greenwich and Local Mean Sidereal Time
- Equatorial -> Horizontal conversion (Alt/Az)
- Time-series visibility sampling
- Longest continuous observation window detection
- Console + file logging (`logs/astronomy_visibility.log`)
- Optional JSON report export

## CLI Usage

Example (Munich observer, Vega target, 6-hour horizon):

```powershell
c:/ai/LIARA/.venv/Scripts/python.exe scripts/astronomy_visibility.py \
  --target-name Vega \
  --ra-hours 18.6156 \
  --dec-deg 38.7837 \
  --lat-deg 48.1372 \
  --lon-deg 11.5756 \
  --duration-hours 6 \
  --step-minutes 20 \
  --min-altitude-deg 25 \
  --json-out logs/astronomy/vega_visibility.json
```

## Output

The program prints a JSON document with:

- `target`, `observer`
- `visible_samples`, `total_samples`
- `best_window` (or `null`)
- `samples` containing timestamp, altitude, azimuth, visibility flag

## Tests

Unit tests are in `tests/unit/test_astronomy_visibility.py`.

Run:

```powershell
c:/ai/LIARA/.venv/Scripts/python.exe -m pytest tests/unit/test_astronomy_visibility.py -q
```

## Validation Notes

- J2000 Julian Date reference is covered by tests (`2451545.0`)
- GMST reference for J2000 is checked against known value (~`18.697374558` h)
- A geometric zenith case (`dec == latitude`, `HA == 0`) is validated
- Longest visibility-window extraction is tested with controlled sample data
