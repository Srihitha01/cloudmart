import json
import os

import boto3
import pymysql


# ==========================================================
# AWS CLIENTS
# ==========================================================

ssm = boto3.client("ssm")
events = boto3.client("events")
lambda_client = boto3.client("lambda")


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

    role, authenticated_customer_id = (
        get_auth_context(event)
    )

    url_customer_id = get_url_customer_id(
        event
    )

    if role == "CUSTOMER":

        if authenticated_customer_id is None:

            raise PermissionError(
                "Customer identity is missing"
            )

        if (
            authenticated_customer_id
            !=
            url_customer_id
        ):

            raise PermissionError(
                "You cannot access another customer's orders"
            )

        return (
            role,
            authenticated_customer_id
        )

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
        order_data
    ).encode(
        "utf-8"
    )

    result = lambda_client.invoke(
        FunctionName=
            ORDER_PROCESSOR_FUNCTION_NAME,

        InvocationType=
            "RequestResponse",

        Payload=payload
    )

    if result.get(
        "FunctionError"
    ):

        raise RuntimeError(
            "Order processor Lambda execution failed"
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
        request_id=context.aws_request_id,
        processor_status_code=
            processor_response.get(
                "statusCode"
            )
    )

    return processor_response


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
        "total_amount": str(
            total_amount
        )
    }

    if reason:

        detail["reason"] = reason

    if items is not None:

        detail["items"] = items

    result = events.put_events(
        Entries=[
            {
                "EventBusName":
                    EVENT_BUS_NAME,

                "Source":
                    "cloudmart.orders",

                "DetailType":
                    detail_type,

                "Detail":
                    json.dumps(
                        detail,
                        default=str
                    )
            }
        ]
    )

    if result.get(
        "FailedEntryCount",
        0
    ) != 0:

        log_event(
            "ERROR",
            "Order event publishing failed",
            order_id=order_id,
            detail_type=detail_type,
            event_result=result
        )

        raise RuntimeError(
            f"Failed to publish {detail_type} event"
        )

    log_event(
        "INFO",
        "Order event published",
        order_id=order_id,
        detail_type=detail_type
    )


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

    result = events.put_events(
        Entries=[
            {
                "EventBusName":
                    EVENT_BUS_NAME,

                "Source":
                    "cloudmart.inventory",

                "DetailType":
                    "InventoryChanged",

                "Detail":
                    json.dumps(
                        detail
                    )
            }
        ]
    )

    log_event(
        "INFO",
        "Inventory event published",
        product_id=product_id,
        old_stock=old_stock,
        new_stock=new_stock,
        event_result=result
    )


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

        requested_status = (
            request_body.get(
                "status",
                ""
            )
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

        connection = get_db_connection()

        with connection.cursor() as cursor:

            # ------------------------------------------
            # LOCK ORDER
            # ------------------------------------------

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