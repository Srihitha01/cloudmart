import json
import os
import boto3


ssm = boto3.client("ssm")

TOKEN_PARAMETER = os.environ["TOKEN_PARAMETER"]


def log_event(level, message, **details):
    record = {
        "level": level,
        "service": "cloudmart-lambda-authorizer",
        "message": message,
        **details,
    }

    print(json.dumps(record))


def get_token_from_ssm():
    response = ssm.get_parameter(
        Name=TOKEN_PARAMETER,
        WithDecryption=True
    )

    return response["Parameter"]["Value"]


def deny(reason, request_id):
    log_event(
        "WARN",
        reason,
        request_id=request_id
    )

    return {
        "isAuthorized": False
    }


def allow(request_id):
    log_event(
        "INFO",
        "Authorization successful",
        request_id=request_id
    )

    return {
        "isAuthorized": True,
        "context": {
            "authenticated": "true"
        }
    }


def lambda_handler(event, context):
    request_id = context.aws_request_id

    log_event(
        "INFO",
        "Authorization request received",
        request_id=request_id
    )

    try:
        # ------------------------------------------------------
        # Read request headers
        # ------------------------------------------------------

        headers = event.get("headers") or {}

        authorization_header = (
            headers.get("authorization")
            or headers.get("Authorization")
        )

        # ------------------------------------------------------
        # Token missing
        # ------------------------------------------------------

        if not authorization_header:
            return deny(
                "Authorization header is missing",
                request_id
            )

        # ------------------------------------------------------
        # Validate Bearer format
        # ------------------------------------------------------

        authorization_header = authorization_header.strip()

        if not authorization_header.startswith("Bearer "):
            return deny(
                "Invalid authorization format",
                request_id
            )

        # ------------------------------------------------------
        # Extract token
        # ------------------------------------------------------

        supplied_token = authorization_header[len("Bearer "):].strip()

        if not supplied_token:
            return deny(
                "Bearer token is empty",
                request_id
            )

        # ------------------------------------------------------
        # Read expected token from SSM SecureString
        # ------------------------------------------------------

        try:
            expected_token = get_token_from_ssm()

        except Exception as exc:
            log_event(
                "ERROR",
                "Failed to retrieve authentication token from SSM",
                request_id=request_id,
                error_type=type(exc).__name__
            )

            return {
                "isAuthorized": False
            }

        # ------------------------------------------------------
        # Compare tokens
        # ------------------------------------------------------

        if supplied_token != expected_token:
            return deny(
                "Invalid authentication token",
                request_id
            )

        # ------------------------------------------------------
        # Token is valid
        # ------------------------------------------------------

        return allow(request_id)

    except Exception as exc:
        log_event(
            "ERROR",
            "Unexpected authorization error",
            request_id=request_id,
            error_type=type(exc).__name__
        )

        return {
            "isAuthorized": False
        }