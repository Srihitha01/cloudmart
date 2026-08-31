import json
import os

import boto3


# ==========================================================
# AWS CLIENT
# ==========================================================

ssm = boto3.client("ssm")


# ==========================================================
# HELPERS
# ==========================================================

def log_event(level, message, **details):

    record = {
        "level": level,
        "service": "cloudmart-lambda-authorizer",
        "message": message,
        **details
    }

    print(json.dumps(record))


def get_parameter(name):

    response = ssm.get_parameter(
        Name=name,
        WithDecryption=True
    )

    return response["Parameter"]["Value"]


def generate_policy(
    principal_id,
    effect,
    resource
):

    return {
        "principalId": principal_id,

        "policyDocument": {
            "Version": "2012-10-17",

            "Statement": [
                {
                    "Action":
                        "execute-api:Invoke",

                    "Effect":
                        effect,

                    "Resource":
                        resource
                }
            ]
        }
    }


def get_stage_wildcard(method_arn):

    """
    Convert:

    arn:aws:execute-api:region:account:api-id/stage/METHOD/path

    into:

    arn:aws:execute-api:region:account:api-id/stage/*/*
    """

    parts = method_arn.split(":")

    if len(parts) < 6:

        return method_arn

    api_gateway_resource = parts[5]

    resource_parts = api_gateway_resource.split("/")

    if len(resource_parts) < 2:

        return method_arn

    api_id = resource_parts[0]
    stage = resource_parts[1]

    return (
        ":".join(parts[:5])
        + ":"
        + api_id
        + "/"
        + stage
        + "/*/*"
    )


# ==========================================================
# MAIN AUTHORIZER
# ==========================================================

def lambda_handler(event, context):

    request_id = context.aws_request_id

    method_arn = event.get(
        "methodArn",
        "*"
    )

    try:

        log_event(
            "INFO",
            "Authorization request received",
            request_id=request_id
        )

        # --------------------------------------------------
        # Get configured SSM parameter
        # --------------------------------------------------

        token_parameter = os.environ[
            "AUTH_TOKEN_PARAMETER"
        ]

        expected_token = get_parameter(
            token_parameter
        )

        # --------------------------------------------------
        # TOKEN authorizer event
        #
        # API Gateway TOKEN authorizers receive the
        # Authorization header value in authorizationToken.
        # --------------------------------------------------

        authorization_header = event.get(
            "authorizationToken",
            ""
        )

        # --------------------------------------------------
        # Fallback for direct/local invocation
        # --------------------------------------------------

        if not authorization_header:

            headers = event.get(
                "headers",
                {}
            ) or {}

            authorization_header = (
                headers.get("Authorization")
                or headers.get("authorization")
                or ""
            )

        # --------------------------------------------------
        # Validate presence
        # --------------------------------------------------

        if not authorization_header:

            log_event(
                "WARN",
                "Authorization token missing",
                request_id=request_id
            )

            return generate_policy(
                "unauthorized",
                "Deny",
                method_arn
            )

        # --------------------------------------------------
        # Accept:
        #
        # Bearer <token>
        #
        # or direct token
        # --------------------------------------------------

        provided_token = (
            authorization_header.strip()
        )

        if provided_token.startswith(
            "Bearer "
        ):

            provided_token = provided_token[
                len("Bearer "):
            ].strip()

        # --------------------------------------------------
        # Validate token
        # --------------------------------------------------

        if not provided_token:

            log_event(
                "WARN",
                "Authorization token empty",
                request_id=request_id
            )

            return generate_policy(
                "unauthorized",
                "Deny",
                method_arn
            )

        if provided_token != expected_token:

            log_event(
                "WARN",
                "Authorization token invalid",
                request_id=request_id
            )

            return generate_policy(
                "unauthorized",
                "Deny",
                method_arn
            )

        # --------------------------------------------------
        # Valid token
        #
        # Allow all methods/resources within this API stage.
        # This avoids an exact methodArn policy being reused
        # incorrectly between GET/POST/PUT/DELETE requests.
        # --------------------------------------------------

        policy_resource = get_stage_wildcard(
            method_arn
        )

        log_event(
            "INFO",
            "Authorization successful",
            request_id=request_id
        )

        return generate_policy(
            "authorized-user",
            "Allow",
            policy_resource
        )

    except Exception as error:

        log_event(
            "ERROR",
            "Authorization failed",
            request_id=request_id,
            error_type=type(error).__name__,
            error=str(error)
        )

        return generate_policy(
            "unauthorized",
            "Deny",
            method_arn
        )