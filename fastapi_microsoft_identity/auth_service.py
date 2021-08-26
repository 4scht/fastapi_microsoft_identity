import httpx
from httpx import Response
from fastapi import Request
from functools import wraps
from jose import jwt
import fastapi

tenant_id=None
client_id=None

def initialize(tenant_id_, client_id_):
    global tenant_id, client_id
    tenant_id = tenant_id_
    client_id = client_id_

class AuthError(Exception):
    def __init__(self, error_msg:str, status_code:int):
        super().__init__(error_msg)

        self.error_msg = error_msg
        self.status_code = status_code

def get_token_auth_header(request: Request):
    auth = request.headers.get("Authorization", None)
    if not auth:
        raise AuthError("Authentication error: Authorization header is missing", 401)

    parts = auth.split()

    if parts[0].lower() != "bearer":
        raise AuthError("Authentication error: Authorization header must start with ' Bearer'", 401)
    elif len(parts) == 1:
        raise AuthError("Authentication error: Token not found", 401)
    elif len(parts) > 2:
        raise AuthError("Authentication error: Authorization header must be 'Bearer <token>'", 401)

    token = parts[1]
    return token

def validate_scope(required_scope:str, request: Request):
    has_valid_scope = False
    token = get_token_auth_header(request);
    unverified_claims = jwt.get_unverified_claims(token)
    if unverified_claims.get("scp"):
            token_scopes = unverified_claims["scp"].split()
            for token_scope in token_scopes:
                if token_scope == required_scope:
                    has_valid_scope = True
    else:
        raise AuthError("IDW10201: Neither scope or roles claim was found in the bearer token", 403)
    if not has_valid_scope:
        raise AuthError(f'IDW10203: The "scope" or "scp" claim does not contain scopes {required_scope} or was not found', 403)                

def requires_auth(f):
    @wraps(f)
    async def decorated(*args, **kwargs):
        try:
            token = get_token_auth_header(kwargs["request"])
            url = f'https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys'
            
            async with httpx.AsyncClient() as client:
                resp: Response = await client.get(url)
                if resp.status_code != 200:
                    raise AuthError("Problem with Azure AD discovery URL", status_code=404)

                jwks = resp.json()
                unverified_header = jwt.get_unverified_header(token)
                rsa_key = {}
                for key in jwks["keys"]:
                    if key["kid"] == unverified_header["kid"]:
                        rsa_key = {
                            "kty": key["kty"],
                            "kid": key["kid"],
                            "use": key["use"],
                            "n": key["n"],
                            "e": key["e"]
                        }
        except Exception:
            return fastapi.Response(content="Invalid_header: Unable to parse authentication", status_code= 401)
        if rsa_key:
            token_version = __get_token_version(token)
            if token_version == "1.0":
                __decode_JWT_v1(token, rsa_key, client_id, tenant_id)
            else:
                __decode_JWT_v2(token, rsa_key, client_id, tenant_id)
            return await f(*args, **kwargs)
        return fastapi.Response(content="Invalid header error: Unable to find appropriate key", status_code=401)
    return decorated

def __decode_JWT_v1(token, rsa_key, audience:str, tenandId:str):
    try:
        payload = jwt.decode(
            token,
            rsa_key,
            algorithms=["RS256"],
            audience=f'api://{audience}',
            issuer=f'https://sts.windows.net/{tenandId}/'
        )
    except jwt.ExpiredSignatureError:
        raise AuthError("Token error: The token has expired", 401)
    except jwt.JWTClaimsError:
        raise AuthError("Token error: Please check the audience and issuer", 401)
    except Exception:
        raise AuthError("Token error: Unable to parse authentication", 401)

def __decode_JWT_v2(token, rsa_key, audience:str, tenandId:str):
    try:
        payload = jwt.decode(
            token,
            rsa_key,
            algorithms=["RS256"],
            audience=audience,
            issuer=f'https://login.microsoftonline.com/{tenandId}/v2.0'
        )
    except jwt.ExpiredSignatureError:
        raise AuthError("Token error: The token has expired", 401)
    except jwt.JWTClaimsError:
        raise AuthError("Token error: Please check the audience and issuer", 401)
    except Exception:
        raise AuthError("Token error: Unable to parse authentication", 401)

def __get_token_version(token):
    unverified_claims = jwt.get_unverified_claims(token)
    if unverified_claims.get("ver"):
        return unverified_claims["ver"]   
    else:
        raise AuthError("Missing version claim from token. Unable to validate", 403)