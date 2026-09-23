import os
import hmac
import logging
import boto3
import pymysql
from datetime import datetime
from flask import Flask, render_template, render_template_string, request, redirect, url_for, session

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
:root{--navy:#101d3a;--navy-2:#17284d;--blue:#2563eb;--blue-2:#4f7cff;--bg:#f4f7fc;--surface:#fff;--line:#e5eaf3;--text:#17233c;--muted:#718096;--green:#087f5b;--amber:#b86b08;--red:#c53030;--shadow:0 10px 30px rgba(23,35,60,.07)}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:linear-gradient(135deg,#f7f9fd 0%,#eef4fc 100%);color:var(--text);font:14px/1.55 Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",Arial,sans-serif}
a{color:#1d4ed8;text-underline-offset:3px}a:hover{text-decoration:none}
.layout{display:flex;min-height:100vh}.side{width:252px;flex:0 0 252px;background:linear-gradient(180deg,var(--navy) 0%,#0e1931 100%);color:#fff;padding:25px 14px;position:sticky;top:0;height:100vh;box-shadow:8px 0 30px rgba(16,29,58,.08)}
.brand{padding:5px 14px 26px;border-bottom:1px solid rgba(255,255,255,.13)}.brand h1{margin:0;font-size:24px;letter-spacing:-.6px;font-weight:850}.brand p{margin:5px 0 0;color:#b9c7e1;font-size:12px;letter-spacing:.15px}
.nav{display:grid;gap:7px;margin-top:25px}.nav a{display:flex;align-items:center;gap:11px;color:#d9e4fa;padding:12px 14px;border:1px solid transparent;border-radius:12px;font-weight:700;text-decoration:none;transition:background .2s,border-color .2s,transform .2s,box-shadow .2s}.nav a:before{content:"";width:7px;height:7px;border:1px solid #8da5d0;border-radius:50%;flex:0 0 7px}.nav a:hover{background:rgba(255,255,255,.08);border-color:rgba(255,255,255,.08);transform:translateX(2px)}.nav a.active{background:linear-gradient(135deg,#2563eb,#477bff);border-color:rgba(255,255,255,.1);color:#fff;box-shadow:0 8px 20px rgba(37,99,235,.28)}.nav a.active:before{background:#fff;border-color:#fff}
.side-footer{position:absolute;left:14px;right:14px;bottom:20px;border-top:1px solid rgba(255,255,255,.13);padding:18px 4px 0}.admin-label{color:#aebedb;font-size:10px;letter-spacing:.13em;text-transform:uppercase;margin:0 12px 10px;font-weight:800}.logout-btn{display:flex;align-items:center;gap:10px;color:#fecaca!important;background:rgba(185,28,28,.13);border:1px solid rgba(252,165,165,.2);padding:11px 13px!important;border-radius:11px!important}.logout-btn:hover{background:#c53030!important;color:#fff!important;box-shadow:0 7px 18px rgba(197,48,48,.22);transform:none!important}.logout-icon{font-size:16px}
.main{flex:1;min-width:0}.top{background:rgba(255,255,255,.92);backdrop-filter:blur(10px);border-bottom:1px solid var(--line);padding:20px 34px;display:flex;justify-content:space-between;align-items:center;gap:18px;position:sticky;top:0;z-index:5;box-shadow:0 2px 14px rgba(16,29,58,.04)}.top h2{margin:0;font-size:22px;letter-spacing:-.35px;font-weight:850}.top small{display:block;color:var(--muted);margin-top:2px}.top .btn{white-space:nowrap}
.content{padding:32px;max-width:1700px;margin:auto}.hero h1{margin:0 0 7px;font-size:31px;letter-spacing:-.8px;font-weight:850}.hero p{margin:0;color:var(--muted);font-size:14px}.toolbar{display:flex;gap:10px;flex-wrap:wrap;margin:24px 0}.search{display:flex;gap:8px;flex:1;min-width:240px}
input,select,textarea{border:1px solid #d7e0ee;border-radius:11px;padding:12px 13px;width:100%;background:#fff;color:var(--text);font:inherit;outline:none;transition:border-color .2s,box-shadow .2s}input:focus,select:focus,textarea:focus{border-color:#7aa2ff;box-shadow:0 0 0 4px rgba(37,99,235,.09)}button,.btn{display:inline-flex;align-items:center;justify-content:center;gap:8px;background:linear-gradient(135deg,#2563eb,#477bff);color:white;border:0;border-radius:11px;padding:11px 16px;font-weight:800;text-decoration:none;cursor:pointer;box-shadow:0 5px 14px rgba(37,99,235,.15);transition:transform .2s,box-shadow .2s,filter .2s}button:hover,.btn:hover{filter:brightness(1.03);transform:translateY(-1px);box-shadow:0 8px 18px rgba(37,99,235,.22)}.btn.alt{background:#edf2fa;color:#26344d;box-shadow:none}.btn.alt:hover{background:#e3ebf8;box-shadow:none}
.cards{display:grid;grid-template-columns:repeat(5,minmax(130px,1fr));gap:16px;margin:24px 0}.card{display:block;color:inherit;text-decoration:none;position:relative;overflow:hidden;transition:transform .2s,box-shadow .2s,border-color .2s}.card:after{content:"→";position:absolute;right:17px;bottom:15px;color:#4f7cff;font-size:17px;font-weight:900;opacity:.7}.card:hover{transform:translateY(-4px);box-shadow:0 14px 32px rgba(23,35,60,.13);border-color:#cbdafa}.card,.panel{background:rgba(255,255,255,.96);border:1px solid var(--line);border-radius:17px;box-shadow:var(--shadow)}.card{padding:20px}.label{color:#6c7b94;font-size:10px;text-transform:uppercase;font-weight:850;letter-spacing:.09em}.number{font-size:31px;line-height:1.2;font-weight:900;margin-top:10px;letter-spacing:-.6px}
.panel{padding:24px;margin-bottom:24px;overflow:hidden}.heading{display:flex;justify-content:space-between;gap:10px;align-items:center;margin-bottom:17px}.heading h3{margin:0;font-size:18px;letter-spacing:-.25px}.muted{color:var(--muted);font-size:12px}.table{overflow-x:auto;border:1px solid #edf1f7;border-radius:12px}table{width:100%;border-collapse:collapse;font-size:13px;background:#fff}th,td{padding:14px 13px;border-bottom:1px solid #edf1f7;text-align:left;white-space:nowrap}tr:last-child td{border-bottom:0}tbody tr{transition:background .15s}tbody tr:hover{background:#f7faff}th{background:#f6f8fc;color:#60708b;font-size:10px;text-transform:uppercase;letter-spacing:.07em;font-weight:850}td a{font-weight:700}.badge{display:inline-flex;align-items:center;padding:5px 10px;border-radius:999px;font-size:10px;font-weight:900;letter-spacing:.02em}.green{background:#e6f8ef;color:var(--green)}.amber{background:#fff4df;color:var(--amber)}.red{background:#ffebeb;color:var(--red)}.blue{background:#eaf1ff;color:var(--blue)}
.detail{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:15px 28px}.detail div{padding:12px 0;border-bottom:1px solid var(--line);overflow-wrap:anywhere}.detail label{display:block;color:var(--muted);font-size:10px;text-transform:uppercase;font-weight:900;letter-spacing:.06em;margin-bottom:5px}.empty{text-align:center;padding:32px;color:var(--muted)}.note{color:var(--muted);font-size:12px;margin-top:18px}.panel pre{font-family:"SFMono-Regular",Consolas,"Liberation Mono",monospace;font-size:12px}
@media(max-width:1250px){.cards{grid-template-columns:repeat(3,1fr)}.content{padding:25px}}
@media(max-width:750px){.side{width:78px;flex-basis:78px;padding:18px 8px}.brand{text-align:center;padding:0 0 20px}.brand h1{font-size:0}.brand h1:after{content:'CM';font-size:19px}.brand p,.nav span,.admin-label,.logout-icon{display:none}.nav a{justify-content:center;padding:13px 8px}.nav a:before{margin:0;width:8px;height:8px}.side-footer{left:8px;right:8px}.top{padding:16px 18px}.top h2{font-size:18px}.content{padding:18px}.hero h1{font-size:26px}.cards{grid-template-columns:repeat(2,1fr);gap:11px}.card{padding:15px}.number{font-size:27px}.panel{padding:16px}.detail{grid-template-columns:1fr}.toolbar{margin:18px 0}}
</style></head><body><div class="layout"><aside class="side"><div class="brand"><h1>CloudMart</h1><p>Operations Console</p></div><nav class="nav">
<a class="{% if request.endpoint == 'dashboard' %}active{% endif %}" href="{{url_for('dashboard')}}"><span>Overview</span></a><a class="{% if request.endpoint in ['products_page', 'product_detail'] %}active{% endif %}" href="{{url_for('products_page')}}"><span>Products</span></a><a class="{% if request.endpoint in ['orders_page', 'order_detail'] %}active{% endif %}" href="{{url_for('orders_page')}}"><span>Orders</span></a><a class="{% if request.endpoint in ['customers_page', 'customer_detail'] %}active{% endif %}" href="{{url_for('customers_page')}}"><span>Customers</span></a><a class="{% if request.endpoint == 'events_page' %}active{% endif %}" href="{{url_for('events_page')}}"><span>Event History</span></a><a class="{% if request.endpoint in ['reports_page', 'report_view'] %}active{% endif %}" href="{{url_for('reports_page')}}"><span>Daily Reports</span></a>{% if cloudwatch_url %}<a href="{{cloudwatch_url}}" target="_blank" rel="noopener"><span>Monitoring</span></a>{% endif %}</nav><div class="side-footer"><p class="admin-label">Administrator</p><a class="logout-btn" href="{{url_for('logout')}}"><span class="logout-icon">↪</span><span>Log out</span></a></div></aside>
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


@app.route("/")
def dashboard():
    g = guard()
    if g: return g
    try:
        summary = context()["summary"]
        products = all_rows("""SELECT p.product_id,p.name,COALESCE(c.name,'Uncategorized') category,p.price,p.stock_quantity,p.reorder_threshold FROM products p LEFT JOIN categories c ON p.category_id=c.category_id WHERE p.deleted_at IS NULL ORDER BY p.product_id LIMIT 12""")
        orders = all_rows("SELECT order_id,customer_id,status,order_date,total_amount FROM orders ORDER BY order_date DESC LIMIT 12")
        values = context(summary=summary, products=products, orders=orders)
        return render_template("index.html", title="Overview", **values)
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
    q = request.args.get("q", "").strip(); like = f"%{q}%"
    status_filter = request.args.get("status", "").strip().upper()
    where = "(%s='' OR CAST(order_id AS CHAR) LIKE %s OR CAST(customer_id AS CHAR) LIKE %s OR status LIKE %s)"
    args = [q, like, like, like]
    heading = "Orders"
    if status_filter == "FAILED":
        where += " AND status = 'FAILED'"
        heading = "Failed orders"
    rows = all_rows(f"""SELECT order_id,customer_id,status,order_date,total_amount FROM orders WHERE {where} ORDER BY order_date DESC LIMIT 200""", tuple(args))
    table = render_template_string("""<tr><th>Order</th><th>Customer</th><th>Date</th><th>Amount</th><th>Status</th><th></th></tr>{%for r in rows%}<tr><td><a href="{{url_for('order_detail',order_id=r.order_id)}}">#{{r.order_id}}</a></td><td>#{{r.customer_id}}</td><td>{{shown(r.order_date)}}</td><td>{{rupees(r.total_amount)}}</td><td>{{r.status}}</td><td><a href="{{url_for('order_detail',order_id=r.order_id)}}">Details</a></td></tr>{%else%}<tr><td colspan="6" class="empty">No orders found.</td></tr>{%endfor%}""", rows=rows, shown=shown, rupees=rupees)
    return page("Orders", LIST, q=q, placeholder="Search order ID, customer ID, or status", heading=heading, rows=rows, table=table)


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
    body = """{% if cloudwatch_url %}<div class="panel"><div class="heading"><h3>Monitoring</h3><span class="muted">CloudWatch</span></div><p>Open the CloudMart operations dashboard in CloudWatch.</p><a class="btn" href="{{cloudwatch_url}}" target="_blank" rel="noopener">Open CloudWatch dashboard</a></div>{% endif %}<div class="panel"><div class="heading"><h3>Daily reports</h3><span class="muted">{{report_bucket or 'Bucket not configured'}}</span></div>{%if reports%}<div class="table"><table><tr><th>Report</th><th>Size</th><th>Generated</th><th>Actions</th></tr>{%for r in reports%}<tr><td>{{r.key}}</td><td>{{r.size}} bytes</td><td>{{shown(r.last_modified)}}</td><td><a class="btn alt" href="{{url_for('report_view', key=r.key)}}">View report</a> <a class="btn alt" href="{{r.url}}" target="_blank" rel="noopener">Download CSV</a></td></tr>{%endfor%}</table></div>{%else%}<div class="empty">No generated reports found. Verify the report Lambda, EventBridge schedule, and S3 bucket parameter.</div>{%endif%}</div>"""
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
        report_url = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=3600,
        )
        body = """<div class="toolbar"><a class="btn alt" href="{{url_for('reports_page')}}">← Back to reports</a><a class="btn alt" href="{{report_url}}" target="_blank" rel="noopener">Download CSV</a></div><div class="panel"><div class="heading"><h3>{{key}}</h3><span class="muted">Private S3 report · presigned for 1 hour</span></div><pre style="white-space:pre-wrap;overflow:auto;background:#f8faff;border:1px solid var(--line);border-radius:10px;padding:18px;line-height:1.6">{{content}}</pre></div>"""
        return page("Report details", body, key=key, content=raw, report_url=report_url)
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
        presigned_url = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=3600,
        )
        return redirect(presigned_url)
    except Exception as exc:
        logger.exception("Presigned report URL generation failed")
        return f"Unable to create report download URL: {exc}", 500


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
