-- =========================================================
-- CLOUDMART RDS MYSQL DATABASE SCHEMA
-- =========================================================
-- Requirements:
-- 1. MySQL database
-- 2. No DynamoDB
-- 3. No SQS
-- 4. Customer bearer_token stores SHA-256 hash
-- 5. bearer_token is NOT UNIQUE
-- 6. customer_id is the only customer primary key
-- 7. Soft delete for customers and products
-- 8. Inventory maintained in products table
-- =========================================================


CREATE DATABASE IF NOT EXISTS cloudmart;

USE cloudmart;


-- =========================================================
-- 1. CATEGORIES
-- =========================================================

CREATE TABLE IF NOT EXISTS categories (

    category_id INT AUTO_INCREMENT PRIMARY KEY,

    name VARCHAR(150) NOT NULL,

    description VARCHAR(500),

    created_at DATETIME NOT NULL
        DEFAULT CURRENT_TIMESTAMP,

    updated_at DATETIME NOT NULL
        DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP

);


-- =========================================================
-- 2. CUSTOMERS
-- =========================================================
-- bearer_token:
-- The customer manually enters the token during creation.
-- Lambda hashes the token using SHA-256 before storing it.
-- SHA-256 hash length = 64 hexadecimal characters.
--
-- bearer_token intentionally has NO UNIQUE constraint.
-- Only customer_id is the primary key.
-- =========================================================

CREATE TABLE IF NOT EXISTS customers (

    customer_id INT AUTO_INCREMENT PRIMARY KEY,

    name VARCHAR(150) NOT NULL,

    email VARCHAR(255) NOT NULL,

    bearer_token CHAR(64) NULL,

    address VARCHAR(500),

    created_at DATETIME NOT NULL
        DEFAULT CURRENT_TIMESTAMP,

    updated_at DATETIME NOT NULL
        DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,

    deleted_at DATETIME NULL,

    deleted_by VARCHAR(150) NULL,

    delete_reason VARCHAR(500) NULL,

    status VARCHAR(20) NOT NULL
        DEFAULT 'ACTIVE',

    CONSTRAINT uq_customers_email
        UNIQUE (email),

    INDEX idx_customers_deleted_at (deleted_at),

    INDEX idx_customers_name (name)

);


-- =========================================================
-- 3. PRODUCTS
-- =========================================================
-- Inventory is maintained directly in this table.
-- stock_quantity = current available stock
-- reorder_threshold = low-stock alert threshold
-- =========================================================

CREATE TABLE IF NOT EXISTS products (

    product_id INT AUTO_INCREMENT PRIMARY KEY,

    category_id INT NOT NULL,

    name VARCHAR(150) NOT NULL,

    description VARCHAR(500),

    price DECIMAL(10,2) NOT NULL,

    stock_quantity INT NOT NULL
        DEFAULT 0,

    reorder_threshold INT NOT NULL
        DEFAULT 5,

    created_at DATETIME NOT NULL
        DEFAULT CURRENT_TIMESTAMP,

    updated_at DATETIME NOT NULL
        DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,

    deleted_at DATETIME NULL,

    status VARCHAR(20) NOT NULL
        DEFAULT 'ACTIVE',

    CONSTRAINT fk_products_category

        FOREIGN KEY (category_id)

        REFERENCES categories(category_id)

        ON UPDATE CASCADE

        ON DELETE RESTRICT,

    CONSTRAINT chk_products_price

        CHECK (price >= 0),

    CONSTRAINT chk_products_stock_quantity

        CHECK (stock_quantity >= 0),

    CONSTRAINT chk_products_reorder_threshold

        CHECK (reorder_threshold >= 0),

    INDEX idx_products_category_id (category_id),

    INDEX idx_products_name (name),

    INDEX idx_products_stock_quantity (stock_quantity),

    INDEX idx_products_deleted_at (deleted_at)

);


-- =========================================================
-- 4. ORDERS
-- =========================================================

CREATE TABLE IF NOT EXISTS orders (

    order_id INT AUTO_INCREMENT PRIMARY KEY,

    customer_id INT NOT NULL,

    status VARCHAR(30) NOT NULL
        DEFAULT 'PENDING',

    order_date DATETIME NOT NULL
        DEFAULT CURRENT_TIMESTAMP,

    total_amount DECIMAL(10,2) NOT NULL
        DEFAULT 0.00,

    created_at DATETIME NOT NULL
        DEFAULT CURRENT_TIMESTAMP,

    updated_at DATETIME NOT NULL
        DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,

    CONSTRAINT fk_orders_customer

        FOREIGN KEY (customer_id)

        REFERENCES customers(customer_id)

        ON UPDATE CASCADE

        ON DELETE RESTRICT,

    CONSTRAINT chk_orders_total_amount

        CHECK (total_amount >= 0),

    INDEX idx_orders_customer_id (customer_id),

    INDEX idx_orders_status (status),

    INDEX idx_orders_order_date (order_date)

);


-- =========================================================
-- 5. ORDER_ITEMS
-- =========================================================

CREATE TABLE IF NOT EXISTS order_items (

    order_item_id INT AUTO_INCREMENT PRIMARY KEY,

    order_id INT NOT NULL,

    product_id INT NOT NULL,

    quantity INT NOT NULL,

    unit_price DECIMAL(10,2) NOT NULL,

    created_at DATETIME NOT NULL
        DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_order_items_order

        FOREIGN KEY (order_id)

        REFERENCES orders(order_id)

        ON UPDATE CASCADE

        ON DELETE CASCADE,

    CONSTRAINT fk_order_items_product

        FOREIGN KEY (product_id)

        REFERENCES products(product_id)

        ON UPDATE CASCADE

        ON DELETE RESTRICT,

    CONSTRAINT chk_order_items_quantity

        CHECK (quantity > 0),

    CONSTRAINT chk_order_items_unit_price

        CHECK (unit_price >= 0),

    INDEX idx_order_items_order_id (order_id),

    INDEX idx_order_items_product_id (product_id)

);


-- =========================================================
-- 6. ORDER_LOGS
-- =========================================================
-- Stores order status history and failure details.
-- =========================================================

CREATE TABLE IF NOT EXISTS order_logs (

    order_log_id INT AUTO_INCREMENT PRIMARY KEY,

    order_id INT NOT NULL,

    previous_status VARCHAR(30),

    new_status VARCHAR(30) NOT NULL,

    changed_by VARCHAR(150),

    note VARCHAR(500),

    created_at DATETIME NOT NULL
        DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_order_logs_order

        FOREIGN KEY (order_id)

        REFERENCES orders(order_id)

        ON UPDATE CASCADE

        ON DELETE CASCADE,

    INDEX idx_order_logs_order_id (order_id),

    INDEX idx_order_logs_created_at (created_at),

    INDEX idx_order_logs_new_status (new_status)

);


-- =========================================================
-- 7. SAMPLE CATEGORIES
-- =========================================================

INSERT INTO categories (
    name,
    description
)

SELECT
    'Electronics',
    'Electronic devices and accessories'

WHERE NOT EXISTS (

    SELECT 1

    FROM categories

    WHERE name = 'Electronics'

);


INSERT INTO categories (
    name,
    description
)

SELECT
    'Home Appliances',
    'Appliances and household equipment'

WHERE NOT EXISTS (

    SELECT 1

    FROM categories

    WHERE name = 'Home Appliances'

);


INSERT INTO categories (
    name,
    description
)

SELECT
    'Books',
    'Books and educational materials'

WHERE NOT EXISTS (

    SELECT 1

    FROM categories

    WHERE name = 'Books'

);


-- =========================================================
-- 8. DEMO / SEED DATA (5 RECORDS PER CORE TABLE)
-- =========================================================
-- Demo bearer token for every seeded customer: minni@123
-- The database stores SHA-256(token), never the plaintext token.
-- Seed rows are inserted only when their natural identifying value is absent.
-- These are test fixtures; review before using in production.
-- =========================================================

-- Categories: five demo categories
INSERT INTO categories (name, description)
SELECT 'Electronics', 'Electronic devices and accessories'
WHERE NOT EXISTS (SELECT 1 FROM categories WHERE name = 'Electronics');

INSERT INTO categories (name, description)
SELECT 'Home Appliances', 'Appliances and household equipment'
WHERE NOT EXISTS (SELECT 1 FROM categories WHERE name = 'Home Appliances');

INSERT INTO categories (name, description)
SELECT 'Books', 'Books and educational materials'
WHERE NOT EXISTS (SELECT 1 FROM categories WHERE name = 'Books');

INSERT INTO categories (name, description)
SELECT 'Accessories', 'Everyday technology and personal accessories'
WHERE NOT EXISTS (SELECT 1 FROM categories WHERE name = 'Accessories');

INSERT INTO categories (name, description)
SELECT 'Office Supplies', 'Supplies for home and office use'
WHERE NOT EXISTS (SELECT 1 FROM categories WHERE name = 'Office Supplies');


-- Customers: five demo customers, all sharing the same demo token.
-- Customer email is unique in this schema; bearer_token intentionally is not.
INSERT INTO customers (name, email, bearer_token, address, status)
SELECT 'Rahul Sharma', 'rahul.sharma@example.com', SHA2('minni@123', 256),
       'Hyderabad, Telangana', 'ACTIVE'
WHERE NOT EXISTS (SELECT 1 FROM customers WHERE email = 'rahul.sharma@example.com');

INSERT INTO customers (name, email, bearer_token, address, status)
SELECT 'Priya Reddy', 'priya.reddy@example.com', SHA2('minni@123', 256),
       'Bengaluru, Karnataka', 'ACTIVE'
WHERE NOT EXISTS (SELECT 1 FROM customers WHERE email = 'priya.reddy@example.com');

INSERT INTO customers (name, email, bearer_token, address, status)
SELECT 'Ananya Rao', 'ananya.rao@example.com', SHA2('minni@123', 256),
       'Hyderabad, Telangana', 'ACTIVE'
WHERE NOT EXISTS (SELECT 1 FROM customers WHERE email = 'ananya.rao@example.com');

INSERT INTO customers (name, email, bearer_token, address, status)
SELECT 'Vikram Kumar', 'vikram.kumar@example.com', SHA2('minni@123', 256),
       'Chennai, Tamil Nadu', 'ACTIVE'
WHERE NOT EXISTS (SELECT 1 FROM customers WHERE email = 'vikram.kumar@example.com');

INSERT INTO customers (name, email, bearer_token, address, status, deleted_at, deleted_by, delete_reason)
SELECT 'Meera Iyer', 'meera.iyer@example.com', SHA2('minni@123', 256),
       'Pune, Maharashtra', 'DELETED', CURRENT_TIMESTAMP,
       'schema-seed', 'Demonstration soft-deleted customer'
WHERE NOT EXISTS (SELECT 1 FROM customers WHERE email = 'meera.iyer@example.com');


-- Products: five demo products. One has low stock; one is soft-deleted.
-- Inventory is represented by products.stock_quantity in this schema.
INSERT INTO products (category_id, name, description, price, stock_quantity, reorder_threshold, status)
SELECT c.category_id, 'Wireless Mouse', 'Wireless optical mouse', 799.00, 25, 5, 'ACTIVE'
FROM categories c
WHERE c.name = 'Electronics'
  AND NOT EXISTS (SELECT 1 FROM products WHERE name = 'Wireless Mouse')
LIMIT 1;

INSERT INTO products (category_id, name, description, price, stock_quantity, reorder_threshold, status)
SELECT c.category_id, 'USB-C Hub', 'Multiport USB-C adapter', 1499.00, 2, 5, 'ACTIVE'
FROM categories c
WHERE c.name = 'Electronics'
  AND NOT EXISTS (SELECT 1 FROM products WHERE name = 'USB-C Hub')
LIMIT 1;

INSERT INTO products (category_id, name, description, price, stock_quantity, reorder_threshold, status)
SELECT c.category_id, 'Cloud Computing Basics', 'Introduction to cloud computing', 599.00, 12, 3, 'ACTIVE'
FROM categories c
WHERE c.name = 'Books'
  AND NOT EXISTS (SELECT 1 FROM products WHERE name = 'Cloud Computing Basics')
LIMIT 1;

INSERT INTO products (category_id, name, description, price, stock_quantity, reorder_threshold, status)
SELECT c.category_id, 'Desk Lamp', 'Adjustable LED desk lamp', 1099.00, 18, 4, 'ACTIVE'
FROM categories c
WHERE c.name = 'Home Appliances'
  AND NOT EXISTS (SELECT 1 FROM products WHERE name = 'Desk Lamp')
LIMIT 1;

INSERT INTO products (category_id, name, description, price, stock_quantity, reorder_threshold, status, deleted_at)
SELECT c.category_id, 'Notebook Set', 'Pack of three ruled notebooks', 249.00, 30, 5, 'DELETED', CURRENT_TIMESTAMP
FROM categories c
WHERE c.name = 'Office Supplies'
  AND NOT EXISTS (SELECT 1 FROM products WHERE name = 'Notebook Set')
LIMIT 1;


-- Orders: five examples covering CONFIRMED, PENDING, CANCELLED and FAILED.
-- Customer/product references are resolved by email/name rather than fixed IDs.
INSERT INTO orders (customer_id, status, total_amount)
SELECT c.customer_id, 'CONFIRMED', 799.00
FROM customers c
WHERE c.email = 'rahul.sharma@example.com'
  AND NOT EXISTS (SELECT 1 FROM orders o WHERE o.customer_id = c.customer_id AND o.total_amount = 799.00 AND o.status = 'CONFIRMED')
LIMIT 1;

INSERT INTO orders (customer_id, status, total_amount)
SELECT c.customer_id, 'PENDING', 1499.00
FROM customers c
WHERE c.email = 'priya.reddy@example.com'
  AND NOT EXISTS (SELECT 1 FROM orders o WHERE o.customer_id = c.customer_id AND o.total_amount = 1499.00 AND o.status = 'PENDING')
LIMIT 1;

INSERT INTO orders (customer_id, status, total_amount)
SELECT c.customer_id, 'CONFIRMED', 599.00
FROM customers c
WHERE c.email = 'ananya.rao@example.com'
  AND NOT EXISTS (SELECT 1 FROM orders o WHERE o.customer_id = c.customer_id AND o.total_amount = 599.00 AND o.status = 'CONFIRMED')
LIMIT 1;

INSERT INTO orders (customer_id, status, total_amount)
SELECT c.customer_id, 'CANCELLED', 1099.00
FROM customers c
WHERE c.email = 'vikram.kumar@example.com'
  AND NOT EXISTS (SELECT 1 FROM orders o WHERE o.customer_id = c.customer_id AND o.total_amount = 1099.00 AND o.status = 'CANCELLED')
LIMIT 1;

INSERT INTO orders (customer_id, status, total_amount)
SELECT c.customer_id, 'FAILED', 1499.00
FROM customers c
WHERE c.email = 'rahul.sharma@example.com'
  AND NOT EXISTS (SELECT 1 FROM orders o WHERE o.customer_id = c.customer_id AND o.total_amount = 1499.00 AND o.status = 'FAILED')
LIMIT 1;


-- Order items: one corresponding line item for each seeded example order.
INSERT INTO order_items (order_id, product_id, quantity, unit_price)
SELECT o.order_id, p.product_id, 1, p.price
FROM orders o
JOIN customers c ON c.customer_id = o.customer_id
JOIN products p ON p.name = 'Wireless Mouse'
WHERE c.email = 'rahul.sharma@example.com' AND o.status = 'CONFIRMED' AND o.total_amount = 799.00
  AND NOT EXISTS (SELECT 1 FROM order_items oi WHERE oi.order_id = o.order_id AND oi.product_id = p.product_id)
LIMIT 1;

INSERT INTO order_items (order_id, product_id, quantity, unit_price)
SELECT o.order_id, p.product_id, 1, p.price
FROM orders o
JOIN customers c ON c.customer_id = o.customer_id
JOIN products p ON p.name = 'USB-C Hub'
WHERE c.email = 'priya.reddy@example.com' AND o.status = 'PENDING' AND o.total_amount = 1499.00
  AND NOT EXISTS (SELECT 1 FROM order_items oi WHERE oi.order_id = o.order_id AND oi.product_id = p.product_id)
LIMIT 1;

INSERT INTO order_items (order_id, product_id, quantity, unit_price)
SELECT o.order_id, p.product_id, 1, p.price
FROM orders o
JOIN customers c ON c.customer_id = o.customer_id
JOIN products p ON p.name = 'Cloud Computing Basics'
WHERE c.email = 'ananya.rao@example.com' AND o.status = 'CONFIRMED' AND o.total_amount = 599.00
  AND NOT EXISTS (SELECT 1 FROM order_items oi WHERE oi.order_id = o.order_id AND oi.product_id = p.product_id)
LIMIT 1;

INSERT INTO order_items (order_id, product_id, quantity, unit_price)
SELECT o.order_id, p.product_id, 1, p.price
FROM orders o
JOIN customers c ON c.customer_id = o.customer_id
JOIN products p ON p.name = 'Desk Lamp'
WHERE c.email = 'vikram.kumar@example.com' AND o.status = 'CANCELLED' AND o.total_amount = 1099.00
  AND NOT EXISTS (SELECT 1 FROM order_items oi WHERE oi.order_id = o.order_id AND oi.product_id = p.product_id)
LIMIT 1;

INSERT INTO order_items (order_id, product_id, quantity, unit_price)
SELECT o.order_id, p.product_id, 1, p.price
FROM orders o
JOIN customers c ON c.customer_id = o.customer_id
JOIN products p ON p.name = 'USB-C Hub'
WHERE c.email = 'rahul.sharma@example.com' AND o.status = 'FAILED' AND o.total_amount = 1499.00
  AND NOT EXISTS (SELECT 1 FROM order_items oi WHERE oi.order_id = o.order_id AND oi.product_id = p.product_id)
LIMIT 1;


-- Order logs: five status-history examples, including a failure record.
INSERT INTO order_logs (order_id, previous_status, new_status, changed_by, note)
SELECT o.order_id, NULL, 'CONFIRMED', 'seed-script', 'Demonstration successful order'
FROM orders o JOIN customers c ON c.customer_id = o.customer_id
WHERE c.email = 'rahul.sharma@example.com' AND o.status = 'CONFIRMED' AND o.total_amount = 799.00
  AND NOT EXISTS (SELECT 1 FROM order_logs l WHERE l.order_id = o.order_id AND l.new_status = 'CONFIRMED' AND l.changed_by = 'seed-script')
LIMIT 1;

INSERT INTO order_logs (order_id, previous_status, new_status, changed_by, note)
SELECT o.order_id, NULL, 'PENDING', 'seed-script', 'Demonstration pending order'
FROM orders o JOIN customers c ON c.customer_id = o.customer_id
WHERE c.email = 'priya.reddy@example.com' AND o.status = 'PENDING' AND o.total_amount = 1499.00
  AND NOT EXISTS (SELECT 1 FROM order_logs l WHERE l.order_id = o.order_id AND l.new_status = 'PENDING' AND l.changed_by = 'seed-script')
LIMIT 1;

INSERT INTO order_logs (order_id, previous_status, new_status, changed_by, note)
SELECT o.order_id, NULL, 'CONFIRMED', 'seed-script', 'Demonstration confirmed order'
FROM orders o JOIN customers c ON c.customer_id = o.customer_id
WHERE c.email = 'ananya.rao@example.com' AND o.status = 'CONFIRMED' AND o.total_amount = 599.00
  AND NOT EXISTS (SELECT 1 FROM order_logs l WHERE l.order_id = o.order_id AND l.new_status = 'CONFIRMED' AND l.changed_by = 'seed-script')
LIMIT 1;

INSERT INTO order_logs (order_id, previous_status, new_status, changed_by, note)
SELECT o.order_id, 'PENDING', 'CANCELLED', 'seed-script', 'Demonstration cancellation history'
FROM orders o JOIN customers c ON c.customer_id = o.customer_id
WHERE c.email = 'vikram.kumar@example.com' AND o.status = 'CANCELLED' AND o.total_amount = 1099.00
  AND NOT EXISTS (SELECT 1 FROM order_logs l WHERE l.order_id = o.order_id AND l.new_status = 'CANCELLED' AND l.changed_by = 'seed-script')
LIMIT 1;

INSERT INTO order_logs (order_id, previous_status, new_status, changed_by, note)
SELECT o.order_id, 'PENDING', 'FAILED', 'seed-script', 'Demonstration failed order for failure-path testing'
FROM orders o JOIN customers c ON c.customer_id = o.customer_id
WHERE c.email = 'rahul.sharma@example.com' AND o.status = 'FAILED' AND o.total_amount = 1499.00
  AND NOT EXISTS (SELECT 1 FROM order_logs l WHERE l.order_id = o.order_id AND l.new_status = 'FAILED' AND l.changed_by = 'seed-script')
LIMIT 1;


-- =========================================================
-- 9. CRUD EXAMPLES (COMMENTED; DO NOT RUN DURING INITIALIZATION)
-- =========================================================
-- These are SQL illustrations for direct database testing. Normal app
-- operations should go through API Gateway/Lambda so authorization,
-- validation, inventory handling, events, and audit logic are applied.
-- Physical DELETE is intentionally avoided for customers/products/orders.
--
-- CREATE examples:
-- INSERT INTO categories (name, description) VALUES ('Demo Category', 'Test');
-- INSERT INTO customers (name,email,bearer_token,address)
--   VALUES ('Demo User','demo@example.com',SHA2('minni@123',256),'Hyderabad');
-- INSERT INTO products (category_id,name,description,price,stock_quantity,reorder_threshold)
--   VALUES (1,'Demo Product','Test product',100.00,10,2);
-- INSERT INTO orders (customer_id,status,total_amount) VALUES (1,'PENDING',100.00);
-- INSERT INTO order_items (order_id,product_id,quantity,unit_price) VALUES (1,1,1,100.00);
-- INSERT INTO order_logs (order_id,previous_status,new_status,changed_by,note)
--   VALUES (1,NULL,'PENDING','manual-test','Created for testing');
--
-- READ examples:
-- SELECT * FROM categories;
-- SELECT customer_id,name,email,status,deleted_at FROM customers;
-- SELECT * FROM products WHERE deleted_at IS NULL;
-- SELECT * FROM products WHERE stock_quantity <= reorder_threshold AND deleted_at IS NULL;
-- SELECT * FROM orders WHERE status = 'FAILED';
-- SELECT * FROM order_items WHERE order_id = 1;
-- SELECT * FROM order_logs WHERE order_id = 1 ORDER BY created_at;
--
-- UPDATE examples:
-- UPDATE categories SET description='Updated description' WHERE category_id=1;
-- UPDATE customers SET address='Updated address' WHERE customer_id=1 AND deleted_at IS NULL;
-- UPDATE products SET price=120.00 WHERE product_id=1 AND deleted_at IS NULL;
-- UPDATE products SET stock_quantity=2 WHERE product_id=1 AND deleted_at IS NULL;
-- UPDATE orders SET status='CONFIRMED' WHERE order_id=1 AND status='PENDING';
-- INSERT INTO order_logs (order_id,previous_status,new_status,changed_by,note)
--   VALUES (1,'PENDING','CONFIRMED','manual-test','Status changed in test');
--
-- SOFT DELETE / RESTORE examples:
-- UPDATE customers SET status='DELETED', deleted_at=CURRENT_TIMESTAMP,
--   deleted_by='manual-test', delete_reason='Test soft delete'
--   WHERE customer_id=5 AND deleted_at IS NULL;
-- UPDATE customers SET status='ACTIVE', deleted_at=NULL, deleted_by=NULL,
--   delete_reason=NULL WHERE customer_id=5;
-- UPDATE products SET status='DELETED', deleted_at=CURRENT_TIMESTAMP
--   WHERE product_id=5 AND deleted_at IS NULL;
-- UPDATE products SET status='ACTIVE', deleted_at=NULL
--   WHERE product_id=5;
--
-- Order status changes should follow application authorization/ownership rules.
-- In particular, customer cancellation must be limited to the owning customer;
-- do not use direct SQL to bypass the Lambda's checks. The schema has no
-- dedicated low_stock table: low-stock is represented by products where
-- stock_quantity <= reorder_threshold, and the application emits the event.
-- The schema has no separate order-failure table: failures are represented by
-- orders.status='FAILED' and their history in order_logs.
--
-- =========================================================
-- 10. DATABASE VERIFICATION
-- =========================================================
SELECT 'TABLES' AS section;
SHOW TABLES;

SELECT 'CUSTOMER DATA' AS section;
SELECT customer_id, name, email, LENGTH(bearer_token) AS token_hash_length,
       address, status, deleted_at, deleted_by, delete_reason
FROM customers ORDER BY customer_id;

SELECT 'PRODUCT DATA' AS section;
SELECT product_id, category_id, name, price, stock_quantity, reorder_threshold,
       status, deleted_at
FROM products ORDER BY product_id;

SELECT 'LOW STOCK PRODUCTS' AS section;
SELECT product_id, name, stock_quantity, reorder_threshold
FROM products
WHERE deleted_at IS NULL AND status = 'ACTIVE'
  AND stock_quantity <= reorder_threshold
ORDER BY product_id;

SELECT 'ORDER DATA INCLUDING FAILED' AS section;
SELECT order_id, customer_id, status, total_amount, order_date
FROM orders ORDER BY order_id;

SELECT 'ORDER ITEMS' AS section;
SELECT order_item_id, order_id, product_id, quantity, unit_price
FROM order_items ORDER BY order_item_id;

SELECT 'ORDER LOGS' AS section;
SELECT order_log_id, order_id, previous_status, new_status, changed_by, note, created_at
FROM order_logs ORDER BY order_log_id;
