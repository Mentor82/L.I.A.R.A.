import JSON

function output_result(text)
    print(JSON.json(Dict("output" => text)))
end

function try_parse_float(text)
    try
        return parse(Float64, text)
    catch
        return nothing
    end
end

function format_number(value)
    if abs(value - round(value)) < 1e-9
        return string(Int(round(value)))
    end
    return string(round(value, digits=6))
end

function normalize_math_operators(query)
    return replace(
        query,
        r"\bmultipliziert\s+mit\b" => "*",
        r"\bmultiplied\s+by\b" => "*",
        r"\bgeteilt\s+durch\b" => "/",
        r"\bdivided\s+by\b" => "/",
        r"\bmal\b" => "*",
        r"\btimes\b" => "*",
        r"\bplus\b" => "+",
        r"\bminus\b" => "-",
        "×" => "*",
        "÷" => "/",
    )
end

function eval_simple_expression(query)
    expr_match = match(r"\d[\d\s\+\-\*\/\(\)\.]*[\+\-\*\/][\d\s\+\-\*\/\(\)\.]*\d", query)
    if expr_match === nothing
        return nothing
    end

    expr = replace(expr_match.match, " " => "")
    if occursin("//", expr)
        return nothing
    end

    try
        result = Base.include_string(
            Module(),
            "value = " * expr * "\nprint(value)",
        )
        return nothing
    catch
        return nothing
    end
end

function safe_eval_expression(query)
    expr_match = match(r"\d[\d\s\+\-\*\/\(\)\.]*[\+\-\*\/][\d\s\+\-\*\/\(\)\.]*\d", query)
    if expr_match === nothing
        return nothing
    end
    expr = replace(expr_match.match, " " => "")
    if !occursin(r"^[0-9\+\-\*\/\(\)\.]+$", expr)
        return nothing
    end
    try
        value = Base.include_string(Module(), "result = " * expr * "\nresult")
        return format_number(Float64(value))
    catch
        return nothing
    end
end

function factorial_value(n)
    if n < 0
        return nothing
    end
    value = big(1)
    for i in 2:n
        value *= i
    end
    return string(value)
end

function fibonacci_value(n)
    if n <= 0
        return "0"
    elseif n == 1
        return "1"
    end
    a = big(0)
    b = big(1)
    for _ in 2:n
        a, b = b, a + b
    end
    return string(b)
end

input_raw = read(stdin, String)
data = JSON.parse(input_raw)
query = normalize_math_operators(lowercase(strip(String(get(data, "query", "")))))

expr_result = safe_eval_expression(query)
if expr_result !== nothing
    output_result(expr_result)
    exit()
end

c2f = match(r"(\d+(?:\.\d+)?)\s*(?:grad\s*)?celsius\s*(?:in|to|nach|zu)\s*fahrenheit", query)
if c2f !== nothing
    c = parse(Float64, c2f.captures[1])
    output_result("$(format_number(c))°C = $(format_number(c * 9 / 5 + 32))°F")
    exit()
end

f2c = match(r"(\d+(?:\.\d+)?)\s*(?:grad\s*)?fahrenheit\s*(?:in|to|nach|zu)\s*celsius", query)
if f2c !== nothing
    f = parse(Float64, f2c.captures[1])
    output_result("$(format_number(f))°F = $(format_number((f - 32) * 5 / 9))°C")
    exit()
end

c2k = match(r"(\d+(?:\.\d+)?)\s*(?:grad\s*)?celsius\s*(?:in|to|nach|zu)\s*kelvin", query)
if c2k !== nothing
    c = parse(Float64, c2k.captures[1])
    output_result("$(format_number(c))°C = $(format_number(c + 273.15)) K")
    exit()
end

sqrt_match = match(r"(?:sqrt|wurzel|square root)[^\d]*(\d+(?:\.\d+)?)", query)
if sqrt_match !== nothing
    value = parse(Float64, sqrt_match.captures[1])
    output_result(format_number(sqrt(value)))
    exit()
end

fact_match = match(r"(?:factorial|fakultät|fakultaet)[^\d]*(\d+)", query)
if fact_match !== nothing
    value = factorial_value(parse(Int, fact_match.captures[1]))
    if value !== nothing
        output_result(value)
        exit()
    end
end

fib_match = match(r"fibonacci[^\d]*(\d+)", query)
if fib_match !== nothing
    output_result(fibonacci_value(parse(Int, fib_match.captures[1])))
    exit()
end

byte_match = match(r"(\d+(?:\.\d+)?)\s*(gb|mb|kb|tb|bytes?)\s*(?:in|to|nach|zu)\s*(gb|mb|kb|tb|bytes?)", query)
if byte_match !== nothing
    value = parse(Float64, byte_match.captures[1])
    src = replace(byte_match.captures[2], "bytes" => "byte")
    dst = replace(byte_match.captures[3], "bytes" => "byte")
    units = Dict("byte" => 1.0, "kb" => 1024.0, "mb" => 1024.0^2, "gb" => 1024.0^3, "tb" => 1024.0^4)
    converted = value * units[src] / units[dst]
    output_result("$(format_number(value)) $(src) = $(format_number(converted)) $(dst)")
    exit()
end

output_result("Konnte die Anfrage nicht automatisch mit Julia auswerten.")
