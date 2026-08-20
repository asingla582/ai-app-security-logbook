import uuid

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from .auth import User, get_current_user
from .config import WEB_ORIGIN
from .routes_chat import router as chat_router
from .routes_notes import router as notes_router
from .routes_orgs import router as orgs_router

app = FastAPI(title="AI App Security Logbook API")

# Auth is via bearer token, not cookies, so credentials are not allowed and the
# origin is a specific host, never a wildcard.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[WEB_ORIGIN],
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Correlation-Id"],
)

app.include_router(orgs_router)
app.include_router(notes_router)
app.include_router(chat_router)


@app.middleware("http")
async def correlation_id(request: Request, call_next):
    cid = request.headers.get("X-Correlation-Id") or str(uuid.uuid4())
    request.state.correlation_id = cid
    response = await call_next(request)
    response.headers["X-Correlation-Id"] = cid
    return response


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/me")
def me(user: User = Depends(get_current_user)):
    return {"id": user.id, "email": user.email}
