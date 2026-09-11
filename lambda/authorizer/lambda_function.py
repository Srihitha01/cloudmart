import json
import os

import boto3


# ==========================================================
# AWS CLIENT
# ==========================================================

ssm = boto3.client("ssm")


# ==========================================================
# ENVIRONMENT VARIABLES
# ==========================================================

AUTH_TOKEN_PARAMETER = os.environ[
    "AUTH_TOKEN_PARAMETER"
]

CUSTOMER_TOKENS_PARAMETER = os.environ[
    "CUSTOMER_TOKENS_PARAMETER"
]


# ==========================================================
# GENERATE POLICY
# ==========================================================

def generate_policy(
    principal_id,
    effect,
    method_arn,
    context=None
):

    policy_document = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Action": "execute-api:Invoke",
                "Effect": effect,
                "Resource": method_arn
            }
        ]
    }

    response = {
        "principalId": str(principal_id),
        "policyDocument": policy_document
    }

    if context:

        response["context"] = {
            key: str(value)
            for key, value in context.items()
        }

    return response


# ==========================================================
# DENY POLICY
# ==========================================================

def deny_policy(method_arn):

    return generate_policy(
        principal_id="unauthorized",
        effect="Deny",
        method_arn=method_arn
    )


# ==========================================================
# GET SSM PARAMETER
# ==========================================================

def get_parameter(parameter_name):

    response = ssm.get_parameter(
        Name=parameter_name,
        WithDecryption=True
    )

    return response[
        "Parameter"
    ][
        "Value"
    ]


# ==========================================================
# GET TOKEN
#
# TOKEN Lambda Authorizer receives:
#
# event["authorizationToken"]
#
# Example:
#
# Bearer CustomerToken@3
# ==========================================================

def get_bearer_token(event):

    authorization = event.get(
        "authorizationToken"
    )

    # Fallback support for other event formats
    if not authorization:

        headers = (
            event.get("headers")
            or {}
        )

        authorization = (
            headers.get("Authorization")
            or headers.get("authorization")
        )

    if not authorization:

        return None

    authorization = authorization.strip()

    if authorization.lower().startswith(
        "bearer "
    ):

        return authorization[7:].strip()

    return authorization


# ==========================================================
# GET CUSTOMER TOKENS FROM SSM
# ==========================================================

def get_customer_tokens():

    value = get_parameter(
        CUSTOMER_TOKENS_PARAMETER
    )

    try:

        tokens = json.loads(value)

    except json.JSONDecodeError:

        raise ValueError(
            "Customer tokens parameter contains invalid JSON"
        )

    if not isinstance(tokens, dict):

        raise ValueError(
            "Customer tokens parameter must be a JSON object"
        )

    normalized_tokens = {}

    for token, customer_id in tokens.items():

        try:

            normalized_customer_id = int(
                customer_id
            )

        except (
            TypeError,
            ValueError
        ):

            continue

        normalized_tokens[
            str(token)
        ] = normalized_customer_id

    return normalized_tokens


# ==========================================================
# AUTHORIZE ADMIN
# ==========================================================

def authorize_admin(
    token,
    method_arn
):

    admin_token = get_parameter(
        AUTH_TOKEN_PARAMETER
    )

    if token != admin_token:

        return None

    return generate_policy(
        principal_id="admin",

        effect="Allow",

        method_arn=method_arn,

        context={
            "role": "ADMIN",
            "customer_id": ""
        }
    )


# ==========================================================
# AUTHORIZE CUSTOMER
# ==========================================================

def authorize_customer(
    token,
    method_arn
):

    customer_tokens = (
        get_customer_tokens()
    )

    authenticated_customer_id = (
        customer_tokens.get(token)
    )

    if authenticated_customer_id is None:

        return None

    return generate_policy(
        principal_id=
            f"customer-{authenticated_customer_id}",

        effect="Allow",

        method_arn=method_arn,

        context={
            "role": "CUSTOMER",

            "customer_id":
                authenticated_customer_id
        }
    )


# ==========================================================
# LAMBDA HANDLER
# ==========================================================

def lambda_handler(
    event,
    context
):

    print(
        json.dumps(
            {
                "message":
                    "Authorization request received",

                "event_type":
                    event.get("type"),

                "method_arn":
                    event.get("methodArn")
            }
        )
    )

    # ======================================================
    # GET METHOD ARN
    # ======================================================

    method_arn = event.get(
        "methodArn"
    )

    if not method_arn:

        raise Exception(
            "Unauthorized"
        )

    # ======================================================
    # GET TOKEN
    # ======================================================

    token = get_bearer_token(
        event
    )

    if not token:

        print(
            json.dumps(
                {
                    "message":
                        "Authorization denied - token missing"
                }
            )
        )

        return deny_policy(
            method_arn
        )

    try:

        # ==================================================
        # ADMIN AUTHORIZATION
        # ==================================================

        admin_response = authorize_admin(
            token=token,

            method_arn=method_arn
        )

        if admin_response:

            print(
                json.dumps(
                    {
                        "message":
                            "Admin authorized"
                    }
                )
            )

            return admin_response

        # ==================================================
        # CUSTOMER AUTHORIZATION
        # ==================================================

        customer_response = (
            authorize_customer(
                token=token,

                method_arn=method_arn
            )
        )

        if customer_response:

            authenticated_customer_id = (
                customer_response[
                    "context"
                ][
                    "customer_id"
                ]
            )

            print(
                json.dumps(
                    {
                        "message":
                            "Customer authorized",

                        "customer_id":
                            authenticated_customer_id
                    }
                )
            )

            return customer_response

        # ==================================================
        # INVALID TOKEN
        # ==================================================

        print(
            json.dumps(
                {
                    "message":
                        "Authorization denied - invalid token"
                }
            )
        )

        return deny_policy(
            method_arn
        )

    except Exception as exc:

        print(
            json.dumps(
                {
                    "message":
                        "Authorization error",

                    "error_type":
                        type(exc).__name__,

                    "error":
                        str(exc)
                }
            )
        )

        return deny_policy(
            method_arn
        )