import hashlib
import hmac
import json
import os
import time

import boto3
import pymysql
from botocore.config import Config


# ==========================================================
# CONFIGURATION
# ==========================================================

AWS_CONFIG = Config(
    connect_timeout=2,
    read_timeout=3,
    retries={"max_attempts": 1, "mode": "standard"},
)

ssm = boto3.client("ssm", config=AWS_CONFIG)

DB_NAME_PARAMETER = os.environ["DB_NAME_PARAMETER"]
DB_ENDPOINT_PARAMETER = os.environ["DB_ENDPOINT_PARAMETER"]
DB_PORT_PARAMETER = os.environ["DB_PORT_PARAMETER"]
DB_USERNAME_PARAMETER = os.environ["DB_USERNAME_PARAMETER"]
DB_PASSWORD_PARAMETER = os.environ["DB_PASSWORD_PARAMETER"]
ADMIN_TOKEN_PARAMETER = os.environ["ADMIN_TOKEN_PARAMETER"]

_PARAMETER_CACHE = None
_PARAMETER_CACHE_AT = 0.0
_PARAMETER_CACHE_TTL_SECONDS = 300
_ADMIN_TOKEN_CACHE = None


def log(level, message, **details):
    print(json.dumps({
        "level": level,
        "service": "cloudmart-authorizer",
        "message": message,
        **details,
    }, default=str))


def make_stage_wildcard(method_arn):
    try:
        parts = method_arn.split(":")
        if len(parts) < 6:
            return method_arn

        resource = parts[5].split("/")
        if len(resource) < 2:
            return method_arn

        api_id = resource[0]
        stage = resource[1]

        return (
            f"{parts[0]}:{parts[1]}:{parts[2]}:{parts[3]}:"
            f"{parts[4]}:{api_id}/{stage}/*/*"
        )
    except Exception:
        return method_arn


def generate_policy(principal_id, effect, method_arn, context=None):
    policy = {
        "principalId": str(principal_id),
        "policyDocument": {
            "Version": "2012-10-17",
            "Statement": [{
                "Action": "execute-api:Invoke",
                "Effect": effect,
                "Resource": make_stage_wildcard(method_arn),
            }],
        },
    }

    if context:
        policy["context"] = {
            str(k): str(v)
            for k, v in context.items()
        }

    return policy


def load_database_parameters():
    global _PARAMETER_CACHE, _PARAMETER_CACHE_AT

    now = time.monotonic()
    if (
        _PARAMETER_CACHE is not None
        and (now - _PARAMETER_CACHE_AT) < _PARAMETER_CACHE_TTL_SECONDS
    ):
        return _PARAMETER_CACHE

    names = [
        DB_NAME_PARAMETER,
        DB_ENDPOINT_PARAMETER,
        DB_PORT_PARAMETER,
        DB_USERNAME_PARAMETER,
        DB_PASSWORD_PARAMETER,
    ]

    result = ssm.get_parameters(
        Names=names,
        WithDecryption=True,
    )

    returned = {
        item["Name"]: item["Value"]
        for item in result.get("Parameters", [])
    }

    missing = [name for name in names if name not in returned]
    if missing:
        raise RuntimeError(
            "Missing CloudMart DB parameters: " + ", ".join(missing)
        )

    _PARAMETER_CACHE = {
        "database": returned[DB_NAME_PARAMETER],
        "host": returned[DB_ENDPOINT_PARAMETER],
        "port": int(returned[DB_PORT_PARAMETER]),
        "username": returned[DB_USERNAME_PARAMETER],
        "password": returned[DB_PASSWORD_PARAMETER],
    }
    _PARAMETER_CACHE_AT = now
    return _PARAMETER_CACHE


def get_admin_token():
    global _ADMIN_TOKEN_CACHE

    if _ADMIN_TOKEN_CACHE is not None:
        return _ADMIN_TOKEN_CACHE

    result = ssm.get_parameter(
        Name=ADMIN_TOKEN_PARAMETER,
        WithDecryption=True,
    )

    token = str(result["Parameter"]["Value"] or "").strip()
    if not token:
        raise RuntimeError("Admin authentication token is empty")

    _ADMIN_TOKEN_CACHE = token
    return token


def get_connection():
    db = load_database_parameters()

    return pymysql.connect(
        host=db["host"],
        port=db["port"],
        user=db["username"],
        password=db["password"],
        database=db["database"],
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=3,
        read_timeout=4,
        write_timeout=4,
        autocommit=True,
    )


def extract_bearer_token(event):
    raw = event.get("authorizationToken", "")
    if not isinstance(raw, str):
        return None

    parts = raw.strip().split()
    if len(parts) != 2:
        return None

    scheme, token = parts
    if scheme.lower() != "bearer" or not token:
        return None

    return token.strip()


def hash_token(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def authenticate_customer(token):
    """
    A bearer token is a credential, not a customer primary key.

    Multiple ACTIVE customers may intentionally share the same token.
    The authorizer therefore validates only that at least one ACTIVE
    customer has the hash and returns the hash itself in context.
    Service Lambdas combine that hash with the URL customer_id.
    """
    token_hash = hash_token(token)
    connection = None

    try:
        connection = get_connection()

        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT EXISTS(
                    SELECT 1
                    FROM customers
                    WHERE bearer_token = %s
                      AND status = 'ACTIVE'
                      AND deleted_at IS NULL
                ) AS token_valid
                """,
                (token_hash,),
            )

            row = cursor.fetchone()

        if not row or int(row["token_valid"]) != 1:
            return None

        return {
            "role": "CUSTOMER",
            "token_hash": token_hash,
        }

    except Exception as exc:
        log(
            "ERROR",
            "Customer authentication failed",
            error_type=type(exc).__name__,
            error=str(exc),
        )
        return None

    finally:
        if connection is not None:
            connection.close()


def authenticate_admin(token):
    try:
        configured = get_admin_token()

        if hmac.compare_digest(
            token.encode("utf-8"),
            configured.encode("utf-8"),
        ):
            return {
                "role": "ADMIN",
                "token_hash": "",
            }

        return None

    except Exception as exc:
        log(
            "ERROR",
            "Admin authentication failed",
            error_type=type(exc).__name__,
            error=str(exc),
        )
        return None


def lambda_handler(event, context):
    request_id = getattr(context, "aws_request_id", "unknown")
    method_arn = event.get("methodArn", "*")

    token = extract_bearer_token(event)

    log(
        "INFO",
        "Authentication request received",
        request_id=request_id,
        authorizer_type=event.get("type", "UNKNOWN"),
    )

    if token is None:
        log(
            "WARN",
            "Authentication denied",
            request_id=request_id,
            reason="INVALID_BEARER_TOKEN_FORMAT",
        )
        return generate_policy(
            "anonymous",
            "Deny",
            method_arn,
        )

    customer_identity = authenticate_customer(token)
    if customer_identity is not None:
        return generate_policy(
            principal_id=customer_identity["token_hash"],
            effect="Allow",
            method_arn=method_arn,
            context=customer_identity,
        )

    admin_identity = authenticate_admin(token)
    if admin_identity is not None:
        return generate_policy(
            principal_id="admin",
            effect="Allow",
            method_arn=method_arn,
            context=admin_identity,
        )

    log(
        "WARN",
        "Authentication denied",
        request_id=request_id,
        reason="INVALID_TOKEN",
    )

    return generate_policy(
        "anonymous",
        "Deny",
        method_arn,
    )
