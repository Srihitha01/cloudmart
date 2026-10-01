import os
import hmac
import logging
import boto3
import pymysql
from botocore.config import Config
from datetime import datetime, date, timedelta
import calendar
import re
from flask import Flask, render_template, request, redirect, url_for, session

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
s3 = boto3.client(
    "s3",
    region_name=REGION,
    config=Config(
        signature_version="s3v4",
        s3={"addressing_style": "path"},
    ),
)


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


REPORT_DATE_RE = re.compile(r"(20\d{2}-\d{2}-\d{2})")


def report_date_from_key(key, fallback=None):
    match = REPORT_DATE_RE.search(key or "")
    if match:
        try:
            return date.fromisoformat(match.group(1))
        except ValueError:
            pass
    if isinstance(fallback, datetime):
        return fallback.date()
    if isinstance(fallback, date):
        return fallback
    return None


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
            report_date = report_date_from_key(obj["Key"], obj["LastModified"])
            rows.append({
                "key": obj["Key"],
                "size": obj["Size"],
                "last_modified": obj["LastModified"],
                "report_date": report_date.isoformat() if report_date else "",
                "url": url_for("report_download", key=obj["Key"]),
            })
        rows.sort(key=lambda x: x["last_modified"], reverse=True)
        return rows[:50], bucket
    except Exception:
        logger.exception("Unable to list daily reports")
        return [], bucket


def inventory_budget():
    raw = os.getenv("INVENTORY_BUDGET", "50000").strip()
    try:
        value = float(raw)
        return value if value > 0 else 50000.0
    except ValueError:
        return 50000.0


def dashboard_metrics():
    today = date.today()
    tomorrow = today + timedelta(days=1)
    month_start = today.replace(day=1)
    if today.month == 12:
        month_end = date(today.year + 1, 1, 1)
    else:
        month_end = date(today.year, today.month + 1, 1)

    inventory_value = scalar(
        "SELECT COALESCE(SUM(price * stock_quantity),0) value "
        "FROM products WHERE deleted_at IS NULL"
    )
    month_sales = scalar(
        "SELECT COALESCE(SUM(total_amount),0) value FROM orders "
        "WHERE order_date >= %s AND order_date < %s",
        (month_start, month_end),
    )
    orders_today = scalar(
        "SELECT COUNT(*) value FROM orders WHERE order_date >= %s AND order_date < %s",
        (today, tomorrow),
    )
    avg_order = scalar("SELECT COALESCE(AVG(total_amount),0) value FROM orders")
    pending = scalar("SELECT COUNT(*) value FROM orders WHERE status='PENDING'")
    confirmed = scalar("SELECT COUNT(*) value FROM orders WHERE status='CONFIRMED'")
    delivered = scalar("SELECT COUNT(*) value FROM orders WHERE status='DELIVERED'")
    cancelled = scalar("SELECT COUNT(*) value FROM orders WHERE status='CANCELLED'")

    status_rows = all_rows(
        "SELECT status, COUNT(*) count FROM orders GROUP BY status ORDER BY status"
    )
    status_counts = {str(row["status"]): int(row["count"]) for row in status_rows}

    series_start = today - timedelta(days=13)
    series_end = tomorrow
    series_rows = all_rows(
        "SELECT DATE(order_date) order_day, COALESCE(SUM(total_amount),0) amount "
        "FROM orders WHERE order_date >= %s AND order_date < %s "
        "GROUP BY DATE(order_date) ORDER BY DATE(order_date)",
        (series_start, series_end),
    )
    by_day = {}
    for row in series_rows:
        key = row["order_day"]
        if hasattr(key, "isoformat"):
            key = key.isoformat()
        by_day[str(key)] = float(row["amount"] or 0)

    sales_series = []
    for offset in range(14):
        day = series_start + timedelta(days=offset)
        sales_series.append({
            "date": day.isoformat(),
            "label": day.strftime("%d %b"),
            "amount": by_day.get(day.isoformat(), 0.0),
        })

    budget = inventory_budget()
    usage_pct = min(100.0, (float(inventory_value or 0) / budget) * 100) if budget else 0.0

    return {
        "inventory_value": float(inventory_value or 0),
        "inventory_budget": budget,
        "inventory_budget_used_pct": usage_pct,
        "month_sales": float(month_sales or 0),
        "orders_today": int(orders_today or 0),
        "avg_order": float(avg_order or 0),
        "pending": int(pending or 0),
        "confirmed": int(confirmed or 0),
        "delivered": int(delivered or 0),
        "cancelled": int(cancelled or 0),
        "status_counts": status_counts,
        "sales_series": sales_series,
    }


def report_calendar_data(selected_date, available_reports):
    report_dates = {
        row["report_date"]
        for row in available_reports
        if row.get("report_date")
    }
    month_matrix = calendar.monthcalendar(selected_date.year, selected_date.month)
    weeks = []
    for week in month_matrix:
        days = []
        for day_num in week:
            if day_num == 0:
                days.append(None)
                continue
            current = date(selected_date.year, selected_date.month, day_num)
            days.append({
                "day": day_num,
                "iso": current.isoformat(),
                "selected": current == selected_date,
                "has_report": current.isoformat() in report_dates,
                "today": current == date.today(),
            })
        weeks.append(days)

    return {
        "year": selected_date.year,
        "month": selected_date.month,
        "month_name": selected_date.strftime("%B %Y"),
        "weeks": weeks,
        "prev_year": (selected_date.replace(day=1) - timedelta(days=1)).year,
        "prev_month": (selected_date.replace(day=1) - timedelta(days=1)).month,
        "next_year": (
            (selected_date.replace(day=28) + timedelta(days=4)).replace(day=1)
        ).year,
        "next_month": (
            (selected_date.replace(day=28) + timedelta(days=4)).replace(day=1)
        ).month,
    }


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


def page(title, view, **values):
    """Render all dashboard pages through the shared templates/index.html file."""
    values = context(**values)
    values["title"] = title
    values["view"] = view
    return render_template("index.html", **values)


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
        return render_template("index.html", view="login", title="Login", error="Invalid token or authentication configuration.")
    return render_template("index.html", view="login", title="Login", error=None)


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
    if g:
        return g
    try:
        summary = context()["summary"]
        products = all_rows(
            """SELECT p.product_id,p.name,COALESCE(c.name,'Uncategorized') category,
                      p.price,p.stock_quantity,p.reorder_threshold,p.status
               FROM products p
               LEFT JOIN categories c ON p.category_id=c.category_id
               WHERE p.deleted_at IS NULL
               ORDER BY p.product_id
               LIMIT 12"""
        )
        orders = all_rows(
            "SELECT order_id,customer_id,status,order_date,total_amount "
            "FROM orders ORDER BY order_date DESC LIMIT 12"
        )

        available_reports = reports()[0]
        today = date.today()
        selected_raw = request.args.get("report_date", "").strip()
        try:
            selected_date = date.fromisoformat(selected_raw) if selected_raw else today
        except ValueError:
            selected_date = today

        selected_report = next(
            (r for r in available_reports if r.get("report_date") == selected_date.isoformat()),
            None,
        )

        selected_month_raw = request.args.get("month", "").strip()
        calendar_date = selected_date
        if selected_month_raw:
            try:
                calendar_date = date.fromisoformat(f"{selected_month_raw}-01")
            except ValueError:
                calendar_date = selected_date

        values = context(
            summary=summary,
            products=products,
            orders=orders,
            metrics=dashboard_metrics(),
            selected_date=selected_date,
            selected_report=selected_report,
            report_calendar=report_calendar_data(calendar_date, available_reports),
        )
        return render_template("index.html", title="Overview", view="dashboard", **values)
    except Exception as exc:
        logger.exception("Dashboard load failed")
        return page(
            "Overview",
            "error",
            error="Dashboard data could not be loaded. Check the dashboard logs.",
        )


@app.route("/products")
def products_page():
    g = guard()
    if g: return g
    q = request.args.get("q", "").strip(); like = f"%{q}%"
    stock_filter = request.args.get("filter", "").strip().lower()
    where = "(%s='' OR p.name LIKE %s OR CAST(p.product_id AS CHAR) LIKE %s)"
    args = [q, like, like]
    heading = "Products & inventory"
    if stock_filter == "low_stock":
        where += " AND p.status = 'ACTIVE' AND p.stock_quantity <= p.reorder_threshold"
        heading = "Low-stock products"
    rows = all_rows(f"""SELECT p.product_id,p.name,COALESCE(c.name,'Uncategorized') category,p.price,p.stock_quantity,p.reorder_threshold,p.status FROM products p LEFT JOIN categories c ON p.category_id=c.category_id WHERE {where} ORDER BY p.product_id""", tuple(args))
    return page("Products", "list", entity="products", q=q, placeholder="Search product name or ID", heading=heading, rows=rows, stock_filter=stock_filter)


@app.route("/products/<int:product_id>")
def product_detail(product_id):
    g = guard()
    if g: return g
    product = one("SELECT p.*,COALESCE(c.name,'Uncategorized') category FROM products p LEFT JOIN categories c ON p.category_id=c.category_id WHERE p.product_id=%s",(product_id,))
    history = all_rows("""SELECT oi.order_id,o.customer_id,oi.quantity,o.status,o.order_date FROM order_items oi JOIN orders o ON oi.order_id=o.order_id WHERE oi.product_id=%s ORDER BY o.order_date DESC LIMIT 50""",(product_id,))

    return page("Product details", "product_detail", product=product, history=history)


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
    return page("Orders", "list", entity="orders", q=q, placeholder="Search order ID, customer ID, or status", heading=heading, rows=rows, status_filter=status_filter)


@app.route("/orders/<int:order_id>")
def order_detail(order_id):
    g = guard()
    if g: return g
    order = one("SELECT * FROM orders WHERE order_id=%s",(order_id,))
    items = all_rows("SELECT oi.*,p.name product_name FROM order_items oi LEFT JOIN products p ON oi.product_id=p.product_id WHERE oi.order_id=%s ORDER BY oi.order_item_id",(order_id,))
    logs = all_rows("SELECT previous_status,new_status,changed_by,note,created_at FROM order_logs WHERE order_id=%s ORDER BY created_at DESC",(order_id,))

    return page("Order details", "order_detail", order=order, items=items, logs=logs)


@app.route("/customers")
def customers_page():
    g = guard()
    if g: return g
    q = request.args.get("q", "").strip(); like = f"%{q}%"
    rows = all_rows("""SELECT customer_id,name,email,status,created_at FROM customers WHERE %s='' OR CAST(customer_id AS CHAR) LIKE %s OR name LIKE %s OR email LIKE %s ORDER BY customer_id DESC LIMIT 200""",(q,like,like,like))
    return page("Customers", "list", entity="customers", q=q, placeholder="Search customer ID, name, or email", heading="Customers", rows=rows)


@app.route("/customers/<int:customer_id>")
def customer_detail(customer_id):
    g = guard()
    if g: return g
    customer = one("SELECT customer_id,name,email,address,status,created_at,updated_at,deleted_at FROM customers WHERE customer_id=%s",(customer_id,))
    orders = all_rows("SELECT order_id,order_date,total_amount,status FROM orders WHERE customer_id=%s ORDER BY order_date DESC LIMIT 100",(customer_id,))

    return page("Customer details", "customer_detail", customer=customer, orders=orders)


@app.route("/events")
def events_page():
    g = guard()
    if g: return g
    q = request.args.get("q", "").strip(); like = f"%{q}%"
    events = all_rows("""SELECT order_id,previous_status,new_status,changed_by,note,created_at FROM order_logs WHERE %s='' OR CAST(order_id AS CHAR) LIKE %s OR new_status LIKE %s OR note LIKE %s ORDER BY created_at DESC LIMIT 300""",(q,like,like,like))

    return page("Event history", "events", q=q, events=events)


@app.route("/reports")
def reports_page():
    g = guard()
    if g:
        return g

    available_reports = reports()[0]
    today = date.today()
    selected_raw = request.args.get("report_date", "").strip()
    try:
        selected_date = date.fromisoformat(selected_raw) if selected_raw else today
    except ValueError:
        selected_date = today

    selected_report = next(
        (r for r in available_reports if r.get("report_date") == selected_date.isoformat()),
        None,
    )

    return page(
        "Daily reports",
        "reports",
        selected_date=selected_date,
        selected_report=selected_report,
        report_calendar=report_calendar_data(selected_date, available_reports),
    )


@app.route("/reports/view")
def report_view():
    g = guard()
    if g: return g
    key = request.args.get("key", "").strip()
    bucket = report_bucket()
    if not bucket or not key.startswith("daily-reports/") or not key.lower().endswith(".csv"):
        return page("Report details", "error", error="Invalid report key."), 400
    try:
        raw = s3.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8-sig", errors="replace")
        report_url = url_for("report_download", key=key)

        return page("Report details", "report_view", key=key, content=raw, report_url=report_url)
    except Exception as exc:
        logger.exception("Report preview failed")
        return page("Report details", "error", error="Unable to read report. Check the dashboard logs."), 500
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

    return page("Search", "search", q=q, results=results)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")))
