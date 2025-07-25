# File: main.py

import os
from fastapi import FastAPI
from dotenv import load_dotenv

from api.routers.drugs import router as drugs_router
from api.routers.allergy import router as allergy_router

# Load environment variables (so routers can pick them up if needed)
load_dotenv("api.env")

app = FastAPI(
    title="Drug Interaction API",
    version="1.0.0"
)

# Mount our two routers
app.include_router(drugs_router)
app.include_router(allergy_router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=9999,
        reload=True
    )
