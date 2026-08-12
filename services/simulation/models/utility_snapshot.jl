import JSON

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

function as_float(v, default)
    if v === nothing; return Float64(default); end
    if v isa Number; return Float64(v); end
    if v isa AbstractString
        try; return parse(Float64, v); catch; return Float64(default); end
    end
    return Float64(default)
end

as_int(v, default) = Int(round(as_float(v, default)))
clip(v, lo, hi) = max(lo, min(hi, Float64(v)))
round6(x) = round(Float64(x), digits=6)
round8(x) = round(Float64(x), digits=8)

# ---------------------------------------------------------------------------
# IO
# ---------------------------------------------------------------------------

raw = read(stdin, String)
data = JSON.parse(raw)

utility        = as_float(get(data, "utility",        0.0), 0.0)
entropy_before = clip(as_float(get(data, "entropy_before", 0.0), 0.0), 0.0, 1.0)
entropy_after  = clip(as_float(get(data, "entropy_after",  0.0), 0.0), 0.0, 1.0)
step           = max(0, as_int(get(data, "step", 0), 0))
gamma          = clip(as_float(get(data, "gamma", 0.95), 0.95), 1e-6, 1.0)

# ---------------------------------------------------------------------------
# Phase 2a — Information Gain   IG = H_before - H_after
# ---------------------------------------------------------------------------

ig = round6(entropy_before - entropy_after)
ig_direction = if ig > 1e-6
    "gain"
elseif ig < -1e-6
    "loss"
else
    "neutral"
end

# ---------------------------------------------------------------------------
# Phase 2b — Confidence-Weighted Utility   U' = U * (1 - H_after)
# ---------------------------------------------------------------------------

discount_factor  = round6(1.0 - entropy_after)
weighted_utility = round6(utility * discount_factor)

# ---------------------------------------------------------------------------
# Phase 2c — Temporal Discounting   discounted = U' * gamma^step
# ---------------------------------------------------------------------------

discount_weight  = round8(gamma ^ step)
discounted_value = round6(weighted_utility * discount_weight)

# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

result = Dict(
    "utility_ig"              => ig,
    "utility_entropy_before"  => round6(entropy_before),
    "utility_entropy_after"   => round6(entropy_after),
    "utility_ig_direction"    => ig_direction,
    "utility_weighted"        => weighted_utility,
    "utility_raw"             => round6(utility),
    "utility_discount_factor" => discount_factor,
    "utility_discounted"      => discounted_value,
    "utility_step"            => step,
    "utility_gamma"           => round6(gamma),
    "utility_discount_weight" => discount_weight,
    "utility_compute_backend" => "julia",
    "utility_compute_path"    => "primary",
)

println(JSON.json(Dict("utility_snapshot" => result)))
