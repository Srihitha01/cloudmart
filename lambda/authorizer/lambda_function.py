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
# RESPONSE HELPERS
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
        "principalId": str(
            principal_id
        ),
        "policyDocument": policy_document
    }

    if context:

        # API Gateway authorizer context values
        # should be strings
        response["context"] = {
            key: str(value)
            for key, value
            in context.items()
        }

    return response


# ==========================================================
# DENY POLICY
# ==========================================================

def deny_policy(
    method_arn
):

    return generate_policy(
        principal_id="unauthorized",
        effect="Deny",
        method_arn=method_arn
    )


# ==========================================================
# GET SSM PARAMETER
# ==========================================================

def get_parameter(
    parameter_name
):

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
# ==========================================================

def get_bearer_token(
    event
):

    headers = (
        event.get("headers")
        or {}
    )

    authorization = (
        headers.get("Authorization")
        or
        headers.get("authorization")
    )

    if not authorization:

        return None

    authorization = (
        authorization.strip()
    )

    if (
        authorization.lower()
        .startswith("bearer ")
    ):

        return authorization[
            7:
        ].strip()

    # Also allow direct token
    return authorization


# ==========================================================
# GET REQUEST INFORMATION
# ==========================================================

def get_request_info(
    event
):

    method = event.get(
        "httpMethod",
        ""
    )

    resource = event.get(
        "resource",
        ""
    )

    path = event.get(
        "path",
        ""
    )

    path_parameters = (
        event.get(
            "pathParameters"
        )
        or {}
    )

    return (
        method,
        resource,
        path,
        path_parameters
    )


# ==========================================================
# GET CUSTOMER ID FROM PATH PARAMETERS
# ==========================================================

def get_customer_id_from_path(
    path_parameters
):

    customer_id = (
        path_parameters.get(
            "customer_id"
        )
    )

    if customer_id is None:

        return None

    try:

        customer_id = int(
            customer_id
        )

    except (
        TypeError,
        ValueError
    ):

        return None

    if customer_id <= 0:

        return None

    return customer_id


# ==========================================================
# CUSTOMER ROUTES
# ==========================================================

def is_customer_route(
    resource
):

    customer_routes = [

        "/customers/{customer_id}/orders",

        "/customers/{customer_id}/orders/{order_id}",

        "/customers/{customer_id}/orders/{order_id}/status"
    ]

    return (
        resource
        in
        customer_routes
    )


# ==========================================================
# ADMIN ROUTES
# ==========================================================

def is_admin_route(
    resource
):

    admin_routes = [

        "/orders",

        "/orders/{id}",

        "/orders/{id}/status"
    ]

    return (
        resource
        in
        admin_routes
    )


# ==========================================================
# GET CUSTOMER TOKENS
# ==========================================================

def get_customer_tokens():

    value = get_parameter(
        CUSTOMER_TOKENS_PARAMETER
    )

    try:

        tokens = json.loads(
            value
        )

    except json.JSONDecodeError:

        raise ValueError(
            "Customer tokens parameter contains invalid JSON"
        )

    if not isinstance(
        tokens,
        dict
    ):

        raise ValueError(
            "Customer tokens parameter must be a JSON object"
        )

    normalized_tokens = {}

    for token, customer_id in (
        tokens.items()
    ):

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
# ADMIN AUTHORIZATION
# ==========================================================

def authorize_admin(
    token,
    method_arn,
    resource
):

    admin_token = get_parameter(
        AUTH_TOKEN_PARAMETER
    )

    if token != admin_token:

        return None

    # Admin is allowed only on admin routes.
    # Product routes can also be added separately
    # if your existing project requires them.

    if is_customer_route(
        resource
    ):

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
# CUSTOMER AUTHORIZATION
# ==========================================================

def authorize_customer(
    token,
    method_arn,
    resource,
    path_parameters
):

    # Customer must access customer-specific route

    if not is_customer_route(
        resource
    ):

        return None

    customer_tokens = (
        get_customer_tokens()
    )

    authenticated_customer_id = (
        customer_tokens.get(
            token
        )
    )

    if (
        authenticated_customer_id
        is None
    ):

        return None

    url_customer_id = (
        get_customer_id_from_path(
            path_parameters
        )
    )

    if (
        url_customer_id
        is None
    ):

        return None

    # ======================================================
    # MOST IMPORTANT SECURITY CHECK
    #
    # Token customer_id MUST match URL customer_id
    # ======================================================

    if (
        authenticated_customer_id
        !=
        url_customer_id
    ):

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

    method_arn = event.get(
        "methodArn"
    )

    if not method_arn:

        raise Exception(
            "Unauthorized"
        )

    token = get_bearer_token(
        event
    )

    if not token:

        return deny_policy(
            method_arn
        )

    try:

        (
            method,
            resource,
            path,
            path_parameters
        ) = get_request_info(
            event
        )

        print(
            json.dumps(
                {
                    "message":
                        "Authorization request received",

                    "method":
                        method,

                    "resource":
                        resource,

                    "path":
                        path,

                    "path_parameters":
                        path_parameters
                }
            )
        )

        # ==================================================
        # ADMIN
        # ==================================================

        admin_response = (
            authorize_admin(
                token=token,

                method_arn=method_arn,

                resource=resource
            )
        )

        if admin_response:

            print(
                json.dumps(
                    {
                        "message":
                            "Admin authorized",

                        "resource":
                            resource
                    }
                )
            )

            return admin_response

        # ==================================================
        # CUSTOMER
        # ==================================================

        customer_response = (
            authorize_customer(
                token=token,

                method_arn=method_arn,

                resource=resource,

                path_parameters=
                    path_parameters
            )
        )

        if customer_response:

            print(
                json.dumps(
                    {
                        "message":
                            "Customer authorized",

                        "resource":
                            resource,

                        "customer_id":
                            customer_response[
                                "context"
                            ][
                                "customer_id"
                            ]
                    }
                )
            )

            return customer_response

        # ==================================================
        # INVALID AUTHORIZATION
        # ==================================================

        print(
            json.dumps(
                {
                    "message":
                        "Authorization denied",

                    "resource":
                        resource
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