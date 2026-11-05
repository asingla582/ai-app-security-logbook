import uuid

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from opentelemetry.context import Context
from opentelemetry.trace import Status, StatusCode

from .auth import User, get_current_user
from .config import WEB_ORIGIN
from .routes_actions import router as actions_router
from .routes_audit import router as audit_router
from .routes_chat import router as chat_router
from .routes_documents import router as documents_router
from .routes_notes import router as notes_router
from .routes_orgs import router as orgs_router
from .telemetry import client_link, configure_tracing, traced

configure_tracing()

app = FastAPI(title="AI App Security Logbook API")

# Auth is via bearer token, not cookies, so credentials are not allowed and the
# origin is a specific host, never a wildcard.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[WEB_ORIGIN],
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "traceparent"],
    expose_headers=["X-Correlation-Id"],
)

app.include_router(orgs_router)
app.include_router(notes_router)
app.include_router(chat_router)
app.include_router(documents_router)
app.include_router(actions_router)
app.include_router(audit_router)


@app.middleware("http")
async def correlation_id(request: Request, call_next):
    # Server-issued, always. A client-chosen id could file a request under another
    # tenant's incident, so an incoming X-Correlation-Id is ignored.
    cid = str(uuid.uuid4())
    request.state.correlation_id = cid
    # context=Context() starts a new trace whatever the client sent.
    with traced(
        f"HTTP {request.method}", context=Context(),
        links=client_link(request.headers.get("traceparent")),
        attributes={"http.request.method": request.method, "app.correlation_id": cid},
    ) as span:
        try:
            response = await call_next(request)
            status = response.status_code
        except Exception:
            status = 500
            raise
        finally:
            route = request.scope.get("route")
            if route is not None:
                span.update_name(f"HTTP {request.method} {route.path}")
                span.set_attribute("http.route", route.path)
            span.set_attribute("http.response.status_code", status)
            if status >= 500:
                span.set_status(Status(StatusCode.ERROR))
    response.headers["X-Correlation-Id"] = cid
    return response


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/me")
def me(user: User = Depends(get_current_user)):
    return {"id": user.id, "email": user.email}
