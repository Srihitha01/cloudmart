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

ADMIN_TOKEN_PARAMETER = os.environ[
    "ADMIN_TOKEN_PARAMETER"
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
    auth_context=None
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

    if auth_context:

        response["context"] = {
            key: str(value)
            for key, value in auth_context.items()
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
# GET BEARER TOKEN
# ==========================================================

def get_bearer_token(event):

    authorization = event.get(
        "authorizationToken"
    )

    # Fallback support
    if not authorization:

        headers = event.get(
            "headers"
        ) or {}

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

        customer_tokens = json.loads(
            value
        )

    except json.JSONDecodeError:

        raise ValueError(
            "Customer tokens parameter contains invalid JSON"
        )

    if not isinstance(
        customer_tokens,
        dict
    ):

        raise ValueError(
            "Customer tokens parameter must be a JSON object"
        )

    normalized_tokens = {}

    for token, customer_id in customer_tokens.items():

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
# GET REQUEST DETAILS FROM METHOD ARN
# ==========================================================

def get_request_details(method_arn):

    try:

        execute_api_part = method_arn.split(
            ":"
        )[5]

        parts = execute_api_part.split(
            "/"
        )

        http_method = (
            parts[2]
            if len(parts) > 2
            else ""
        )

        resource_path = (
            "/"
            + "/".join(parts[3:])
            if len(parts) > 3
            else "/"
        )

        return (
            http_method,
            resource_path
        )

    except Exception:

        return (
            "",
            ""
        )


# ==========================================================
# NORMALIZE API PATH
# ==========================================================

def normalize_path(resource_path):

    parts = [

        part

        for part in resource_path.split("/")

        if part
    ]


    # PRODUCTS

    if len(parts) == 1:

        if parts[0] == "products":

            return "/products"


    if (

        len(parts) == 2

        and parts[0] == "products"

    ):

        return "/products/{id}"


    # CUSTOMER ORDERS

    if (

        len(parts) == 3

        and parts[0] == "customers"

        and parts[2] == "orders"

    ):

        return (
            "/customers/{customer_id}/orders"
        )


    # SINGLE CUSTOMER ORDER

    if (

        len(parts) == 4

        and parts[0] == "customers"

        and parts[2] == "orders"

    ):

        return (
            "/customers/{customer_id}"
            "/orders/{order_id}"
        )


    # CUSTOMER ORDER STATUS

    if (

        len(parts) == 5

        and parts[0] == "customers"

        and parts[2] == "orders"

        and parts[4] == "status"

    ):

        return (
            "/customers/{customer_id}"
            "/orders/{order_id}/status"
        )


    return resource_path


# ==========================================================
# GET CUSTOMER ID FROM URL
# ==========================================================

def get_customer_id_from_path(resource_path):

    parts = [

        part

        for part in resource_path.split("/")

        if part
    ]


    if (

        len(parts) >= 2

        and parts[0] == "customers"

    ):

        try:

            return int(
                parts[1]
            )

        except (
            ValueError,
            TypeError
        ):

            return None


    return None


# ==========================================================
# CHECK CUSTOMER ROUTE ACCESS
# ==========================================================

def customer_is_allowed(
    http_method,
    resource_path
):

    normalized_path = normalize_path(
        resource_path
    )


    allowed_routes = {

        # PRODUCTS

        (
            "GET",
            "/products"
        ),

        (
            "GET",
            "/products/{id}"
        ),


        # CREATE ORDER

        (
            "POST",
            "/customers/{customer_id}/orders"
        ),


        # GET CUSTOMER ORDERS

        (
            "GET",
            "/customers/{customer_id}/orders"
        ),


        # GET SINGLE ORDER

        (
            "GET",
            "/customers/{customer_id}/orders/{order_id}"
        ),


        # UPDATE ORDER

        (
            "PUT",
            "/customers/{customer_id}/orders/{order_id}"
        ),


        # UPDATE ORDER STATUS / CANCEL

        (
            "PATCH",
            "/customers/{customer_id}/orders/{order_id}/status"
        )
    }


    return (

        http_method,
        normalized_path

    ) in allowed_routes


# ==========================================================
# AUTHORIZE ADMIN
# ==========================================================

def authorize_admin(
    token,
    method_arn
):

    admin_token = get_parameter(
        ADMIN_TOKEN_PARAMETER
    )

    if token != admin_token:

        return None

    return generate_policy(
        principal_id="admin",

        effect="Allow",

        method_arn=method_arn,

        auth_context={
            "role": "ADMIN",
            "customer_id": ""
        }
    )


# ==========================================================
# AUTHORIZE CUSTOMER
# ==========================================================

def authorize_customer(
    token,
    method_arn,
    http_method,
    resource_path
):

    customer_tokens = get_customer_tokens()

    authenticated_customer_id = (
        customer_tokens.get(token)
    )

    if authenticated_customer_id is None:

        return None


    # VALIDATE ROUTE

    if not customer_is_allowed(
        http_method,
        resource_path
    ):

        print(
            json.dumps(
                {
                    "message":
                        "Customer route not allowed",

                    "http_method":
                        http_method,

                    "resource_path":
                        resource_path
                }
            )
        )

        return deny_policy(
            method_arn
        )


    # VALIDATE CUSTOMER ID IN URL

    url_customer_id = (
        get_customer_id_from_path(
            resource_path
        )
    )


    if (

        url_customer_id is not None

        and url_customer_id
        != authenticated_customer_id

    ):

        print(
            json.dumps(
                {
                    "message":
                        "Customer ID mismatch",

                    "authenticated_customer_id":
                        authenticated_customer_id,

                    "url_customer_id":
                        url_customer_id
                }
            )
        )

        return deny_policy(
            method_arn
        )


    # CUSTOMER AUTHORIZED

    return generate_policy(
        principal_id=(
            f"customer-{authenticated_customer_id}"
        ),

        effect="Allow",

        method_arn=method_arn,

        auth_context={
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


    print(
        json.dumps(
            {
                "message":
                    "Authorization request received",

                "method_arn":
                    method_arn
            }
        )
    )


    if not method_arn:

        raise Exception(
            "Unauthorized"
        )


    try:

        # GET TOKEN

        token = get_bearer_token(
            event
        )


        if not token:

            print(
                json.dumps(
                    {
                        "message":
                            "Token missing"
                    }
                )
            )

            return deny_policy(
                method_arn
            )


        # GET REQUEST DETAILS

        (
            http_method,
            resource_path
        ) = get_request_details(
            method_arn
        )


        print(
            json.dumps(
                {
                    "http_method":
                        http_method,

                    "resource_path":
                        resource_path,

                    "normalized_path":
                        normalize_path(
                            resource_path
                        )
                }
            )
        )


        # ==================================================
        # ADMIN AUTHORIZATION
        # ==================================================

        admin_response = authorize_admin(
            token,
            method_arn
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

        customer_response = authorize_customer(
            token,
            method_arn,
            http_method,
            resource_path
        )


        if customer_response:

            print(
                json.dumps(
                    {
                        "message":
                            "Customer authorization completed",

                        "effect":
                            customer_response[
                                "policyDocument"
                            ][
                                "Statement"
                            ][0][
                                "Effect"
                            ]
                    }
                )
            )

            return customer_response


        # INVALID TOKEN

        print(
            json.dumps(
                {
                    "message":
                        "Invalid token"
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