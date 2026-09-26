
import os
import json
import hashlib
import logging
import time
import boto3
import pymysql


# ==========================================================
# LOGGING
# ==========================================================

logger = logging.getLogger()
logger.setLevel(logging.INFO)


# ==========================================================
# ENVIRONMENT VARIABLES
# ==========================================================

DB_ENDPOINT_PARAMETER = os.environ["DB_ENDPOINT_PARAMETER"]
DB_PORT_PARAMETER = os.environ["DB_PORT_PARAMETER"]
DB_NAME_PARAMETER = os.environ["DB_NAME_PARAMETER"]
DB_USERNAME_PARAMETER = os.environ["DB_USERNAME_PARAMETER"]
DB_PASSWORD_PARAMETER = os.environ["DB_PASSWORD_PARAMETER"]

ssm = boto3.client("ssm")

_PARAMETER_CACHE = {}
_PARAMETER_CACHE_AT = 0.0
_PARAMETER_CACHE_TTL_SECONDS = 300


# ==========================================================
# RESPONSE HELPERS
# ==========================================================

def response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json"
        },
        "body": json.dumps(body, default=str)
    }


def success(status_code, message, data=None):
    body = {
        "success": True,
        "message": message
    }

    if data is not None:
        body["data"] = data

    return response(status_code, body)


def error(status_code, message):
    return response(
        status_code,
        {
            "success": False,
            "message": message
        }
    )


# ==========================================================
# SSM AND DATABASE
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


def get_connection():
    db = load_database_parameters()
    return pymysql.connect(
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

def get_authorizer_context(event):
    request_context = event.get("requestContext", {})
    authorizer = request_context.get("authorizer", {})

    return {
        key: value
        for key, value in authorizer.items()
        if value is not None
    }


def get_role(event):
    context = get_authorizer_context(event)

    return str(
        context.get("role", "")
    ).upper()


def get_authenticated_customer_id(event):
    context = get_authorizer_context(event)

    customer_id = context.get("customer_id")

    if customer_id is None:
        return None

    try:
        return int(customer_id)
    except (TypeError, ValueError):
        return None


def is_admin(event):
    return get_role(event) == "ADMIN"


# ==========================================================
# REQUEST HELPERS
# ==========================================================

def get_http_method(event):
    return event.get(
        "httpMethod",
        event.get("requestContext", {})
        .get("http", {})
        .get("method", "")
    ).upper()


def get_resource(event):
    return event.get(
        "resource",
        event.get("path", "")
    )


def get_path_customer_id(event):
    path_parameters = event.get("pathParameters") or {}

    value = path_parameters.get("customer_id")

    if value is None:
        return None

    try:
        customer_id = int(value)

        if customer_id <= 0:
            return None

        return customer_id

    except (TypeError, ValueError):
        return None


def parse_body(event):
    body = event.get("body")

    if not body:
        return {}

    if isinstance(body, dict):
        return body

    try:
        parsed = json.loads(body)

        if not isinstance(parsed, dict):
            return None

        return parsed

    except (json.JSONDecodeError, TypeError):
        return None


# ==========================================================
# TOKEN HELPERS
# ==========================================================

def hash_bearer_token(token):
    return hashlib.sha256(
        token.encode("utf-8")
    ).hexdigest()


# ==========================================================
# VALIDATION
# ==========================================================

def validate_customer_payload(
    data,
    partial=False,
    allow_bearer_token=False
):
    if not isinstance(data, dict):
        return "Request body must be a JSON object"

    allowed_fields = {
        "name",
        "email",
        "address"
    }

    if allow_bearer_token:
        allowed_fields.add("bearer_token")

    unknown_fields = set(data.keys()) - allowed_fields

    if unknown_fields:
        return "Unsupported fields: " + ", ".join(
            sorted(unknown_fields)
        )

    if not partial:
        required_fields = ["name", "email"]

        if allow_bearer_token:
            required_fields.append("bearer_token")

        for field in required_fields:
            if field not in data:
                return f"{field} is required"

            if not isinstance(data[field], str):
                return f"{field} must be a string"

            if not data[field].strip():
                return f"{field} cannot be empty"

    if "name" in data:
        if not isinstance(data["name"], str):
            return "name must be a string"

        if not data["name"].strip():
            return "name cannot be empty"

        if len(data["name"].strip()) > 150:
            return "name is too long"

    if "email" in data:
        if not isinstance(data["email"], str):
            return "email must be a string"

        email = data["email"].strip().lower()

        if "@" not in email or "." not in email:
            return "Invalid email format"

        if len(email) > 255:
            return "email is too long"

    if "address" in data:
        if data["address"] is not None:
            if not isinstance(data["address"], str):
                return "address must be a string"

            if len(data["address"]) > 500:
                return "address is too long"

    if "bearer_token" in data:
        if not allow_bearer_token:
            return "bearer_token cannot be updated"

        if not isinstance(data["bearer_token"], str):
            return "bearer_token must be a string"

        if not data["bearer_token"].strip():
            return "bearer_token cannot be empty"

        if len(data["bearer_token"].strip()) > 500:
            return "bearer_token is too long"

    return None


# ==========================================================
# CUSTOMER ACCESS VALIDATION
# ==========================================================


def can_access_customer(event, customer_id):
    """
    ADMIN: any customer.
    CUSTOMER: the bearer-token hash in authorizer context must match
    the requested customer_id. This allows one token to be shared by
    multiple customers while keeping URL-level ownership isolation.
    """
    if is_admin(event):
        return True

    authorizer = get_authorizer_context(event)
    token_hash = authorizer.get("token_hash")

    if not isinstance(token_hash, str):
        return False

    token_hash = token_hash.strip().lower()
    if len(token_hash) != 64:
        return False

    connection = None
    try:
        connection = get_connection()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT customer_id
                FROM customers
                WHERE customer_id = %s
                  AND bearer_token = %s
                  AND status = 'ACTIVE'
                  AND deleted_at IS NULL
                """,
                (customer_id, token_hash),
            )
            return cursor.fetchone() is not None
    finally:
        if connection is not None:
            connection.close()

def create_customer(event):
    data = parse_body(event)

    if data is None:
        return error(400, "Invalid JSON request body")

    validation_error = validate_customer_payload(
        data,
        allow_bearer_token=True
    )

    if validation_error:
        return error(400, validation_error)

    name = data["name"].strip()
    email = data["email"].strip().lower()
    address = data.get("address")
    raw_token = data["bearer_token"].strip()
    token_hash = hash_bearer_token(raw_token)

    connection = None

    try:
        connection = get_connection()

        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO customers
                (
                    name,
                    email,
                    bearer_token,
                    address,
                    status
                )
                VALUES
                (
                    %s,
                    %s,
                    %s,
                    %s,
                    'ACTIVE'
                )
                """,
                (
                    name,
                    email,
                    token_hash,
                    address
                )
            )

            customer_id = cursor.lastrowid

        connection.commit()

        logger.info(
            json.dumps(
                {
                    "event": "CUSTOMER_CREATED",
                    "customer_id": customer_id
                }
            )
        )

        return success(
            201,
            "Customer created successfully",
            {
                "customer_id": customer_id,
                "name": name,
                "email": email,
                "address": address,
                "status": "ACTIVE"
            }
        )

    except pymysql.err.IntegrityError:
        if connection:
            connection.rollback()

        return error(
            409,
            "A customer with this email already exists"
        )

    except Exception:
        if connection:
            connection.rollback()

        logger.exception("Customer creation failed")

        return error(
            500,
            "Customer creation failed"
        )

    finally:
        if connection:
            connection.close()



# ==========================================================
# GET ALL CUSTOMERS
# ADMIN ONLY
# ==========================================================

def get_customers(event):
    if not is_admin(event):
        return error(
            403,
            "Only administrators can access all customers"
        )

    connection = None

    try:
        connection = get_connection()

        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    customer_id,
                    name,
                    email,
                    address,
                    status,
                    created_at,
                    updated_at
                FROM customers
                WHERE deleted_at IS NULL
                ORDER BY customer_id ASC
                """
            )

            customers = cursor.fetchall()

        return success(
            200,
            "Customers retrieved successfully",
            {
                "count": len(customers),
                "customers": customers
            }
        )

    except Exception:
        logger.exception("All customers retrieval failed")

        return error(
            500,
            "Customers retrieval failed"
        )

    finally:
        if connection:
            connection.close()


# ==========================================================
# GET CUSTOMER
# ==========================================================

def get_customer(event):
    customer_id = get_path_customer_id(event)

    if customer_id is None:
        return error(400, "Valid customer_id is required")

    if not can_access_customer(event, customer_id):
        return error(
            403,
            "You are not allowed to access this customer"
        )

    connection = None

    try:
        connection = get_connection()

        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    customer_id,
                    name,
                    email,
                    address,
                    status,
                    created_at,
                    updated_at
                FROM customers
                WHERE customer_id = %s
                  AND deleted_at IS NULL
                LIMIT 1
                """,
                (customer_id,)
            )

            customer = cursor.fetchone()

        if customer is None:
            return error(404, "Customer not found")

        return success(
            200,
            "Customer retrieved successfully",
            customer
        )

    except Exception:
        logger.exception("Customer retrieval failed")

        return error(
            500,
            "Customer retrieval failed"
        )

    finally:
        if connection:
            connection.close()


# ==========================================================
# UPDATE CUSTOMER
# ==========================================================

def update_customer(event):
    customer_id = get_path_customer_id(event)

    if customer_id is None:
        return error(400, "Valid customer_id is required")

    if not can_access_customer(event, customer_id):
        return error(
            403,
            "You are not allowed to update this customer"
        )

    data = parse_body(event)

    if data is None:
        return error(400, "Invalid JSON request body")

    validation_error = validate_customer_payload(
        data,
        partial=True,
        allow_bearer_token=False
    )

    if validation_error:
        return error(400, validation_error)

    if not data:
        return error(
            400,
            "At least one field is required for update"
        )

    allowed_fields = {
        "name",
        "email",
        "address"
    }

    updates = []
    values = []

    for field in allowed_fields:
        if field in data:
            value = data[field]

            if field == "name":
                value = value.strip()

            if field == "email":
                value = value.strip().lower()

            updates.append(f"{field} = %s")
            values.append(value)

    if not updates:
        return error(
            400,
            "Only name, email, or address can be updated"
        )

    values.append(customer_id)

    connection = None

    try:
        connection = get_connection()

        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT customer_id
                FROM customers
                WHERE customer_id = %s
                  AND deleted_at IS NULL
                LIMIT 1
                """,
                (customer_id,)
            )

            existing = cursor.fetchone()

            if existing is None:
                return error(404, "Customer not found")

            query = f"""
                UPDATE customers
                SET {", ".join(updates)}
                WHERE customer_id = %s
                  AND deleted_at IS NULL
            """

            cursor.execute(query, values)

        connection.commit()

        logger.info(
            json.dumps(
                {
                    "event": "CUSTOMER_UPDATED",
                    "customer_id": customer_id
                }
            )
        )

        return success(
            200,
            "Customer updated successfully",
            {
                "customer_id": customer_id
            }
        )

    except pymysql.err.IntegrityError:
        if connection:
            connection.rollback()

        return error(
            409,
            "A customer with this email already exists"
        )

    except Exception:
        if connection:
            connection.rollback()

        logger.exception("Customer update failed")

        return error(
            500,
            "Customer update failed"
        )

    finally:
        if connection:
            connection.close()


# ==========================================================
# SOFT DELETE CUSTOMER
# ==========================================================

def delete_customer(event):
    customer_id = get_path_customer_id(event)

    if customer_id is None:
        return error(400, "Valid customer_id is required")

    # Customer deletion is ADMIN ONLY.
    # A customer may read/update their own record, but must never
    # be able to deactivate/delete an account through this endpoint.
    if not is_admin(event):
        return error(
            403,
            "Only administrators can delete customers"
        )

    connection = None

    try:
        connection = get_connection()

        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT customer_id
                FROM customers
                WHERE customer_id = %s
                  AND deleted_at IS NULL
                LIMIT 1
                """,
                (customer_id,)
            )

            existing = cursor.fetchone()

            if existing is None:
                return error(404, "Customer not found")

            cursor.execute(
                """
                UPDATE customers
                SET
                    deleted_at = NOW(),
                    deleted_by = %s,
                    delete_reason = %s,
                    status = 'INACTIVE'
                WHERE customer_id = %s
                  AND deleted_at IS NULL
                """,
                (
                    str(
                        get_authenticated_customer_id(event)
                        or "ADMIN"
                    ),
                    "Customer soft deleted",
                    customer_id
                )
            )

        connection.commit()

        logger.info(
            json.dumps(
                {
                    "event": "CUSTOMER_SOFT_DELETED",
                    "customer_id": customer_id
                }
            )
        )

        return success(
            200,
            "Customer deleted successfully",
            {
                "customer_id": customer_id,
                "status": "INACTIVE"
            }
        )

    except Exception:
        if connection:
            connection.rollback()

        logger.exception("Customer deletion failed")

        return error(
            500,
            "Customer deletion failed"
        )

    finally:
        if connection:
            connection.close()


# ==========================================================
# LAMBDA HANDLER
# ==========================================================

def lambda_handler(event, context):
    request_id = getattr(
        context,
        "aws_request_id",
        "unknown"
    )

    method = get_http_method(event)
    resource = get_resource(event)

    logger.info(
        json.dumps(
            {
                "event": "CUSTOMER_REQUEST",
                "request_id": request_id,
                "method": method,
                "resource": resource
            }
        )
    )

    try:
        if method == "POST" and resource == "/customers":
            return create_customer(event)

        if method == "GET" and resource == "/customers":
            return get_customers(event)

        if method == "GET" and resource == "/customers/{customer_id}":
            return get_customer(event)

        if method in ("PUT", "PATCH") and resource == "/customers/{customer_id}":
            return update_customer(event)

        if method == "DELETE" and resource == "/customers/{customer_id}":
            return delete_customer(event)

        return error(
            404,
            "Customer route not found"
        )

    except Exception:
        logger.exception(
            "Unhandled customer Lambda error"
        )

        return error(
            500,
            "Internal server error"
        )