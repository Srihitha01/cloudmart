import os
import boto3
import pymysql

from flask import Flask, render_template_string

app = Flask(__name__)

AWS_REGION = os.environ.get("AWS_REGION", "ap-south-1")

DB_ENDPOINT_PARAMETER = os.environ.get(
    "DB_ENDPOINT_PARAMETER",
    "/cloudmart/dev/db/endpoint"
)

DB_PORT_PARAMETER = os.environ.get(
    "DB_PORT_PARAMETER",
    "/cloudmart/dev/db/port"
)

DB_NAME_PARAMETER = os.environ.get(
    "DB_NAME_PARAMETER",
    "/cloudmart/dev/db/name"
)

DB_USERNAME_PARAMETER = os.environ.get(
    "DB_USERNAME_PARAMETER",
    "/cloudmart/dev/db/username"
)

DB_PASSWORD_PARAMETER = os.environ.get(
    "DB_PASSWORD_PARAMETER",
    "/cloudmart/dev/db/password"
)

REPORT_BUCKET = os.environ.get(
    "REPORT_BUCKET",
    ""
)

ssm = boto3.client("ssm", region_name=AWS_REGION)
s3 = boto3.client("s3", region_name=AWS_REGION)


def get_parameter(name):
    response = ssm.get_parameter(
        Name=name,
        WithDecryption=True
    )
    return response["Parameter"]["Value"]


def get_db_connection():
    return pymysql.connect(
        host=get_parameter(DB_ENDPOINT_PARAMETER),
        port=int(get_parameter(DB_PORT_PARAMETER)),
        user=get_parameter(DB_USERNAME_PARAMETER),
        password=get_parameter(DB_PASSWORD_PARAMETER),
        database=get_parameter(DB_NAME_PARAMETER),
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=10,
        autocommit=True
    )


def get_dashboard_data():
    connection = get_db_connection()

    try:
        with connection.cursor() as cursor:

            cursor.execute("""
                SELECT
                    p.product_id,
                    p.name,
                    COALESCE(c.name, 'Uncategorized') AS category,
                    p.stock_quantity,
                    p.reorder_threshold,
                    p.price
                FROM products p
                LEFT JOIN categories c
                    ON p.category_id = c.category_id
                WHERE p.deleted_at IS NULL
                ORDER BY p.product_id
            """)
            products = cursor.fetchall()

            cursor.execute("""
                SELECT
                    o.order_id,
                    o.customer_id,
                    o.status,
                    o.order_date,
                    o.total_amount
                FROM orders o
                ORDER BY o.order_date DESC
                LIMIT 50
            """)
            orders = cursor.fetchall()

            cursor.execute("""
                SELECT
                    ol.order_id,
                    ol.previous_status,
                    ol.new_status,
                    ol.changed_by,
                    ol.note,
                    ol.created_at
                FROM order_logs ol
                ORDER BY ol.created_at DESC
                LIMIT 50
            """)
            order_logs = cursor.fetchall()

            cursor.execute("""
                SELECT COUNT(*) AS total
                FROM products
                WHERE deleted_at IS NULL
            """)
            product_count = cursor.fetchone()["total"]

            cursor.execute("""
                SELECT COUNT(*) AS total
                FROM orders
            """)
            order_count = cursor.fetchone()["total"]

            cursor.execute("""
                SELECT COUNT(*) AS total
                FROM products
                WHERE deleted_at IS NULL
                  AND stock_quantity <= reorder_threshold
            """)
            low_stock_count = cursor.fetchone()["total"]

            cursor.execute("""
                SELECT COUNT(*) AS total
                FROM orders
                WHERE status = 'FAILED'
            """)
            failed_order_count = cursor.fetchone()["total"]

        return {
            "products": products,
            "orders": orders,
            "order_logs": order_logs,
            "product_count": product_count,
            "order_count": order_count,
            "low_stock_count": low_stock_count,
            "failed_order_count": failed_order_count
        }

    finally:
        connection.close()


def get_reports():
    if not REPORT_BUCKET:
        return []

    try:
        response = s3.list_objects_v2(
            Bucket=REPORT_BUCKET,
            Prefix="daily-reports/"
        )

        reports = []

        for obj in response.get("Contents", []):
            key = obj["Key"]

            if not key.endswith(".csv"):
                continue

            reports.append({
                "key": key,
                "size": obj["Size"],
                "last_modified": obj["LastModified"],
                "url": s3.generate_presigned_url(
                    "get_object",
                    Params={
                        "Bucket": REPORT_BUCKET,
                        "Key": key
                    },
                    ExpiresIn=3600
                )
            })

        reports.sort(
            key=lambda x: x["last_modified"],
            reverse=True
        )

        return reports[:10]

    except Exception:
        return []


@app.route("/")
def dashboard():

    try:
        data = get_dashboard_data()
        error = None
    except Exception as exc:
        data = {
            "products": [],
            "orders": [],
            "order_logs": [],
            "product_count": 0,
            "order_count": 0,
            "low_stock_count": 0,
            "failed_order_count": 0
        }
        error = str(exc)

    reports = get_reports()

    return render_template_string(
        HTML,
        data=data,
        reports=reports,
        error=error,
        report_bucket=REPORT_BUCKET
    )


@app.route("/health")
def health():
    return {
        "status": "healthy",
        "service": "CloudMart Operations Dashboard"
    }


HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>CloudMart Operations Dashboard</title>

    <meta name="viewport"
          content="width=device-width, initial-scale=1">

    <style>
        body {
            font-family: Arial, sans-serif;
            margin: 0;
            background: #f4f6f8;
            color: #222;
        }

        header {
            background: #172033;
            color: white;
            padding: 24px 35px;
        }

        header h1 {
            margin: 0;
        }

        header p {
            margin-bottom: 0;
            opacity: 0.8;
        }

        .container {
            padding: 30px;
        }

        .cards {
            display: grid;
            grid-template-columns:
                repeat(auto-fit, minmax(180px, 1fr));
            gap: 18px;
            margin-bottom: 30px;
        }

        .card {
            background: white;
            border-radius: 10px;
            padding: 20px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.08);
        }

        .card h3 {
            margin-top: 0;
            font-size: 14px;
            color: #666;
        }

        .number {
            font-size: 32px;
            font-weight: bold;
        }

        .section {
            background: white;
            border-radius: 10px;
            padding: 20px;
            margin-bottom: 25px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.08);
            overflow-x: auto;
        }

        table {
            width: 100%;
            border-collapse: collapse;
        }

        th, td {
            padding: 11px;
            border-bottom: 1px solid #ddd;
            text-align: left;
            white-space: nowrap;
        }

        th {
            background: #f1f3f5;
        }

        .low {
            font-weight: bold;
        }

        .failed {
            font-weight: bold;
        }

        .error {
            background: #fff0f0;
            border: 1px solid #ffcccc;
            padding: 15px;
            margin-bottom: 25px;
            border-radius: 8px;
        }

        a {
            color: #1565c0;
            text-decoration: none;
        }

        a:hover {
            text-decoration: underline;
        }

        .refresh {
            float: right;
            color: white;
            background: #2d6cdf;
            padding: 10px 15px;
            border-radius: 6px;
        }
    </style>
</head>

<body>

<header>
    <a class="refresh" href="/">Refresh</a>

    <h1>CloudMart Operations Dashboard</h1>

    <p>
        Live inventory, orders, order history and daily reports
    </p>
</header>

<div class="container">

{% if error %}
<div class="error">
    <strong>Database error:</strong>
    {{ error }}
</div>
{% endif %}

<div class="cards">

    <div class="card">
        <h3>Total Products</h3>
        <div class="number">
            {{ data.product_count }}
        </div>
    </div>

    <div class="card">
        <h3>Total Orders</h3>
        <div class="number">
            {{ data.order_count }}
        </div>
    </div>

    <div class="card">
        <h3>Low Stock Products</h3>
        <div class="number">
            {{ data.low_stock_count }}
        </div>
    </div>

    <div class="card">
        <h3>Failed Orders</h3>
        <div class="number">
            {{ data.failed_order_count }}
        </div>
    </div>

</div>


<div class="section">

<h2>Current Inventory</h2>

<table>

<tr>
    <th>ID</th>
    <th>Product</th>
    <th>Category</th>
    <th>Price</th>
    <th>Stock</th>
    <th>Reorder Threshold</th>
</tr>

{% for product in data.products %}

<tr>
    <td>{{ product.product_id }}</td>
    <td>{{ product.name }}</td>
    <td>{{ product.category }}</td>
    <td>{{ product.price }}</td>

    <td class="{% if product.stock_quantity <= product.reorder_threshold %}low{% endif %}">
        {{ product.stock_quantity }}
    </td>

    <td>{{ product.reorder_threshold }}</td>
</tr>

{% endfor %}

</table>

</div>


<div class="section">

<h2>Recent Orders</h2>

<table>

<tr>
    <th>Order ID</th>
    <th>Customer ID</th>
    <th>Status</th>
    <th>Order Date</th>
    <th>Total Amount</th>
</tr>

{% for order in data.orders %}

<tr>
    <td>{{ order.order_id }}</td>
    <td>{{ order.customer_id }}</td>
    <td>{{ order.status }}</td>
    <td>{{ order.order_date }}</td>
    <td>{{ order.total_amount }}</td>
</tr>

{% endfor %}

</table>

</div>


<div class="section">

<h2>Order History</h2>

<table>

<tr>
    <th>Order ID</th>
    <th>Previous Status</th>
    <th>New Status</th>
    <th>Changed By</th>
    <th>Note</th>
    <th>Timestamp</th>
</tr>

{% for log in data.order_logs %}

<tr>
    <td>{{ log.order_id }}</td>
    <td>{{ log.previous_status or '-' }}</td>
    <td>{{ log.new_status }}</td>
    <td>{{ log.changed_by or '-' }}</td>
    <td>{{ log.note or '-' }}</td>
    <td>{{ log.created_at }}</td>
</tr>

{% endfor %}

</table>

</div>


<div class="section">

<h2>Daily Reports</h2>

{% if reports %}

<table>

<tr>
    <th>Report</th>
    <th>Size</th>
    <th>Generated</th>
    <th>Open</th>
</tr>

{% for report in reports %}

<tr>
    <td>{{ report.key }}</td>
    <td>{{ report.size }} bytes</td>
    <td>{{ report.last_modified }}</td>
    <td>
        <a href="{{ report.url }}" target="_blank">
            View CSV
        </a>
    </td>
</tr>

{% endfor %}

</table>

{% else %}

<p>No generated reports found.</p>

{% endif %}

</div>

</div>

</body>
</html>
"""


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=80)
