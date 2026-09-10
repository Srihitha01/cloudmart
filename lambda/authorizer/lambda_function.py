import json
import os
import boto3
import logging


# ==========================================================
# CONFIGURATION
# ==========================================================

ssm = boto3.client("ssm")


logger = logging.getLogger()
logger.setLevel(logging.INFO)


# ==========================================================
# LOGGING
# ==========================================================

def log_event(
    level,
    message,
    **kwargs
):

    log_data = {
        "message": message,
        **kwargs
    }

    if level == "ERROR":
        logger.error(json.dumps(log_data))

    elif level == "WARNING":
        logger.warning(json.dumps(log_data))

    else:
        logger.info(json.dumps(log_data))


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

    if context:

        policy[
            "context"
        ] = context

    return policy


# ==========================================================
# GET REQUEST DETAILS
# ==========================================================

def get_request_details(
    method_arn
):

    try:

        execute_api_part = method_arn.split(
            ":"
        )[5]

        parts = execute_api_part.split(
            "/"
        )

        http_method = ""

        resource_path = "/"


        if len(parts) > 2:

            http_method = parts[2]


        if len(parts) > 3:

            resource_path = (
                "/"
                + "/".join(
                    parts[3:]
                )
            )


        return {

            "http_method": http_method,

            "resource_path": resource_path
        }


    except Exception:

        return {

            "http_method": "",

            "resource_path": "/"
        }


# ==========================================================
# NORMALIZE RESOURCE PATH
# ==========================================================

def normalize_resource_path(
    resource_path
):

    parts = [

        part

        for part in resource_path.split(
            "/"
        )

        if part

    ]


    # ======================================================
    # ROOT
    # ======================================================

    if not parts:

        return "/"


    # ======================================================
    # PRODUCTS
    # ======================================================

    if parts[0] == "products":

        if len(parts) == 1:

            return "/products"


        if len(parts) == 2:

            return "/products/{id}"


    # ======================================================
    # ORDERS
    # ======================================================

    if parts[0] == "orders":

        if len(parts) == 1:

            return "/orders"


        if len(parts) == 2:

            return "/orders/{id}"


        if (
            len(parts) == 3
            and parts[2] == "status"
        ):

            return (
                "/orders/{id}/status"
            )


    return resource_path


# ==========================================================
# ADMIN AUTHORIZATION
# ==========================================================

def admin_is_allowed(
    http_method,
    resource_path
):

    normalized_path = (
        normalize_resource_path(
            resource_path
        )
    )


    allowed_routes = {

        "GET": [

            "/products",

            "/products/{id}",

            "/orders",

            "/orders/{id}"

        ],

        "POST": [

            "/products"

        ],

        "PUT": [

            "/products/{id}"

        ],

        "DELETE": [

            "/products/{id}"

        ],

        "PATCH": [

            "/orders/{id}/status"

        ]

    }


    allowed_paths = (
        allowed_routes.get(
            http_method,
            []
        )
    )


    return (
        normalized_path
        in allowed_paths
    )


# ==========================================================
# CUSTOMER AUTHORIZATION
# ==========================================================

def customer_is_allowed(
    http_method,
    resource_path
):

    normalized_path = (
        normalize_resource_path(
            resource_path
        )
    )


    allowed_routes = {

        "GET": [

            "/products",

            "/products/{id}",

            "/orders",

            "/orders/{id}"

        ],

        "POST": [

            "/orders"

        ],

        "PATCH": [

            "/orders/{id}/status"

        ]

    }


    allowed_paths = (
        allowed_routes.get(
            http_method,
            []
        )
    )


    return (
        normalized_path
        in allowed_paths
    )


# ==========================================================
# EXTRACT AUTHORIZATION TOKEN
# ==========================================================

def get_token(
    event
):

    authorization_header = event.get(
        "authorizationToken",
        ""
    )


    # ======================================================
    # FALLBACK
    # ======================================================

    if not authorization_header:

        headers = event.get(
            "headers",
            {}
        ) or {}


        authorization_header = (

            headers.get(
                "Authorization"
            )

            or

            headers.get(
                "authorization"
            )

            or

            ""

        )


    if not authorization_header:

        return None


    provided_token = (
        authorization_header.strip()
    )


    # ======================================================
    # REMOVE BEARER
    # ======================================================

    if provided_token.lower().startswith(
        "bearer "
    ):

        provided_token = (
            provided_token[
                7:
            ].strip()
        )


    if not provided_token:

        return None


    return provided_token


# ==========================================================
# LAMBDA HANDLER
# ==========================================================

def lambda_handler(
    event,
    context
):

    method_arn = event.get(
        "methodArn",
        "*"
    )


    request_id = getattr(
        context,
        "aws_request_id",
        "unknown"
    )


    try:

        # ==================================================
        # LOG REQUEST
        # ==================================================

        log_event(

            "INFO",

            "Authorization request received",

            request_id=request_id,

            method_arn=method_arn

        )


        # ==================================================
        # GET TOKEN
        # ==================================================

        provided_token = get_token(
            event
        )


        if not provided_token:

            log_event(

                "WARNING",

                "Authorization token missing",

                request_id=request_id

            )


            return generate_policy(

                principal_id="unauthorized",

                effect="Deny",

                resource=method_arn

            )


        # ==================================================
        # GET PARAMETERS
        # ==================================================

        admin_token_parameter = os.environ[
            "ADMIN_TOKEN_PARAMETER"
        ]


        customer_tokens_parameter = os.environ[
            "CUSTOMER_TOKENS_PARAMETER"
        ]


        admin_token = get_parameter(
            admin_token_parameter
        ).strip()


        customer_tokens_json = (
            get_parameter(
                customer_tokens_parameter
            )
        )


        customer_tokens = json.loads(
            customer_tokens_json
        )


        # ==================================================
        # VALIDATE CUSTOMER TOKENS FORMAT
        # ==================================================

        if not isinstance(
            customer_tokens,
            dict
        ):

            raise ValueError(
                "Customer tokens parameter must be a JSON object"
            )


        # ==================================================
        # GET REQUEST DETAILS
        # ==================================================

        request_details = (
            get_request_details(
                method_arn
            )
        )


        http_method = (
            request_details[
                "http_method"
            ]
        )


        resource_path = (
            request_details[
                "resource_path"
            ]
        )


        normalized_path = (
            normalize_resource_path(
                resource_path
            )
        )


        # ==================================================
        # ADMIN AUTHENTICATION
        # ==================================================

        if provided_token == admin_token:


            # ==============================================
            # CHECK ADMIN PERMISSION
            # ==============================================

            if not admin_is_allowed(

                http_method,

                resource_path

            ):


                log_event(

                    "WARNING",

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


            # ==============================================
            # ADMIN SUCCESS
            # ==============================================

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

                "WARNING",

                "Invalid customer token",

                request_id=request_id

            )


            return generate_policy(

                principal_id="unauthorized",

                effect="Deny",

                resource=method_arn

            )


        # ==================================================
        # VALIDATE CUSTOMER ID
        # ==================================================

        try:

            customer_id = int(
                customer_id
            )


        except (
            TypeError,
            ValueError
        ):

            log_event(

                "ERROR",

                "Invalid customer ID in token mapping",

                request_id=request_id,

                customer_value=str(
                    customer_id
                )

            )


            return generate_policy(

                principal_id="unauthorized",

                effect="Deny",

                resource=method_arn

            )


        # ==================================================
        # CUSTOMER ENDPOINT AUTHORIZATION
        # ==================================================

        if not customer_is_allowed(

            http_method,

            resource_path

        ):


            log_event(

                "WARNING",

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
        # CUSTOMER SUCCESS
        # ==================================================

        log_event(

            "INFO",

            "Customer authorization successful",

            request_id=request_id,

            customer_id=customer_id,

            http_method=http_method,

            resource=normalized_path

        )


        # ==================================================
        # VERY IMPORTANT
        #
        # THIS customer_id IS SENT TO
        # requestContext.authorizer.customer_id
        #
        # ORDER LAMBDA WILL USE THIS VALUE
        # ==================================================

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

            error_type=type(
                error
            ).__name__,

            error=str(
                error
            )

        )


        return generate_policy(

            principal_id="unauthorized",

            effect="Deny",

            resource=method_arn

        )