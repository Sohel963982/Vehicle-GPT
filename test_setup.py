import time
import can, cantools, pandas, numpy, scipy, psutil
import ollama

print("Libraries imported OK")

start = time.perf_counter()
reply = ollama.chat(
    model="qwen2.5:0.5b",
    messages=[{"role": "user", "content": "Reply with exactly: READY"}],
    options={"temperature": 0, "num_predict": 10},
)
print("Model says:", reply["message"]["content"])
print(f"Time taken: {time.perf_counter() - start:.2f} seconds")