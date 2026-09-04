import json
import os

import boto3
import pymysql


# ==========================================================
# AWS CLIENTS
# ==========================================================

ssm = boto3.client("ssm")
lambda_client = boto3.client("lambda")
events = boto3.client("events")


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
# EVENTBRIDGE
# ==========================================================

def publish_order_event(
    detail_type,
    order_id,
    customer_id,
    status,
    total_amount=None,
    reason=None
):
    detail = {
        "order_id": order_id,
        "customer_id": customer_id,
        "status": status
    }

    if total_amount is not None:
        detail["total_amount"] = total_amount

    if reason is not None:
        detail["reason"] = reason

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

        if result.get("FailedEntryCount", 0) != 0:
            log_event(
                "ERROR",
                "Order event publishing failed",
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
            "ERROR",
            "Order event publishing exception",
            order_id=order_id,
            detail_type=detail_type,
            error_type=type(exc).__name__,
            error=str(exc)
        )
        return False


# ==========================================================
# PATCH /orders/{id}/status
# ==========================================================

def update_order_status(event, context):

    path_parameters = event.get("pathParameters") or {}
    order_id = path_parameters.get("id")

    if order_id is None:
        return response(400, {"message": "Order id is required"})

    try:
        order_id = int(order_id)
    except (TypeError, ValueError):
        return response(400, {"message": "Order id must be an integer"})

    if order_id <= 0:
        return response(400, {"message": "Order id must be greater than zero"})

    body = parse_body(event)
    requested_status = str(body.get("status", "")).strip().upper()

    if requested_status not in {"DELIVERED", "CANCELLED"}:
        return response(
            400,
            {
                "message": "status must be DELIVERED or CANCELLED"
            }
        )

    connection = None
    inventory_events = []
    order = None

    try:
        connection = get_db_connection()

        with connection.cursor() as cursor:

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
                (order_id,)
            )

            order = cursor.fetchone()

            if order is None:
                return response(404, {"message": "Order not found"})

            current_status = order["status"]

            if current_status != "CONFIRMED":
                return response(
                    409,
                    {
                        "message": (
                            f"Order {order_id} cannot be changed from "
                            f"{current_status} to {requested_status}"
                        ),
                        "current_status": current_status
                    }
                )

            if requested_status == "CANCELLED":

                cursor.execute(
                    """
                    SELECT
                        product_id,
                        quantity
                    FROM order_items
                    WHERE order_id = %s
                    ORDER BY order_item_id
                    """,
                    (order_id,)
                )

                items = cursor.fetchall()

                for item in items:
                    cursor.execute(
                        """
                        SELECT
                            product_id,
                            name,
                            stock_quantity,
                            reorder_threshold
                        FROM products
                        WHERE product_id = %s
                        FOR UPDATE
                        """,
                        (item["product_id"],)
                    )

                    product = cursor.fetchone()

                    if product is None:
                        raise ValueError(
                            f"Product {item['product_id']} not found"
                        )

                    old_stock = int(product["stock_quantity"])
                    quantity = int(item["quantity"])
                    new_stock = old_stock + quantity

                    cursor.execute(
                        """
                        UPDATE products
                        SET stock_quantity = %s
                        WHERE product_id = %s
                          AND deleted_at IS NULL
                        """,
                        (new_stock, item["product_id"])
                    )

                    if cursor.rowcount != 1:
                        raise ValueError(
                            f"Product {item['product_id']} is deleted"
                        )

                    inventory_events.append(
                        {
                            "product_id": int(product["product_id"]),
                            "product_name": product["name"],
                            "old_stock": old_stock,
                            "new_stock": new_stock,
                            "reorder_threshold": int(
                                product["reorder_threshold"]
                            ),
                            "low_stock": (
                                new_stock
                                <= int(product["reorder_threshold"])
                            )
                        }
                    )

                note = "Order cancelled and inventory restored"

            else:
                note = "Order marked as delivered"

            cursor.execute(
                """
                UPDATE orders
                SET status = %s
                WHERE order_id = %s
                  AND status = %s
                """,
                (
                    requested_status,
                    order_id,
                    "CONFIRMED"
                )
            )

            if cursor.rowcount != 1:
                raise ValueError("Order status update failed")

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
                    "CONFIRMED",
                    requested_status,
                    "system",
                    note
                )
            )

        connection.commit()

        detail_type = (
            "OrderDelivered"
            if requested_status == "DELIVERED"
            else "OrderCancelled"
        )

        publish_order_event(
            detail_type=detail_type,
            order_id=order_id,
            customer_id=order["customer_id"],
            status=requested_status,
            total_amount=order["total_amount"],
            reason=note
        )

        # Publish inventory changes created by cancellation.
        if requested_status == "CANCELLED":
            for inventory in inventory_events:
                try:
                    events.put_events(
                        Entries=[
                            {
                                "EventBusName": EVENT_BUS_NAME,
                                "Source": "cloudmart.product",
                                "DetailType": "Inventory Changed",
                                "Detail": json.dumps(
                                    inventory,
                                    default=str
                                )
                            }
                        ]
                    )
                except Exception as exc:
                    log_event(
                        "ERROR",
                        "Inventory event publishing failed",
                        order_id=order_id,
                        product_id=inventory["product_id"],
                        error_type=type(exc).__name__,
                        error=str(exc)
                    )

        return response(
            200,
            {
                "message": f"Order {requested_status.lower()} successfully",
                "order_id": order_id,
                "customer_id": order["customer_id"],
                "status": requested_status,
                "total_amount": order["total_amount"]
            }
        )

    except ValueError as exc:
        if connection is not None:
            connection.rollback()

        return response(
            400,
            {"message": str(exc)}
        )

    except Exception as exc:
        if connection is not None:
            connection.rollback()

        log_event(
            "ERROR",
            "Order status update failed",
            request_id=context.aws_request_id,
            order_id=order_id,
            error_type=type(exc).__name__,
            error=str(exc)
        )

        return response(
            500,
            {
                "message": "Internal server error",
                "request_id": context.aws_request_id
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

        # ------------------------------------------------------
        # PATCH /orders/{id}/status
        # ------------------------------------------------------

        if (
            http_method == "PATCH"
            and resource == "/orders/{id}/status"
        ):

            return update_order_status(
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