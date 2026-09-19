import os
import csv
import io
import boto3
import pymysql
from datetime import datetime, timezone

ssm = boto3.client("ssm")
s3 = boto3.client("s3")

DB_NAME_PARAMETER = os.environ["DB_NAME_PARAMETER"]
DB_ENDPOINT_PARAMETER = os.environ["DB_ENDPOINT_PARAMETER"]
DB_PORT_PARAMETER = os.environ["DB_PORT_PARAMETER"]
DB_USERNAME_PARAMETER = os.environ["DB_USERNAME_PARAMETER"]
DB_PASSWORD_PARAMETER = os.environ["DB_PASSWORD_PARAMETER"]
REPORT_BUCKET = os.environ["REPORT_BUCKET"]

def get_parameter(name):
    return ssm.get_parameter(
        Name=name,
        WithDecryption=True
    )["Parameter"]["Value"]

def get_connection():
    return pymysql.connect(
        host=get_parameter(DB_ENDPOINT_PARAMETER),
        port=int(get_parameter(DB_PORT_PARAMETER)),
        user=get_parameter(DB_USERNAME_PARAMETER),
        password=get_parameter(DB_PASSWORD_PARAMETER),
        database=get_parameter(DB_NAME_PARAMETER),
        connect_timeout=10,
        read_timeout=30,
        write_timeout=30,
        cursorclass=pymysql.cursors.DictCursor
    )

def lambda_handler(event, context):
    connection = get_connection()

    try:
        with connection.cursor() as cursor:

            cursor.execute("""
                SELECT
                    product_id,
                    name,
                    price,
                    stock_quantity,
                    status
                FROM products
                ORDER BY product_id
            """)
            products = cursor.fetchall()

            cursor.execute("""
                SELECT
                    o.order_id,
                    o.customer_id,
                    o.status,
                    o.total_amount,
                    o.created_at
                FROM orders o
                ORDER BY o.created_at DESC
                LIMIT 50
            """)
            orders = cursor.fetchall()

        generated_at = datetime.now(timezone.utc)
        date_string = generated_at.strftime("%Y-%m-%d")
        key = f"daily-reports/cloudmart-daily-report-{date_string}.csv"

        output = io.StringIO()
        writer = csv.writer(output)

        writer.writerow(["CloudMart Daily Report"])
        writer.writerow(["Generated At", generated_at.isoformat()])
        writer.writerow([])

        writer.writerow([
            "PRODUCT INVENTORY",
        ])
        writer.writerow([
            "product_id",
            "name",
            "price",
            "stock_quantity",
            "status"
        ])

        for product in products:
            writer.writerow([
                product["product_id"],
                product["name"],
                product["price"],
                product["stock_quantity"],
                product["status"]
            ])

        writer.writerow([])
        writer.writerow(["RECENT ORDERS"])
        writer.writerow([
            "order_id",
            "customer_id",
            "status",
            "total_amount",
            "created_at"
        ])

        for order in orders:
            writer.writerow([
                order["order_id"],
                order["customer_id"],
                order["status"],
                order["total_amount"],
                order["created_at"]
            ])

        s3.put_object(
            Bucket=REPORT_BUCKET,
            Key=key,
            Body=output.getvalue().encode("utf-8"),
            ContentType="text/csv"
        )

        result = {
            "statusCode": 200,
            "message": "Daily CloudMart report generated",
            "bucket": REPORT_BUCKET,
            "key": key,
            "products": len(products),
            "orders": len(orders)
        }

        print(result)
        return result

    finally:
        connection.close()
