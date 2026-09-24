import os
import json
import logging
import hmac

import boto3
from botocore.config import Config


# ==========================================================
# LOGGING
# ==========================================================

logger = logging.getLogger()
logger.setLevel(logging.INFO)


# ==========================================================
# ENVIRONMENT
# ==========================================================

ADMIN_TOKEN_PARAMETER = os.environ["ADMIN_TOKEN_PARAMETER"]
CUSTOMER_TOKENS_PARAMETER = os.environ["CUSTOMER_TOKENS_PARAMETER"]


# ==========================================================
# AWS CLIENT
# ==========================================================

AWS_CLIENT_CONFIG = Config(
    connect_timeout=3,
    read_timeout=5,
    retries={"max_attempts": 1, "mode": "standard"},
)

ssm = boto3.client("ssm", config=AWS_CLIENT_CONFIG)


# ==========================================================
# SSM HELPERS
# ==========================================================

def get_parameter(parameter_name):
    response = ssm.get_parameter(
        Name=parameter_name,
        WithDecryption=True,
    )
    return response["Parameter"]["Value"]


def get_customer_token_map():
    """
    SecureString JSON mapping maintained by Customer Lambda:

        {
            "ABC123XYZ": 10,
            "DEF456XYZ": 11
        }

    RDS continues to store only the SHA-256 token hash.
    The authorizer stays outside the VPC, so it does not connect
    directly to the private RDS instance.
    """
    raw_value = get_parameter(CUSTOMER_TOKENS_PARAMETER)

    if not raw_value:
        return {}

    try:
        mapping = json.loads(raw_value)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "Customer token SSM parameter contains invalid JSON"
        ) from exc

    if not isinstance(mapping, dict):
        raise RuntimeError(
            "Customer token SSM parameter must contain a JSON object"
        )

    normalized = {}

    for token, customer_id in mapping.items():
        try:
            normalized[str(token)] = int(customer_id)
        except (TypeError, ValueError):
            logger.warning(
                json.dumps(
                    {
                        "event": "INVALID_CUSTOMER_TOKEN_MAPPING_ENTRY",
                        "customer_id": str(customer_id),
                    }
                )
            )

    return normalized


# ==========================================================
# TOKEN EXTRACTION
# ==========================================================

def extract_bearer_token(event):
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


# ==========================================================
# CUSTOMER AUTHENTICATION
# ==========================================================

def authenticate_customer(token):
    """
    Authorizer-side lookup.

    Customer Lambda maintains the SecureString mapping when a
    customer is created and removes it during soft deletion.

    The customers table still stores the SHA-256 token hash.
    """
    mapping = get_customer_token_map()
    customer_id = mapping.get(token)

    if customer_id is None:
        return None

    return {
        "customer_id": str(customer_id),
        "role": "CUSTOMER",
    }


# ==========================================================
# ADMIN AUTHENTICATION
# ==========================================================

def authenticate_admin(token):
    configured_admin_token = get_parameter(ADMIN_TOKEN_PARAMETER)

    if not configured_admin_token:
        return None

    if not hmac.compare_digest(
        token.encode("utf-8"),
        configured_admin_token.encode("utf-8"),
    ):
        return None

    return {
        "customer_id": "",
        "role": "ADMIN",
    }


# ==========================================================
# POLICY GENERATION
# ==========================================================

def generate_policy(principal_id, effect, method_arn, context=None):
    policy = {
        "principalId": str(principal_id),
        "policyDocument": {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Action": "execute-api:Invoke",
                    "Effect": effect,
                    "Resource": method_arn,
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


# ==========================================================
# MAIN HANDLER
# ==========================================================

def lambda_handler(event, context):
    request_id = getattr(context, "aws_request_id", "unknown")
    method_arn = event.get("methodArn", "*")

    logger.info(
        json.dumps(
            {
                "event": "AUTHENTICATION_REQUEST",
                "request_id": request_id,
                "authorizer_type": event.get("type", "UNKNOWN"),
            }
        )
    )

    token = extract_bearer_token(event)

    if token is None:
        logger.warning(
            json.dumps(
                {
                    "event": "AUTHENTICATION_DENIED",
                    "reason": "INVALID_BEARER_TOKEN_FORMAT",
                    "request_id": request_id,
                }
            )
        )
        return deny("anonymous", method_arn)

    # Admin is checked first.
    try:
        admin_identity = authenticate_admin(token)

        if admin_identity is not None:
            logger.info(
                json.dumps(
                    {
                        "event": "AUTHENTICATION_ALLOWED",
                        "role": "ADMIN",
                        "request_id": request_id,
                    }
                )
            )
            return allow("admin", method_arn, admin_identity)

    except Exception:
        logger.exception(
            json.dumps(
                {
                    "event": "ADMIN_AUTHENTICATION_ERROR",
                    "request_id": request_id,
                }
            )
        )

    # Customer authentication uses the SSM SecureString mapping.
    try:
        customer_identity = authenticate_customer(token)

        if customer_identity is not None:
            logger.info(
                json.dumps(
                    {
                        "event": "AUTHENTICATION_ALLOWED",
                        "role": "CUSTOMER",
                        "customer_id": customer_identity["customer_id"],
                        "request_id": request_id,
                    }
                )
            )
            return allow(
                customer_identity["customer_id"],
                method_arn,
                customer_identity,
            )

    except Exception:
        logger.exception(
            json.dumps(
                {
                    "event": "CUSTOMER_AUTHENTICATION_ERROR",
                    "request_id": request_id,
                }
            )
        )

    logger.warning(
        json.dumps(
            {
                "event": "AUTHENTICATION_DENIED",
                "reason": "INVALID_CREDENTIALS",
                "request_id": request_id,
            }
        )
    )

    return deny("anonymous", method_arn)
