
import hashlib
import json
import os
import time
from decimal import Decimal

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

ssm = boto3.client("ssm", config=AWS_API_CONFIG)
events = boto3.client("events", config=AWS_API_CONFIG)


# ==========================================================
# SSM PARAMETER NAMES
# ==========================================================

DB_NAME_PARAMETER = os.environ["DB_NAME_PARAMETER"]
DB_ENDPOINT_PARAMETER = os.environ["DB_ENDPOINT_PARAMETER"]
DB_PORT_PARAMETER = os.environ["DB_PORT_PARAMETER"]
DB_USERNAME_PARAMETER = os.environ["DB_USERNAME_PARAMETER"]
DB_PASSWORD_PARAMETER = os.environ["DB_PASSWORD_PARAMETER"]
EVENT_BUS_NAME = os.environ["EVENT_BUS_NAME"]

_PARAMETER_CACHE = {}
_PARAMETER_CACHE_AT = 0.0
_PARAMETER_CACHE_TTL_SECONDS = 300


# ==========================================================
# STRUCTURED LOGGING
# ==========================================================

def log_event(level, message, **details):
    record = {
        "level": level,
        "service": "cloudmart-product-lambda",
        "message": message,
        **details
    }

    print(json.dumps(record, default=str))



def publish_custom_metric(metric_name, value=1):
    """Emit a CloudWatch EMF metric through Lambda logs; never call CloudWatch synchronously."""
    try:
        metric_record = {
            "_aws": {
                "Timestamp": int(time.time() * 1000),
                "CloudWatchMetrics": [
                    {
                        "Namespace": "CloudMart/Business",
                        "Dimensions": [[]],
                        "Metrics": [
                            {
                                "Name": metric_name,
                                "Unit": "Count",
                            }
                        ],
                    }
                ],
            },
            metric_name: value,
        }
        print(json.dumps(metric_record, default=str))
    except Exception as exc:
        log_event(
            "WARN",
            "CloudWatch EMF metric emission failed",
            metric_name=metric_name,
            error_type=type(exc).__name__,
            error=str(exc),
        )


def publish_inventory_event(event_detail, request_id=None, product_id=None):
    """Publish the normal inventory event and, when low, a dedicated alert event."""
    entries = [{
        "EventBusName": EVENT_BUS_NAME,
        "Source": "cloudmart.product",
        "DetailType": "Inventory Changed",
        "Detail": json.dumps(event_detail, default=str),
    }]

    if bool(event_detail.get("low_stock")):
        entries.append({
            "EventBusName": EVENT_BUS_NAME,
            "Source": "cloudmart.product",
            "DetailType": "LowStockAlert",
            "Detail": json.dumps(event_detail, default=str),
        })

    try:
        result = events.put_events(Entries=entries)
        ok = int(result.get("FailedEntryCount", 0) or 0) == 0
        if not ok:
            log_event(
                "WARN",
                "Product EventBridge publish returned failed entries",
                request_id=request_id,
                product_id=product_id,
                result=result,
            )
        return ok
    except Exception as exc:
        log_event(
            "WARN",
            "Product EventBridge publish skipped; product update retained",
            request_id=request_id,
            product_id=product_id,
            error_type=type(exc).__name__,
            error=str(exc),
        )
        return False

def publish_inventory_count(connection):
    """Publish total available inventory quantity as a best-effort metric."""
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT COALESCE(SUM(stock_quantity), 0) AS inventory_count
                FROM products
                WHERE deleted_at IS NULL
                """
            )
            result = cursor.fetchone() or {}
            publish_custom_metric(
                "InventoryCount",
                result.get("inventory_count", 0)
            )
    except Exception as exc:
        log_event(
            "WARN",
            "Inventory count metric skipped",
            error_type=type(exc).__name__,
            error=str(exc)
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


def load_database_parameters():
    global _PARAMETER_CACHE, _PARAMETER_CACHE_AT

    now = time.monotonic()
    if _PARAMETER_CACHE and (now - _PARAMETER_CACHE_AT) < _PARAMETER_CACHE_TTL_SECONDS:
        return _PARAMETER_CACHE

    names = [
        DB_NAME_PARAMETER,
        DB_ENDPOINT_PARAMETER,
        DB_PORT_PARAMETER,
        DB_USERNAME_PARAMETER,
        DB_PASSWORD_PARAMETER,
    ]

    result = ssm.get_parameters(
        Names=names,
        WithDecryption=True,
    )

    returned = {
        item["Name"]: item["Value"]
        for item in result.get("Parameters", [])
    }
    missing = [name for name in names if name not in returned]
    if missing:
        raise RuntimeError(
            "Missing CloudMart database parameters: " + ", ".join(missing)
        )

    _PARAMETER_CACHE = {
        "database": returned[DB_NAME_PARAMETER],
        "host": returned[DB_ENDPOINT_PARAMETER],
        "port": int(returned[DB_PORT_PARAMETER]),
        "username": returned[DB_USERNAME_PARAMETER],
        "password": returned[DB_PASSWORD_PARAMETER],
    }
    _PARAMETER_CACHE_AT = now
    return _PARAMETER_CACHE


def get_parameter(name):
    db = load_database_parameters()
    mapping = {
        DB_NAME_PARAMETER: db["database"],
        DB_ENDPOINT_PARAMETER: db["host"],
        DB_PORT_PARAMETER: str(db["port"]),
        DB_USERNAME_PARAMETER: db["username"],
        DB_PASSWORD_PARAMETER: db["password"],
    }
    if name not in mapping:
        raise RuntimeError(f"Unsupported SSM parameter requested: {name}")
    return mapping[name]


def get_db_connection():
    db = load_database_parameters()
    connection = pymysql.connect(
        host=db["host"],
        port=db["port"],
        user=db["username"],
        password=db["password"],
        database=db["database"],
        connect_timeout=3,
        read_timeout=5,
        write_timeout=5,
        autocommit=False,
        cursorclass=pymysql.cursors.DictCursor,
    )
    with connection.cursor() as cursor:
        cursor.execute("SET SESSION innodb_lock_wait_timeout = 5")
    return connection

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
            "Request body must contain a JSON object"
        )

    return parsed


# ==========================================================
# PRODUCT ID
# ==========================================================

def get_product_id(event):

    path_parameters = (
        event.get("pathParameters") or {}
    )

    product_id = path_parameters.get(
        "id"
    )

    if product_id is None:
        return None

    try:

        return int(product_id)

    except (TypeError, ValueError):

        raise ValueError(
            "Product id must be an integer"
        )


# ==========================================================
# CREATE VALIDATION
# ==========================================================

def validate_create_payload(data):

    required_fields = [
        "category_id",
        "name",
        "price"
    ]

    missing = [
        field
        for field in required_fields
        if field not in data
    ]

    if missing:

        raise ValueError(
            "Missing required fields: "
            + ", ".join(missing)
        )

    try:

        category_id = int(
            data["category_id"]
        )

    except (TypeError, ValueError):

        raise ValueError(
            "category_id must be an integer"
        )

    name = str(
        data["name"]
    ).strip()

    if not name:

        raise ValueError(
            "name is required"
        )

    try:

        price = Decimal(
            str(data["price"])
        )

    except Exception:

        raise ValueError(
            "price must be a valid number"
        )

    if price < 0:

        raise ValueError(
            "price cannot be negative"
        )

    try:

        stock_quantity = int(
            data.get(
                "stock_quantity",
                0
            )
        )

    except (TypeError, ValueError):

        raise ValueError(
            "stock_quantity must be an integer"
        )

    if stock_quantity < 0:

        raise ValueError(
            "stock_quantity cannot be negative"
        )

    try:

        reorder_threshold = int(
            data.get(
                "reorder_threshold",
                5
            )
        )

    except (TypeError, ValueError):

        raise ValueError(
            "reorder_threshold must be an integer"
        )

    if reorder_threshold < 0:

        raise ValueError(
            "reorder_threshold cannot be negative"
        )

    description = data.get(
        "description"
    )

    if description is not None:

        description = str(
            description
        ).strip()

    return {
        "category_id":
            category_id,
        "name":
            name,
        "description":
            description,
        "price":
            price,
        "stock_quantity":
            stock_quantity,
        "reorder_threshold":
            reorder_threshold
    }




# ==========================================================
# CREATE PRODUCT
# ==========================================================

def create_product(
    event,
    context
):

    permission_error = require_admin(event)
    if permission_error is not None:
        return permission_error

    data = parse_body(event)

    product = validate_create_payload(
        data
    )

    connection = None

    try:

        connection = get_db_connection()

        with connection.cursor() as cursor:

            cursor.execute(
                """
                INSERT INTO products (
                    category_id,
                    name,
                    description,
                    price,
                    stock_quantity,
                    reorder_threshold
                )
                VALUES (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s
                )
                """,
                (
                    product["category_id"],
                    product["name"],
                    product["description"],
                    product["price"],
                    product["stock_quantity"],
                    product["reorder_threshold"]
                )
            )

            product_id = cursor.lastrowid

        connection.commit()

        publish_inventory_count(connection)

        low_stock = (
            product["stock_quantity"]
            <= product["reorder_threshold"]
        )
        if low_stock:
            publish_custom_metric("LowStockEvents")

        publish_inventory_event(
            event_detail={
                "product_id": product_id,
                "product_name": product["name"],
                "old_stock": product["stock_quantity"],
                "new_stock": product["stock_quantity"],
                "low_stock_threshold": product["reorder_threshold"],
                "low_stock": low_stock,
            },
            request_id=context.aws_request_id,
            product_id=product_id,
        )

        log_event(
            "INFO",
            "Product created",
            request_id=
                context.aws_request_id,
            product_id=
                product_id
        )

        return response(
            201,
            {
                "message":
                    "Product created successfully",
                "product_id":
                    product_id
            }
        )

    except pymysql.err.IntegrityError as exc:

        if connection is not None:

            connection.rollback()

        log_event(
            "ERROR",
            "Product creation failed",
            request_id=
                context.aws_request_id,
            error_type=
                type(exc).__name__,
            error=str(exc)
        )

        return response(
            400,
            {
                "message":
                    "Invalid product data"
            }
        )

    finally:

        if connection is not None:
            connection.close()


# ==========================================================
# GET ALL PRODUCTS
# ==========================================================

def get_products(
    event,
    context
):

    connection = None

    try:

        connection = get_db_connection()

        with connection.cursor() as cursor:

            cursor.execute(
                """
                SELECT
                    product_id,
                    category_id,
                    name,
                    description,
                    price,
                    stock_quantity,
                    reorder_threshold,
                    created_at,
                    updated_at
                FROM products
                WHERE deleted_at IS NULL
                ORDER BY product_id
                """
            )

            products = cursor.fetchall()

        publish_inventory_count(connection)

        log_event(
            "INFO",
            "Products retrieved",
            request_id=
                context.aws_request_id,
            count=
                len(products)
        )

        return response(
            200,
            products
        )

    finally:

        if connection is not None:
            connection.close()


# ==========================================================
# GET ONE PRODUCT
# ==========================================================

def get_product(
    event,
    context
):

    product_id = get_product_id(
        event
    )

    if product_id is None:

        return response(
            400,
            {
                "message":
                    "Product id is required"
            }
        )

    connection = None

    try:

        connection = get_db_connection()

        with connection.cursor() as cursor:

            cursor.execute(
                """
                SELECT
                    product_id,
                    category_id,
                    name,
                    description,
                    price,
                    stock_quantity,
                    reorder_threshold,
                    created_at,
                    updated_at
                FROM products
                WHERE product_id = %s
                  AND deleted_at IS NULL
                """,
                (product_id,)
            )

            product = cursor.fetchone()

        if product is None:

            return response(
                404,
                {
                    "message":
                        "Product not found"
                }
            )

        log_event(
            "INFO",
            "Product retrieved",
            request_id=
                context.aws_request_id,
            product_id=
                product_id
        )

        return response(
            200,
            product
        )

    finally:

        if connection is not None:
            connection.close()


# ==========================================================
# UPDATE PRODUCT
# ==========================================================

def update_product(
    event,
    context
):

    permission_error = require_admin(event)
    if permission_error is not None:
        return permission_error

    product_id = get_product_id(
        event
    )

    if product_id is None:

        return response(
            400,
            {
                "message":
                    "Product id is required"
            }
        )

    data = parse_body(
        event
    )

    allowed_fields = {
        "category_id",
        "name",
        "description",
        "price",
        "stock_quantity",
        "reorder_threshold"
    }

    update_fields = {
        key: data[key]
        for key in allowed_fields
        if key in data
    }

    if not update_fields:

        return response(
            400,
            {
                "message":
                    "No valid fields provided for update"
            }
        )

    # ------------------------------------------------------
    # Validate category
    # ------------------------------------------------------

    if "category_id" in update_fields:

        try:

            update_fields[
                "category_id"
            ] = int(
                update_fields[
                    "category_id"
                ]
            )

        except (TypeError, ValueError):

            return response(
                400,
                {
                    "message":
                        "category_id must be an integer"
                }
            )

    # ------------------------------------------------------
    # Validate name
    # ------------------------------------------------------

    if "name" in update_fields:

        update_fields["name"] = str(
            update_fields["name"]
        ).strip()

        if not update_fields["name"]:

            return response(
                400,
                {
                    "message":
                        "name cannot be empty"
                }
            )

    # ------------------------------------------------------
    # Validate price
    # ------------------------------------------------------

    if "price" in update_fields:

        try:

            update_fields["price"] = Decimal(
                str(
                    update_fields["price"]
                )
            )

        except Exception:

            return response(
                400,
                {
                    "message":
                        "price must be a valid number"
                }
            )

        if update_fields["price"] < 0:

            return response(
                400,
                {
                    "message":
                        "price cannot be negative"
                }
            )

    # ------------------------------------------------------
    # Validate stock
    # ------------------------------------------------------

    if "stock_quantity" in update_fields:

        try:

            update_fields[
                "stock_quantity"
            ] = int(
                update_fields[
                    "stock_quantity"
                ]
            )

        except (TypeError, ValueError):

            return response(
                400,
                {
                    "message":
                        "stock_quantity must be an integer"
                }
            )

        if update_fields[
            "stock_quantity"
        ] < 0:

            return response(
                400,
                {
                    "message":
                        "stock_quantity cannot be negative"
                }
            )

    # ------------------------------------------------------
    # Validate threshold
    # ------------------------------------------------------

    if "reorder_threshold" in update_fields:

        try:

            update_fields[
                "reorder_threshold"
            ] = int(
                update_fields[
                    "reorder_threshold"
                ]
            )

        except (TypeError, ValueError):

            return response(
                400,
                {
                    "message":
                        "reorder_threshold must be an integer"
                }
            )

        if update_fields[
            "reorder_threshold"
        ] < 0:

            return response(
                400,
                {
                    "message":
                        "reorder_threshold cannot be negative"
                }
            )

    # ------------------------------------------------------
    # Normalize description
    # ------------------------------------------------------

    if "description" in update_fields:

        if update_fields[
            "description"
        ] is not None:

            update_fields[
                "description"
            ] = str(
                update_fields[
                    "description"
                ]
            ).strip()

    connection = None

    try:

        connection = get_db_connection()

        with connection.cursor() as cursor:

            # ------------------------------------------------
            # Read old inventory state
            # ------------------------------------------------

            cursor.execute(
                """
                SELECT
                    product_id,
                    name,
                    stock_quantity,
                    reorder_threshold
                FROM products
                WHERE product_id = %s
                  AND deleted_at IS NULL
                """,
                (product_id,)
            )

            existing_product = (
                cursor.fetchone()
            )

            if existing_product is None:

                return response(
                    404,
                    {
                        "message":
                            "Product not found"
                    }
                )

            old_stock_quantity = int(
                existing_product[
                    "stock_quantity"
                ]
            )

            old_reorder_threshold = int(
                existing_product[
                    "reorder_threshold"
                ]
            )

            # ------------------------------------------------
            # Build update query
            # ------------------------------------------------

            fields = []
            values = []

            for field, value in update_fields.items():

                fields.append(
                    f"{field} = %s"
                )

                values.append(
                    value
                )

            values.append(
                product_id
            )

            query = f"""
                UPDATE products
                SET {", ".join(fields)}
                WHERE product_id = %s
                  AND deleted_at IS NULL
            """

            cursor.execute(
                query,
                tuple(values)
            )

        connection.commit()

        publish_inventory_count(connection)

        # ----------------------------------------------------
        # Determine new inventory state
        # ----------------------------------------------------

        new_stock_quantity = int(
            update_fields.get(
                "stock_quantity",
                old_stock_quantity
            )
        )

        new_reorder_threshold = int(
            update_fields.get(
                "reorder_threshold",
                old_reorder_threshold
            )
        )

        stock_changed = new_stock_quantity != old_stock_quantity
        threshold_changed = new_reorder_threshold != old_reorder_threshold
        inventory_condition_changed = stock_changed or threshold_changed

        low_stock = new_stock_quantity <= new_reorder_threshold

        if inventory_condition_changed and low_stock:
            publish_custom_metric("LowStockEvents")

        event_published = False

        # ----------------------------------------------------
        # Publish inventory event
        # ----------------------------------------------------

        if inventory_condition_changed:

            event_detail = {
                "product_id": product_id,
                "product_name": existing_product["name"],
                "old_stock": old_stock_quantity,
                "new_stock": new_stock_quantity,
                "low_stock_threshold": new_reorder_threshold,
                "low_stock": low_stock
            }

            event_published = publish_inventory_event(
                event_detail=event_detail,
                request_id=context.aws_request_id,
                product_id=product_id
            )

        # ----------------------------------------------------
        # Final structured log
        # ----------------------------------------------------

        log_event(
            "INFO",
            "Product updated",
            request_id=
                context.aws_request_id,
            product_id=
                product_id,
            previous_stock_quantity=
                old_stock_quantity,
            new_stock_quantity=
                new_stock_quantity,
            reorder_threshold=
                new_reorder_threshold,
            low_stock=
                low_stock,
            event_published=
                event_published
        )

        return response(
            200,
            {
                "message":
                    "Product updated successfully",

                "product_id":
                    product_id,

                "previous_stock_quantity":
                    old_stock_quantity,

                "new_stock_quantity":
                    new_stock_quantity,

                "reorder_threshold":
                    new_reorder_threshold,

                "low_stock":
                    low_stock,

                "event_published":
                    event_published
            }
        )

    except pymysql.err.IntegrityError as exc:

        if connection is not None:
            connection.rollback()

        log_event(
            "ERROR",
            "Product update failed",
            request_id=
                context.aws_request_id,
            product_id=
                product_id,
            error_type=
                type(exc).__name__,
            error=str(exc)
        )

        return response(
            400,
            {
                "message":
                    "Invalid product update"
            }
        )

    except Exception as exc:

        if connection is not None:
            connection.rollback()

        log_event(
            "ERROR",
            "Product update failed",
            request_id=
                context.aws_request_id,
            product_id=
                product_id,
            error_type=
                type(exc).__name__,
            error=str(exc)
        )

        return response(
            500,
            {
                "message":
                    "Product update failed",
                "request_id":
                    context.aws_request_id
            }
        )

    finally:

        if connection is not None:
            connection.close()


# ==========================================================
# DELETE PRODUCT
# ==========================================================

def delete_product(
    event,
    context
):

    permission_error = require_admin(event)
    if permission_error is not None:
        return permission_error

    product_id = get_product_id(
        event
    )

    if product_id is None:

        return response(
            400,
            {
                "message":
                    "Product id is required"
            }
        )

    connection = None

    try:

        connection = get_db_connection()

        with connection.cursor() as cursor:

            cursor.execute(
                """
                SELECT
                    product_id
                FROM products
                WHERE product_id = %s
                  AND deleted_at IS NULL
                """,
                (product_id,)
            )

            product = cursor.fetchone()

            if product is None:

                return response(
                    404,
                    {
                        "message":
                            "Product not found"
                    }
                )

            # Soft delete
            cursor.execute(
                """
                UPDATE products
                SET
                    deleted_at = NOW(),
                    status = 'INACTIVE'
                WHERE product_id = %s
                  AND deleted_at IS NULL
                """,
                (product_id,)
            )

        connection.commit()

        publish_inventory_count(connection)

        log_event(
            "INFO",
            "Product deleted",
            request_id=
                context.aws_request_id,
            product_id=
                product_id
        )

        return response(
            200,
            {
                "message":
                    "Product deleted successfully",
                "product_id":
                    product_id
            }
        )

    finally:

        if connection is not None:
            connection.close()


# ==========================================================
# SERVICE-LAYER AUTHORIZATION
# ==========================================================

def get_authorizer_context(event):
    return (event.get("requestContext") or {}).get("authorizer") or {}


def is_admin(event):
    return str(
        get_authorizer_context(event).get("role", "")
    ).upper() == "ADMIN"


def require_admin(event):
    if not is_admin(event):
        return response(
            403,
            {
                "message": "Admin permission is required"
            }
        )

    return None


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
        or event.get(
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
        "Product request received",
        request_id=
            request_id,
        http_method=
            http_method,
        resource=
            resource
    )

    try:



        # ----------------------------------------------------
        # POST /products
        # ----------------------------------------------------

        if (
            http_method == "POST"
            and resource == "/products"
        ):

            return create_product(
                event,
                context
            )

        # ----------------------------------------------------
        # GET /products
        # ----------------------------------------------------

        if (
            http_method == "GET"
            and resource == "/products"
        ):

            return get_products(
                event,
                context
            )

        # ----------------------------------------------------
        # GET /products/{id}
        # ----------------------------------------------------

        if (
            http_method == "GET"
            and resource == "/products/{id}"
        ):

            return get_product(
                event,
                context
            )

        # ----------------------------------------------------
        # PUT /products/{id}
        # ----------------------------------------------------

        if (
            http_method == "PUT"
            and resource == "/products/{id}"
        ):

            return update_product(
                event,
                context
            )

        # ----------------------------------------------------
        # DELETE /products/{id}
        # ----------------------------------------------------

        if (
            http_method == "DELETE"
            and resource == "/products/{id}"
        ):

            return delete_product(
                event,
                context
            )

        # ----------------------------------------------------
        # Unsupported route
        # ----------------------------------------------------

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
            "Invalid product request",
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
            "Product request failed",
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