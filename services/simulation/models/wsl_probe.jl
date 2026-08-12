using JSON3
input = JSON3.read(read(stdin, String))
println(JSON3.write(Dict("ok"=>true, "sum"=> (input["a"] + input["b"]))))
