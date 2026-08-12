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

# ---------------------------------------------------------------------------
# IO
# ---------------------------------------------------------------------------

raw = read(stdin, String)
data = JSON.parse(raw)

context_debug = get(data, "context_debug", Dict{String,Any}())
memory_items = max(0, as_int(get(data, "memory_items", 0), 0))
tool_calls = max(0, as_int(get(data, "tool_calls", 0), 0))
risk_series = get(data, "risk_series", Float64[])
lambda_l1 = max(0.0, as_float(get(data, "lambda_l1", 0.05), 0.05))
lambda_l2 = max(0.0, as_float(get(data, "lambda_l2", 0.01), 0.01))

node_count = max(0, as_int(get(context_debug, "graph_nodes", get(context_debug, "node_count", 0)), 0))
edge_count = max(0, as_int(get(context_debug, "graph_edges", get(context_debug, "edge_count", 0)), 0))
community_count = max(1, as_int(get(context_debug, "graph_communities", get(context_debug, "community_count", 1)), 1))
shortest_path_to_goal = max(0.0, as_float(get(context_debug, "shortest_path_to_goal", get(context_debug, "path_to_goal", 0.0)), 0.0))

# ---------------------------------------------------------------------------
# Graph structure metrics
# ---------------------------------------------------------------------------

if node_count <= 1
    clustering = 0.0
else
    clustering = clip((2.0 * edge_count) / (node_count * (node_count - 1)), 0.0, 1.0)
end

modularity = clip(1.0 - (1.0 / community_count), 0.0, 1.0)
path_pressure = clip(shortest_path_to_goal / (shortest_path_to_goal + 1.0), 0.0, 1.0)

# ---------------------------------------------------------------------------
# Stability heuristic
# ---------------------------------------------------------------------------

clean = [as_float(v, 0.0) for v in risk_series]
if length(clean) < 2
    derivative = 0.0
    stable = true
    stability_score = 1.0
else
    derivative = clean[end] - clean[end - 1]
    abs_d = abs(derivative)
    stable = abs_d < 1.0
    stability_score = clip(1.0 - min(1.0, abs_d), 0.0, 1.0)
end

# ---------------------------------------------------------------------------
# Regularization penalties
# ---------------------------------------------------------------------------

m = Float64(memory_items)
t = Float64(tool_calls)
penalty_l1 = lambda_l1 * (abs(m) + abs(t))
penalty_l2 = lambda_l2 * (m * m + t * t)
penalty_total = penalty_l1 + penalty_l2

# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

snapshot = Dict(
    "structure_clustering" => round6(clustering),
    "structure_modularity" => round6(modularity),
    "structure_shortest_path" => round6(shortest_path_to_goal),
    "structure_path_pressure" => round6(path_pressure),
    "stability_derivative" => round6(derivative),
    "stability_is_stable" => stable,
    "stability_score" => round6(stability_score),
    "regularization_l1" => round6(penalty_l1),
    "regularization_l2" => round6(penalty_l2),
    "regularization_total" => round6(penalty_total),
    "structure_compute_backend" => "julia",
    "structure_compute_path" => "primary",
)

println(JSON.json(Dict("structure_stability_snapshot" => snapshot)))
