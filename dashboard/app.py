import os
import boto3
import pymysql

from flask import Flask, render_template_string, request, redirect, url_for, session

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "change-this-secret-key")

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

AUTH_TOKEN_PARAMETER = os.environ.get(
    "AUTH_TOKEN_PARAMETER",
    "/cloudmart/dev/auth/token"
)

def get_admin_token():
    return get_parameter(AUTH_TOKEN_PARAMETER)

def login_required():
    return session.get("authenticated") is True


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



LOGIN_HTML = """
<!doctype html>
<html>
<head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>CloudMart Admin Login</title>
<style>
body{margin:0;font-family:Inter,Arial;background:#eef2f7;display:grid;place-items:center;min-height:100vh}
.box{background:white;padding:34px;border-radius:18px;width:min(390px,90%);box-shadow:0 12px 35px #17203320}
h1{color:#172033;margin-top:0}input,button{width:100%;box-sizing:border-box;padding:13px;margin-top:12px;border-radius:9px;border:1px solid #ccd3df}
button{background:#2563eb;color:white;border:0;font-weight:bold;cursor:pointer}.error{color:#b91c1c;margin-top:12px}
</style>
</head>
<body><div class="box"><h1>CloudMart Admin</h1><p>Enter your bearer token to continue.</p>
<form method="post"><input name="token" type="password" placeholder="Bearer token" required>
<button type="submit">Sign in</button></form>
{% if error %}<div class="error">{{ error }}</div>{% endif %}
</div></body></html>
"""

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        supplied = request.form.get("token", "").strip()
        try:
            expected = get_admin_token().strip()
            if supplied.startswith("Bearer "):
                supplied = supplied[7:].strip()
            if expected.startswith("Bearer "):
                expected = expected[7:].strip()
            if supplied and supplied == expected:
                session["authenticated"] = True
                return redirect(url_for("dashboard"))
        except Exception:
            pass
        return render_template_string(LOGIN_HTML, error="Invalid token or authentication configuration.")
    return render_template_string(LOGIN_HTML, error=None)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

def require_login():
    if not login_required():
        return redirect(url_for("login"))
    return None

@app.route("/products")
def products_page():
    guard = require_login()
    if guard: return guard
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT p.product_id,p.name,COALESCE(c.name,'Uncategorized') category,
                                  p.price,p.stock_quantity,p.reorder_threshold
                           FROM products p LEFT JOIN categories c ON p.category_id=c.category_id
                           WHERE p.deleted_at IS NULL ORDER BY p.product_id""")
            rows = cur.fetchall()
        return render_template_string(TABLE_HTML, title="Products", columns=["ID","Name","Category","Price","Stock","Threshold"],
                                      rows=[[r.get("product_id"),r.get("name"),r.get("category"),r.get("price"),
                                             r.get("stock_quantity"),r.get("reorder_threshold")] for r in rows])
    finally: conn.close()

@app.route("/customers")
def customers_page():
    guard = require_login()
    if guard: return guard
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM customers ORDER BY customer_id DESC LIMIT 100")
            rows = cur.fetchall()
        columns = list(rows[0].keys()) if rows else ["Message"]
        values = [[r.get(c) for c in columns] for r in rows] if rows else [["No customers found"]]
        return render_template_string(TABLE_HTML, title="Customers", columns=columns, rows=values)
    finally: conn.close()

@app.route("/orders/<int:order_id>")
def order_detail(order_id):
    guard = require_login()
    if guard: return guard
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM orders WHERE order_id=%s", (order_id,))
            order = cur.fetchone()
            cur.execute("SELECT * FROM order_logs WHERE order_id=%s ORDER BY created_at DESC", (order_id,))
            logs = cur.fetchall()
        return render_template_string(DETAIL_HTML, order=order, logs=logs)
    finally: conn.close()

@app.route("/search")
def search():
    guard = require_login()
    if guard: return guard
    q = request.args.get("q", "").strip()
    if not q: return redirect(url_for("dashboard"))
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            like = "%" + q + "%"
            cur.execute("""SELECT product_id AS id,name AS value,'Product' AS type
                           FROM products WHERE deleted_at IS NULL AND (name LIKE %s OR CAST(product_id AS CHAR) LIKE %s)
                           UNION ALL
                           SELECT order_id AS id,CAST(order_id AS CHAR) AS value,'Order' AS type
                           FROM orders WHERE CAST(order_id AS CHAR) LIKE %s
                           LIMIT 50""", (like,like,like))
            results = cur.fetchall()
        return render_template_string(SEARCH_HTML, q=q, results=results)
    finally: conn.close()

TABLE_HTML = """
<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{ title }}</title><style>
body{font-family:Arial;margin:0;background:#f4f6fa;color:#172033}.top{background:#172033;color:white;padding:20px 5%}
main{padding:25px 5%}.panel{background:white;padding:20px;border-radius:14px;overflow:auto}
table{width:100%;border-collapse:collapse}th,td{padding:12px;border-bottom:1px solid #e5e7eb;text-align:left;white-space:nowrap}
th{background:#eef2f7}a{color:#2563eb;text-decoration:none}.back{color:white;float:right}
</style></head><body><div class="top"><a class="back" href="/">Dashboard</a><h1>{{ title }}</h1></div>
<main><div class="panel"><table><tr>{% for c in columns %}<th>{{ c }}</th>{% endfor %}</tr>
{% for row in rows %}<tr>{% for v in row %}<td>{{ v if v is not none else '-' }}</td>{% endfor %}</tr>{% endfor %}
</table></div></main></body></html>
"""

DETAIL_HTML = """
<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Order Details</title><style>body{font-family:Arial;background:#f4f6fa;padding:25px}.box{background:white;padding:22px;border-radius:14px;margin-bottom:20px}dt{font-weight:bold;margin-top:10px}dd{margin:3px 0}</style></head>
<body><a href="/">← Dashboard</a><h1>Order Details</h1>
<div class="box">{% if order %}<dl>{% for k,v in order.items() %}<dt>{{k}}</dt><dd>{{v}}</dd>{% endfor %}</dl>{% else %}Order not found{% endif %}</div>
<div class="box"><h2>Order History</h2>{% for log in logs %}<p>{{log.created_at}} — {{log.previous_status}} → {{log.new_status}} — {{log.note or ''}}</p>{% else %}<p>No history found.</p>{% endfor %}</div>
</body></html>
"""

SEARCH_HTML = """
<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Search</title><style>body{font-family:Arial;padding:25px;background:#f4f6fa}.item{background:white;padding:15px;margin:10px 0;border-radius:10px}</style></head>
<body><a href="/">← Dashboard</a><h1>Search results for "{{q}}"</h1>
{% for r in results %}<div class="item"><b>{{r.type}}</b>: {{r.value}} (ID: {{r.id}})
{% if r.type == 'Order' %} — <a href="/orders/{{r.id}}">Open order</a>{% endif %}</div>
{% else %}<p>No results found.</p>{% endfor %}</body></html>
"""

@app.route("/")
def dashboard():
    guard = require_login()
    if guard:
        return guard

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

    <p>Live inventory, orders, order history and daily reports</p>
    <p>
      <a href="/products" style="color:white;margin-right:15px">Products</a>
      <a href="/customers" style="color:white;margin-right:15px">Customers</a>
      <a href="/logout" style="color:white">Logout</a>
    </p>
    <form action="/search" method="get">
      <input name="q" placeholder="Search product or order ID" style="padding:10px;border-radius:6px;border:0">
      <button style="padding:10px;border:0;border-radius:6px">Search</button>
    </form>
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
    <td><a href="/orders/{{ order.order_id }}">{{ order.order_id }}</a></td>
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
