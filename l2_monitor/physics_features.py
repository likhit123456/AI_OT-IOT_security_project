"""Measured-only, process-invariant features for the TE telemetry detector.

Mass balances explain legitimate valve/flow transients instead of treating large
changes as faults. No labels, rationale, scenario names or SIS truth are inputs.
"""

import math

from l2_monitor.telemetry_classifier import _stats
from l2_monitor.telemetry_classifier import features as validate_window

VERSION = 2
VALVES = (("f1", 6.4046), ("f2", 0.4246), ("purge", 0.352), ("product", 0.0417))


def _average(xs):
    return (0.5 * xs[0] + sum(xs[1:-1]) + 0.5 * xs[-1]) / (len(xs) - 1)


def _balances(w, start, end):
    p, level, a = (w[k][start:end] for k in ("pressure", "level", "a_in_purge"))
    duration = w["window_s"] * (end - start - 1) / (len(w["pressure"]) - 1)
    vapor = [
        pressure * (122.0 - 0.3 * lev) / (8.314 * 373.0)
        for pressure, lev in zip(p, level)
    ]
    liquid = [2.49 * lev for lev in level]
    f1, f2, purge, product = (
        w[k][start:end] for k in ("f1_flow", "f2_flow", "purge_flow", "product_flow")
    )
    scale = 1 + _average(
        [
            abs(x) + abs(y) + abs(z) + abs(t)
            for x, y, z, t in zip(f1, f2, purge, product)
        ]
    )
    nv_rate = (vapor[-1] - vapor[0]) * 3600 / duration
    nl_rate = (liquid[-1] - liquid[0]) * 3600 / duration
    a_rate = (vapor[-1] * a[-1] - vapor[0] * a[0]) * 3600 / duration
    total_flux = [x + y - z - 2 * t for x, y, z, t in zip(f1, f2, purge, product)]
    a_flux = [
        0.485 * x + y - ai * z - t for x, y, ai, z, t in zip(f1, f2, a, purge, product)
    ]

    # Integrals are bounded by adjacent sampled flux extrema. This accommodates
    # legitimate controller steps between samples instead of flagging their size.
    def interval_distance(observed, flux, quantization):
        low = (
            sum(min(x, y) for x, y in zip(flux, flux[1:])) / (len(flux) - 1)
            - quantization
        )
        high = (
            sum(max(x, y) for x, y in zip(flux, flux[1:])) / (len(flux) - 1)
            + quantization
        )
        return max(0.0, low - observed, observed - high) / scale

    nv_error = 0.01 * (
        (122 - 0.3 * min(level)) / (8.314 * 373) + max(p) * 0.3 / (8.314 * 373)
    )
    nl_error = 0.01 * 2.49
    total_error = (nv_error + 2 * nl_error) * 3600 / duration + 0.05
    a_error = (
        0.001 * max(vapor) + max(a) * nv_error + nl_error
    ) * 3600 / duration + 0.03
    return [
        interval_distance(nv_rate + 2 * nl_rate, total_flux, total_error),
        interval_distance(a_rate + nl_rate, a_flux, a_error),
    ]


def physics_features(w):
    validate_window(w)
    n = len(w["pressure"])
    duration = w.get("window_s")
    if (
        not isinstance(duration, (int, float))
        or not math.isfinite(duration)
        or not 0 < duration <= 300
    ):
        raise ValueError("invalid window duration")
    output = []
    gaps, residuals = [], []
    for valve, cv in VALVES:
        pos, cmd, flow = (
            w[f"{valve}_{suffix}"] for suffix in ("valve_pos", "valve_cmd", "flow")
        )
        expected = [
            cv
            * v
            * (math.sqrt(max(0, p - 100)) if valve in ("purge", "product") else 1.0)
            for v, p in zip(pos, w["pressure"])
        ]
        # Account for two-decimal position/flow and pressure rounding in the
        # serving format. A closed valve with a rounded 0.01 flow is not a fault.
        residual = []
        for actual, exp, pressure in zip(flow, expected, w["pressure"]):
            gain = cv * (
                math.sqrt(max(0, pressure - 100))
                if valve in ("purge", "product")
                else 1.0
            )
            tolerance = 0.005 * gain + 0.005
            if valve in ("purge", "product") and pressure > 100:
                tolerance += abs(exp) * 0.005 / (2 * (pressure - 100))
            difference = actual - exp
            residual.append(
                math.copysign(max(0.0, abs(difference) - tolerance), difference)
                / max(1.0, abs(exp))
            )
        gap = [
            math.copysign(max(0.0, abs(actual - target) - 0.02), actual - target) / 100
            for actual, target in zip(pos, cmd)
        ]
        output.extend(_stats(residual))
        output.extend(_stats(gap))
        gaps.append(max(map(abs, gap)))
        residuals.append(max(map(abs, residual)))
    output.extend(sorted(residuals))
    output.extend(sorted(gaps))
    for start, end in ((0, n), (0, n // 2 + 1), (n // 2, n)):
        output.extend(_balances(w, start, end))
    # Endpoint-only balances can miss an instrument dropout that recovers before
    # the end of the window. Check overlapping local balances as well.
    local = [_balances(w, i, i + 4) for i in range(n - 3)]
    output.extend(float(max(b[j] for b in local) > 0.25) for j in (0, 1))
    output.append(max(output[104:110]))
    output.append(float(max(max(b) for b in local) > 0.25))
    p = w["pressure"]
    center = (n - 1) / 2
    slope = (
        sum((i - center) * (x - sum(p) / n) for i, x in enumerate(p))
        / sum((i - center) ** 2 for i in range(n))
        * (n - 1)
        / duration
    )
    # Existing soft pressure envelope: these are observable hazard checks, not
    # source-specific flow/valve averages that a forest can memorize as labels.
    output.extend(
        [
            float(max(p) >= 2850 and slope > -0.5),
            float(slope >= 1.5 and p[-1] >= 2750),
            max(p) / 3000,
            p[-1] / 3000,
            slope / 5,
        ]
    )
    if not all(math.isfinite(x) for x in output):
        raise ValueError("non-finite physical feature")
    return output
