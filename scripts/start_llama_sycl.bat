@echo off
call "C:\Program Files (x86)\Intel\oneAPI\setvars.bat" intel64 >nul 2>&1
"C:\ai\LIARA\llama-builds-final\sycl-fp16-intel-arc\llama-server.exe" ^
    --host 127.0.0.1 ^
    --port 8000 ^
    --model "C:\ai\models\llama\models\qwen2.5-1.5b-instruct-q5_k_m.gguf" ^
    --threads 22 ^
    --ctx-size 8192 ^
    --n-gpu-layers 99 ^
    -ngl 99
