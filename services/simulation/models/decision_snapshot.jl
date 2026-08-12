import JSON

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

raw = read(stdin, String)
data = JSON.parse(raw)

total_cost = max(0.0, as_float(get(data, "total_cost", 0.0), 0.0))
actionable_risk = max(0.0, as_float(get(data, "actionable_risk", 0.0), 0.0))
context_entropy = clip(as_float(get(data, "context_entropy", 0.0), 0.0), 0.0, 1.0)
utility_discounted = as_float(get(data, "utility_discounted", 0.0), 0.0)
stability_score = clip(as_float(get(data, "stability_score", 1.0), 1.0), 0.0, 1.0)
regularization_total = max(0.0, as_float(get(data, "regularization_total", 0.0), 0.0))
path_pressure = clip(as_float(get(data, "path_pressure", 0.0), 0.0), 0.0, 1.0)
mode_floor = String(get(data, "mode_floor", "advisory"))
repair_preferred = Bool(get(data, "repair_preferred", false))
soft_risk_max = max(1e-6, as_float(get(data, "soft_risk_max", 5.0), 5.0))
hard_risk_max = max(soft_risk_max, as_float(get(data, "hard_risk_max", 8.0), 8.0))

objective_scores = Dict(
    "cost" => round6(1.0 / (1.0 + total_cost)),
    "risk" => round6(1.0 - clip(actionable_risk / hard_risk_max, 0.0, 1.0)),
    "uncertainty" => round6(1.0 - context_entropy),
    "utility" => round6(clip((utility_discounted + 10.0) / 20.0, 0.0, 1.0)),
    "stability" => round6(stability_score),
    "regularization" => round6(1.0 / (1.0 + regularization_total)),
    "structure" => round6(1.0 - path_pressure),
    "score" => (repair_preferred || mode_floor in ["soft", "hard"]) ? 0.0 : 1.0,
)

weak_flags = Dict(
    "risk" => actionable_risk > soft_risk_max,
    "utility" => utility_discounted < 0.0,
    "uncertainty" => context_entropy > 0.7,
    "stability" => stability_score < 0.5,
    "regularization" => regularization_total > 1.0,
    "structure" => path_pressure > 0.75,
    "score" => (repair_preferred || mode_floor in ["soft", "hard"]),
)
weak_objectives = [k for (k, v) in weak_flags if v]
weak_count = length(weak_objectives)

deficits = Dict(
    "risk" => actionable_risk > hard_risk_max ? 2.0 : (1.0 - objective_scores["risk"]),
    "utility" => (1.0 - objective_scores["utility"]) + (utility_discounted < 0.0 ? 0.25 : 0.0),
    "uncertainty" => 1.0 - objective_scores["uncertainty"],
    "stability" => 1.0 - objective_scores["stability"],
    "regularization" => 1.0 - objective_scores["regularization"],
    "structure" => 1.0 - objective_scores["structure"],
    "score" => repair_preferred ? 1.2 : (mode_floor == "hard" ? 1.0 : (mode_floor == "soft" ? 0.75 : 0.0)),
    "cost" => 1.0 - objective_scores["cost"],
)

dominant_objective = first(sort(collect(deficits), by=x -> x[2], rev=true))[1]

pareto_status = weak_count == 0 ? "efficient" : (weak_count >= 3 ? "dominated" : "tradeoff")

if actionable_risk > hard_risk_max || mode_floor == "hard"
    recommended_mode = "hard"
elseif actionable_risk > soft_risk_max || utility_discounted < 0.0 || stability_score < 0.5 || repair_preferred || mode_floor == "soft" || weak_count >= 2
    recommended_mode = "soft"
else
    recommended_mode = "advisory"
end

recommended_action_map = Dict(
    "risk" => (recommended_mode == "hard" ? "stop_agent_mode" : "reduce_context_window"),
    "utility" => "reduce_exploration",
    "uncertainty" => "increase_validation_strictness",
    "stability" => "stabilize_reasoning_chain",
    "regularization" => "reduce_memory_pressure",
    "structure" => "narrow_goal_path",
    "score" => (repair_preferred ? "trigger_repair_loop" : "increase_validation_strictness"),
    "cost" => "reduce_exploration",
)
recommended_action = get(recommended_action_map, dominant_objective, "reduce_exploration")
resolution_basis = dominant_objective in ["risk", "utility", "score"] ? dominant_objective : "multi_objective"

snapshot = Dict(
    "decision_pareto_status" => pareto_status,
    "decision_dominant_objective" => dominant_objective,
    "decision_recommended_mode" => recommended_mode,
    "decision_recommended_action" => recommended_action,
    "decision_resolution_basis" => resolution_basis,
    "decision_objectives" => objective_scores,
    "decision_weak_objectives" => weak_objectives,
    "decision_compute_backend" => "julia",
    "decision_compute_path" => "primary",
)

println(JSON.json(Dict("decision_snapshot" => snapshot)))
