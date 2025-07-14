from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel
import uvicorn
import threading
import time

app = FastAPI()

class LoginRequest(BaseModel):
    username: str
    password: str

class OTPRequest(BaseModel):
    userId: str
    txnType: str

@app.post("/token")
async def post_token(request: LoginRequest):
    if request.username == "test" and request.password == "password":
        return {
            "access_token": "real_auth_token_123",
            "token_type": "bearer",
            "expires_in": 3600,
            "user": {"userId": "98765"}
        }
    raise HTTPException(status_code=400, detail="Invalid credentials")

@app.post("/sendotp")
async def send_otp(request: OTPRequest, request_obj: Request):
    authorization = request_obj.headers.get("Authorization")
    if authorization == "real_auth_token_123" and request.userId == "98765" and request.txnType == "GEN":
        return {"txnType": "GEN"}
    raise HTTPException(status_code=400, detail="Invalid OTP request")


class Server(uvicorn.Server):
    def __init__(self, config: uvicorn.Config):
        super().__init__(config)
        self.started_event = threading.Event()

    async def startup(self, sockets=None):
        await super().startup(sockets=sockets)
        self.started_event.set()

def run_single_server():
    config = uvicorn.Config(app, host="127.0.0.1", port=8001, log_level="info")
    server = Server(config)
    thread = threading.Thread(target=server.run)
    thread.start()
    server.started_event.wait(30) # Wait for server to start
    time.sleep(0.1) # Give the server a moment to bind to the port
    return server, thread


if __name__ == "__main__":
    server, thread = run_single_server()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        server.should_exit = True
        thread.join()
