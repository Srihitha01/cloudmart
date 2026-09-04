import json
import os
from decimal import Decimal

import boto3
import pymysql


# ==========================================================
# AWS CLIENTS
# ==========================================================

ssm = boto3.client("ssm")
events = boto3.client("events")


# ==========================================================
# SSM PARAMETER NAMES
# ==========================================================

DB_NAME_PARAMETER = os.environ["DB_NAME_PARAMETER"]
DB_ENDPOINT_PARAMETER = os.environ["DB_ENDPOINT_PARAMETER"]
DB_PORT_PARAMETER = os.environ["DB_PORT_PARAMETER"]
DB_USERNAME_PARAMETER = os.environ["DB_USERNAME_PARAMETER"]
DB_PASSWORD_PARAMETER = os.environ["DB_PASSWORD_PARAMETER"]
EVENT_BUS_NAME = os.environ["EVENT_BUS_NAME"]


# ==========================================================
# STRUCTURED LOGGING
# ==========================================================

def log_event(level, message, **details):

    record = {
        "level": level,
        "service": "cloudmart-order-processor-lambda",
        "message": message,
        **details
    }

    print(json.dumps(record, default=str))


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

    return parameter["Parameter"]["Value"]


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

    result = events.put_events(
        Entries=[
            {
                "EventBusName": EVENT_BUS_NAME,
                "Source": "cloudmart.orders",
                "DetailType": detail_type,
                "Detail": json.dumps(
                    detail,
                    default=str
                )
            }
        ]
    )

    failed_count = result.get(
        "FailedEntryCount",
        0
    )

    if failed_count != 0:

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


# ==========================================================
# VALIDATE REQUEST
# ==========================================================

def validate_order_payload(event):

    customer_id = event.get(
        "customer_id"
    )

    items = event.get(
        "items"
    )

    if customer_id is None:

        raise ValueError(
            "customer_id is required"
        )

    try:

        customer_id = int(
            customer_id
        )

    except (TypeError, ValueError):

        raise ValueError(
            "customer_id must be an integer"
        )

    if customer_id <= 0:

        raise ValueError(
            "customer_id must be greater than zero"
        )

    if not isinstance(items, list) or not items:

        raise ValueError(
            "items must be a non-empty array"
        )

    validated_items = []

    for item in items:

        if not isinstance(item, dict):

            raise ValueError(
                "Each order item must be an object"
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

        except (TypeError, ValueError):

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

        validated_items.append(
            {
                "product_id": product_id,
                "quantity": quantity
            }
        )

    return {
        "customer_id": customer_id,
        "items": validated_items
    }


# ==========================================================
# CREATE PENDING ORDER
# ==========================================================

def create_pending_order(
    order_data,
    context
):

    connection = None

    customer_id = order_data[
        "customer_id"
    ]

    items = order_data[
        "items"
    ]

    try:

        connection = get_db_connection()

        with connection.cursor() as cursor:

            # --------------------------------------------------
            # Validate customer
            # --------------------------------------------------

            cursor.execute(
                """
                SELECT customer_id
                FROM customers
                WHERE customer_id = %s
                  AND deleted_at IS NULL
                """,
                (customer_id,)
            )

            customer = cursor.fetchone()

            if customer is None:

                raise ValueError(
                    "Customer not found"
                )

            # --------------------------------------------------
            # Read products and calculate total
            # --------------------------------------------------

            product_details = []

            total_amount = Decimal(
                "0.00"
            )

            for item in items:

                product_id = item[
                    "product_id"
                ]

                quantity = item[
                    "quantity"
                ]

                cursor.execute(
                    """
                    SELECT
                        product_id,
                        name,
                        price,
                        stock_quantity,
                        reorder_threshold
                    FROM products
                    WHERE product_id = %s
                      AND deleted_at IS NULL
                    """,
                    (product_id,)
                )

                product = cursor.fetchone()

                if product is None:

                    raise ValueError(
                        f"Product {product_id} not found"
                    )

                unit_price = Decimal(
                    str(
                        product["price"]
                    )
                )

                total_amount += (
                    unit_price * quantity
                )

                product_details.append(
                    {
                        "product_id":
                            product_id,

                        "quantity":
                            quantity,

                        "unit_price":
                            unit_price,

                        "stock_quantity":
                            int(
                                product[
                                    "stock_quantity"
                                ]
                            ),

                        "reorder_threshold":
                            int(
                                product[
                                    "reorder_threshold"
                                ]
                            )
                    }
                )

            # --------------------------------------------------
            # Create PENDING order
            # --------------------------------------------------

            cursor.execute(
                """
                INSERT INTO orders (
                    customer_id,
                    status,
                    total_amount
                )
                VALUES (
                    %s,
                    %s,
                    %s
                )
                """,
                (
                    customer_id,
                    "PENDING",
                    total_amount
                )
            )

            order_id = cursor.lastrowid

            # --------------------------------------------------
            # Create order items
            # --------------------------------------------------

            for product in product_details:

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
                        product[
                            "product_id"
                        ],
                        product[
                            "quantity"
                        ],
                        product[
                            "unit_price"
                        ]
                    )
                )

            # --------------------------------------------------
            # Create order log
            # --------------------------------------------------

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
                    None,
                    "PENDING",
                    "system",
                    "Order created"
                )
            )

        connection.commit()

        log_event(
            "INFO",
            "Order created successfully",
            request_id=context.aws_request_id,
            order_id=order_id,
            customer_id=customer_id,
            total_amount=total_amount
        )

        # ------------------------------------------------------
        # Publish OrderPlaced AFTER the placement transaction
        # commits.
        # ------------------------------------------------------

        publish_order_event(
            detail_type="OrderPlaced",
            order_id=order_id,
            customer_id=customer_id,
            status="PENDING",
            total_amount=total_amount
        )

        return {
            "order_id": order_id,
            "customer_id": customer_id,
            "total_amount": total_amount,
            "products": product_details
        }

    except Exception:

        if connection is not None:

            connection.rollback()

        raise

    finally:

        if connection is not None:

            connection.close()



# ==========================================================
# INVENTORY EVENT
# ==========================================================

def publish_inventory_event(
    product_id,
    product_name,
    old_stock,
    new_stock,
    low_stock_threshold
):
    detail = {
        "product_id": product_id,
        "product_name": product_name,
        "old_stock": old_stock,
        "new_stock": new_stock,
        "low_stock_threshold": low_stock_threshold,
        "low_stock": new_stock <= low_stock_threshold
    }

    try:
        result = events.put_events(
            Entries=[
                {
                    "EventBusName": EVENT_BUS_NAME,
                    "Source": "cloudmart.product",
                    "DetailType": "Inventory Changed",
                    "Detail": json.dumps(detail, default=str)
                }
            ]
        )

        if result.get("FailedEntryCount", 0) != 0:
            log_event(
                "ERROR",
                "Inventory event publishing failed",
                product_id=product_id,
                event_result=result
            )
            return False

        log_event(
            "INFO",
            "Inventory event published",
            product_id=product_id,
            old_stock=old_stock,
            new_stock=new_stock,
            low_stock=detail["low_stock"]
        )
        return True

    except Exception as exc:
        log_event(
            "ERROR",
            "Inventory event publishing exception",
            product_id=product_id,
            error_type=type(exc).__name__,
            error=str(exc)
        )
        return False



# ==========================================================
# CONFIRM ORDER
# ==========================================================

def confirm_order(
    order,
    context
):

    connection = None

    order_id = order[
        "order_id"
    ]

    customer_id = order[
        "customer_id"
    ]

    total_amount = order[
        "total_amount"
    ]

    try:

        connection = get_db_connection()

        with connection.cursor() as cursor:

            # --------------------------------------------------
            # Lock products before checking/deducting stock
            # --------------------------------------------------

            locked_products = []

            for product in order[
                "products"
            ]:

                product_id = product[
                    "product_id"
                ]

                quantity = product[
                    "quantity"
                ]

                cursor.execute(
                    """
                    SELECT
                        product_id,
                        stock_quantity,
                        reorder_threshold
                    FROM products
                    WHERE product_id = %s
                      AND deleted_at IS NULL
                    FOR UPDATE
                    """,
                    (product_id,)
                )

                current_product = (
                    cursor.fetchone()
                )

                if current_product is None:

                    raise ValueError(
                        f"Product {product_id} not found"
                    )

                current_stock = int(
                    current_product[
                        "stock_quantity"
                    ]
                )

                if current_stock < quantity:

                    raise ValueError(
                        f"Insufficient stock for product "
                        f"{product_id}. Available: "
                        f"{current_stock}, Requested: "
                        f"{quantity}"
                    )

                locked_products.append(
                    {
                        "product_id":
                            product_id,

                        "product_name":
                            product.get("name"),

                        "quantity":
                            quantity,

                        "stock_quantity":
                            current_stock,

                        "reorder_threshold":
                            int(
                                current_product[
                                    "reorder_threshold"
                                ]
                            )
                    }
                )

            # --------------------------------------------------
            # Deduct inventory
            # --------------------------------------------------

            for product in locked_products:

                new_stock = (
                    product[
                        "stock_quantity"
                    ]
                    -
                    product[
                        "quantity"
                    ]
                )

                cursor.execute(
                    """
                    UPDATE products
                    SET stock_quantity = %s
                    WHERE product_id = %s
                      AND deleted_at IS NULL
                    """,
                    (
                        new_stock,
                        product[
                            "product_id"
                        ]
                    )
                )

            # --------------------------------------------------
            # Update order status
            # --------------------------------------------------

            cursor.execute(
                """
                UPDATE orders
                SET status = %s
                WHERE order_id = %s
                  AND status = %s
                """,
                (
                    "CONFIRMED",
                    order_id,
                    "PENDING"
                )
            )

            if cursor.rowcount != 1:

                raise ValueError(
                    "Order could not be confirmed"
                )

            # --------------------------------------------------
            # Order log
            # --------------------------------------------------

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
                    "PENDING",
                    "CONFIRMED",
                    "system",
                    "Order confirmed and inventory deducted"
                )
            )

        # ------------------------------------------------------
        # Commit inventory + order status together
        # ------------------------------------------------------

        connection.commit()

        log_event(
            "INFO",
            "Order confirmed",
            request_id=context.aws_request_id,
            order_id=order_id,
            customer_id=customer_id,
            total_amount=total_amount
        )

        publish_order_event(
            detail_type="OrderConfirmed",
            order_id=order_id,
            customer_id=customer_id,
            status="CONFIRMED",
            total_amount=total_amount
        )

        for product in locked_products:
            publish_inventory_event(
                product_id=product["product_id"],
                product_name=product.get("product_name"),
                old_stock=product["stock_quantity"],
                new_stock=(
                    product["stock_quantity"]
                    - product["quantity"]
                ),
                low_stock_threshold=product["reorder_threshold"]
            )

        return {
            "order_id": order_id,
            "customer_id": customer_id,
            "status": "CONFIRMED",
            "total_amount": total_amount
        }

    except Exception as exc:

        if connection is not None:

            connection.rollback()

        log_event(
            "ERROR",
            "Order confirmation failed",
            request_id=context.aws_request_id,
            order_id=order_id,
            customer_id=customer_id,
            error_type=type(exc).__name__,
            error=str(exc)
        )

        publish_order_event(
            detail_type="OrderFailed",
            order_id=order_id,
            customer_id=customer_id,
            status="FAILED",
            reason=str(exc)
        )

        # ------------------------------------------------------
        # Mark order FAILED in a separate transaction.
        # The inventory/order confirmation transaction above
        # has already been rolled back.
        # ------------------------------------------------------

        mark_order_failed(
            order_id,
            str(exc),
            context
        )

        raise

    finally:

        if connection is not None:

            connection.close()


# ==========================================================
# MARK ORDER FAILED
# ==========================================================

def mark_order_failed(
    order_id,
    reason,
    context
):

    connection = None

    try:

        connection = get_db_connection()

        with connection.cursor() as cursor:

            cursor.execute(
                """
                UPDATE orders
                SET status = %s
                WHERE order_id = %s
                  AND status = %s
                """,
                (
                    "FAILED",
                    order_id,
                    "PENDING"
                )
            )

            if cursor.rowcount == 1:

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
                        "PENDING",
                        "FAILED",
                        "system",
                        reason
                    )
                )

        connection.commit()

        log_event(
            "INFO",
            "Order marked as failed",
            request_id=context.aws_request_id,
            order_id=order_id
        )

    except Exception as exc:

        if connection is not None:

            connection.rollback()

        log_event(
            "ERROR",
            "Failed to update order failure status",
            request_id=context.aws_request_id,
            order_id=order_id,
            error_type=type(exc).__name__,
            error=str(exc)
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

    log_event(
        "INFO",
        "Order processor request received",
        request_id=request_id
    )

    try:

        order_data = validate_order_payload(
            event
        )

        # ------------------------------------------------------
        # Step 1: Place order
        # ------------------------------------------------------

        order = create_pending_order(
            order_data,
            context
        )

        # ------------------------------------------------------
        # Step 2: Confirm order + deduct inventory
        # ------------------------------------------------------

        result = confirm_order(
            order,
            context
        )

        return response(
            200,
            {
                "message":
                    "Order processed successfully",

                "order_id":
                    result["order_id"],

                "customer_id":
                    result["customer_id"],

                "status":
                    result["status"],

                "total_amount":
                    result["total_amount"]
            }
        )

    except ValueError as exc:

        log_event(
            "WARN",
            "Order processing validation failed",
            request_id=request_id,
            error=str(exc)
        )

        return response(
            400,
            {
                "message": str(exc),
                "request_id": request_id
            }
        )

    except Exception as exc:

        log_event(
            "ERROR",
            "Order processor failed",
            request_id=request_id,
            error_type=type(exc).__name__,
            error=str(exc)
        )

        return response(
            500,
            {
                "message":
                    "Order processing failed",

                "request_id":
                    request_id
            }
        )