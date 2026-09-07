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
# GET SSM PARAMETER
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

    role = authorizer.get(
        "role",
        ""
    )

    customer_id = authorizer.get(
        "customer_id",
        ""
    )

    if customer_id:

        try:

            customer_id = int(customer_id)

        except (TypeError, ValueError):

            customer_id = None

    else:

        customer_id = None

    return role, customer_id


# ==========================================================
# GET ORDER ID
# ==========================================================

def get_order_id(event):

    path_parameters = (
        event.get("pathParameters") or {}
    )

    order_id = path_parameters.get("id")

    if order_id is None:

        raise ValueError(
            "Order id is required"
        )

    try:

        return int(order_id)

    except (TypeError, ValueError):

        raise ValueError(
            "Order id must be an integer"
        )


# ==========================================================
# INVOKE ORDER PROCESSOR
# ==========================================================

def invoke_order_processor(
    order_data,
    context
):

    payload = json.dumps(
        order_data
    ).encode("utf-8")

    result = lambda_client.invoke(
        FunctionName=ORDER_PROCESSOR_FUNCTION_NAME,
        InvocationType="RequestResponse",
        Payload=payload
    )

    raw_payload = result[
        "Payload"
    ].read()

    if isinstance(raw_payload, bytes):

        raw_payload = raw_payload.decode(
            "utf-8"
        )

    processor_response = json.loads(
        raw_payload
    )

    log_event(
        "INFO",
        "Order processor invoked",
        request_id=context.aws_request_id,
        processor_status_code=processor_response.get(
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

    order_data = parse_body(event)

    role, authenticated_customer_id = (
        get_auth_context(event)
    )


    # ======================================================
    # CUSTOMER AUTHORIZATION
    # ======================================================

    if role == "CUSTOMER":

        if authenticated_customer_id is None:

            return response(
                403,
                {
                    "message":
                        "Customer identity is missing"
                }
            )

        # Customer ID always comes from token.
        order_data["customer_id"] = (
            authenticated_customer_id
        )


    # ======================================================
    # ADMIN VALIDATION
    # ======================================================

    elif role == "ADMIN":

        if "customer_id" not in order_data:

            return response(
                400,
                {
                    "message":
                        "customer_id is required"
                }
            )

    else:

        return response(
            403,
            {
                "message":
                    "Unauthorized user"
            }
        )


    # ======================================================
    # ITEMS VALIDATION
    # ======================================================

    if "items" not in order_data:

        return response(
            400,
            {
                "message":
                    "items is required"
            }
        )

    if not isinstance(
        order_data["items"],
        list
    ):

        return response(
            400,
            {
                "message":
                    "items must be an array"
            }
        )

    if not order_data["items"]:

        return response(
            400,
            {
                "message":
                    "items cannot be empty"
            }
        )


    # ======================================================
    # INVOKE ORDER PROCESSOR
    # ======================================================

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
        request_id=context.aws_request_id,
        customer_id=order_data.get(
            "customer_id"
        ),
        order_id=processor_body.get(
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

    order_id = get_order_id(event)

    role, authenticated_customer_id = (
        get_auth_context(event)
    )

    connection = None

    try:

        connection = get_db_connection()

        with connection.cursor() as cursor:

            # ==================================================
            # GET ORDER
            # ==================================================

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


            # ==================================================
            # CUSTOMER OWNERSHIP CHECK
            # ==================================================

            if role == "CUSTOMER":

                if (
                    authenticated_customer_id
                    != order["customer_id"]
                ):

                    return response(
                        403,
                        {
                            "message":
                                "You can only view your own orders"
                        }
                    )

            elif role != "ADMIN":

                return response(
                    403,
                    {
                        "message":
                            "Unauthorized user"
                    }
                )


            # ==================================================
            # GET ORDER ITEMS
            # ==================================================

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
            request_id=context.aws_request_id,
            order_id=order_id
        )


        return response(
            200,
            order
        )

    finally:

        if connection is not None:

            connection.close()


# ==========================================================
# GET /orders
# ==========================================================

def get_customer_orders(
    event,
    context
):

    role, authenticated_customer_id = (
        get_auth_context(event)
    )

    query_parameters = (
        event.get(
            "queryStringParameters"
        ) or {}
    )


    # ======================================================
    # DETERMINE CUSTOMER ID
    # ======================================================

    if role == "CUSTOMER":

        if authenticated_customer_id is None:

            return response(
                403,
                {
                    "message":
                        "Customer identity is missing"
                }
            )

        customer_id = authenticated_customer_id


    elif role == "ADMIN":

        customer_id = query_parameters.get(
            "customerId"
        )

        if customer_id is not None:

            try:

                customer_id = int(customer_id)

            except (TypeError, ValueError):

                return response(
                    400,
                    {
                        "message":
                            "customerId must be an integer"
                    }
                )

    else:

        return response(
            403,
            {
                "message":
                    "Unauthorized user"
            }
        )


    connection = None

    try:

        connection = get_db_connection()

        with connection.cursor() as cursor:

            # ==================================================
            # CUSTOMER - ONLY THEIR ORDERS
            # ==================================================

            if customer_id is not None:

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

            # ==================================================
            # ADMIN - ALL ORDERS
            # ==================================================

            else:

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


            # ==================================================
            # GET ITEMS FOR EACH ORDER
            # ==================================================

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
                        order["order_id"],
                    )
                )

                order["items"] = (
                    cursor.fetchall()
                )


        log_event(
            "INFO",
            "Orders retrieved",
            request_id=context.aws_request_id,
            role=role,
            customer_id=customer_id,
            count=len(orders)
        )


        return response(
            200,
            orders
        )

    finally:

        if connection is not None:

            connection.close()


# ==========================================================
# PUBLISH ORDER EVENT
# ==========================================================

def publish_order_event(
    detail_type,
    order_id,
    customer_id,
    status,
    total_amount,
    reason=None
):

    detail = {
        "order_id": order_id,
        "customer_id": customer_id,
        "status": status,
        "total_amount": float(
            total_amount
        )
    }

    if reason:
        detail["reason"] = reason


    events.put_events(
        Entries=[
            {
                "EventBusName": EVENT_BUS_NAME,
                "Source": "cloudmart.order",
                "DetailType": detail_type,
                "Detail": json.dumps(
                    detail,
                    default=str
                )
            }
        ]
    )


# ==========================================================
# PATCH /orders/{id}/status
# ==========================================================

def update_order_status(
    event,
    context
):

    order_id = get_order_id(event)

    request_body = parse_body(event)

    requested_status = (
        request_body.get("status", "")
        .strip()
        .upper()
    )

    note = request_body.get(
        "note",
        ""
    )

    role, authenticated_customer_id = (
        get_auth_context(event)
    )


    # ======================================================
    # STATUS VALIDATION
    # ======================================================

    if requested_status not in [
        "CANCELLED",
        "DELIVERED"
    ]:

        return response(
            400,
            {
                "message":
                    "Status must be CANCELLED or DELIVERED"
            }
        )


    # ======================================================
    # CUSTOMER CAN ONLY CANCEL
    # ======================================================

    if role == "CUSTOMER":

        if requested_status != "CANCELLED":

            return response(
                403,
                {
                    "message":
                        "Customers can only cancel orders"
                }
            )

        if authenticated_customer_id is None:

            return response(
                403,
                {
                    "message":
                        "Customer identity is missing"
                }
            )


    elif role != "ADMIN":

        return response(
            403,
            {
                "message":
                    "Unauthorized user"
            }
        )


    connection = None

    try:

        connection = get_db_connection()

        inventory_events = []

        with connection.cursor() as cursor:

            # ==================================================
            # GET ORDER
            # ==================================================

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

                return response(
                    404,
                    {
                        "message":
                            "Order not found"
                    }
                )


            # ==================================================
            # CUSTOMER OWNERSHIP CHECK
            # ==================================================

            if role == "CUSTOMER":

                if (
                    order["customer_id"]
                    != authenticated_customer_id
                ):

                    return response(
                        403,
                        {
                            "message":
                                "You can only cancel your own orders"
                        }
                    )


            current_status = order["status"]


            # ==================================================
            # STATUS TRANSITIONS
            # ==================================================

            if requested_status == "CANCELLED":

                if current_status != "CONFIRMED":

                    return response(
                        400,
                        {
                            "message":
                                "Only CONFIRMED orders can be cancelled"
                        }
                    )


            if requested_status == "DELIVERED":

                if current_status != "CONFIRMED":

                    return response(
                        400,
                        {
                            "message":
                                "Only CONFIRMED orders can be delivered"
                        }
                    )


            # ==================================================
            # RESTORE STOCK ON CANCELLATION
            # ==================================================

            if requested_status == "CANCELLED":

                cursor.execute(
                    """
                    SELECT
                        product_id,
                        quantity
                    FROM order_items
                    WHERE order_id = %s
                    """,
                    (order_id,)
                )

                order_items = cursor.fetchall()


                for item in order_items:

                    cursor.execute(
                        """
                        UPDATE products
                        SET
                            stock_quantity =
                                stock_quantity + %s,
                            updated_at =
                                CURRENT_TIMESTAMP
                        WHERE product_id = %s
                        """,
                        (
                            item["quantity"],
                            item["product_id"]
                        )
                    )


                    cursor.execute(
                        """
                        SELECT
                            product_id,
                            name,
                            stock_quantity,
                            restock_threshold
                        FROM products
                        WHERE product_id = %s
                        """,
                        (
                            item["product_id"],
                        )
                    )

                    product = cursor.fetchone()


                    if product:

                        inventory_events.append(
                            {
                                "product_id":
                                    product["product_id"],

                                "product_name":
                                    product["name"],

                                "stock_quantity":
                                    product[
                                        "stock_quantity"
                                    ],

                                "restock_threshold":
                                    product[
                                        "restock_threshold"
                                    ],

                                "reason":
                                    "ORDER_CANCELLED"
                            }
                        )


            # ==================================================
            # UPDATE ORDER STATUS
            # ==================================================

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
                    current_status
                )
            )


            if cursor.rowcount != 1:

                raise ValueError(
                    "Order status update failed"
                )


            # ==================================================
            # ORDER LOG
            # ==================================================

            changed_by = (
                f"customer-{authenticated_customer_id}"
                if role == "CUSTOMER"
                else "admin"
            )


            if not note:

                note = (
                    "Order cancelled"
                    if requested_status == "CANCELLED"
                    else "Order delivered"
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
                    current_status,
                    requested_status,
                    changed_by,
                    note
                )
            )


        connection.commit()


        # ======================================================
        # PUBLISH ORDER EVENT
        # ======================================================

        detail_type = (
            "OrderCancelled"
            if requested_status == "CANCELLED"
            else "OrderDelivered"
        )


        publish_order_event(
            detail_type=detail_type,
            order_id=order_id,
            customer_id=order["customer_id"],
            status=requested_status,
            total_amount=order["total_amount"],
            reason=note
        )


        # ======================================================
        # PUBLISH INVENTORY EVENTS
        # ======================================================

        if requested_status == "CANCELLED":

            for inventory in inventory_events:

                try:

                    events.put_events(
                        Entries=[
                            {
                                "EventBusName":
                                    EVENT_BUS_NAME,

                                "Source":
                                    "cloudmart.product",

                                "DetailType":
                                    "Inventory Changed",

                                "Detail":
                                    json.dumps(
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
                        product_id=inventory[
                            "product_id"
                        ],
                        error_type=type(exc).__name__,
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
                    order["customer_id"],

                "status":
                    requested_status,

                "total_amount":
                    order["total_amount"]
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
            "Order status update failed",
            request_id=context.aws_request_id,
            order_id=order_id,
            error_type=type(exc).__name__,
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

    request_id = context.aws_request_id

    http_method = (
        event.get("httpMethod")
        or event.get(
            "requestContext",
            {}
        )
        .get(
            "http",
            {}
        )
        .get("method")
    )

    resource = event.get(
        "resource",
        ""
    )


    log_event(
        "INFO",
        "Order request received",
        request_id=request_id,
        http_method=http_method,
        resource=resource
    )


    try:

        # ======================================================
        # POST /orders
        # ======================================================

        if (
            http_method == "POST"
            and resource == "/orders"
        ):

            return create_order(
                event,
                context
            )


        # ======================================================
        # GET /orders
        # ======================================================

        if (
            http_method == "GET"
            and resource == "/orders"
        ):

            return get_customer_orders(
                event,
                context
            )


        # ======================================================
        # GET /orders/{id}
        # ======================================================

        if (
            http_method == "GET"
            and resource == "/orders/{id}"
        ):

            return get_order(
                event,
                context
            )


        # ======================================================
        # PATCH /orders/{id}/status
        # ======================================================

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
            request_id=request_id,
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
            request_id=request_id,
            error_type=type(exc).__name__,
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