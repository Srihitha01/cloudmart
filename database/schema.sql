
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
-- 8. SAMPLE CUSTOMERS
-- =========================================================
-- Sample tokens are hashed using MySQL SHA2().
-- These are only sample records for testing.
-- =========================================================

INSERT INTO customers (

    name,

    email,

    bearer_token,

    address

)

SELECT

    'Rahul Sharma',

    'rahul.sharma@example.com',

    SHA2('rahul-sample-token', 256),

    'Hyderabad, Telangana'

WHERE NOT EXISTS (

    SELECT 1

    FROM customers

    WHERE email = 'rahul.sharma@example.com'

);


INSERT INTO customers (

    name,

    email,

    bearer_token,

    address

)

SELECT

    'Priya Reddy',

    'priya.reddy@example.com',

    SHA2('priya-sample-token', 256),

    'Bengaluru, Karnataka'

WHERE NOT EXISTS (

    SELECT 1

    FROM customers

    WHERE email = 'priya.reddy@example.com'

);


-- =========================================================
-- 9. SAMPLE PRODUCTS
-- =========================================================

INSERT INTO products (

    category_id,

    name,

    description,

    price,

    stock_quantity,

    reorder_threshold

)

SELECT

    category_id,

    'Wireless Mouse',

    'Wireless optical mouse',

    799.00,

    25,

    5

FROM categories

WHERE name = 'Electronics'

  AND NOT EXISTS (

      SELECT 1

      FROM products

      WHERE name = 'Wireless Mouse'

  )

LIMIT 1;


INSERT INTO products (

    category_id,

    name,

    description,

    price,

    stock_quantity,

    reorder_threshold

)

SELECT

    category_id,

    'Cloud Computing Basics',

    'Introduction to cloud computing',

    599.00,

    12,

    3

FROM categories

WHERE name = 'Books'

  AND NOT EXISTS (

      SELECT 1

      FROM products

      WHERE name = 'Cloud Computing Basics'

  )

LIMIT 1;


-- =========================================================
-- 10. DATABASE VERIFICATION
-- =========================================================

SELECT
    'TABLES' AS section;

SHOW TABLES;


SELECT
    'CUSTOMER TABLE STRUCTURE' AS section;

DESCRIBE customers;


SELECT
    'CUSTOMER INDEXES' AS section;

SHOW INDEX FROM customers;


SELECT
    'PRODUCT TABLE STRUCTURE' AS section;

DESCRIBE products;


SELECT
    'ORDER TABLE STRUCTURE' AS section;

DESCRIBE orders;


SELECT
    'ORDER ITEMS TABLE STRUCTURE' AS section;

DESCRIBE order_items;


SELECT
    'ORDER LOGS TABLE STRUCTURE' AS section;

DESCRIBE order_logs;


SELECT
    'CUSTOMER DATA' AS section;

SELECT

    customer_id,

    name,

    email,

    LENGTH(bearer_token) AS token_hash_length,

    address,

    status,

    deleted_at,

    deleted_by,

    delete_reason

FROM customers

ORDER BY customer_id;


SELECT
    'PRODUCT DATA' AS section;

SELECT

    product_id,

    category_id,

    name,

    price,

    stock_quantity,

    reorder_threshold,

    status,

    deleted_at

FROM products

ORDER BY product_id;


SELECT
    'ORDER DATA' AS section;

SELECT

    order_id,

    customer_id,

    status,

    total_amount,

    order_date

FROM orders

ORDER BY order_id;


SELECT
    'ORDER ITEM DATA' AS section;

SELECT

    order_item_id,

    order_id,

    product_id,

    quantity,

    unit_price

FROM order_items

ORDER BY order_item_id;


SELECT
    'ORDER LOG DATA' AS section;

SELECT

    order_log_id,

    order_id,

    previous_status,

    new_status,

    changed_by,

    note,

    created_at

FROM order_logs

ORDER BY order_log_id;