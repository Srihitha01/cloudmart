import base64
import json
import math
import os
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Optional, Tuple

import boto3
import pymysql
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError


# ==========================================================
# AWS CLIENT CONFIGURATION
# ==========================================================
# Keep every AWS SDK call bounded so a private-subnet/VPC
# connectivity problem cannot consume the full API timeout.

AWS_API_CONFIG = Config(
    connect_timeout=2,
    read_timeout=2,
    retries={
        "max_attempts": 1,
        "mode": "standard",
    },
)

ssm = boto3.client("ssm", config=AWS_API_CONFIG)
events = boto3.client("events", config=AWS_API_CONFIG)


# ==========================================================
# ENVIRONMENT
# ==========================================================

DB_NAME_PARAMETER = os.environ["DB_NAME_PARAMETER"]
DB_ENDPOINT_PARAMETER = os.environ["DB_ENDPOINT_PARAMETER"]
DB_PORT_PARAMETER = os.environ["DB_PORT_PARAMETER"]
DB_USERNAME_PARAMETER = os.environ["DB_USERNAME_PARAMETER"]
DB_PASSWORD_PARAMETER = os.environ["DB_PASSWORD_PARAMETER"]
EVENT_BUS_NAME = os.environ["EVENT_BUS_NAME"]

SSM_PARAMETER_NAMES = {
    "db_name": DB_NAME_PARAMETER,
    "db_host": DB_ENDPOINT_PARAMETER,
    "db_port": DB_PORT_PARAMETER,
    "db_username": DB_USERNAME_PARAMETER,
    "db_password": DB_PASSWORD_PARAMETER,
}

# Warm-Lambda cache. A cold start makes one SSM request instead of five.
_PARAMETER_CACHE: Optional[Dict[str, str]] = None


# ==========================================================
# CUSTOM EXCEPTIONS
# ==========================================================

class DependencyError(Exception):
    """A required AWS/RDS dependency is unavailable or misconfigured."""


# ==========================================================
# STRUCTURED LOGGING
# ==========================================================

def log_event(level: str, message: str, **details: Any) -> None:
    record = {
        "level": level,
        "service": "cloudmart-product-lambda",
        "message": message,
        **details,
    }
    print(json.dumps(record, default=str))


# ==========================================================
# CLOUDWATCH METRICS VIA EMF
# ==========================================================
# This avoids an extra synchronous cloudwatch.put_metric_data()
# network call during customer-facing API requests.


def publish_custom_metric(metric_name: str, value: float = 1) -> None:
    try:
        metric_value = float(value)
        if not math.isfinite(metric_value):
            metric_value = 0.0

        emf_record = {
            "_aws": {
                "Timestamp": __import__("time").time_ns() // 1_000_000,
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
            metric_name: metric_value,
        }
        print(json.dumps(emf_record, default=str))
    except Exception as exc:
        # A metric must never break a successful business operation.
        log_event(
            "WARN",
            "Custom metric emission skipped",
            metric_name=metric_name,
            error_type=type(exc).__name__,
            error=str(exc),
        )


# ==========================================================
# RESPONSE
# ==========================================================

def response(status_code: int, body: Any) -> Dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type,Authorization",
            "Access-Control-Allow-Methods": "GET,POST,PUT,PATCH,DELETE,OPTIONS",
        },
        "body": json.dumps(body, default=str),
    }


# ==========================================================
# SSM PARAMETERS
# ==========================================================

def _load_parameters() -> Dict[str, str]:
    global _PARAMETER_CACHE

    if _PARAMETER_CACHE is not None:
        return _PARAMETER_CACHE

    log_event(
        "INFO",
        "Loading database parameters from SSM",
        parameter_names=list(SSM_PARAMETER_NAMES.values()),
    )

    try:
        result = ssm.get_parameters(
            Names=list(SSM_PARAMETER_NAMES.values()),
            WithDecryption=True,
        )
    except (BotoCoreError, ClientError) as exc:
        log_event(
            "ERROR",
            "Unable to read database parameters from SSM",
            error_type=type(exc).__name__,
            error=str(exc),
        )
        raise DependencyError("Database configuration is temporarily unavailable") from exc

    values_by_name = {
        item["Name"]: item["Value"]
        for item in result.get("Parameters", [])
    }

    missing = [
        name
        for name in SSM_PARAMETER_NAMES.values()
        if name not in values_by_name
    ]

    if missing:
        log_event(
            "ERROR",
            "Required SSM parameters are missing",
            missing_parameters=missing,
        )
        raise DependencyError("Required database configuration is missing")

    _PARAMETER_CACHE = {
        key: values_by_name[name]
        for key, name in SSM_PARAMETER_NAMES.items()
    }

    log_event(
        "INFO",
        "Database parameters loaded from SSM",
        cached=True,
    )

    return _PARAMETER_CACHE


# Backward-compatible helper for internal use.
def get_parameter(name: str) -> str:
    params = _load_parameters()
    for key, parameter_name in SSM_PARAMETER_NAMES.items():
        if parameter_name == name:
            return params[key]

    try:
        result = ssm.get_parameter(
            Name=name,
            WithDecryption=True,
        )
        return result["Parameter"]["Value"]
    except (BotoCoreError, ClientError) as exc:
        raise DependencyError("Required configuration is unavailable") from exc


# ==========================================================
# DATABASE CONNECTION
# ==========================================================

def get_db_connection() -> pymysql.connections.Connection:
    params = _load_parameters()

    try:
        db_port = int(params["db_port"])
    except (TypeError, ValueError) as exc:
        raise DependencyError("Database port configuration is invalid") from exc

    log_event(
        "INFO",
        "Connecting to RDS",
        host=params["db_host"],
        port=db_port,
        database=params["db_name"],
    )

    try:
        connection = pymysql.connect(
            host=params["db_host"],
            port=db_port,
            user=params["db_username"],
            password=params["db_password"],
            database=params["db_name"],
            connect_timeout=4,
            read_timeout=4,
            write_timeout=4,
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=False,
        )

        # Prevent SELECT ... FOR UPDATE from waiting indefinitely for a lock.
        with connection.cursor() as cursor:
            cursor.execute("SET SESSION innodb_lock_wait_timeout = 3")

        log_event("INFO", "RDS connection established")
        return connection

    except pymysql.MySQLError as exc:
        log_event(
            "ERROR",
            "RDS connection failed",
            error_type=type(exc).__name__,
            error=str(exc),
        )
        raise DependencyError("Database is temporarily unavailable") from exc


# ==========================================================
# REQUEST HELPERS
# ==========================================================

def parse_body(event: Dict[str, Any]) -> Dict[str, Any]:
    body = event.get("body")

    if body is None:
        return {}

    if isinstance(body, dict):
        return body

    if not isinstance(body, str):
        raise ValueError("Request body must be JSON")

    if event.get("isBase64Encoded"):
        try:
            body = base64.b64decode(body).decode("utf-8")
        except Exception as exc:
            raise ValueError("Request body contains invalid base64 data") from exc

    if not body.strip():
        return {}

    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ValueError("Request body contains invalid JSON") from exc

    if not isinstance(parsed, dict):
        raise ValueError("Request body must contain a JSON object")

    return parsed


def get_product_id(event: Dict[str, Any]) -> Optional[int]:
    path_parameters = event.get("pathParameters") or {}
    raw_id = path_parameters.get("id") or path_parameters.get("product_id")

    if raw_id is None:
        return None

    try:
        product_id = int(raw_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("Product id must be an integer") from exc

    if product_id <= 0:
        raise ValueError("Product id must be greater than zero")

    return product_id


def get_request_id(context: Any) -> Optional[str]:
    return getattr(context, "aws_request_id", None)


# ==========================================================
# VALIDATION
# ==========================================================

def validate_create_payload(data: Dict[str, Any]) -> Dict[str, Any]:
    required_fields = ["category_id", "name", "price"]
    missing = [field for field in required_fields if field not in data]

    if missing:
        raise ValueError("Missing required fields: " + ", ".join(missing))

    try:
        category_id = int(data["category_id"])
    except (TypeError, ValueError) as exc:
        raise ValueError("category_id must be an integer") from exc

    if category_id <= 0:
        raise ValueError("category_id must be greater than zero")

    name = str(data["name"]).strip()
    if not name:
        raise ValueError("name is required")

    try:
        price = Decimal(str(data["price"]))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("price must be a valid number") from exc

    if not price.is_finite():
        raise ValueError("price must be a finite number")

    if price < 0:
        raise ValueError("price cannot be negative")

    try:
        stock_quantity = int(data.get("stock_quantity", 0))
    except (TypeError, ValueError) as exc:
        raise ValueError("stock_quantity must be an integer") from exc

    if stock_quantity < 0:
        raise ValueError("stock_quantity cannot be negative")

    try:
        reorder_threshold = int(data.get("reorder_threshold", 5))
    except (TypeError, ValueError) as exc:
        raise ValueError("reorder_threshold must be an integer") from exc

    if reorder_threshold < 0:
        raise ValueError("reorder_threshold cannot be negative")

    description = data.get("description")
    if description is not None:
        description = str(description).strip()

    return {
        "category_id": category_id,
        "name": name,
        "description": description,
        "price": price,
        "stock_quantity": stock_quantity,
        "reorder_threshold": reorder_threshold,
    }


def validate_update_payload(data: Dict[str, Any]) -> Dict[str, Any]:
    allowed_fields = {
        "category_id",
        "name",
        "description",
        "price",
        "stock_quantity",
        "reorder_threshold",
    }

    update_fields = {
        key: data[key]
        for key in allowed_fields
        if key in data
    }

    if not update_fields:
        raise ValueError("No valid fields provided for update")

    if "category_id" in update_fields:
        try:
            update_fields["category_id"] = int(update_fields["category_id"])
        except (TypeError, ValueError) as exc:
            raise ValueError("category_id must be an integer") from exc
        if update_fields["category_id"] <= 0:
            raise ValueError("category_id must be greater than zero")

    if "name" in update_fields:
        update_fields["name"] = str(update_fields["name"]).strip()
        if not update_fields["name"]:
            raise ValueError("name cannot be empty")

    if "price" in update_fields:
        try:
            update_fields["price"] = Decimal(str(update_fields["price"]))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError("price must be a valid number") from exc
        if not update_fields["price"].is_finite():
            raise ValueError("price must be a finite number")
        if update_fields["price"] < 0:
            raise ValueError("price cannot be negative")

    if "stock_quantity" in update_fields:
        try:
            update_fields["stock_quantity"] = int(update_fields["stock_quantity"])
        except (TypeError, ValueError) as exc:
            raise ValueError("stock_quantity must be an integer") from exc
        if update_fields["stock_quantity"] < 0:
            raise ValueError("stock_quantity cannot be negative")

    if "reorder_threshold" in update_fields:
        try:
            update_fields["reorder_threshold"] = int(update_fields["reorder_threshold"])
        except (TypeError, ValueError) as exc:
            raise ValueError("reorder_threshold must be an integer") from exc
        if update_fields["reorder_threshold"] < 0:
            raise ValueError("reorder_threshold cannot be negative")

    if "description" in update_fields and update_fields["description"] is not None:
        update_fields["description"] = str(update_fields["description"]).strip()

    return update_fields


# ==========================================================
# SERVICE AUTHORIZATION
# ==========================================================

def get_authorizer_context(event: Dict[str, Any]) -> Dict[str, Any]:
    return (event.get("requestContext") or {}).get("authorizer") or {}


def is_admin(event: Dict[str, Any]) -> bool:
    auth = get_authorizer_context(event)
    return str(auth.get("role", "")).strip().upper() == "ADMIN"


def require_admin(event: Dict[str, Any]) -> None:
    if not is_admin(event):
        raise PermissionError("Admin permission is required")


# ==========================================================
# PRODUCT QUERIES
# ==========================================================

def fetch_product(cursor: Any, product_id: int) -> Optional[Dict[str, Any]]:
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
            status,
            created_at,
            updated_at
        FROM products
        WHERE product_id = %s
          AND deleted_at IS NULL
        """,
        (product_id,),
    )
    return cursor.fetchone()


def publish_inventory_count(connection: Any) -> None:
    """Best-effort inventory metric; no CloudWatch API network call."""
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
                result.get("inventory_count", 0),
            )
    except Exception as exc:
        log_event(
            "WARN",
            "Inventory count metric skipped",
            error_type=type(exc).__name__,
            error=str(exc),
        )


# ==========================================================
# EVENTBRIDGE
# ==========================================================

def publish_inventory_event(
    event_detail: Dict[str, Any],
    request_id: Optional[str],
    product_id: int,
) -> bool:
    """Best effort only. Never blocks the successful DB update indefinitely."""
    try:
        log_event(
            "INFO",
            "Publishing inventory event",
            request_id=request_id,
            product_id=product_id,
        )

        result = events.put_events(
            Entries=[
                {
                    "EventBusName": EVENT_BUS_NAME,
                    "Source": "cloudmart.product",
                    "DetailType": "Inventory Changed",
                    "Detail": json.dumps(event_detail, default=str),
                }
            ]
        )

        failed_count = int(result.get("FailedEntryCount", 0))
        if failed_count:
            log_event(
                "WARN",
                "Inventory event rejected by EventBridge; DB update retained",
                request_id=request_id,
                product_id=product_id,
                failed_count=failed_count,
                event_result=result,
            )
            return False

        log_event(
            "INFO",
            "Inventory event published",
            request_id=request_id,
            product_id=product_id,
        )
        return True

    except (BotoCoreError, ClientError, Exception) as exc:  # noqa: B036
        log_event(
            "WARN",
            "Inventory event skipped; DB update retained",
            request_id=request_id,
            product_id=product_id,
            error_type=type(exc).__name__,
            error=str(exc),
        )
        return False


# ==========================================================
# CREATE PRODUCT
# ==========================================================

def create_product(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    require_admin(event)
    data = validate_create_payload(parse_body(event))
    request_id = get_request_id(context)
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
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    data["category_id"],
                    data["name"],
                    data["description"],
                    data["price"],
                    data["stock_quantity"],
                    data["reorder_threshold"],
                ),
            )
            product_id = cursor.lastrowid

        connection.commit()

        # Business metrics are best-effort after the transaction succeeds.
        publish_inventory_count(connection)
        if data["stock_quantity"] <= data["reorder_threshold"]:
            publish_custom_metric("LowStockEvents")

        log_event(
            "INFO",
            "Product created",
            request_id=request_id,
            product_id=product_id,
        )

        return response(
            201,
            {
                "message": "Product created successfully",
                "product_id": product_id,
            },
        )

    except pymysql.err.IntegrityError as exc:
        if connection:
            connection.rollback()
        log_event(
            "ERROR",
            "Product creation failed",
            request_id=request_id,
            error_type=type(exc).__name__,
            error=str(exc),
        )
        return response(400, {"message": "Invalid product data"})
    finally:
        if connection:
            connection.close()


# ==========================================================
# GET ALL PRODUCTS
# ==========================================================

def get_products(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    connection = None
    request_id = get_request_id(context)

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

        # Keep the dashboard metric, but never make it an AWS network dependency.
        publish_inventory_count(connection)

        log_event(
            "INFO",
            "Products retrieved",
            request_id=request_id,
            count=len(products),
        )
        return response(200, products)

    finally:
        if connection:
            connection.close()


# ==========================================================
# GET ONE PRODUCT
# ==========================================================

def get_product(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    product_id = get_product_id(event)
    if product_id is None:
        return response(400, {"message": "Product id is required"})

    connection = None
    request_id = get_request_id(context)

    try:
        connection = get_db_connection()
        with connection.cursor() as cursor:
            product = fetch_product(cursor, product_id)

        if product is None:
            return response(404, {"message": "Product not found"})

        log_event(
            "INFO",
            "Product retrieved",
            request_id=request_id,
            product_id=product_id,
        )
        return response(200, product)

    finally:
        if connection:
            connection.close()


# ==========================================================
# UPDATE PRODUCT / INVENTORY
# ==========================================================

def update_product(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    require_admin(event)

    product_id = get_product_id(event)
    if product_id is None:
        return response(400, {"message": "Product id is required"})

    update_fields = validate_update_payload(parse_body(event))
    request_id = get_request_id(context)
    connection = None

    log_event(
        "INFO",
        "Starting product update",
        request_id=request_id,
        product_id=product_id,
        fields=list(update_fields.keys()),
    )

    try:
        connection = get_db_connection()

        with connection.cursor() as cursor:
            # Lock the row so concurrent stock changes cannot overwrite each other.
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
                FOR UPDATE
                """,
                (product_id,),
            )
            existing_product = cursor.fetchone()

            if existing_product is None:
                connection.rollback()
                return response(404, {"message": "Product not found"})

            old_stock_quantity = int(existing_product["stock_quantity"])
            old_reorder_threshold = int(existing_product["reorder_threshold"])

            fields = []
            values = []
            for field, value in update_fields.items():
                fields.append(f"{field} = %s")
                values.append(value)

            fields.append("updated_at = CURRENT_TIMESTAMP")
            values.append(product_id)

            cursor.execute(
                f"""
                UPDATE products
                SET {", ".join(fields)}
                WHERE product_id = %s
                  AND deleted_at IS NULL
                """,
                tuple(values),
            )

            updated_product = fetch_product(cursor, product_id)

        connection.commit()

        new_stock_quantity = int(
            update_fields.get("stock_quantity", old_stock_quantity)
        )
        new_reorder_threshold = int(
            update_fields.get("reorder_threshold", old_reorder_threshold)
        )
        stock_changed = new_stock_quantity != old_stock_quantity
        low_stock = new_stock_quantity <= new_reorder_threshold

        # Metrics happen after the DB commit and cannot roll it back.
        publish_inventory_count(connection)
        if stock_changed and low_stock:
            publish_custom_metric("LowStockEvents")

        event_published = False
        if stock_changed:
            event_detail = {
                "product_id": product_id,
                "product_name": existing_product["name"],
                "old_stock": old_stock_quantity,
                "new_stock": new_stock_quantity,
                "low_stock_threshold": new_reorder_threshold,
                "low_stock": low_stock,
            }
            event_published = publish_inventory_event(
                event_detail=event_detail,
                request_id=request_id,
                product_id=product_id,
            )

        log_event(
            "INFO",
            "Product updated",
            request_id=request_id,
            product_id=product_id,
            previous_stock_quantity=old_stock_quantity,
            new_stock_quantity=new_stock_quantity,
            reorder_threshold=new_reorder_threshold,
            low_stock=low_stock,
            event_published=event_published,
        )

        return response(
            200,
            {
                "message": "Product updated successfully",
                "product_id": product_id,
                "previous_stock_quantity": old_stock_quantity,
                "new_stock_quantity": new_stock_quantity,
                "reorder_threshold": new_reorder_threshold,
                "low_stock": low_stock,
                "event_published": event_published,
            },
        )

    except pymysql.err.IntegrityError as exc:
        if connection:
            connection.rollback()
        log_event(
            "ERROR",
            "Product update failed",
            request_id=request_id,
            product_id=product_id,
            error_type=type(exc).__name__,
            error=str(exc),
        )
        return response(400, {"message": "Invalid product update"})

    finally:
        if connection:
            connection.close()


# ==========================================================
# DELETE PRODUCT
# ==========================================================

def delete_product(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    require_admin(event)

    product_id = get_product_id(event)
    if product_id is None:
        return response(400, {"message": "Product id is required"})

    connection = None
    request_id = get_request_id(context)

    try:
        connection = get_db_connection()

        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT product_id
                FROM products
                WHERE product_id = %s
                  AND deleted_at IS NULL
                FOR UPDATE
                """,
                (product_id,),
            )
            product = cursor.fetchone()

            if product is None:
                connection.rollback()
                return response(404, {"message": "Product not found"})

            cursor.execute(
                """
                UPDATE products
                SET
                    deleted_at = CURRENT_TIMESTAMP,
                    status = 'INACTIVE',
                    updated_at = CURRENT_TIMESTAMP
                WHERE product_id = %s
                  AND deleted_at IS NULL
                """,
                (product_id,),
            )

        connection.commit()
        publish_inventory_count(connection)

        log_event(
            "INFO",
            "Product deleted",
            request_id=request_id,
            product_id=product_id,
        )

        return response(
            200,
            {
                "message": "Product deleted successfully",
                "product_id": product_id,
            },
        )

    finally:
        if connection:
            connection.close()


# ==========================================================
# MAIN LAMBDA HANDLER
# ==========================================================

def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    request_id = get_request_id(context)

    request_context = event.get("requestContext") or {}
    http_method = (
        event.get("httpMethod")
        or request_context.get("http", {}).get("method")
        or ""
    ).upper()

    resource = event.get("resource", "")

    log_event(
        "INFO",
        "Product request received",
        request_id=request_id,
        http_method=http_method,
        resource=resource,
    )

    try:
        if http_method == "OPTIONS":
            return response(200, {"message": "OK"})

        if http_method == "POST" and resource == "/products":
            return create_product(event, context)

        if http_method == "GET" and resource == "/products":
            return get_products(event, context)

        if http_method == "GET" and resource == "/products/{id}":
            return get_product(event, context)

        if http_method == "PUT" and resource == "/products/{id}":
            return update_product(event, context)

        if http_method == "DELETE" and resource == "/products/{id}":
            return delete_product(event, context)

        return response(404, {"message": "Unsupported API route"})

    except PermissionError as exc:
        log_event(
            "WARN",
            "Authorization denied",
            request_id=request_id,
            error=str(exc),
        )
        return response(403, {"message": str(exc)})

    except ValueError as exc:
        log_event(
            "WARN",
            "Invalid product request",
            request_id=request_id,
            error=str(exc),
        )
        return response(400, {"message": str(exc)})

    except DependencyError as exc:
        log_event(
            "ERROR",
            "Required dependency unavailable",
            request_id=request_id,
            error_type=type(exc).__name__,
            error=str(exc),
        )
        return response(
            503,
            {
                "message": str(exc),
                "request_id": request_id,
            },
        )

    except pymysql.err.OperationalError as exc:
        log_event(
            "ERROR",
            "Database operational error",
            request_id=request_id,
            error_type=type(exc).__name__,
            error=str(exc),
        )
        return response(
            503,
            {
                "message": "Database is temporarily unavailable",
                "request_id": request_id,
            },
        )

    except pymysql.MySQLError as exc:
        log_event(
            "ERROR",
            "Database operation failed",
            request_id=request_id,
            error_type=type(exc).__name__,
            error=str(exc),
        )
        return response(
            500,
            {
                "message": "Database operation failed",
                "request_id": request_id,
            },
        )

    except Exception as exc:
        log_event(
            "ERROR",
            "Product request failed",
            request_id=request_id,
            error_type=type(exc).__name__,
            error=str(exc),
        )
        return response(
            500,
            {
                "message": "Internal server error",
                "request_id": request_id,
            },
        )
