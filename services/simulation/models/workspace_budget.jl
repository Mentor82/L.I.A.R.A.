import JSON

function asfloat(value, default)
    if value isa Number
        return Float64(value)
    end
    try
        return parse(Float64, string(value))
    catch
        return Float64(default)
    end
end
round6(value) = round(Float64(value), digits=6)

data = JSON.parse(read(stdin, String))
depth = max(0, Int(round(asfloat(get(data, "depth", 0), 0))))
tokens = max(0, Int(round(asfloat(get(data, "tokens", 0), 0))))
tools = max(0, Int(round(asfloat(get(data, "tools", 0), 0))))
entropy = clamp(asfloat(get(data, "entropy", 0.0), 0.0), 0.0, 1.0)
branching = max(0.0, asfloat(get(data, "branching_factor", 1.0), 1.0))
goal_progress = asfloat(get(data, "goal_progress", 0.0), 0.0)
policy_risk = max(0.0, asfloat(get(data, "policy_risk", 0.0), 0.0))

alpha = asfloat(get(data, "alpha", 0.35), 0.35)
beta = asfloat(get(data, "beta", 0.00025), 0.00025)
gamma = asfloat(get(data, "gamma", 0.75), 0.75)
delta = asfloat(get(data, "delta", 1.5), 1.5)
cost_soft = asfloat(get(data, "cost_soft", 5.0), 5.0)
cost_hard = asfloat(get(data, "cost_hard", 8.0), 8.0)
risk_soft = asfloat(get(data, "risk_soft", 5.0), 5.0)
risk_hard = asfloat(get(data, "risk_hard", 8.0), 8.0)

depth_cost = alpha * depth
token_cost = beta * tokens
tool_cost = gamma * tools
entropy_cost = delta * entropy
cost_total = depth_cost + token_cost + tool_cost + entropy_cost
utility = goal_progress - cost_total
confidence_adjusted_utility = utility * (1.0 - entropy)
rds_v2 = log2(1 + max(0.0, depth * branching)) + (0.8 * entropy)
uncertainty_risk = entropy
complexity_risk = rds_v2
risk_total = (0.5 * policy_risk) + (0.2 * uncertainty_risk) + (0.3 * complexity_risk)
actionable_risk = (0.5 * policy_risk) + (0.2 * uncertainty_risk)

if actionable_risk > risk_hard
    mode, release, reason = "hard", false, "actionable_risk_exceeds_hard_max"
elseif cost_total > cost_hard
    mode, release, reason = "hard", false, "cost_exceeds_hard_max"
elseif actionable_risk > risk_soft || cost_total > cost_soft
    mode = "soft"
    release = confidence_adjusted_utility > 0.0
    reason = release ? "positive_utility_under_soft_control" : "negative_utility_under_soft_control"
else
    mode, release, reason = "advisory", true, "within_calibrated_budget"
end

decision = Dict(
    "cost_components" => Dict(
        "depth" => round6(depth_cost),
        "tokens" => round6(token_cost),
        "tools" => round6(tool_cost),
        "entropy" => round6(entropy_cost),
    ),
    "cost_total" => round6(cost_total),
    "goal_progress" => round6(goal_progress),
    "utility" => round6(utility),
    "confidence_adjusted_utility" => round6(confidence_adjusted_utility),
    "rds_v2" => round6(rds_v2),
    "risk_total" => round6(risk_total),
    "actionable_risk" => round6(actionable_risk),
    "control_mode" => mode,
    "release" => release,
    "reason" => reason,
)

print(JSON.json(Dict("decision" => decision)))
