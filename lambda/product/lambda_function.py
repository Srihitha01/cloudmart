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
        "service": "cloudmart-product-lambda",
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
# REQUEST BODY
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

        stock_changed = (
            new_stock_quantity
            != old_stock_quantity
        )

        low_stock = (
            new_stock_quantity
            <= new_reorder_threshold
        )

        event_published = False

        # ----------------------------------------------------
        # Publish inventory event
        # ----------------------------------------------------

        if stock_changed:

            event_detail = {
                "product_id": product_id,
                "product_name": existing_product["name"],
                "old_stock": old_stock_quantity,
                "new_stock": new_stock_quantity,
                "low_stock_threshold": new_reorder_threshold,
                "low_stock": low_stock
            }

            event_result = events.put_events(
                Entries=[
                    {
                        "EventBusName": EVENT_BUS_NAME,
                        "Source": "cloudmart.product",
                        "DetailType": "Inventory Changed",
                        "Detail": json.dumps(
                            event_detail
                        )
                    }
                ]
            )

            failed_count = event_result.get(
                "FailedEntryCount",
                0
            )

            if failed_count != 0:

                log_event(
                    "ERROR",
                    "Inventory event publishing failed",
                    request_id=
                        context.aws_request_id,
                    product_id=
                        product_id,
                    event_result=
                        event_result
                )

            else:

                event_published = True

                log_event(
                    "INFO",
                    "Inventory change event published",
                    request_id=
                        context.aws_request_id,
                    product_id=
                        product_id,
                    previous_stock_quantity=
                        old_stock_quantity,
                    stock_quantity=
                        new_stock_quantity,
                    reorder_threshold=
                        new_reorder_threshold,
                    low_stock=
                        low_stock
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
                SET deleted_at = NOW()
                WHERE product_id = %s
                  AND deleted_at IS NULL
                """,
                (product_id,)
            )

        connection.commit()

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