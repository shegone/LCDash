from fastapi import FastAPI

app = FastAPI(
    title="LCDash",
    description="Logan County 911 Operations Dashboard",
    version="0.1.0"
)

@app.get("/")
def home():
    return {
        "application": "LCDash",
        "version": "0.1.0",
        "status": "Running"
    }