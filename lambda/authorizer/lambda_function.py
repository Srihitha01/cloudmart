import os
import json
import hashlib
import logging
import hmac

import boto3
import pymysql


# ==========================================================
# LOGGING
# ==========================================================

logger = logging.getLogger()
logger.setLevel(logging.INFO)


# ==========================================================
# ENVIRONMENT
# ==========================================================

DB_ENDPOINT_PARAMETER = os.environ["DB_ENDPOINT_PARAMETER"]
DB_PORT_PARAMETER = os.environ["DB_PORT_PARAMETER"]
DB_NAME_PARAMETER = os.environ["DB_NAME_PARAMETER"]
DB_USERNAME_PARAMETER = os.environ["DB_USERNAME_PARAMETER"]
DB_PASSWORD_PARAMETER = os.environ["DB_PASSWORD_PARAMETER"]
ADMIN_TOKEN_PARAMETER = os.environ["ADMIN_TOKEN_PARAMETER"]

ssm = boto3.client("ssm")


# ==========================================================
# SSM
# ==========================================================

def get_parameter(parameter_name):
    response = ssm.get_parameter(
        Name=parameter_name,
        WithDecryption=True
    )
    return response["Parameter"]["Value"]


# ==========================================================
# DATABASE
# ==========================================================

def get_connection():
    return pymysql.connect(
        host=get_parameter(DB_ENDPOINT_PARAMETER),
        port=int(get_parameter(DB_PORT_PARAMETER)),
        user=get_parameter(DB_USERNAME_PARAMETER),
        password=get_parameter(DB_PASSWORD_PARAMETER),
        database=get_parameter(DB_NAME_PARAMETER),
        connect_timeout=10,
        read_timeout=10,
        write_timeout=10,
        autocommit=True,
        cursorclass=pymysql.cursors.DictCursor
    )


# ==========================================================
# TOKEN HELPERS
# ==========================================================

def extract_bearer_token(event):
    """
    TOKEN authorizer input:
    {
        "type": "TOKEN",
        "authorizationToken": "Bearer <token>",
        "methodArn": "..."
    }
    """
    authorization_header = event.get("authorizationToken", "")

    if not isinstance(authorization_header, str):
        return None

    parts = authorization_header.strip().split()

    if len(parts) != 2:
        return None

    scheme, token = parts

    if scheme.lower() != "bearer" or not token:
        return None

    return token


def hash_token(token):
    return hashlib.sha256(
        token.encode("utf-8")
    ).hexdigest()


# ==========================================================
# CUSTOMER AUTHENTICATION
# ==========================================================

def authenticate_customer(token):
    """
    Authentication only:
    - Hash the supplied bearer token.
    - Find an active customer.
    - Return identity context.

    Route permissions, ownership, order status rules, and
    business validation must be handled by service Lambdas.
    """
    token_hash = hash_token(token)
    connection = None

    try:
        connection = get_connection()

        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT customer_id
                FROM customers
                WHERE bearer_token = %s
                  AND deleted_at IS NULL
                  AND status = 'ACTIVE'
                LIMIT 1
                """,
                (token_hash,)
            )

            customer = cursor.fetchone()

            if not customer:
                return None

            return {
                "customer_id": str(customer["customer_id"]),
                "role": "CUSTOMER"
            }

    except Exception:
        logger.exception(
            json.dumps({
                "event": "CUSTOMER_AUTHENTICATION_ERROR"
            })
        )
        return None

    finally:
        if connection is not None:
            connection.close()


# ==========================================================
# ADMIN AUTHENTICATION
# ==========================================================

def authenticate_admin(token):
    try:
        configured_admin_token = get_parameter(
            ADMIN_TOKEN_PARAMETER
        )

        if not configured_admin_token:
            return None

        is_valid = hmac.compare_digest(
            token.encode("utf-8"),
            configured_admin_token.encode("utf-8")
        )

        if not is_valid:
            return None

        return {
            "customer_id": "",
            "role": "ADMIN"
        }

    except Exception:
        logger.exception(
            json.dumps({
                "event": "ADMIN_AUTHENTICATION_ERROR"
            })
        )
        return None


# ==========================================================
# POLICY GENERATION
# ==========================================================

def generate_policy(
    principal_id,
    effect,
    method_arn,
    context=None
):
    policy = {
        "principalId": str(principal_id),
        "policyDocument": {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Action": "execute-api:Invoke",
                    "Effect": effect,
                    "Resource": method_arn
                }
            ]
        }
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
        context=identity
    )


def deny(principal_id, method_arn):
    return generate_policy(
        principal_id=principal_id,
        effect="Deny",
        method_arn=method_arn
    )


# ==========================================================
# HANDLER
# ==========================================================

def lambda_handler(event, context):
    request_id = getattr(
        context,
        "aws_request_id",
        "unknown"
    )

    method_arn = event.get("methodArn", "*")

    logger.info(
        json.dumps({
            "event": "AUTHENTICATION_REQUEST",
            "request_id": request_id,
            "authorizer_type": event.get("type", "UNKNOWN")
        })
    )

    token = extract_bearer_token(event)

    if token is None:
        logger.warning(
            json.dumps({
                "event": "AUTHENTICATION_DENIED",
                "reason": "INVALID_BEARER_TOKEN_FORMAT",
                "request_id": request_id
            })
        )

        return deny(
            principal_id="unauthorized",
            method_arn=method_arn
        )

    identity = authenticate_admin(token)

    if identity is None:
        identity = authenticate_customer(token)

    if identity is None:
        logger.warning(
            json.dumps({
                "event": "AUTHENTICATION_DENIED",
                "reason": "INVALID_CREDENTIALS",
                "request_id": request_id
            })
        )

        return deny(
            principal_id="unauthorized",
            method_arn=method_arn
        )

    role = identity["role"]
    customer_id = identity.get("customer_id", "")

    principal_id = (
        "admin"
        if role == "ADMIN"
        else customer_id
    )

    logger.info(
        json.dumps({
            "event": "AUTHENTICATION_SUCCESS",
            "request_id": request_id,
            "role": role,
            "customer_id": customer_id
        })
    )

    return allow(
        principal_id=principal_id,
        method_arn=method_arn,
        identity={
            "role": role,
            "customer_id": customer_id
        }
    )
