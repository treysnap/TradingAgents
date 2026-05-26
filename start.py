import os
import uvicorn

port = int(os.environ.get("PORT", 8000))
print(f"[start.py] Starting on PORT={port}", flush=True)
uvicorn.run("api:app", host="0.0.0.0", port=port)
