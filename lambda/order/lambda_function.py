import json
import os
import time

import boto3
import pymysql
from botocore.config import Config


# ==========================================================
# AWS CLIENTS
# ==========================================================

AWS_API_CONFIG = Config(
    connect_timeout=2,
    read_timeout=3,
    retries={"max_attempts": 1, "mode": "standard"}
)

ORDER_PROCESSOR_INVOKE_CONFIG = Config(
    connect_timeout=2,
    read_timeout=22,
    retries={"max_attempts": 1, "mode": "standard"}
)

ssm = boto3.client("ssm", config=AWS_API_CONFIG)
events = boto3.client("events", config=AWS_API_CONFIG)
lambda_client = boto3.client(
    "lambda",
    config=ORDER_PROCESSOR_INVOKE_CONFIG
)
cloudwatch = boto3.client("cloudwatch", config=AWS_API_CONFIG)

_PARAMETER_CACHE = {}
_PARAMETER_CACHE_TTL_SECONDS = 300


# ==========================================================
# ENVIRONMENT VARIABLES
# ==========================================================

DB_NAME_PARAMETER = os.environ["DB_NAME_PARAMETER"]
DB_ENDPOINT_PARAMETER = os.environ["DB_ENDPOINT_PARAMETER"]
DB_PORT_PARAMETER = os.environ["DB_PORT_PARAMETER"]
DB_USERNAME_PARAMETER = os.environ["DB_USERNAME_PARAMETER"]
DB_PASSWORD_PARAMETER = os.environ["DB_PASSWORD_PARAMETER"]

EVENT_BUS_NAME = os.environ["EVENT_BUS_NAME"]

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

def response(status_code, body):

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
    """Read an SSM parameter with a short warm-container cache."""
    now = time.monotonic()
    cached = _PARAMETER_CACHE.get(name)

    if cached is not None:
        cached_value, cached_at = cached
        if now - cached_at < _PARAMETER_CACHE_TTL_SECONDS:
            return cached_value

    try:
        parameter = ssm.get_parameter(
            Name=name,
            WithDecryption=True
        )
        value = parameter["Parameter"]["Value"]
    except Exception as exc:
        log_event(
            "ERROR",
            "Unable to read SSM parameter",
            parameter_name=name,
            error_type=type(exc).__name__,
            error=str(exc)
        )
        raise RuntimeError(
            "CloudMart configuration could not be loaded"
        ) from exc

    _PARAMETER_CACHE[name] = (value, now)
    return value



# ==========================================================
# DATABASE CONNECTION
# ==========================================================

def get_db_connection():
    connection = None

    try:
        connection = pymysql.connect(
            host=get_parameter(DB_ENDPOINT_PARAMETER),
            port=int(get_parameter(DB_PORT_PARAMETER)),
            user=get_parameter(DB_USERNAME_PARAMETER),
            password=get_parameter(DB_PASSWORD_PARAMETER),
            database=get_parameter(DB_NAME_PARAMETER),
            connect_timeout=5,
            read_timeout=5,
            write_timeout=5,
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=False
        )

        with connection.cursor() as cursor:
            cursor.execute(
                "SET SESSION innodb_lock_wait_timeout = 5"
            )

        return connection

    except Exception:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass
        raise



# ==========================================================
# PARSE REQUEST BODY
# ==========================================================

def parse_body(event):

    body = event.get("body")

    if body is None:

        return {}

    if isinstance(body, dict):

        return body

    if not isinstance(body, str):

        raise ValueError(
            "Request body must be JSON"
        )

    if not body.strip():

        return {}

    try:

        parsed = json.loads(body)

    except json.JSONDecodeError:

        raise ValueError(
            "Request body contains invalid JSON"
        )

    if not isinstance(parsed, dict):

        raise ValueError(
            "Request body must be a JSON object"
        )

    return parsed


# ==========================================================
# GET AUTHORIZER CONTEXT
# ==========================================================

def get_auth_context(event):

    request_context = event.get(
        "requestContext",
        {}
    )

    authorizer = request_context.get(
        "authorizer",
        {}
    )

    role = str(
        authorizer.get(
            "role",
            ""
        )
    ).strip().upper()

    customer_id = authorizer.get(
        "customer_id",
        ""
    )

    if customer_id:

        try:

            customer_id = int(
                customer_id
            )

        except (
            TypeError,
            ValueError
        ):

            customer_id = None

    else:

        customer_id = None

    return (
        role,
        customer_id
    )


def get_authorizer_token_hash(event):
    """
    Read the SHA-256 bearer-token hash placed in authorizer context.

    The same token may belong to multiple customers, so the token hash
    is used together with the customer_id from the URL to authorize
    the specific customer.
    """
    request_context = event.get(
        "requestContext",
        {}
    )

    authorizer = request_context.get(
        "authorizer",
        {}
    )

    token_hash = authorizer.get(
        "token_hash"
    )

    if not isinstance(token_hash, str):
        return None

    token_hash = token_hash.strip().lower()

    if len(token_hash) != 64:
        return None

    return token_hash


# ==========================================================
# GET PATH PARAMETER
# ==========================================================

def get_path_parameter(
    event,
    parameter_name
):

    path_parameters = (
        event.get(
            "pathParameters"
        )
        or {}
    )

    value = path_parameters.get(
        parameter_name
    )

    return value


# ==========================================================
# GET CUSTOMER ID FROM URL
# ==========================================================

def get_url_customer_id(event):

    customer_id = get_path_parameter(
        event,
        "customer_id"
    )

    if customer_id is None:

        raise ValueError(
            "Customer id is required in URL"
        )

    try:

        customer_id = int(
            customer_id
        )

    except (
        TypeError,
        ValueError
    ):

        raise ValueError(
            "Customer id must be an integer"
        )

    if customer_id <= 0:

        raise ValueError(
            "Customer id must be greater than zero"
        )

    return customer_id


# ==========================================================
# GET ORDER ID
# ==========================================================

def get_order_id(event):

    path_parameters = (
        event.get(
            "pathParameters"
        )
        or {}
    )

    order_id = (
        path_parameters.get("order_id")
        or
        path_parameters.get("id")
    )

    if order_id is None:

        raise ValueError(
            "Order id is required"
        )

    try:

        order_id = int(
            order_id
        )

    except (
        TypeError,
        ValueError
    ):

        raise ValueError(
            "Order id must be an integer"
        )

    if order_id <= 0:

        raise ValueError(
            "Order id must be greater than zero"
        )

    return order_id


# ==========================================================
# VALIDATE CUSTOMER URL
# ==========================================================

def validate_customer_access(
    event,
    allow_admin=False
):

    role, _authenticated_customer_id = (
        get_auth_context(event)
    )

    url_customer_id = get_url_customer_id(
        event
    )

    if role == "CUSTOMER":

        token_hash = get_authorizer_token_hash(
            event
        )

        if token_hash is None:

            raise PermissionError(
                "Customer bearer-token context is missing"
            )

        # IMPORTANT:
        # bearer_token is intentionally NOT UNIQUE. The URL customer_id
        # selects the customer, while the bearer-token hash proves that
        # the same token is associated with that customer.
        connection = None

        try:

            connection = get_db_connection()

            with connection.cursor() as cursor:

                cursor.execute(
                    """
                    SELECT customer_id
                    FROM customers
                    WHERE customer_id = %s
                      AND bearer_token = %s
                      AND status = 'ACTIVE'
                      AND deleted_at IS NULL
                    LIMIT 1
                    """,
                    (
                        url_customer_id,
                        token_hash
                    )
                )

                customer = cursor.fetchone()

            if customer is None:

                raise PermissionError(
                    "Bearer token is not authorized for this customer"
                )

            return (
                role,
                url_customer_id
            )

        finally:

            if connection is not None:
                connection.close()

    if (
        role == "ADMIN"
        and
        allow_admin
    ):

        return (
            role,
            url_customer_id
        )

    raise PermissionError(
        "Unauthorized user"
    )


# ==========================================================
# VALIDATE ORDER ITEMS
# ==========================================================

def validate_order_items(items):

    if (
        not isinstance(items, list)
        or
        not items
    ):

        raise ValueError(
            "items must be a non-empty array"
        )

    if len(items) > 50:
        raise ValueError(
            "A maximum of 50 products can be included in one order"
        )

    validated_items = []

    product_ids = set()

    for item in items:

        if not isinstance(
            item,
            dict
        ):

            raise ValueError(
                "Each item must be an object"
            )

        product_id = item.get(
            "product_id"
        )

        quantity = item.get(
            "quantity"
        )

        if product_id is None:

            raise ValueError(
                "product_id is required"
            )

        if quantity is None:

            raise ValueError(
                "quantity is required"
            )

        try:

            product_id = int(
                product_id
            )

            quantity = int(
                quantity
            )

        except (
            TypeError,
            ValueError
        ):

            raise ValueError(
                "product_id and quantity must be integers"
            )

        if product_id <= 0:

            raise ValueError(
                "product_id must be greater than zero"
            )

        if quantity <= 0:

            raise ValueError(
                "quantity must be greater than zero"
            )

        if product_id in product_ids:

            raise ValueError(
                "Duplicate product_id is not allowed"
            )

        product_ids.add(
            product_id
        )

        validated_items.append(
            {
                "product_id": product_id,
                "quantity": quantity
            }
        )

    return validated_items


# ==========================================================
# INVOKE ORDER PROCESSOR
# ==========================================================

def invoke_order_processor(
    order_data,
    context
):
    payload = json.dumps(
        order_data,
        default=str
    ).encode("utf-8")

    try:
        result = lambda_client.invoke(
            FunctionName=ORDER_PROCESSOR_FUNCTION_NAME,
            InvocationType="RequestResponse",
            Payload=payload
        )

        if result.get("FunctionError"):
            log_event(
                "ERROR",
                "Order processor returned a function error",
                request_id=context.aws_request_id,
                function_error=result.get("FunctionError")
            )
            raise RuntimeError(
                "Order processor Lambda execution failed"
            )

        raw_payload = result["Payload"].read()

        if isinstance(raw_payload, bytes):
            raw_payload = raw_payload.decode("utf-8")

        if not raw_payload:
            raise RuntimeError(
                "Order processor returned an empty response"
            )

        try:
            processor_response = json.loads(raw_payload)
        except json.JSONDecodeError as exc:
            log_event(
                "ERROR",
                "Order processor returned invalid JSON",
                request_id=context.aws_request_id,
                error=str(exc)
            )
            raise RuntimeError(
                "Order processor returned an invalid response"
            ) from exc

        if not isinstance(processor_response, dict):
            raise RuntimeError(
                "Order processor returned an invalid response object"
            )

        log_event(
            "INFO",
            "Order processor invoked",
            request_id=context.aws_request_id,
            processor_status_code=processor_response.get("statusCode")
        )

        return processor_response

    except Exception as exc:
        log_event(
            "ERROR",
            "Order processor invocation failed",
            request_id=context.aws_request_id,
            error_type=type(exc).__name__,
            error=str(exc)
        )
        raise



# ==========================================================
# PUBLISH CUSTOM CLOUDWATCH BUSINESS METRIC
# ==========================================================

def publish_custom_metric(metric_name, value=1):

    try:

        cloudwatch.put_metric_data(
            Namespace="CloudMart/Business",
            MetricData=[
                {
                    "MetricName": metric_name,
                    "Value": value,
                    "Unit": "Count"
                }
            ]
        )

        log_event(
            "INFO",
            "Custom CloudWatch metric published",
            metric_name=metric_name,
            value=value
        )

    except Exception as exc:

        # Metric publication must not change a successful database operation
        log_event(
            "ERROR",
            "Custom CloudWatch metric publication failed",
            metric_name=metric_name,
            value=value,
            error_type=type(exc).__name__,
            error=str(exc)
        )


# ==========================================================
# PUBLISH ORDER EVENT
# ==========================================================

def publish_order_event(
    detail_type,
    order_id,
    customer_id,
    status,
    total_amount,
    reason=None,
    items=None
):
    detail = {
        "order_id": order_id,
        "customer_id": customer_id,
        "status": status,
        "total_amount": str(total_amount)
    }

    if reason:
        detail["reason"] = reason

    if items is not None:
        detail["items"] = items

    try:
        result = events.put_events(
            Entries=[
                {
                    "EventBusName": EVENT_BUS_NAME,
                    "Source": "cloudmart.orders",
                    "DetailType": detail_type,
                    "Detail": json.dumps(detail, default=str)
                }
            ]
        )

        if result.get("FailedEntryCount", 0):
            log_event(
                "WARN",
                "Order event publishing failed; database state retained",
                order_id=order_id,
                detail_type=detail_type,
                event_result=result
            )
            return False

        log_event(
            "INFO",
            "Order event published",
            order_id=order_id,
            detail_type=detail_type
        )
        return True

    except Exception as exc:
        log_event(
            "WARN",
            "Order event publishing skipped; database state retained",
            order_id=order_id,
            detail_type=detail_type,
            error_type=type(exc).__name__,
            error=str(exc)
        )
        return False



# ==========================================================
# PUBLISH INVENTORY EVENT
# ==========================================================

def publish_inventory_event(
    product_id,
    old_stock,
    new_stock
):
    detail = {
        "product_id": product_id,
        "old_stock": old_stock,
        "new_stock": new_stock
    }

    try:
        result = events.put_events(
            Entries=[
                {
                    "EventBusName": EVENT_BUS_NAME,
                    "Source": "cloudmart.inventory",
                    "DetailType": "InventoryChanged",
                    "Detail": json.dumps(detail)
                }
            ]
        )

        if result.get("FailedEntryCount", 0):
            log_event(
                "WARN",
                "Inventory event publishing failed; database state retained",
                product_id=product_id,
                old_stock=old_stock,
                new_stock=new_stock,
                event_result=result
            )
            return False

        log_event(
            "INFO",
            "Inventory event published",
            product_id=product_id,
            old_stock=old_stock,
            new_stock=new_stock
        )
        return True

    except Exception as exc:
        log_event(
            "WARN",
            "Inventory event publishing skipped; database state retained",
            product_id=product_id,
            old_stock=old_stock,
            new_stock=new_stock,
            error_type=type(exc).__name__,
            error=str(exc)
        )
        return False



# ==========================================================
# POST
# /customers/{customer_id}/orders
# ==========================================================

def create_order(
    event,
    context
):

    try:

        (
            role,
            customer_id
        ) = validate_customer_access(
            event
        )

        request_body = parse_body(
            event
        )

        items = validate_order_items(
            request_body.get(
                "items"
            )
        )

        # ----------------------------------------------
        # customer_id is NEVER taken from request body
        # ----------------------------------------------

        order_data = {
            "customer_id":
                customer_id,

            "items":
                items
        }

        processor_response = (
            invoke_order_processor(
                order_data,
                context
            )
        )

        status_code = (
            processor_response.get(
                "statusCode",
                500
            )
        )

        response_body = (
            processor_response.get(
                "body",
                {}
            )
        )

        if isinstance(
            response_body,
            str
        ):

            try:

                response_body = json.loads(
                    response_body
                )

            except json.JSONDecodeError:

                response_body = {
                    "message":
                        response_body
                }

        return response(
            status_code,
            response_body
        )

    except PermissionError as exc:

        return response(
            403,
            {
                "message":
                    str(exc)
            }
        )

    except ValueError as exc:

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
            "Order creation failed",
            request_id=context.aws_request_id,
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
                    context.aws_request_id
            }
        )


# ==========================================================
# GET CUSTOMER ORDERS
#
# /customers/{customer_id}/orders
# ==========================================================

def get_customer_orders(
    event,
    context
):

    connection = None

    try:

        (
            role,
            customer_id
        ) = validate_customer_access(
            event
        )

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
                (
                    customer_id,
                )
            )

            orders = cursor.fetchall()

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

        return response(
            200,
            orders
        )

    except PermissionError as exc:

        return response(
            403,
            {
                "message":
                    str(exc)
            }
        )

    except ValueError as exc:

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
            "Get customer orders failed",
            request_id=context.aws_request_id,
            error_type=
                type(exc).__name__,
            error=str(exc)
        )

        return response(
            500,
            {
                "message":
                    "Internal server error"
            }
        )

    finally:

        if connection is not None:

            connection.close()


# ==========================================================
# GET CUSTOMER ORDER
#
# /customers/{customer_id}/orders/{order_id}
# ==========================================================

def get_customer_order(
    event,
    context
):

    connection = None

    try:

        (
            role,
            customer_id
        ) = validate_customer_access(
            event
        )

        order_id = get_order_id(
            event
        )

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
                WHERE order_id = %s
                  AND customer_id = %s
                """,
                (
                    order_id,
                    customer_id
                )
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
                    order_id,
                )
            )

            order["items"] = (
                cursor.fetchall()
            )

        return response(
            200,
            order
        )

    except PermissionError as exc:

        return response(
            403,
            {
                "message":
                    str(exc)
            }
        )

    except ValueError as exc:

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
            "Get customer order failed",
            request_id=context.aws_request_id,
            error_type=
                type(exc).__name__,
            error=str(exc)
        )

        return response(
            500,
            {
                "message":
                    "Internal server error"
            }
        )

    finally:

        if connection is not None:

            connection.close()


# ==========================================================
# UPDATE CUSTOMER ORDER
#
# PUT
# /customers/{customer_id}/orders/{order_id}
# ==========================================================

def update_customer_order(
    event,
    context
):

    connection = None

    inventory_events = []

    try:

        (
            role,
            customer_id
        ) = validate_customer_access(
            event
        )

        order_id = get_order_id(
            event
        )

        request_body = parse_body(
            event
        )

        new_items = validate_order_items(
            request_body.get(
                "items"
            )
        )

        connection = get_db_connection()

        with connection.cursor() as cursor:

            # ----------------------------------------------
            # LOCK ORDER
            # ----------------------------------------------

            cursor.execute(
                """
                SELECT
                    order_id,
                    customer_id,
                    status,
                    total_amount
                FROM orders
                WHERE order_id = %s
                  AND customer_id = %s
                FOR UPDATE
                """,
                (
                    order_id,
                    customer_id
                )
            )

            order = cursor.fetchone()

            if order is None:

                raise ValueError(
                    "Order not found"
                )

            # ----------------------------------------------
            # Only CONFIRMED orders can be updated
            # ----------------------------------------------

            if order[
                "status"
            ] != "CONFIRMED":

                raise ValueError(
                    "Only CONFIRMED orders can be updated"
                )

            # ----------------------------------------------
            # GET OLD ITEMS
            # ----------------------------------------------

            cursor.execute(
                """
                SELECT
                    product_id,
                    quantity,
                    unit_price
                FROM order_items
                WHERE order_id = %s
                """,
                (
                    order_id,
                )
            )

            old_items = cursor.fetchall()

            # ----------------------------------------------
            # RESTORE OLD INVENTORY
            # ----------------------------------------------

            for item in old_items:

                cursor.execute(
                    """
                    SELECT
                        product_id,
                        stock_quantity
                    FROM products
                    WHERE product_id = %s
                    FOR UPDATE
                    """,
                    (
                        item[
                            "product_id"
                        ],
                    )
                )

                product = cursor.fetchone()

                if product is None:

                    raise ValueError(
                        "Product no longer exists"
                    )

                old_stock = product[
                    "stock_quantity"
                ]

                restored_stock = (
                    old_stock
                    +
                    item["quantity"]
                )

                cursor.execute(
                    """
                    UPDATE products
                    SET stock_quantity = %s
                    WHERE product_id = %s
                    """,
                    (
                        restored_stock,
                        item[
                            "product_id"
                        ]
                    )
                )

                inventory_events.append(
                    {
                        "product_id":
                            item["product_id"],

                        "old_stock":
                            old_stock,

                        "new_stock":
                            restored_stock
                    }
                )

            # ----------------------------------------------
            # VALIDATE AND LOCK NEW PRODUCTS
            # ----------------------------------------------

            processed_items = []

            total_amount = 0

            for item in new_items:

                cursor.execute(
                    """
                    SELECT
                        product_id,
                        name,
                        price,
                        stock_quantity,
                        status,
                        reorder_threshold
                    FROM products
                    WHERE product_id = %s
                      AND status = 'ACTIVE'
                    FOR UPDATE
                    """,
                    (
                        item[
                            "product_id"
                        ],
                    )
                )

                product = cursor.fetchone()

                if product is None:

                    raise ValueError(
                        f"Product {item['product_id']} is not available"
                    )

                quantity = item[
                    "quantity"
                ]

                available_stock = product[
                    "stock_quantity"
                ]

                if available_stock < quantity:

                    raise ValueError(
                        f"Insufficient stock for product "
                        f"{item['product_id']}"
                    )

                unit_price = product[
                    "price"
                ]

                item_total = (
                    unit_price
                    *
                    quantity
                )

                total_amount += (
                    item_total
                )

                processed_items.append(
                    {
                        "product_id":
                            product[
                                "product_id"
                            ],

                        "product_name":
                            product[
                                "name"
                            ],

                        "quantity":
                            quantity,

                        "unit_price":
                            unit_price,

                        "available_stock":
                            available_stock
                    }
                )

            # ----------------------------------------------
            # DELETE OLD ORDER ITEMS
            # ----------------------------------------------

            cursor.execute(
                """
                DELETE FROM order_items
                WHERE order_id = %s
                """,
                (
                    order_id,
                )
            )

            # ----------------------------------------------
            # CREATE NEW ORDER ITEMS
            # ----------------------------------------------

            for item in processed_items:

                cursor.execute(
                    """
                    INSERT INTO order_items (
                        order_id,
                        product_id,
                        quantity,
                        unit_price
                    )
                    VALUES (
                        %s,
                        %s,
                        %s,
                        %s
                    )
                    """,
                    (
                        order_id,
                        item[
                            "product_id"
                        ],
                        item[
                            "quantity"
                        ],
                        item[
                            "unit_price"
                        ]
                    )
                )

            # ----------------------------------------------
            # DEDUCT NEW INVENTORY
            # ----------------------------------------------

            for item in processed_items:

                old_stock = item[
                    "available_stock"
                ]

                new_stock = (
                    old_stock
                    -
                    item["quantity"]
                )

                cursor.execute(
                    """
                    UPDATE products
                    SET stock_quantity = %s
                    WHERE product_id = %s
                    """,
                    (
                        new_stock,
                        item[
                            "product_id"
                        ]
                    )
                )

                inventory_events.append(
                    {
                        "product_id":
                            item[
                                "product_id"
                            ],

                        "old_stock":
                            old_stock,

                        "new_stock":
                            new_stock
                    }
                )

            # ----------------------------------------------
            # UPDATE ORDER TOTAL
            # ----------------------------------------------

            cursor.execute(
                """
                UPDATE orders
                SET
                    total_amount = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE order_id = %s
                """,
                (
                    total_amount,
                    order_id
                )
            )

            # ----------------------------------------------
            # ORDER LOG
            # ----------------------------------------------

            cursor.execute(
                """
                INSERT INTO order_logs (
                    order_id,
                    previous_status,
                    new_status,
                    changed_by,
                    note
                )
                VALUES (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s
                )
                """,
                (
                    order_id,
                    order[
                        "status"
                    ],
                    order[
                        "status"
                    ],
                    f"customer-{customer_id}",
                    "Order items and quantities updated"
                )
            )

        # ----------------------------------------------
        # COMMIT EVERYTHING TOGETHER
        # ----------------------------------------------

        connection.commit()

        # ----------------------------------------------
        # PUBLISH INVENTORY EVENTS
        # ----------------------------------------------

        for inventory in inventory_events:

            try:

                publish_inventory_event(
                    product_id=
                        inventory[
                            "product_id"
                        ],

                    old_stock=
                        inventory[
                            "old_stock"
                        ],

                    new_stock=
                        inventory[
                            "new_stock"
                        ]
                )

            except Exception as exc:

                log_event(
                    "ERROR",
                    "Inventory event publishing failed",
                    order_id=order_id,
                    error=str(exc)
                )

        # ----------------------------------------------
        # PUBLISH ORDER UPDATED EVENT
        # ----------------------------------------------

        publish_order_event(
            detail_type=
                "OrderUpdated",

            order_id=
                order_id,

            customer_id=
                customer_id,

            status=
                order["status"],

            total_amount=
                total_amount,

            reason=
                "Customer updated order items",

            items=
                [
                    {
                        "product_id":
                            item[
                                "product_id"
                            ],

                        "quantity":
                            item[
                                "quantity"
                            ]
                    }
                    for item
                    in processed_items
                ]
        )

        log_event(
            "INFO",
            "Order updated successfully",
            request_id=
                context.aws_request_id,

            order_id=
                order_id,

            customer_id=
                customer_id,

            total_amount=
                total_amount
        )

        return response(
            200,
            {
                "message":
                    "Order updated successfully",

                "order_id":
                    order_id,

                "customer_id":
                    customer_id,

                "status":
                    order[
                        "status"
                    ],

                "total_amount":
                    total_amount,

                "items":
                    [
                        {
                            "product_id":
                                item[
                                    "product_id"
                                ],

                            "product_name":
                                item[
                                    "product_name"
                                ],

                            "quantity":
                                item[
                                    "quantity"
                                ],

                            "unit_price":
                                item[
                                    "unit_price"
                                ]
                        }
                        for item
                        in processed_items
                    ]
            }
        )

    except PermissionError as exc:

        if connection is not None:

            connection.rollback()

        return response(
            403,
            {
                "message":
                    str(exc)
            }
        )

    except ValueError as exc:

        if connection is not None:

            connection.rollback()

        return response(
            400,
            {
                "message":
                    str(exc)
            }
        )

    except Exception as exc:

        if connection is not None:

            connection.rollback()

        log_event(
            "ERROR",
            "Order update failed",
            request_id=
                context.aws_request_id,

            error_type=
                type(exc).__name__,

            error=
                str(exc)
        )

        return response(
            500,
            {
                "message":
                    "Internal server error",

                "request_id":
                    context.aws_request_id
            }
        )

    finally:

        if connection is not None:

            connection.close()


# ==========================================================
# PATCH ORDER STATUS
#
# CUSTOMER:
# /customers/{customer_id}/orders/{order_id}/status
#
# ADMIN:
# /orders/{id}/status
# ==========================================================

def update_order_status(
    event,
    context
):

    connection = None

    inventory_events = []

    try:

        order_id = get_order_id(
            event
        )

        request_body = parse_body(
            event
        )

        raw_status = request_body.get(
            "status",
            ""
        )

        if not isinstance(raw_status, str):
            return response(
                400,
                {
                    "message": "status must be a string"
                }
            )

        requested_status = raw_status.strip().upper()

        if requested_status == "":
            return response(
                400,
                {
                    "message": "status is required"
                }
            )

        raw_note = request_body.get(
            "note",
            ""
        )

        if raw_note is None:
            note = ""
        elif isinstance(raw_note, str):
            note = raw_note.strip()
        else:
            return response(
                400,
                {
                    "message": "note must be a string"
                }
            )

        role, authenticated_customer_id = (
            get_auth_context(event)
        )

        resource = event.get(
            "resource",
            ""
        )

        # ----------------------------------------------
        # CUSTOMER CANCEL
        # ----------------------------------------------

        if role == "CUSTOMER":

            if (
                requested_status
                !=
                "CANCELLED"
            ):

                return response(
                    403,
                    {
                        "message":
                            "Customer can only cancel orders"
                    }
                )

            url_customer_id = (
                get_url_customer_id(
                    event
                )
            )

            if (
                authenticated_customer_id
                !=
                url_customer_id
            ):

                return response(
                    403,
                    {
                        "message":
                            "You cannot modify another customer's order"
                    }
                )

            customer_id = (
                authenticated_customer_id
            )

        # ----------------------------------------------
        # ADMIN DELIVER
        # ----------------------------------------------

        elif role == "ADMIN":

            if (
                requested_status
                !=
                "DELIVERED"
            ):

                return response(
                    403,
                    {
                        "message":
                            "Admin can only mark orders as DELIVERED"
                    }
                )

            customer_id = None

        else:

            return response(
                403,
                {
                    "message":
                        "Unauthorized user"
                }
            )

        log_event(
            "INFO",
            "Opening database connection for order status update",
            request_id=context.aws_request_id,
            order_id=order_id,
            customer_id=customer_id,
            requested_status=requested_status
        )

        connection = get_db_connection()

        log_event(
            "INFO",
            "Database connection established for order status update",
            request_id=context.aws_request_id,
            order_id=order_id
        )

        with connection.cursor() as cursor:

            # ------------------------------------------
            # LOCK ORDER
            # ------------------------------------------

            log_event(
                "INFO",
                "Attempting to lock order row",
                request_id=context.aws_request_id,
                order_id=order_id,
                customer_id=customer_id
            )

            if customer_id is not None:

                cursor.execute(
                    """
                    SELECT
                        order_id,
                        customer_id,
                        status,
                        total_amount
                    FROM orders
                    WHERE order_id = %s
                      AND customer_id = %s
                    FOR UPDATE
                    """,
                    (
                        order_id,
                        customer_id
                    )
                )

            else:

                cursor.execute(
                    """
                    SELECT
                        order_id,
                        customer_id,
                        status,
                        total_amount
                    FROM orders
                    WHERE order_id = %s
                    FOR UPDATE
                    """,
                    (
                        order_id,
                    )
                )

            order = cursor.fetchone()

            log_event(
                "INFO",
                "Order row lock/read completed",
                request_id=context.aws_request_id,
                order_id=order_id,
                found=order is not None
            )

            if order is None:

                raise ValueError(
                    "Order not found"
                )

            # ------------------------------------------
            # CUSTOMER CANCELLATION
            # ------------------------------------------

            if (
                requested_status
                == "CANCELLED"
            ):

                if (
                    order["status"]
                    !=
                    "CONFIRMED"
                ):

                    raise ValueError(
                        "Only CONFIRMED orders can be cancelled"
                    )

                # --------------------------------------
                # GET ORDER ITEMS
                # --------------------------------------

                cursor.execute(
                    """
                    SELECT
                        product_id,
                        quantity
                    FROM order_items
                    WHERE order_id = %s
                    """,
                    (
                        order_id,
                    )
                )

                order_items = (
                    cursor.fetchall()
                )

                # --------------------------------------
                # RESTORE INVENTORY
                # --------------------------------------

                for item in order_items:

                    cursor.execute(
                        """
                        SELECT
                            product_id,
                            stock_quantity
                        FROM products
                        WHERE product_id = %s
                        FOR UPDATE
                        """,
                        (
                            item[
                                "product_id"
                            ],
                        )
                    )

                    product = (
                        cursor.fetchone()
                    )

                    if product is None:

                        raise ValueError(
                            "Product not found"
                        )

                    old_stock = (
                        product[
                            "stock_quantity"
                        ]
                    )

                    new_stock = (
                        old_stock
                        +
                        item[
                            "quantity"
                        ]
                    )

                    cursor.execute(
                        """
                        UPDATE products
                        SET stock_quantity = %s
                        WHERE product_id = %s
                        """,
                        (
                            new_stock,
                            item[
                                "product_id"
                            ]
                        )
                    )

                    inventory_events.append(
                        {
                            "product_id":
                                item[
                                    "product_id"
                                ],

                            "old_stock":
                                old_stock,

                            "new_stock":
                                new_stock
                        }
                    )

            # ------------------------------------------
            # ADMIN DELIVERY
            # ------------------------------------------

            elif (
                requested_status
                == "DELIVERED"
            ):

                if (
                    order["status"]
                    !=
                    "CONFIRMED"
                ):

                    raise ValueError(
                        "Only CONFIRMED orders can be delivered"
                    )

            # ------------------------------------------
            # UPDATE STATUS
            # ------------------------------------------

            cursor.execute(
                """
                UPDATE orders
                SET
                    status = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE order_id = %s
                """,
                (
                    requested_status,
                    order_id
                )
            )

            # ------------------------------------------
            # ORDER LOG
            # ------------------------------------------

            changed_by = (
                f"customer-{order['customer_id']}"
                if role == "CUSTOMER"
                else
                "admin"
            )

            cursor.execute(
                """
                INSERT INTO order_logs (
                    order_id,
                    previous_status,
                    new_status,
                    changed_by,
                    note
                )
                VALUES (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s
                )
                """,
                (
                    order_id,
                    order[
                        "status"
                    ],
                    requested_status,
                    changed_by,
                    note
                )
            )

        connection.commit()

        # ----------------------------------------------
        # PUBLISH SUCCESSFUL CANCELLATION METRIC
        # ----------------------------------------------

        if requested_status == "CANCELLED":

            publish_custom_metric(
                "OrdersCancelled"
            )

        # ----------------------------------------------
        # PUBLISH ORDER EVENT
        # ----------------------------------------------

        detail_type = (
            "OrderCancelled"
            if requested_status == "CANCELLED"
            else "OrderDelivered"
        )

        publish_order_event(
            detail_type=
                detail_type,

            order_id=
                order_id,

            customer_id=
                order[
                    "customer_id"
                ],

            status=
                requested_status,

            total_amount=
                order[
                    "total_amount"
                ],

            reason=
                note
        )

        # ----------------------------------------------
        # INVENTORY EVENTS AFTER CANCELLATION
        # ----------------------------------------------

        if (
            requested_status
            == "CANCELLED"
        ):

            for inventory in inventory_events:

                try:

                    publish_inventory_event(
                        product_id=
                            inventory[
                                "product_id"
                            ],

                        old_stock=
                            inventory[
                                "old_stock"
                            ],

                        new_stock=
                            inventory[
                                "new_stock"
                            ]
                    )

                except Exception as exc:

                    log_event(
                        "ERROR",
                        "Inventory event publishing failed",
                        order_id=order_id,
                        error=str(exc)
                    )

        return response(
            200,
            {
                "message":
                    f"Order {requested_status.lower()} successfully",

                "order_id":
                    order_id,

                "customer_id":
                    order[
                        "customer_id"
                    ],

                "status":
                    requested_status,

                "total_amount":
                    order[
                        "total_amount"
                    ]
            }
        )

    except PermissionError as exc:

        if connection is not None:
            connection.rollback()

        return response(
            403,
            {
                "message":
                    str(exc)
            }
        )

    except ValueError as exc:

        if connection is not None:

            connection.rollback()

        return response(
            400,
            {
                "message":
                    str(exc)
            }
        )

    except pymysql.err.OperationalError as exc:

        if connection is not None:
            connection.rollback()

        error_code = exc.args[0] if exc.args else None

        log_event(
            "ERROR",
            "Database operation failed during order status update",
            request_id=context.aws_request_id,
            order_id=order_id if "order_id" in locals() else None,
            error_code=error_code,
            error_type=type(exc).__name__,
            error=str(exc)
        )

        # MySQL 1205 = lock wait timeout exceeded.
        # Return a client-safe conflict instead of allowing API Gateway
        # to reach a 29-30 second 504 timeout.
        if error_code == 1205:
            return response(
                409,
                {
                    "message": "Order is currently being modified. Please try again.",
                    "request_id": context.aws_request_id
                }
            )

        return response(
            500,
            {
                "message": "Database operation failed",
                "request_id": context.aws_request_id
            }
        )

    except Exception as exc:

        if connection is not None:

            connection.rollback()

        log_event(
            "ERROR",
            "Order status update failed",
            request_id=
                context.aws_request_id,

            order_id=
                order_id
                if "order_id" in locals()
                else None,

            error_type=
                type(exc).__name__,

            error=
                str(exc)
        )

        return response(
            500,
            {
                "message":
                    "Internal server error",

                "request_id":
                    context.aws_request_id
            }
        )

    finally:

        if connection is not None:

            connection.close()


# ==========================================================
# ADMIN GET ALL ORDERS
#
# GET /orders
# ==========================================================

def get_all_orders(
    event,
    context
):

    connection = None

    try:

        role, customer_id = (
            get_auth_context(event)
        )

        if role != "ADMIN":

            return response(
                403,
                {
                    "message":
                        "Only admin can view all orders"
                }
            )

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
                ORDER BY order_id DESC
                """
            )

            orders = cursor.fetchall()

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

        return response(
            200,
            orders
        )

    except Exception as exc:

        log_event(
            "ERROR",
            "Get all orders failed",
            request_id=
                context.aws_request_id,

            error_type=
                type(exc).__name__,

            error=
                str(exc)
        )

        return response(
            500,
            {
                "message":
                    "Internal server error"
            }
        )

    finally:

        if connection is not None:

            connection.close()


# ==========================================================
# ADMIN GET SINGLE ORDER
#
# GET /orders/{id}
# ==========================================================

def get_order_by_id(
    event,
    context
):

    connection = None

    try:

        role, customer_id = (
            get_auth_context(event)
        )

        if role != "ADMIN":

            return response(
                403,
                {
                    "message":
                        "Only admin can access this endpoint"
                }
            )

        order_id = get_order_id(
            event
        )

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
                WHERE order_id = %s
                """,
                (
                    order_id,
                )
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
                    order_id,
                )
            )

            order["items"] = (
                cursor.fetchall()
            )

        return response(
            200,
            order
        )

    except ValueError as exc:

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
            "Get order failed",
            request_id=
                context.aws_request_id,

            error_type=
                type(exc).__name__,

            error=
                str(exc)
        )

        return response(
            500,
            {
                "message":
                    "Internal server error"
            }
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

    route_key = (
        event.get(
            "routeKey",
            ""
        )
    )

    if not resource and route_key:
        resource = route_key.split(
            " ",
            1
        )[1] if " " in route_key else route_key

    log_event(
        "INFO",
        "Order request received",
        request_id=request_id,
        http_method=http_method,
        resource=resource
    )

    try:

        # ==================================================
        # CUSTOMER CREATE ORDER
        # ==================================================

        if (
            http_method == "POST"
            and
            resource ==
            "/customers/{customer_id}/orders"
        ):

            return create_order(
                event,
                context
            )

        # ==================================================
        # CUSTOMER GET ALL ORDERS
        # ==================================================

        if (
            http_method == "GET"
            and
            resource ==
            "/customers/{customer_id}/orders"
        ):

            return get_customer_orders(
                event,
                context
            )

        # ==================================================
        # CUSTOMER GET SINGLE ORDER
        # ==================================================

        if (
            http_method == "GET"
            and
            resource ==
            "/customers/{customer_id}/orders/{order_id}"
        ):

            return get_customer_order(
                event,
                context
            )

        # ==================================================
        # CUSTOMER UPDATE ORDER
        # ==================================================

        if (
            http_method == "PUT"
            and
            resource ==
            "/customers/{customer_id}/orders/{order_id}"
        ):

            return update_customer_order(
                event,
                context
            )

        # ==================================================
        # CUSTOMER CANCEL ORDER
        # ==================================================

        if (
            http_method == "PATCH"
            and
            resource ==
            "/customers/{customer_id}/orders/{order_id}/status"
        ):

            return update_order_status(
                event,
                context
            )

        # ==================================================
        # ADMIN GET ALL ORDERS
        # ==================================================

        if (
            http_method == "GET"
            and
            resource == "/orders"
        ):

            return get_all_orders(
                event,
                context
            )

        # ==================================================
        # ADMIN GET SINGLE ORDER
        # ==================================================

        if (
            http_method == "GET"
            and
            resource == "/orders/{id}"
        ):

            return get_order_by_id(
                event,
                context
            )

        # ==================================================
        # ADMIN DELIVER ORDER
        # ==================================================

        if (
            http_method == "PATCH"
            and
            resource ==
            "/orders/{id}/status"
        ):

            return update_order_status(
                event,
                context
            )

        # ==================================================
        # ROUTE NOT FOUND
        # ==================================================

        return response(
            404,
            {
                "message":
                    "Route not found"
            }
        )

    except Exception as exc:

        log_event(
            "ERROR",
            "Unhandled order Lambda error",
            request_id=request_id,
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