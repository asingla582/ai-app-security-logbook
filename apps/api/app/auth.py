import jwt
from fastapi import HTTPException, Request
from jwt import PyJWKClient
from pydantic import BaseModel

from .config import SUPABASE_URL

# Supabase signs access tokens asymmetrically (ES256) and publishes the public
# keys at the JWKS endpoint. We verify signatures against those keys and never
# hold a shared secret — the API cannot mint tokens, only validate them.
_jwks = PyJWKClient(f"{SUPABASE_URL}/auth/v1/.well-known/jwks.json")


class User(BaseModel):
    id: str
    email: str


def get_current_user(request: Request) -> User:
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = header.removeprefix("Bearer ")
    try:
        signing_key = _jwks.get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["ES256", "RS256"],
            audience="authenticated",
        )
    except (jwt.PyJWTError, jwt.PyJWKClientError):
        # bad signature, expired, wrong audience, unknown key, or malformed all
        # land here — the caller learns only that the token was rejected, never why
        raise HTTPException(status_code=401, detail="invalid token") from None
    return User(id=claims["sub"], email=claims.get("email", ""))
