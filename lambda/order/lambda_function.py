import json
import os

import boto3
import pymysql


# ==========================================================
# AWS CLIENTS
# ==========================================================

ssm = boto3.client("ssm")
lambda_client = boto3.client("lambda")


# ==========================================================
# ENVIRONMENT VARIABLES
# ==========================================================

DB_NAME_PARAMETER = os.environ["DB_NAME_PARAMETER"]
DB_ENDPOINT_PARAMETER = os.environ["DB_ENDPOINT_PARAMETER"]
DB_PORT_PARAMETER = os.environ["DB_PORT_PARAMETER"]
DB_USERNAME_PARAMETER = os.environ["DB_USERNAME_PARAMETER"]
DB_PASSWORD_PARAMETER = os.environ["DB_PASSWORD_PARAMETER"]

ORDER_PROCESSOR_FUNCTION_NAME = os.environ[
    "ORDER_PROCESSOR_FUNCTION_NAME"
]


# ==========================================================
# STRUCTURED LOGGING
# ==========================================================

def log_event(level, message, **details):

    record = {
        "level": level,
        "service": "cloudmart-order-lambda",
        "message": message,
        **details
    }

    print(
        json.dumps(
            record,
            default=str
        )
    )


# ==========================================================
# API RESPONSE
# ==========================================================

def response(
    status_code,
    body
):

    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json"
        },
        "body": json.dumps(
            body,
            default=str
        )
    }


# ==========================================================
# SSM PARAMETER
# ==========================================================

def get_parameter(name):

    parameter = ssm.get_parameter(
        Name=name,
        WithDecryption=True
    )

    return parameter[
        "Parameter"
    ][
        "Value"
    ]


# ==========================================================
# DATABASE CONNECTION
# ==========================================================

def get_db_connection():

    db_name = get_parameter(
        DB_NAME_PARAMETER
    )

    db_host = get_parameter(
        DB_ENDPOINT_PARAMETER
    )

    db_port = int(
        get_parameter(
            DB_PORT_PARAMETER
        )
    )

    db_username = get_parameter(
        DB_USERNAME_PARAMETER
    )

    db_password = get_parameter(
        DB_PASSWORD_PARAMETER
    )

    return pymysql.connect(
        host=db_host,
        port=db_port,
        user=db_username,
        password=db_password,
        database=db_name,
        connect_timeout=10,
        read_timeout=10,
        write_timeout=10,
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False
    )


# ==========================================================
# PARSE BODY
# ==========================================================

def parse_body(event):

    body = event.get(
        "body"
    )

    if body is None:

        return {}

    if isinstance(
        body,
        dict
    ):

        return body

    if not isinstance(
        body,
        str
    ):

        raise ValueError(
            "Request body must be JSON"
        )

    if not body.strip():

        return {}

    try:

        parsed = json.loads(
            body
        )

    except json.JSONDecodeError:

        raise ValueError(
            "Request body contains invalid JSON"
        )

    if not isinstance(
        parsed,
        dict
    ):

        raise ValueError(
            "Request body must be a JSON object"
        )

    return parsed


# ==========================================================
# INVOKE ORDER PROCESSOR
# ==========================================================

def invoke_order_processor(
    order_data,
    context
):

    payload = json.dumps(
        order_data
    ).encode(
        "utf-8"
    )

    result = lambda_client.invoke(
        FunctionName=
            ORDER_PROCESSOR_FUNCTION_NAME,

        InvocationType=
            "RequestResponse",

        Payload=
            payload
    )

    raw_payload = result[
        "Payload"
    ].read()

    if isinstance(
        raw_payload,
        bytes
    ):

        raw_payload = raw_payload.decode(
            "utf-8"
        )

    processor_response = json.loads(
        raw_payload
    )

    log_event(
        "INFO",
        "Order processor invoked",
        request_id=
            context.aws_request_id,
        processor_status_code=
            processor_response.get(
                "statusCode"
            )
    )

    return processor_response


# ==========================================================
# POST /orders
# ==========================================================

def create_order(
    event,
    context
):

    order_data = parse_body(
        event
    )

    if "customer_id" not in order_data:

        return response(
            400,
            {
                "message":
                    "customer_id is required"
            }
        )

    if "items" not in order_data:

        return response(
            400,
            {
                "message":
                    "items is required"
            }
        )

    processor_response = (
        invoke_order_processor(
            order_data,
            context
        )
    )

    status_code = processor_response.get(
        "statusCode",
        500
    )

    processor_body = processor_response.get(
        "body",
        "{}"
    )

    try:

        processor_body = json.loads(
            processor_body
        )

    except (
        TypeError,
        json.JSONDecodeError
    ):

        processor_body = {
            "message":
                "Invalid processor response"
        }

    if status_code >= 400:

        return response(
            status_code,
            processor_body
        )

    log_event(
        "INFO",
        "Order created",
        request_id=
            context.aws_request_id,
        order_id=
            processor_body.get(
                "order_id"
            )
    )

    return response(
        201,
        processor_body
    )


# ==========================================================
# GET /orders/{id}
# ==========================================================

def get_order(
    event,
    context
):

    path_parameters = (
        event.get(
            "pathParameters"
        ) or {}
    )

    order_id = path_parameters.get(
        "id"
    )

    if order_id is None:

        return response(
            400,
            {
                "message":
                    "Order id is required"
            }
        )

    try:

        order_id = int(
            order_id
        )

    except (
        TypeError,
        ValueError
    ):

        return response(
            400,
            {
                "message":
                    "Order id must be an integer"
            }
        )

    connection = None

    try:

        connection = get_db_connection()

        with connection.cursor() as cursor:

            # --------------------------------------------------
            # Order
            # --------------------------------------------------

            cursor.execute(
                """
                SELECT
                    order_id,
                    customer_id,
                    status,
                    order_date,
                    total_amount,
                    created_at,
                    updated_at
                FROM orders
                WHERE order_id = %s
                """,
                (order_id,)
            )

            order = cursor.fetchone()

            if order is None:

                return response(
                    404,
                    {
                        "message":
                            "Order not found"
                    }
                )

            # --------------------------------------------------
            # Order items
            # --------------------------------------------------

            cursor.execute(
                """
                SELECT
                    order_item_id,
                    order_id,
                    product_id,
                    quantity,
                    unit_price
                FROM order_items
                WHERE order_id = %s
                ORDER BY order_item_id
                """,
                (order_id,)
            )

            items = cursor.fetchall()

        order["items"] = items

        log_event(
            "INFO",
            "Order retrieved",
            request_id=
                context.aws_request_id,
            order_id=
                order_id
        )

        return response(
            200,
            order
        )

    finally:

        if connection is not None:

            connection.close()


# ==========================================================
# GET /orders?customerId=X
# ==========================================================

def get_customer_orders(
    event,
    context
):

    query_parameters = (
        event.get(
            "queryStringParameters"
        ) or {}
    )

    customer_id = query_parameters.get(
        "customerId"
    )

    if customer_id is None:

        return response(
            400,
            {
                "message":
                    "customerId query parameter is required"
            }
        )

    try:

        customer_id = int(
            customer_id
        )

    except (
        TypeError,
        ValueError
    ):

        return response(
            400,
            {
                "message":
                    "customerId must be an integer"
            }
        )

    if customer_id <= 0:

        return response(
            400,
            {
                "message":
                    "customerId must be greater than zero"
            }
        )

    connection = None

    try:

        connection = get_db_connection()

        with connection.cursor() as cursor:

            cursor.execute(
                """
                SELECT
                    order_id,
                    customer_id,
                    status,
                    order_date,
                    total_amount,
                    created_at,
                    updated_at
                FROM orders
                WHERE customer_id = %s
                ORDER BY order_id DESC
                """,
                (customer_id,)
            )

            orders = cursor.fetchall()

            # --------------------------------------------------
            # Retrieve items for each order
            # --------------------------------------------------

            for order in orders:

                cursor.execute(
                    """
                    SELECT
                        order_item_id,
                        order_id,
                        product_id,
                        quantity,
                        unit_price
                    FROM order_items
                    WHERE order_id = %s
                    ORDER BY order_item_id
                    """,
                    (
                        order[
                            "order_id"
                        ],
                    )
                )

                order["items"] = (
                    cursor.fetchall()
                )

        log_event(
            "INFO",
            "Customer orders retrieved",
            request_id=
                context.aws_request_id,
            customer_id=
                customer_id,
            count=
                len(orders)
        )

        return response(
            200,
            orders
        )

    finally:

        if connection is not None:

            connection.close()


# ==========================================================
# MAIN LAMBDA HANDLER
# ==========================================================

def lambda_handler(
    event,
    context
):

    request_id = (
        context.aws_request_id
    )

    http_method = (
        event.get(
            "httpMethod"
        )
        or
        event.get(
            "requestContext",
            {}
        )
        .get(
            "http",
            {}
        )
        .get(
            "method"
        )
    )

    resource = event.get(
        "resource",
        ""
    )

    log_event(
        "INFO",
        "Order request received",
        request_id=
            request_id,
        http_method=
            http_method,
        resource=
            resource
    )

    try:

        # ------------------------------------------------------
        # POST /orders
        # ------------------------------------------------------

        if (
            http_method == "POST"
            and resource == "/orders"
        ):

            return create_order(
                event,
                context
            )

        # ------------------------------------------------------
        # GET /orders/{id}
        # ------------------------------------------------------

        if (
            http_method == "GET"
            and resource == "/orders/{id}"
        ):

            return get_order(
                event,
                context
            )

        # ------------------------------------------------------
        # GET /orders?customerId=X
        # ------------------------------------------------------

        if (
            http_method == "GET"
            and resource == "/orders"
        ):

            return get_customer_orders(
                event,
                context
            )

        return response(
            404,
            {
                "message":
                    "Unsupported API route"
            }
        )

    except ValueError as exc:

        log_event(
            "WARN",
            "Invalid order request",
            request_id=
                request_id,
            error=str(exc)
        )

        return response(
            400,
            {
                "message":
                    str(exc)
            }
        )

    except Exception as exc:

        log_event(
            "ERROR",
            "Order request failed",
            request_id=
                request_id,
            error_type=
                type(exc).__name__,
            error=str(exc)
        )

        return response(
            500,
            {
                "message":
                    "Internal server error",

                "request_id":
                    request_id
            }
        )