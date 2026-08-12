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

clip(v, lo, hi) = max(lo, min(hi, Float64(v)))
round6(x) = round(Float64(x), digits=6)
round8(x) = round(Float64(x), digits=8)

# ---------------------------------------------------------------------------
# IO
# ---------------------------------------------------------------------------

raw = read(stdin, String)
data = JSON.parse(raw)

belief = get(data, "belief", Dict{String,Any}())
observation = get(data, "observation", Dict{String,Any}())
signal_window = get(data, "signal_window", Float64[])
config = get(data, "config", Dict{String,Any}())

prior     = clip(as_float(get(belief, "prior",    0.5), 0.5), 0.0, 1.0)
b_entropy = clip(as_float(get(belief, "entropy",  0.0), 0.0), 0.0, 1.0)
b_var     = max(0.0, as_float(get(belief, "variance", 0.0), 0.0))

likelihood  = clip(as_float(get(observation, "likelihood", 0.5), 0.5), 1e-9, 1.0)
signal      = as_float(get(observation, "signal",  0.5), 0.5)
obs_entropy = clip(as_float(get(observation, "entropy", 0.0), 0.0), 0.0, 1.0)

kalman_gain  = clip(as_float(get(config, "kalman_gain",  0.3),  0.3), 1e-4, 1.0)
min_variance = max(1e-4, as_float(get(config, "min_variance", 1e-4), 1e-4))

# ---------------------------------------------------------------------------
# Bayes update   P(H|E) = P(E|H)*P(H) / P(E)
# ---------------------------------------------------------------------------

likelihood_neg = clip(1.0 - likelihood, 1e-9, 1.0)
marginal = likelihood * prior + likelihood_neg * (1.0 - prior)
if marginal < 1e-12; marginal = 1e-12; end
posterior = clip((likelihood * prior) / marginal, 0.0, 1.0)

bayes = Dict(
    "posterior"  => round6(posterior),
    "prior"      => round6(prior),
    "likelihood" => round6(likelihood),
    "marginal"   => round6(marginal),
)

# ---------------------------------------------------------------------------
# Kalman-like belief tracking   x_{t+1} = x_t + K*(z_t - x_t)
# ---------------------------------------------------------------------------

noise_variance = max(min_variance, obs_entropy)
residual  = signal - prior
estimate  = clip(prior + kalman_gain * residual, 0.0, 1.0)
upd_var   = (1.0 - kalman_gain)^2 * max(min_variance, b_var) + kalman_gain^2 * noise_variance
upd_var   = max(min_variance, upd_var)

kalman = Dict(
    "estimate"    => round6(estimate),
    "residual"    => round6(residual),
    "variance"    => round8(upd_var),
    "kalman_gain" => round6(kalman_gain),
    "prior"       => round6(prior),
    "signal"      => round6(signal),
)

# ---------------------------------------------------------------------------
# Signal variance / confidence
# ---------------------------------------------------------------------------

sigs = Float64[]
for v in signal_window
    if v isa Number; push!(sigs, Float64(v)); end
end
n = length(sigs)

if n == 0
    sig_mean = 0.0; sig_var = 0.0; sig_std = 0.0; sig_conf = 1.0
else
    sig_mean = sum(sigs) / n
    ddof = n > 1 ? 1 : 0
    sig_var  = n <= ddof ? 0.0 : sum((x - sig_mean)^2 for x in sigs) / (n - ddof)
    sig_std  = sqrt(sig_var)
    sig_conf = clip(1.0 / (1.0 + sig_var), 0.0, 1.0)
end

variance_stats = Dict(
    "mean"       => round6(sig_mean),
    "variance"   => round8(sig_var),
    "std"        => round8(sig_std),
    "confidence" => round6(sig_conf),
    "n"          => n,
)

# ---------------------------------------------------------------------------
# Combined snapshot (flat dict mirroring Python's compute_belief_snapshot)
# ---------------------------------------------------------------------------

result = Dict(
    # Bayes
    "belief_posterior" => bayes["posterior"],
    "belief_prior"     => bayes["prior"],
    "belief_likelihood"=> bayes["likelihood"],
    "belief_marginal"  => bayes["marginal"],
    # Kalman
    "belief_estimate"  => kalman["estimate"],
    "belief_residual"  => kalman["residual"],
    "belief_variance"  => kalman["variance"],
    "belief_kalman_gain" => kalman["kalman_gain"],
    # Signal window
    "signal_mean"       => variance_stats["mean"],
    "signal_variance"   => variance_stats["variance"],
    "signal_std"        => variance_stats["std"],
    "signal_confidence" => variance_stats["confidence"],
    "signal_n"          => variance_stats["n"],
    # Audit
    "belief_compute_backend" => "julia",
    "belief_compute_path"    => "primary",
)

print(JSON.json(Dict("belief_snapshot" => result)))
