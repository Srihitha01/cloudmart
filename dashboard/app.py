import os
import hmac
import logging
import boto3
import pymysql
from datetime import datetime
from flask import Flask, Response, render_template_string, request, redirect, url_for, session

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("cloudmart-dashboard")

def load_or_create_flask_secret():
    """Load a persistent Flask session secret, creating it once when needed."""
    configured = os.getenv("FLASK_SECRET_KEY", "").strip()
    if configured:
        return configured

    secret_file = "/etc/cloudmart/dashboard.env"
    try:
        os.makedirs(os.path.dirname(secret_file), mode=0o755, exist_ok=True)

        if os.path.isfile(secret_file):
            with open(secret_file, "r", encoding="utf-8") as handle:
                for line in handle:
                    if line.startswith("FLASK_SECRET_KEY="):
                        saved = line.split("=", 1)[1].strip()
                        if saved:
                            return saved

        import secrets
        generated = secrets.token_hex(32)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        fd = os.open(secret_file, flags, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(f"FLASK_SECRET_KEY={generated}\n")
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            raise
        return generated
    except FileExistsError:
        # Another process created the file first; read its persistent value.
        with open(secret_file, "r", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("FLASK_SECRET_KEY="):
                    saved = line.split("=", 1)[1].strip()
                    if saved:
                        return saved
        raise RuntimeError("Persistent Flask secret exists but is empty.")
    except Exception:
        logger.exception("Unable to load or create persistent Flask session secret")
        raise RuntimeError("FLASK_SECRET_KEY is not configured and could not be persisted.")


app = Flask(__name__)
app.secret_key = load_or_create_flask_secret()
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=False,
    SESSION_COOKIE_PATH="/",
)
REGION = os.getenv("AWS_REGION", "ap-south-1")
ENV = os.getenv("ENVIRONMENT", "dev")
PARAM = {
    "endpoint": os.getenv("DB_ENDPOINT_PARAMETER", f"/cloudmart/{ENV}/db/endpoint"),
    "port": os.getenv("DB_PORT_PARAMETER", f"/cloudmart/{ENV}/db/port"),
    "name": os.getenv("DB_NAME_PARAMETER", f"/cloudmart/{ENV}/db/name"),
    "user": os.getenv("DB_USERNAME_PARAMETER", f"/cloudmart/{ENV}/db/username"),
    "password": os.getenv("DB_PASSWORD_PARAMETER", f"/cloudmart/{ENV}/db/password"),
    "admin": os.getenv("AUTH_TOKEN_PARAMETER", f"/cloudmart/{ENV}/auth/token"),
    "reports": os.getenv("REPORT_BUCKET_PARAMETER", f"/cloudmart/{ENV}/s3/report-bucket"),
}
ssm = boto3.client("ssm", region_name=REGION)
s3 = boto3.client("s3", region_name=REGION)


def parameter(name, required=True):
    try:
        return ssm.get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]
    except Exception:
        logger.exception("SSM parameter lookup failed for %s", name)
        if required:
            raise
        return ""


def report_bucket():
    return os.getenv("REPORT_BUCKET", "").strip() or parameter(PARAM["reports"], False).strip()


def db():
    return pymysql.connect(
        host=parameter(PARAM["endpoint"]),
        port=int(parameter(PARAM["port"])),
        user=parameter(PARAM["user"]),
        password=parameter(PARAM["password"]),
        database=parameter(PARAM["name"]),
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=10,
        read_timeout=20,
        autocommit=True,
    )


def all_rows(sql, args=()):
    con = db()
    try:
        with con.cursor() as cur:
            cur.execute(sql, args)
            return cur.fetchall()
    finally:
        con.close()


def one(sql, args=()):
    rows = all_rows(sql, args)
    return rows[0] if rows else None


def scalar(sql, args=()):
    row = one(sql, args)
    return next(iter(row.values())) if row else 0


def logged_in():
    return session.get("authenticated") is True


def guard():
    return None if logged_in() else redirect(url_for("login", next=request.path))


def token_value(value):
    value = (value or "").strip()
    return value[7:].strip() if value.lower().startswith("bearer ") else value


def shown(value):
    if value is None or value == "":
        return "—"
    if isinstance(value, datetime):
        return value.strftime("%d %b %Y, %I:%M %p")
    return value


def rupees(value):
    return "—" if value is None else f"₹{float(value):,.2f}"


def reports():
    bucket = report_bucket()
    if not bucket:
        return [], ""
    try:
        result = s3.list_objects_v2(Bucket=bucket, Prefix="daily-reports/")
        rows = []
        for obj in result.get("Contents", []):
            if not obj["Key"].lower().endswith(".csv"):
                continue
            rows.append({
                "key": obj["Key"],
                "size": obj["Size"],
                "last_modified": obj["LastModified"],
                "url": s3.generate_presigned_url(
                    "get_object",
                    Params={"Bucket": bucket, "Key": obj["Key"]},
                    ExpiresIn=3600,
                ),
            })
        rows.sort(key=lambda x: x["last_modified"], reverse=True)
        return rows[:20], bucket
    except Exception:
        return [], bucket


def context(**extra):
    extra["reports"], extra["report_bucket"] = reports()
    extra["environment"] = ENV
    extra["aws_region"] = REGION
    extra["cloudwatch_url"] = os.getenv("CLOUDWATCH_DASHBOARD_URL", "").strip()
    extra["shown"] = shown
    extra["rupees"] = rupees
    extra.setdefault("summary", {
        "products": scalar("SELECT COUNT(*) value FROM products WHERE deleted_at IS NULL"),
        "customers": scalar("SELECT COUNT(*) value FROM customers WHERE deleted_at IS NULL"),
        "orders": scalar("SELECT COUNT(*) value FROM orders"),
        "failed": scalar("SELECT COUNT(*) value FROM orders WHERE status='FAILED'"),
        "low": scalar("SELECT COUNT(*) value FROM products WHERE deleted_at IS NULL AND stock_quantity <= reorder_threshold"),
    })
    return extra


BASE = """
<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{ title }} · CloudMart</title><style>
:root{--navy:#14213d;--blue:#2563eb;--bg:#f5f7fb;--line:#e6ebf3;--muted:#6b7280;--green:#15803d;--amber:#b45309;--red:#b91c1c}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:#172033;font:14px Inter,Arial,sans-serif}
.layout{display:flex;min-height:100vh}.side{width:230px;background:var(--navy);color:white;padding:25px 14px;position:sticky;top:0;height:100vh}
.brand{padding:0 12px 25px;border-bottom:1px solid #ffffff25}.brand h1{margin:0;font-size:21px}.brand p{margin:5px 0 0;color:#cbd5e1;font-size:12px}
.nav{display:grid;gap:6px;margin-top:24px}.nav a{color:#dbe5f5;padding:12px;border-radius:9px;font-weight:700}.nav a:hover{background:#ffffff12;text-decoration:none}.nav a.active{background:#2563eb;color:#fff;box-shadow:0 5px 14px #00000020}
.main{flex:1;min-width:0}.top{background:white;border-bottom:1px solid var(--line);padding:18px 30px;display:flex;justify-content:space-between;align-items:center}.top h2{margin:0;font-size:21px}.top small{color:var(--muted)}
.content{padding:28px;max-width:1600px;margin:auto}.hero h1{margin:0 0 6px;font-size:28px}.hero p{margin:0;color:var(--muted)}
.toolbar{display:flex;gap:10px;flex-wrap:wrap;margin:22px 0}.search{display:flex;gap:8px;flex:1;min-width:240px}
input{border:1px solid #d5deeb;border-radius:9px;padding:11px 12px;width:100%}button,.btn{background:var(--blue);color:white;border:0;border-radius:9px;padding:11px 15px;font-weight:800;text-decoration:none;cursor:pointer}.btn.alt{background:#eef2f8;color:#172033}
.cards{display:grid;grid-template-columns:repeat(5,minmax(130px,1fr));gap:15px;margin:22px 0}.card{display:block;color:inherit;text-decoration:none;transition:transform .15s,box-shadow .15s}.card:hover{transform:translateY(-2px);box-shadow:0 10px 28px #14213d18}.card,.panel{background:white;border:1px solid var(--line);border-radius:14px;box-shadow:0 7px 25px #14213d0b}.card{padding:18px}.label{color:var(--muted);font-size:11px;text-transform:uppercase;font-weight:800}.number{font-size:29px;font-weight:900;margin-top:10px}
.panel{padding:21px;margin-bottom:22px;overflow:hidden}.heading{display:flex;justify-content:space-between;gap:10px;align-items:center;margin-bottom:15px}.heading h3{margin:0;font-size:17px}.muted{color:var(--muted);font-size:12px}.table{overflow-x:auto}table{width:100%;border-collapse:collapse;font-size:13px}th,td{padding:13px 11px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}th{background:#f8faff;color:#526078;font-size:10px;text-transform:uppercase}
.badge{display:inline-flex;padding:5px 9px;border-radius:999px;font-size:10px;font-weight:900}.green{background:#eaf8ef;color:var(--green)}.amber{background:#fff7e6;color:var(--amber)}.red{background:#fff0f0;color:var(--red)}.blue{background:#eaf1ff;color:var(--blue)}
.detail{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:15px 25px}.detail div{padding:10px 0;border-bottom:1px solid var(--line);overflow-wrap:anywhere}.detail label{display:block;color:var(--muted);font-size:10px;text-transform:uppercase;font-weight:900;margin-bottom:5px}
.empty{text-align:center;padding:25px;color:var(--muted)}.note{color:var(--muted);font-size:12px;margin-top:18px}
@media(max-width:1100px){.cards{grid-template-columns:repeat(3,1fr)}}@media(max-width:750px){.side{width:72px;padding:18px 8px}.brand{text-align:center;padding:0 0 20px}.brand h1{font-size:0}.brand h1:after{content:'CM';font-size:18px}.brand p,.nav span{display:none}.nav a{text-align:center}.nav a:before{content:'•';font-size:20px}.top{padding:16px}.content{padding:18px}.cards{grid-template-columns:repeat(2,1fr)}.detail{grid-template-columns:1fr}}
</style></head><body><div class="layout"><aside class="side"><div class="brand"><h1>CloudMart</h1><p>Operations Console</p></div><nav class="nav">
<a class="{% if request.endpoint == 'dashboard' %}active{% endif %}" href="{{url_for('dashboard')}}"><span>Overview</span></a><a class="{% if request.endpoint in ['products_page', 'product_detail'] %}active{% endif %}" href="{{url_for('products_page')}}"><span>Products</span></a><a class="{% if request.endpoint in ['orders_page', 'order_detail'] %}active{% endif %}" href="{{url_for('orders_page')}}"><span>Orders</span></a><a class="{% if request.endpoint in ['customers_page', 'customer_detail'] %}active{% endif %}" href="{{url_for('customers_page')}}"><span>Customers</span></a><a class="{% if request.endpoint == 'events_page' %}active{% endif %}" href="{{url_for('events_page')}}"><span>Event History</span></a><a class="{% if request.endpoint in ['reports_page', 'report_view'] %}active{% endif %}" href="{{url_for('reports_page')}}"><span>Daily Reports</span></a>{% if cloudwatch_url %}<a href="{{cloudwatch_url}}" target="_blank" rel="noopener"><span>Monitoring</span></a>{% endif %}</nav></aside>
<section class="main"><header class="top"><div><h2>{{title}}</h2><small>Operations Console · {{aws_region}}</small></div><a class="btn alt" href="{{request.path}}">Refresh</a></header><main class="content">{{body|safe}}</main></section></div></body></html>
"""


def page(title, body, **values):
    values = context(**values)
    values["title"] = title
    values["body"] = render_template_string(body, **values)
    return render_template_string(BASE, **values)


LOGIN = """<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>CloudMart Login</title><style>
body{margin:0;min-height:100vh;display:grid;place-items:center;background:#f5f7fb;font-family:Arial;color:#172033}.box{width:min(400px,90%);background:white;padding:34px;border-radius:18px;box-shadow:0 12px 40px #14213d18}.box h1{margin:0 0 8px}.box p{color:#6b7280}.box input,.box button{width:100%;padding:13px;margin-top:12px;box-sizing:border-box;border-radius:9px}.box input{border:1px solid #d5deeb}.box button{border:0;background:#2563eb;color:white;font-weight:800}.error{margin-top:14px;color:#b91c1c;background:#fff0f0;padding:10px;border-radius:8px}</style></head><body><div class="box"><h1>CloudMart Admin</h1><p>Enter your administrator bearer token to continue.</p><form method="post"><input name="token" type="password" placeholder="Bearer token" required autofocus><button>Sign in</button></form>{%if error%}<div class="error">{{error}}</div>{%endif%}</div></body></html>"""


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        supplied = token_value(request.form.get("token"))
        try:
            expected = token_value(os.getenv("ADMIN_BEARER_TOKEN") or parameter(PARAM["admin"]))
            if supplied and expected and hmac.compare_digest(supplied, expected):
                session.clear()
                session["authenticated"] = True
                return redirect(request.args.get("next") or url_for("dashboard"))
        except Exception:
            logger.exception("Admin login validation failed")
        return render_template_string(LOGIN, error="Invalid token or authentication configuration.")
    return render_template_string(LOGIN, error=None)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/health")
def health():
    return {"status": "healthy", "service": "CloudMart Operations Dashboard"}


DASH = """
<div class="hero"><h1>CloudMart Overview</h1><p>Live operational visibility for inventory, customers, orders, events, and S3 reports.</p></div>
<div class="toolbar"><form class="search" method="get" action="{{url_for('search')}}"><input name="q" placeholder="Search products, orders, or customers"><button>Search</button></form></div>
<div class="cards">
<a class="card" href="{{url_for('products_page')}}"><div class="label">Products</div><div class="number">{{summary['products']}}</div></a>
<a class="card" href="{{url_for('customers_page')}}"><div class="label">Customers</div><div class="number">{{summary['customers']}}</div></a>
<a class="card" href="{{url_for('orders_page')}}"><div class="label">Orders</div><div class="number">{{summary['orders']}}</div></a>
<a class="card" href="{{url_for('products_page', filter='low_stock')}}"><div class="label">Low stock</div><div class="number">{{summary['low']}}</div></a>
<a class="card" href="{{url_for('orders_page', status='FAILED')}}"><div class="label">Failed orders</div><div class="number">{{summary['failed']}}</div></a>
</div>
<div class="panel"><div class="heading"><h3>Current inventory</h3><a href="{{url_for('products_page')}}">View all</a></div><div class="table"><table><tr><th>ID</th><th>Product</th><th>Category</th><th>Price</th><th>Stock</th><th>Status</th><th></th></tr>{%for p in products%}<tr><td>{{p.product_id}}</td><td>{{p.name}}</td><td>{{p.category}}</td><td>{{rupees(p.price)}}</td><td>{{p.stock_quantity}}</td><td>{%if p.stock_quantity<=p.reorder_threshold%}<span class="badge amber">Low stock</span>{%else%}<span class="badge green">Healthy</span>{%endif%}</td><td><a href="{{url_for('product_detail',product_id=p.product_id)}}">Details</a></td></tr>{%else%}<tr><td colspan="7" class="empty">No products found.</td></tr>{%endfor%}</table></div></div>
<div class="panel"><div class="heading"><h3>Recent orders</h3><a href="{{url_for('orders_page')}}">View all</a></div><div class="table"><table><tr><th>Order</th><th>Customer</th><th>Date</th><th>Amount</th><th>Status</th><th></th></tr>{%for o in orders%}<tr><td><a href="{{url_for('order_detail',order_id=o.order_id)}}">#{{o.order_id}}</a></td><td>#{{o.customer_id}}</td><td>{{shown(o.order_date)}}</td><td>{{rupees(o.total_amount)}}</td><td><span class="badge {%if o.status in ['FAILED','CANCELLED']%}red{%elif o.status in ['CONFIRMED','DELIVERED']%}green{%else%}blue{%endif%}">{{o.status}}</span></td><td><a href="{{url_for('order_detail',order_id=o.order_id)}}">Details</a></td></tr>{%else%}<tr><td colspan="6" class="empty">No orders found.</td></tr>{%endfor%}</table></div></div>
"""


@app.route("/")
def dashboard():
    g = guard()
    if g: return g
    try:
        summary = context()["summary"]
        products = all_rows("""SELECT p.product_id,p.name,COALESCE(c.name,'Uncategorized') category,p.price,p.stock_quantity,p.reorder_threshold FROM products p LEFT JOIN categories c ON p.category_id=c.category_id WHERE p.deleted_at IS NULL ORDER BY p.product_id LIMIT 12""")
        orders = all_rows("SELECT order_id,customer_id,status,order_date,total_amount FROM orders ORDER BY order_date DESC LIMIT 12")
        return page("Overview", DASH, summary=summary, products=products, orders=orders)
    except Exception as exc:
        return page("Overview", '<div class="panel"><div class="empty">Database error: {{error}}</div></div>', error=str(exc))


LIST = """
<div class="toolbar"><form class="search" method="get"><input name="q" value="{{q}}" placeholder="{{placeholder}}"><button>Search</button></form></div>
<div class="panel"><div class="heading"><h3>{{heading}}</h3><span class="muted">{{rows|length}} results</span></div><div class="table"><table>{{table|safe}}</table></div></div>
"""


@app.route("/products")
def products_page():
    g = guard()
    if g: return g
    q = request.args.get("q", "").strip(); like = f"%{q}%"
    stock_filter = request.args.get("filter", "").strip().lower()
    where = "p.deleted_at IS NULL AND (%s='' OR p.name LIKE %s OR CAST(p.product_id AS CHAR) LIKE %s)"
    args = [q, like, like]
    heading = "Products & inventory"
    if stock_filter == "low_stock":
        where += " AND p.stock_quantity <= p.reorder_threshold"
        heading = "Low-stock products"
    rows = all_rows(f"""SELECT p.product_id,p.name,COALESCE(c.name,'Uncategorized') category,p.price,p.stock_quantity,p.reorder_threshold FROM products p LEFT JOIN categories c ON p.category_id=c.category_id WHERE {where} ORDER BY p.product_id""", tuple(args))
    table = render_template_string("""<tr><th>ID</th><th>Name</th><th>Category</th><th>Price</th><th>Stock</th><th>Threshold</th><th></th></tr>{%for r in rows%}<tr><td>{{r.product_id}}</td><td>{{r.name}}</td><td>{{r.category}}</td><td>{{rupees(r.price)}}</td><td>{{r.stock_quantity}}</td><td>{{r.reorder_threshold}}</td><td><a href="{{url_for('product_detail',product_id=r.product_id)}}">Details</a></td></tr>{%else%}<tr><td colspan="7" class="empty">No products found.</td></tr>{%endfor%}""", rows=rows, rupees=rupees)
    return page("Products", LIST, q=q, placeholder="Search product name or ID", heading=heading, rows=rows, table=table)


@app.route("/products/<int:product_id>")
def product_detail(product_id):
    g = guard()
    if g: return g
    product = one("SELECT p.*,COALESCE(c.name,'Uncategorized') category FROM products p LEFT JOIN categories c ON p.category_id=c.category_id WHERE p.product_id=%s",(product_id,))
    history = all_rows("""SELECT oi.order_id,o.customer_id,oi.quantity,o.status,o.order_date FROM order_items oi JOIN orders o ON oi.order_id=o.order_id WHERE oi.product_id=%s ORDER BY o.order_date DESC LIMIT 50""",(product_id,))
    body = """<div class="toolbar"><a class="btn alt" href="{{url_for('products_page')}}">← Back</a></div><div class="panel"><div class="heading"><h3>{{product.name if product else 'Product not found'}}</h3></div>{%if product%}<div class="detail">{%for k,v in product.items()%}<div><label>{{k.replace('_',' ')}}</label>{{shown(v)}}</div>{%endfor%}</div>{%else%}<div class="empty">Product not found.</div>{%endif%}</div><div class="panel"><div class="heading"><h3>Related order activity</h3></div><div class="table"><table><tr><th>Order</th><th>Customer</th><th>Quantity</th><th>Status</th><th>Date</th></tr>{%for r in history%}<tr><td><a href="{{url_for('order_detail',order_id=r.order_id)}}">#{{r.order_id}}</a></td><td>#{{r.customer_id}}</td><td>{{r.quantity}}</td><td>{{r.status}}</td><td>{{shown(r.order_date)}}</td></tr>{%else%}<tr><td colspan="5" class="empty">No related activity.</td></tr>{%endfor%}</table></div><p class="note">Inventory is maintained in the products table. Related order activity is shown from order_items.</p></div>"""
    return page("Product details", body, product=product, history=history)


@app.route("/orders")
def orders_page():
    g = guard()
    if g: return g

    q = request.args.get("q", "").strip()
    status_filter = request.args.get("status", "").strip().upper()
    like = f"%{q}%"

    conditions = [
        "(%s = '' OR CAST(order_id AS CHAR) LIKE %s OR CAST(customer_id AS CHAR) LIKE %s OR status LIKE %s)"
    ]
    args = [q, like, like, like]
    heading = "Orders"

    # The Failed Orders card sends status=FAILED. This condition ensures
    # that only failed orders are returned, never the complete order list.
    if status_filter == "FAILED":
        conditions.append("status = %s")
        args.append("FAILED")
        heading = "Failed orders"

    where_clause = " AND ".join(conditions)
    rows = all_rows(
        f"""SELECT order_id, customer_id, status, order_date, total_amount
        FROM orders
        WHERE {where_clause}
        ORDER BY order_date DESC
        LIMIT 200""",
        tuple(args),
    )

    table = render_template_string(
        """<tr><th>Order</th><th>Customer</th><th>Date</th><th>Amount</th><th>Status</th><th></th></tr>
        {% for r in rows %}
        <tr>
          <td><a href="{{url_for('order_detail', order_id=r.order_id)}}">#{{r.order_id}}</a></td>
          <td>#{{r.customer_id}}</td>
          <td>{{shown(r.order_date)}}</td>
          <td>{{rupees(r.total_amount)}}</td>
          <td><span class="badge {% if r.status == 'FAILED' %}red{% elif r.status in ['CONFIRMED','DELIVERED'] %}green{% else %}blue{% endif %}">{{r.status}}</span></td>
          <td><a href="{{url_for('order_detail', order_id=r.order_id)}}">Details</a></td>
        </tr>
        {% else %}
        <tr><td colspan="6" class="empty">No orders found.</td></tr>
        {% endfor %}""",
        rows=rows,
        shown=shown,
        rupees=rupees,
    )
    return page(
        "Orders",
        LIST,
        q=q,
        placeholder="Search order ID, customer ID, or status",
        heading=heading,
        rows=rows,
        table=table,
    )


@app.route("/orders/<int:order_id>")
def order_detail(order_id):
    g = guard()
    if g: return g
    order = one("SELECT * FROM orders WHERE order_id=%s",(order_id,))
    items = all_rows("SELECT oi.*,p.name product_name FROM order_items oi LEFT JOIN products p ON oi.product_id=p.product_id WHERE oi.order_id=%s ORDER BY oi.order_item_id",(order_id,))
    logs = all_rows("SELECT previous_status,new_status,changed_by,note,created_at FROM order_logs WHERE order_id=%s ORDER BY created_at DESC",(order_id,))
    body = """<div class="toolbar"><a class="btn alt" href="{{url_for('orders_page')}}">← Back</a></div><div class="panel"><div class="heading"><h3>Order #{{order.order_id if order else '—'}}</h3></div>{%if order%}<div class="detail">{%for k,v in order.items()%}<div><label>{{k.replace('_',' ')}}</label>{{shown(v)}}</div>{%endfor%}</div>{%else%}<div class="empty">Order not found.</div>{%endif%}</div><div class="panel"><div class="heading"><h3>Order items</h3></div><div class="table"><table><tr><th>Item</th><th>Product</th><th>Quantity</th><th>Unit price</th><th>Subtotal</th></tr>{%for i in items%}<tr><td>{{i.order_item_id}}</td><td>{{i.product_name}}</td><td>{{i.quantity}}</td><td>{{rupees(i.unit_price)}}</td><td>{{rupees(i.quantity*i.unit_price)}}</td></tr>{%else%}<tr><td colspan="5" class="empty">No items found.</td></tr>{%endfor%}</table></div></div><div class="panel"><div class="heading"><h3>Order history</h3></div><div class="table"><table><tr><th>Previous</th><th>New</th><th>Changed by</th><th>Note</th><th>Time</th></tr>{%for l in logs%}<tr><td>{{l.previous_status or '—'}}</td><td>{{l.new_status}}</td><td>{{l.changed_by or '—'}}</td><td>{{l.note or '—'}}</td><td>{{shown(l.created_at)}}</td></tr>{%else%}<tr><td colspan="5" class="empty">No history found.</td></tr>{%endfor%}</table></div></div>"""
    return page("Order details", body, order=order, items=items, logs=logs)


@app.route("/customers")
def customers_page():
    g = guard()
    if g: return g
    q = request.args.get("q", "").strip(); like = f"%{q}%"
    rows = all_rows("""SELECT customer_id,name,email,status,created_at FROM customers WHERE %s='' OR CAST(customer_id AS CHAR) LIKE %s OR name LIKE %s OR email LIKE %s ORDER BY customer_id DESC LIMIT 200""",(q,like,like,like))
    table = render_template_string("""<tr><th>ID</th><th>Name</th><th>Email</th><th>Status</th><th>Created</th><th></th></tr>{%for r in rows%}<tr><td>#{{r.customer_id}}</td><td>{{r.name}}</td><td>{{r.email}}</td><td>{{r.status}}</td><td>{{shown(r.created_at)}}</td><td><a href="{{url_for('customer_detail',customer_id=r.customer_id)}}">Details</a></td></tr>{%else%}<tr><td colspan="6" class="empty">No customers found.</td></tr>{%endfor%}""", rows=rows, shown=shown)
    return page("Customers", LIST, q=q, placeholder="Search customer ID, name, or email", heading="Customers", rows=rows, table=table)


@app.route("/customers/<int:customer_id>")
def customer_detail(customer_id):
    g = guard()
    if g: return g
    customer = one("SELECT customer_id,name,email,address,status,created_at,updated_at,deleted_at FROM customers WHERE customer_id=%s",(customer_id,))
    orders = all_rows("SELECT order_id,order_date,total_amount,status FROM orders WHERE customer_id=%s ORDER BY order_date DESC LIMIT 100",(customer_id,))
    body = """<div class="toolbar"><a class="btn alt" href="{{url_for('customers_page')}}">← Back</a></div><div class="panel"><div class="heading"><h3>{{customer.name if customer else 'Customer not found'}}</h3></div>{%if customer%}<div class="detail">{%for k,v in customer.items()%}<div><label>{{k.replace('_',' ')}}</label>{{shown(v)}}</div>{%endfor%}</div>{%else%}<div class="empty">Customer not found.</div>{%endif%}</div><div class="panel"><div class="heading"><h3>Customer orders</h3></div><div class="table"><table><tr><th>Order</th><th>Date</th><th>Amount</th><th>Status</th></tr>{%for o in orders%}<tr><td><a href="{{url_for('order_detail',order_id=o.order_id)}}">#{{o.order_id}}</a></td><td>{{shown(o.order_date)}}</td><td>{{rupees(o.total_amount)}}</td><td>{{o.status}}</td></tr>{%else%}<tr><td colspan="4" class="empty">No orders found.</td></tr>{%endfor%}</table></div></div>"""
    return page("Customer details", body, customer=customer, orders=orders)


@app.route("/events")
def events_page():
    g = guard()
    if g: return g
    q = request.args.get("q", "").strip(); like = f"%{q}%"
    events = all_rows("""SELECT order_id,previous_status,new_status,changed_by,note,created_at FROM order_logs WHERE %s='' OR CAST(order_id AS CHAR) LIKE %s OR new_status LIKE %s OR note LIKE %s ORDER BY created_at DESC LIMIT 300""",(q,like,like,like))
    body = """<div class="toolbar"><form class="search"><input name="q" value="{{q}}" placeholder="Search order ID, status, or note"><button>Search</button></form></div><div class="panel"><div class="heading"><h3>Event history</h3></div><div class="table"><table><tr><th>Order</th><th>Previous</th><th>New</th><th>Changed by</th><th>Note</th><th>Time</th></tr>{%for e in events%}<tr><td><a href="{{url_for('order_detail',order_id=e.order_id)}}">#{{e.order_id}}</a></td><td>{{e.previous_status or '—'}}</td><td>{{e.new_status}}</td><td>{{e.changed_by or '—'}}</td><td>{{e.note or '—'}}</td><td>{{shown(e.created_at)}}</td></tr>{%else%}<tr><td colspan="6" class="empty">No events found.</td></tr>{%endfor%}</table></div></div>"""
    return page("Event history", body, q=q, events=events)


@app.route("/reports")
def reports_page():
    g = guard()
    if g: return g
    body = """{% if cloudwatch_url %}<div class="panel"><div class="heading"><h3>Monitoring</h3><span class="muted">CloudWatch</span></div><p>Open the CloudMart operations dashboard in CloudWatch.</p><a class="btn" href="{{cloudwatch_url}}" target="_blank" rel="noopener">Open CloudWatch dashboard</a></div>{% endif %}<div class="panel"><div class="heading"><h3>Daily reports</h3><span class="muted">{{report_bucket or 'Bucket not configured'}}</span></div>{%if reports%}<div class="table"><table><tr><th>Report</th><th>Size</th><th>Generated</th><th>Actions</th></tr>{%for r in reports%}<tr><td>{{r.key}}</td><td>{{r.size}} bytes</td><td>{{shown(r.last_modified)}}</td><td><a class="btn alt" href="{{url_for('report_view', key=r.key)}}">View report</a> <a class="btn alt" href="{{url_for('report_download', key=r.key)}}">Download CSV</a></td></tr>{%endfor%}</table></div>{%else%}<div class="empty">No generated reports found. Verify the report Lambda, EventBridge schedule, and S3 bucket parameter.</div>{%endif%}</div>"""
    return page("Daily reports", body)


@app.route("/reports/view")
def report_view():
    g = guard()
    if g: return g
    key = request.args.get("key", "").strip()
    bucket = report_bucket()
    if not bucket or not key.startswith("daily-reports/") or not key.lower().endswith(".csv"):
        return page("Report details", '<div class="panel"><div class="empty">Invalid report.</div></div>'), 400
    try:
        raw = s3.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8-sig", errors="replace")
        body = """<div class="toolbar"><a class="btn alt" href="{{url_for('reports_page')}}">← Back to reports</a><a class="btn alt" href="{{url_for('report_download', key=key)}}">Download CSV</a></div><div class="panel"><div class="heading"><h3>{{key}}</h3><span class="muted">Report preview</span></div><pre style="white-space:pre-wrap;overflow:auto;background:#f8faff;border:1px solid var(--line);border-radius:10px;padding:18px;line-height:1.6">{{content}}</pre></div>"""
        return page("Report details", body, key=key, content=raw)
    except Exception as exc:
        logger.exception("Report preview failed")
        return page("Report details", '<div class="panel"><div class="empty">Unable to read report: {{error}}</div></div>', error=str(exc)), 500


@app.route("/reports/download")
def report_download():
    g = guard()
    if g: return g
    key = request.args.get("key", "").strip()
    bucket = report_bucket()
    if not bucket or not key.startswith("daily-reports/") or not key.lower().endswith(".csv"):
        return "Invalid report", 400
    try:
        raw = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        filename = key.rsplit("/", 1)[-1]
        return Response(raw, mimetype="text/csv", headers={"Content-Disposition": f'attachment; filename="{filename}"'})
    except Exception as exc:
        logger.exception("Report download failed")
        return f"Unable to download report: {exc}", 500


@app.route("/search")
def search():
    g = guard()
    if g: return g
    q = request.args.get("q", "").strip()
    if not q: return redirect(url_for("dashboard"))
    like = f"%{q}%"; results = []
    results += all_rows("SELECT product_id id,name label,'Product' type,CONCAT('Stock: ',stock_quantity) extra FROM products WHERE deleted_at IS NULL AND (name LIKE %s OR CAST(product_id AS CHAR) LIKE %s) LIMIT 50",(like,like))
    results += all_rows("SELECT order_id id,CONCAT('Order #',order_id) label,'Order' type,CONCAT('Customer #',customer_id,' · ',status) extra FROM orders WHERE CAST(order_id AS CHAR) LIKE %s OR CAST(customer_id AS CHAR) LIKE %s OR status LIKE %s LIMIT 50",(like,like,like))
    results += all_rows("SELECT customer_id id,name label,'Customer' type,email extra FROM customers WHERE name LIKE %s OR email LIKE %s OR CAST(customer_id AS CHAR) LIKE %s LIMIT 50",(like,like,like))
    body = """<div class="panel"><div class="heading"><h3>Search results</h3><span class="muted">{{q}}</span></div>{%for r in results%}<div style="padding:14px 0;border-bottom:1px solid var(--line)"><span class="badge blue">{{r.type}}</span>{%if r.type=='Product'%}<a href="{{url_for('product_detail',product_id=r.id)}}"> {{r.label}}</a>{%elif r.type=='Order'%}<a href="{{url_for('order_detail',order_id=r.id)}}"> {{r.label}}</a>{%else%}<a href="{{url_for('customer_detail',customer_id=r.id)}}"> {{r.label}}</a>{%endif%}<div class="muted">{{r.extra}}</div></div>{%else%}<div class="empty">No matching records found.</div>{%endfor%}</div>"""
    return page("Search", body, q=q, results=results)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")))
