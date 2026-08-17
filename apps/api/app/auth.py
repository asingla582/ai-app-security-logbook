import jwt
from fastapi import HTTPException, Request
from pydantic import BaseModel

from .config import SUPABASE_JWT_SECRET


class User(BaseModel):
    id: str
    email: str


def get_current_user(request: Request) -> User:
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = header.removeprefix("Bearer ")
    try:
        claims = jwt.decode(
            token,
            SUPABASE_JWT_SECRET,
            algorithms=["HS256"],
            audience="authenticated",
        )
    except jwt.PyJWTError:
        # expired, wrong signature, wrong audience, or malformed all land here —
        # the caller learns only that the token was rejected, never why. `from None`
        # keeps the underlying jwt error out of the response and traceback.
        raise HTTPException(status_code=401, detail="invalid token") from None
    return User(id=claims["sub"], email=claims.get("email", ""))
