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

    print(json.dumps(record, default=str))


def get_parameter(name):

    response = ssm.get_parameter(
        Name=name,
        WithDecryption=True
    )

    return response["Parameter"]["Value"]


# ==========================================================
# GENERATE IAM POLICY
# ==========================================================

def generate_policy(
    principal_id,
    effect,
    resource,
    context=None
):

    policy = {
        "principalId": principal_id,

        "policyDocument": {
            "Version": "2012-10-17",

            "Statement": [
                {
                    "Action": "execute-api:Invoke",
                    "Effect": effect,
                    "Resource": resource
                }
            ]
        }
    }

    # ------------------------------------------------------
    # CONTEXT PASSED TO BACKEND LAMBDA
    # ------------------------------------------------------

    if context:

        policy["context"] = context

    return policy


# ==========================================================
# GET API DETAILS FROM METHOD ARN
# ==========================================================

def get_request_details(method_arn):

    try:

        arn_parts = method_arn.split(":")

        api_gateway_part = arn_parts[5]

        parts = api_gateway_part.split("/")

        # [api-id, stage, HTTP_METHOD, path...]

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

        return {
            "http_method": http_method,
            "resource_path": resource_path
        }

    except Exception:

        return {
            "http_method": "",
            "resource_path": ""
        }


# ==========================================================
# NORMALIZE RESOURCE PATH
# ==========================================================

def normalize_resource_path(path):

    parts = [
        part
        for part in path.split("/")
        if part
    ]

    # /products/{id}

    if (
        len(parts) == 2
        and parts[0] == "products"
    ):

        return "/products/{id}"

    # /orders/{id}

    if (
        len(parts) == 2
        and parts[0] == "orders"
    ):

        return "/orders/{id}"

    # /orders/{id}/status

    if (
        len(parts) == 3
        and parts[0] == "orders"
        and parts[2] == "status"
    ):

        return "/orders/{id}/status"

    # Base path

    if not parts:

        return "/"

    return "/" + "/".join(parts)


# ==========================================================
# CUSTOMER AUTHORIZATION
# ==========================================================

def customer_is_allowed(
    http_method,
    resource_path
):

    normalized_path = normalize_resource_path(
        resource_path
    )

    # ------------------------------------------------------
    # PRODUCTS
    # Customer can only VIEW products
    # ------------------------------------------------------

    if (
        http_method == "GET"
        and normalized_path == "/products"
    ):
        return True

    if (
        http_method == "GET"
        and normalized_path == "/products/{id}"
    ):
        return True

    # ------------------------------------------------------
    # ORDERS
    # Customer can create orders
    # ------------------------------------------------------

    if (
        http_method == "POST"
        and normalized_path == "/orders"
    ):
        return True

    # Customer can view orders

    if (
        http_method == "GET"
        and normalized_path == "/orders"
    ):
        return True

    if (
        http_method == "GET"
        and normalized_path == "/orders/{id}"
    ):
        return True

    # Customer can request cancellation

    if (
        http_method == "PATCH"
        and normalized_path == "/orders/{id}/status"
    ):
        return True

    return False


# ==========================================================
# ADMIN AUTHORIZATION
# ==========================================================

def admin_is_allowed(
    http_method,
    resource_path
):

    normalized_path = normalize_resource_path(
        resource_path
    )

    # ------------------------------------------------------
    # PRODUCTS
    # Admin can manage products
    # ------------------------------------------------------

    if (
        http_method == "GET"
        and normalized_path == "/products"
    ):
        return True

    if (
        http_method == "GET"
        and normalized_path == "/products/{id}"
    ):
        return True

    if (
        http_method == "POST"
        and normalized_path == "/products"
    ):
        return True

    if (
        http_method == "PATCH"
        and normalized_path == "/products/{id}"
    ):
        return True

    if (
        http_method == "DELETE"
        and normalized_path == "/products/{id}"
    ):
        return True

    # ------------------------------------------------------
    # ORDERS
    # Admin can view and manage order status
    # ------------------------------------------------------

    if (
        http_method == "GET"
        and normalized_path == "/orders"
    ):
        return True

    if (
        http_method == "GET"
        and normalized_path == "/orders/{id}"
    ):
        return True

    if (
        http_method == "PATCH"
        and normalized_path == "/orders/{id}/status"
    ):
        return True

    # ------------------------------------------------------
    # ADMIN CANNOT CREATE ORDERS
    # ------------------------------------------------------

    # POST /orders -> False

    return False


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

        # ==================================================
        # GET AUTHORIZATION HEADER
        # ==================================================

        authorization_header = event.get(
            "authorizationToken",
            ""
        )

        # --------------------------------------------------
        # FALLBACK FOR DIRECT INVOCATION
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

        # ==================================================
        # VALIDATE HEADER
        # ==================================================

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

        # ==================================================
        # EXTRACT BEARER TOKEN
        # ==================================================

        provided_token = authorization_header.strip()

        if provided_token.lower().startswith(
            "bearer "
        ):

            provided_token = provided_token[
                len("Bearer "):
            ].strip()

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

        # ==================================================
        # GET SSM PARAMETERS
        # ==================================================

        admin_token_parameter = os.environ[
            "ADMIN_TOKEN_PARAMETER"
        ]

        customer_tokens_parameter = os.environ[
            "CUSTOMER_TOKENS_PARAMETER"
        ]

        admin_token = get_parameter(
            admin_token_parameter
        )

        customer_tokens_json = get_parameter(
            customer_tokens_parameter
        )

        customer_tokens = json.loads(
            customer_tokens_json
        )

        # ==================================================
        # GET METHOD + RESOURCE
        # ==================================================

        request_details = get_request_details(
            method_arn
        )

        http_method = request_details[
            "http_method"
        ]

        resource_path = request_details[
            "resource_path"
        ]

        normalized_path = normalize_resource_path(
            resource_path
        )

        # ==================================================
        # ADMIN AUTHENTICATION + AUTHORIZATION
        # ==================================================

        if provided_token == admin_token:

            # ----------------------------------------------
            # CHECK ADMIN ENDPOINT PERMISSION
            # ----------------------------------------------

            if not admin_is_allowed(
                http_method,
                resource_path
            ):

                log_event(
                    "WARN",
                    "Admin access denied",
                    request_id=request_id,
                    http_method=http_method,
                    resource=normalized_path
                )

                return generate_policy(
                    principal_id="admin",
                    effect="Deny",
                    resource=method_arn
                )

            # ----------------------------------------------
            # ADMIN AUTHORIZED
            # ----------------------------------------------

            log_event(
                "INFO",
                "Admin authorization successful",
                request_id=request_id,
                http_method=http_method,
                resource=normalized_path
            )

            return generate_policy(
                principal_id="admin",
                effect="Allow",
                resource=method_arn,
                context={
                    "role": "ADMIN",
                    "customer_id": ""
                }
            )

        # ==================================================
        # CUSTOMER AUTHENTICATION
        # ==================================================

        customer_id = customer_tokens.get(
            provided_token
        )

        if customer_id is None:

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

        customer_id = int(
            customer_id
        )

        # ==================================================
        # CUSTOMER METHOD + ENDPOINT AUTHORIZATION
        # ==================================================

        if not customer_is_allowed(
            http_method,
            resource_path
        ):

            log_event(
                "WARN",
                "Customer access denied",
                request_id=request_id,
                customer_id=customer_id,
                http_method=http_method,
                resource=normalized_path
            )

            return generate_policy(
                principal_id=(
                    f"customer-{customer_id}"
                ),
                effect="Deny",
                resource=method_arn
            )

        # ==================================================
        # CUSTOMER AUTHORIZED
        # ==================================================

        log_event(
            "INFO",
            "Customer authorization successful",
            request_id=request_id,
            customer_id=customer_id,
            http_method=http_method,
            resource=normalized_path
        )

        return generate_policy(
            principal_id=(
                f"customer-{customer_id}"
            ),
            effect="Allow",
            resource=method_arn,
            context={
                "role": "CUSTOMER",
                "customer_id": str(
                    customer_id
                )
            }
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