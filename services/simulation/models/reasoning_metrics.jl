import JSON

function as_float(value, default)
    if value === nothing
        return Float64(default)
    end
    if value isa Number
        return Float64(value)
    end
    if value isa AbstractString
        try
            return parse(Float64, value)
        catch
            return Float64(default)
        end
    end
    return Float64(default)
end

function as_int(value, default)
    return Int(round(as_float(value, default)))
end

function round6(x)
    return round(Float64(x), digits=6)
end

raw = read(stdin, String)
data = JSON.parse(raw)
inputs = get(data, "inputs", Dict{String, Any}())
config = get(data, "config", Dict{String, Any}())

depth = max(0, as_int(get(inputs, "depth", 1), 1))
branching_factor_avg = max(0.0, as_float(get(inputs, "branching_factor_avg", 1.0), 1.0))
memory_items = max(0, as_int(get(inputs, "memory_items", 0), 0))
tool_calls = max(0, as_int(get(inputs, "tool_calls", 0), 0))
token_estimate = max(0, as_int(get(inputs, "token_estimate", 0), 0))
context_entropy = max(0.0, as_float(get(inputs, "context_entropy", 0.0), 0.0))
goal_progress = as_float(get(inputs, "goal_progress", 0.0), 0.0)
policy_risk = max(0.0, min(1.0, as_float(get(inputs, "policy_risk", 0.0), 0.0)))

k_depth = as_float(get(config, "k_depth", 1.0), 1.0)
k_memory = as_float(get(config, "k_memory", 1.0), 1.0)
k_tool = as_float(get(config, "k_tool", 2.0), 2.0)
k_entropy = as_float(get(config, "k_entropy", 1.5), 1.5)
lambda_entropy = as_float(get(config, "lambda_entropy", 0.8), 0.8)
w_policy = as_float(get(config, "w_policy", 0.5), 0.5)
w_uncertainty = as_float(get(config, "w_uncertainty", 0.2), 0.2)
w_complexity = as_float(get(config, "w_complexity", 0.3), 0.3)
soft_risk_max = as_float(get(config, "soft_risk_max", 5.0), 5.0)
hard_risk_max = as_float(get(config, "hard_risk_max", 8.0), 8.0)

depth_cost = k_depth * log2(1 + depth)
memory_cost = k_memory * log2(1 + memory_items)
tool_cost = k_tool * tool_calls
entropy_cost = k_entropy * context_entropy
total_cost = depth_cost + memory_cost + tool_cost + entropy_cost

rds_v2 = log2(1 + max(0.0, depth * branching_factor_avg)) + (lambda_entropy * context_entropy)
uncertainty_risk = context_entropy
complexity_risk = max(0.0, rds_v2)
total_risk = (w_policy * policy_risk) + (w_uncertainty * uncertainty_risk) + (w_complexity * complexity_risk)
actionable_risk = (w_policy * policy_risk) + (w_uncertainty * uncertainty_risk)
utility = goal_progress - total_cost

metrics = Dict(
    "depth" => depth,
    "branching_factor_avg" => round6(branching_factor_avg),
    "memory_items" => memory_items,
    "tool_calls" => tool_calls,
    "token_estimate" => token_estimate,
    "context_entropy" => round6(context_entropy),
    "goal_progress" => round6(goal_progress),
    "policy_risk" => round6(policy_risk),
    "depth_cost" => round6(depth_cost),
    "memory_cost" => round6(memory_cost),
    "tool_cost" => round6(tool_cost),
    "entropy_cost" => round6(entropy_cost),
    "total_cost" => round6(total_cost),
    "reasoning_cost" => round6(total_cost),
    "rds_v2" => round6(rds_v2),
    "uncertainty_risk" => round6(uncertainty_risk),
    "complexity_risk" => round6(complexity_risk),
    "total_risk" => round6(total_risk),
    "risk_total" => round6(total_risk),
    "actionable_risk" => round6(actionable_risk),
    "utility" => round6(utility),
    "should_soft_limit" => (actionable_risk > soft_risk_max),
    "should_hard_block" => (actionable_risk > hard_risk_max),
    "rds_mode" => "diagnostic",
    "mode" => "advisory"
)

print(JSON.json(Dict("metrics" => metrics)))
