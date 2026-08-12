# turbine_power.jl — Turbine shaft-power calculator
#
# Input  (stdin, JSON):
#   shaft_speed_rpm  :: Float64  — rotational speed in RPM
#   torque_nm        :: Float64  — torque in Newton-metres
#
# Output (stdout, JSON):
#   power_kw         :: Float64  — shaft power in kilowatts
#   power_w          :: Float64  — shaft power in watts
#   angular_velocity :: Float64  — ω in rad/s
#
# Formula:  P = τ · ω   where ω = 2π · n / 60
#
# Usage:
#   echo '{"shaft_speed_rpm": 1500, "torque_nm": 200}' | julia --startup-file=no turbine_power.jl

import JSON

input_raw = read(stdin, String)
data = JSON.parse(input_raw)

shaft_speed_rpm  = Float64(data["shaft_speed_rpm"])
torque_nm        = Float64(data["torque_nm"])

omega   = 2.0 * pi * shaft_speed_rpm / 60.0   # rad/s
power_w = torque_nm * omega                    # Watts
power_kw = power_w / 1000.0                   # kW

result = Dict(
    "power_kw"         => round(power_kw,         digits=4),
    "power_w"          => round(power_w,           digits=4),
    "angular_velocity" => round(omega,             digits=6),
    "shaft_speed_rpm"  => shaft_speed_rpm,
    "torque_nm"        => torque_nm,
)

print(JSON.json(result))
