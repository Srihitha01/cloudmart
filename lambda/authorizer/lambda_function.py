import os
import json
import hashlib
import logging
import hmac

import boto3
import pymysql
from botocore.config import Config


# ==========================================================
# LOGGING
# ==========================================================

logger = logging.getLogger()
logger.setLevel(logging.INFO)


# ==========================================================
# AWS CLIENT CONFIG
#
# The authorizer must fail fast instead of consuming the
# entire API Gateway timeout when SSM is temporarily slow.
# ==========================================================

AWS_CONFIG = Config(
    connect_timeout=2,
    read_timeout=3,
    retries={
        "max_attempts": 1,
        "mode": "standard",
    },
)

ssm = boto3.client(
    "ssm",
    config=AWS_CONFIG,
)


# ==========================================================
# ENVIRONMENT
# ==========================================================

DB_ENDPOINT_PARAMETER = os.environ["DB_ENDPOINT_PARAMETER"]
DB_PORT_PARAMETER = os.environ["DB_PORT_PARAMETER"]
DB_NAME_PARAMETER = os.environ["DB_NAME_PARAMETER"]
DB_USERNAME_PARAMETER = os.environ["DB_USERNAME_PARAMETER"]
DB_PASSWORD_PARAMETER = os.environ["DB_PASSWORD_PARAMETER"]

ADMIN_TOKEN_PARAMETER = os.environ["ADMIN_TOKEN_PARAMETER"]


# ==========================================================
# WARM-START CACHE
#
# Lambda containers can be reused. Cache the SSM database
# configuration and admin token so every request does not
# make six network calls.
# ==========================================================

_PARAMETER_CACHE = {}
_ADMIN_TOKEN_CACHE = None


# ==========================================================
# JSON LOGGING
# ==========================================================

def log_event(level, message, **details):
    record = {
        "service": "cloudmart-lambda-authorizer",
        "level": level,
        "message": message,
        **details,
    }

    getattr(logger, level.lower())(
        json.dumps(record, default=str)
    )


# ==========================================================
# RESPONSE / POLICY
# ==========================================================

def generate_policy(
    principal_id,
    effect,
    method_arn,
    context=None,
):
    """
    The authorizer authenticates the caller.

    We return a stage-wide Allow policy for an authenticated
    identity. Route/business permissions are enforced again
    by the Product / Customer / Order Lambdas.

    Stage-wide policy also avoids authorizer-cache problems
    where a policy generated for one method would otherwise
    be reused for a different method.
    """

    policy_resource = make_stage_wildcard(method_arn)

    policy = {
        "principalId": str(principal_id),
        "policyDocument": {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Action": "execute-api:Invoke",
                    "Effect": effect,
                    "Resource": policy_resource,
                }
            ],
        },
    }

    if context:
        policy["context"] = {
            str(key): str(value)
            for key, value in context.items()
        }

    return policy


def allow(principal_id, method_arn, identity):
    return generate_policy(
        principal_id=principal_id,
        effect="Allow",
        method_arn=method_arn,
        context=identity,
    )


def deny(principal_id, method_arn):
    return generate_policy(
        principal_id=principal_id,
        effect="Deny",
        method_arn=method_arn,
    )


def make_stage_wildcard(method_arn):
    """
    Example:
      arn:aws:execute-api:ap-south-1:123456789012:apiid/dev/POST/foo

    becomes:
      arn:aws:execute-api:ap-south-1:123456789012:apiid/dev/*/*
    """

    try:
        parts = method_arn.split(":")

        if len(parts) < 6:
            return method_arn

        execute_api_part = parts[5]

        api_parts = execute_api_part.split("/")

        if len(api_parts) < 2:
            return method_arn

        api_id = api_parts[0]
        stage = api_parts[1]

        return (
            f"{parts[0]}:{parts[1]}:{parts[2]}:"
            f"{parts[3]}:{parts[4]}:{api_id}/{stage}/*/*"
        )

    except Exception:
        return method_arn


# ==========================================================
# TOKEN EXTRACTION
# ==========================================================

def extract_bearer_token(event):
    """
    API Gateway TOKEN authorizer normally sends:

        authorizationToken = "Bearer <token>"

    We also support lower-case "bearer".
    """

    authorization_header = event.get(
        "authorizationToken",
        "",
    )

    if not isinstance(
        authorization_header,
        str,
    ):
        return None

    parts = authorization_header.strip().split()

    if len(parts) != 2:
        return None

    scheme, token = parts

    if scheme.lower() != "bearer":
        return None

    token = token.strip()

    if not token:
        return None

    return token


# ==========================================================
# TOKEN HASHING
#
# IMPORTANT:
# The raw bearer token is NEVER compared with the database.
# We hash the supplied token and compare that SHA-256 hash
# with customers.bearer_token.
# ==========================================================

def hash_token(token):
    return hashlib.sha256(
        token.encode("utf-8")
    ).hexdigest()


# ==========================================================
# SSM PARAMETER LOADER
# ==========================================================

def load_database_parameters():
    """
    Load all five DB parameters in ONE SSM request.

    Cached for the lifetime of the warm Lambda container.
    """

    global _PARAMETER_CACHE

    if _PARAMETER_CACHE:
        return _PARAMETER_CACHE

    names = [
        DB_ENDPOINT_PARAMETER,
        DB_PORT_PARAMETER,
        DB_NAME_PARAMETER,
        DB_USERNAME_PARAMETER,
        DB_PASSWORD_PARAMETER,
    ]

    log_event(
        "INFO",
        "Loading database parameters from SSM",
    )

    result = ssm.get_parameters(
        Names=names,
        WithDecryption=True,
    )

    returned = {
        item["Name"]: item["Value"]
        for item in result.get("Parameters", [])
    }

    missing = [
        name
        for name in names
        if name not in returned
    ]

    if missing:
        raise RuntimeError(
            "Missing required SSM database parameters: "
            + ", ".join(missing)
        )

    _PARAMETER_CACHE = {
        "host": returned[DB_ENDPOINT_PARAMETER],
        "port": int(returned[DB_PORT_PARAMETER]),
        "database": returned[DB_NAME_PARAMETER],
        "username": returned[DB_USERNAME_PARAMETER],
        "password": returned[DB_PASSWORD_PARAMETER],
    }

    log_event(
        "INFO",
        "Database parameters loaded",
    )

    return _PARAMETER_CACHE


def get_admin_token():
    global _ADMIN_TOKEN_CACHE

    if _ADMIN_TOKEN_CACHE is not None:
        return _ADMIN_TOKEN_CACHE

    log_event(
        "INFO",
        "Loading admin token from SSM",
    )

    result = ssm.get_parameter(
        Name=ADMIN_TOKEN_PARAMETER,
        WithDecryption=True,
    )

    token = (
        result["Parameter"]["Value"]
        or ""
    ).strip()

    if not token:
        raise RuntimeError(
            "Admin authentication token is empty"
        )

    _ADMIN_TOKEN_CACHE = token

    return _ADMIN_TOKEN_CACHE


# ==========================================================
# DATABASE CONNECTION
# ==========================================================

def get_connection():
    """
    Connect to the private RDS database with bounded timeouts.
    """

    db = load_database_parameters()

    log_event(
        "INFO",
        "Opening RDS connection",
    )

    connection = pymysql.connect(
        host=db["host"],
        port=db["port"],
        user=db["username"],
        password=db["password"],
        database=db["database"],
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=4,
        read_timeout=4,
        write_timeout=4,
        autocommit=True,
    )

    log_event(
        "INFO",
        "RDS connection established",
    )

    return connection


# ==========================================================
# CUSTOMER AUTHENTICATION
#
# REQUIRED BEHAVIOR:
#
#   raw bearer token
#          |
#          v
#   SHA-256(raw token)
#          |
#          v
#   customers.bearer_token
#          |
#          v
#   active customer?
#          |
#          +---- yes ---> CUSTOMER identity
#          |
#          +---- no ----> DENY
#
# No SSM customer-token mapping is used.
# ==========================================================

def authenticate_customer(token):
    token_hash = hash_token(token)

    connection = None

    try:

        connection = get_connection()

        log_event(
            "INFO",
            "Checking hashed bearer token in customers table",
        )

        # IMPORTANT:
        # A bearer token is NOT a unique customer identifier.
        # Multiple ACTIVE customers may intentionally share the same
        # SHA-256 token hash. The authorizer therefore validates only
        # that the token belongs to at least one active customer and
        # passes the token hash downstream. The service Lambda then
        # validates the requested customer_id against that hash.
        with connection.cursor() as cursor:

            cursor.execute(
                """
                SELECT EXISTS(
                    SELECT 1
                    FROM customers
                    WHERE bearer_token = %s
                      AND status = 'ACTIVE'
                      AND deleted_at IS NULL
                ) AS token_exists
                """,
                (token_hash,),
            )

            result = cursor.fetchone() or {}

        if not result.get("token_exists"):

            log_event(
                "WARNING",
                "Bearer token not found in active customer records",
            )

            return None

        log_event(
            "INFO",
            "Bearer token validated against active customer records",
        )

        return {
            "role": "CUSTOMER",
            # The token hash is passed as authorizer context so the
            # service Lambda can verify the URL customer_id.
            "token_hash": token_hash,
        }

    except pymysql.MySQLError as exc:

        log_event(
            "ERROR",
            "Customer authentication database error",
            error_type=type(exc).__name__,
            error=str(exc),
        )

        raise

    except Exception as exc:

        log_event(
            "ERROR",
            "Customer authentication failed unexpectedly",
            error_type=type(exc).__name__,
            error=str(exc),
        )

        raise

    finally:

        if connection is not None:
            connection.close()


# ==========================================================
# ADMIN AUTHENTICATION
#
# Admin token remains in SSM SecureString.
# Customer tokens remain hashed in RDS.
# ==========================================================

def authenticate_admin(token):

    configured_admin_token = get_admin_token()

    if not hmac.compare_digest(
        token,
        configured_admin_token,
    ):
        return None

    log_event(
        "INFO",
        "Admin bearer token validated",
    )

    return {
        "role": "ADMIN",
        "customer_id": "",
    }


# ==========================================================
# MAIN AUTHORIZER
# ==========================================================

def lambda_handler(event, context):

    request_id = getattr(
        context,
        "aws_request_id",
        "unknown",
    )

    method_arn = event.get(
        "methodArn",
        "*",
    )

    log_event(
        "INFO",
        "Authentication request received",
        request_id=request_id,
        authorizer_type=event.get(
            "type",
            "UNKNOWN",
        ),
    )

    token = extract_bearer_token(event)

    if token is None:

        log_event(
            "WARNING",
            "Authentication denied",
            request_id=request_id,
            reason="INVALID_BEARER_TOKEN_FORMAT",
        )

        return deny(
            principal_id="unauthorized",
            method_arn=method_arn,
        )

    try:

        # ------------------------------------------------------
        # ADMIN FIRST
        # ------------------------------------------------------

        admin_identity = authenticate_admin(
            token
        )

        if admin_identity is not None:

            return allow(
                principal_id="admin",
                method_arn=method_arn,
                identity=admin_identity,
            )

        # ------------------------------------------------------
        # CUSTOMER
        # ------------------------------------------------------

        customer_identity = authenticate_customer(
            token
        )

        if customer_identity is None:

            log_event(
                "WARNING",
                "Authentication denied",
                request_id=request_id,
                reason="INVALID_CUSTOMER_TOKEN",
            )

            return deny(
                principal_id="unauthorized",
                method_arn=method_arn,
            )

        return allow(
            principal_id="customer-token",
            method_arn=method_arn,
            identity=customer_identity,
        )

    except pymysql.MySQLError as exc:

        # Do not silently turn a database outage into a confusing
        # invalid-token message. Log the real cause, then fail closed.
        log_event(
            "ERROR",
            "Authentication database unavailable",
            request_id=request_id,
            error_type=type(exc).__name__,
            error=str(exc),
        )

        return deny(
            principal_id="authentication-error",
            method_arn=method_arn,
        )

    except Exception as exc:

        log_event(
            "ERROR",
            "Authorizer failed",
            request_id=request_id,
            error_type=type(exc).__name__,
            error=str(exc),
        )

        return deny(
            principal_id="authentication-error",
            method_arn=method_arn,
        )
