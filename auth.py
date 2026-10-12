"""Validate Volcengine Identity JWTs and resolve the authenticated user."""

from __future__ import annotations

import os
from functools import lru_cache

import jwt
from fastapi import HTTPException, Request


def _issuer() -> str:
    issuer = os.getenv("OIDC_ISSUER", "").strip().rstrip("/")
    if issuer:
        return issuer

    user_pool_id = os.getenv("OIDC_USER_POOL_ID", "").strip()
    if not user_pool_id:
        raise RuntimeError("OIDC_USER_POOL_ID or OIDC_ISSUER is required")
    if not user_pool_id.startswith("userpool-"):
        user_pool_id = f"userpool-{user_pool_id}"
    region = os.getenv("OIDC_REGION", "cn-beijing").strip() or "cn-beijing"
    return f"https://{user_pool_id}.userpool.auth.id.{region}.volces.com"


def _audience() -> str:
    audience = os.getenv("OIDC_AUDIENCE", "").strip()
    if not audience:
        raise RuntimeError("OIDC_AUDIENCE is required")
    return audience


@lru_cache(maxsize=4)
def _jwks_client(issuer: str) -> jwt.PyJWKClient:
    jwks_url = os.getenv("OIDC_JWKS_URL", f"{issuer}/keys")
    return jwt.PyJWKClient(jwks_url)


def authenticated_user_id(request: Request) -> str:
    """Verify the bearer JWT and return its stable `sub` claim."""
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(status_code=401, detail="Bearer access token is required")

    issuer = _issuer()
    try:
        signing_key = _jwks_client(issuer).get_signing_key_from_jwt(token.strip())
        claims = jwt.decode(
            token.strip(),
            signing_key.key,
            algorithms=["RS256"],
            issuer=issuer,
            audience=_audience(),
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="Invalid Identity JWT") from exc

    user_id = str(claims.get("sub") or "").strip()
    if not user_id:
        raise HTTPException(status_code=401, detail="Identity JWT has no sub claim")
    return user_id
