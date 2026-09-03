-- =========================================================
-- CLOUDMART RDS MYSQL DATABASE SCHEMA
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
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP
);


-- =========================================================
-- 2. CUSTOMERS
-- =========================================================

CREATE TABLE IF NOT EXISTS customers (
    customer_id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(150) NOT NULL,
    email VARCHAR(255) NOT NULL,
    address VARCHAR(500),
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,
    deleted_at DATETIME NULL,
    deleted_by VARCHAR(150) NULL,
    delete_reason VARCHAR(500) NULL,

    CONSTRAINT uq_customers_email
        UNIQUE (email),

    INDEX idx_customers_deleted_at (deleted_at)
);


-- =========================================================
-- 3. PRODUCTS
-- =========================================================
-- Inventory is maintained directly in this table through
-- stock_quantity and reorder_threshold.
-- No separate inventory table is required.
-- =========================================================

CREATE TABLE IF NOT EXISTS products (
    product_id INT AUTO_INCREMENT PRIMARY KEY,
    category_id INT NOT NULL,
    name VARCHAR(150) NOT NULL,
    description VARCHAR(500),
    price DECIMAL(10,2) NOT NULL,
    stock_quantity INT NOT NULL DEFAULT 0,
    reorder_threshold INT NOT NULL DEFAULT 5,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,
    deleted_at DATETIME NULL,

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
        CHECK (reorder_threshold >= 0)
);


-- =========================================================
-- 4. ORDERS
-- =========================================================

CREATE TABLE IF NOT EXISTS orders (
    order_id INT AUTO_INCREMENT PRIMARY KEY,
    customer_id INT NOT NULL,
    status VARCHAR(30) NOT NULL DEFAULT 'PENDING',
    order_date DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    total_amount DECIMAL(10,2) NOT NULL DEFAULT 0.00,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,

    CONSTRAINT fk_orders_customer
        FOREIGN KEY (customer_id)
        REFERENCES customers(customer_id)
        ON UPDATE CASCADE
        ON DELETE RESTRICT,

    CONSTRAINT chk_orders_total_amount
        CHECK (total_amount >= 0)
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
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,

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
        CHECK (unit_price >= 0)
);


-- =========================================================
-- 6. ORDER_LOGS
-- =========================================================

CREATE TABLE IF NOT EXISTS order_logs (
    order_log_id INT AUTO_INCREMENT PRIMARY KEY,
    order_id INT NOT NULL,
    previous_status VARCHAR(30),
    new_status VARCHAR(30) NOT NULL,
    changed_by VARCHAR(150),
    note VARCHAR(500),
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_order_logs_order
        FOREIGN KEY (order_id)
        REFERENCES orders(order_id)
        ON UPDATE CASCADE
        ON DELETE CASCADE
);


-- =========================================================
-- INDEXES
-- =========================================================

-- Categories

CREATE INDEX idx_categories_name
    ON categories(name);


-- Customers

CREATE INDEX idx_customers_name
    ON customers(name);


-- Products

CREATE INDEX idx_products_category_id
    ON products(category_id);

CREATE INDEX idx_products_name
    ON products(name);

CREATE INDEX idx_products_stock_quantity
    ON products(stock_quantity);


-- Orders

CREATE INDEX idx_orders_customer_id
    ON orders(customer_id);

CREATE INDEX idx_orders_status
    ON orders(status);

CREATE INDEX idx_orders_order_date
    ON orders(order_date);


-- Order Items

CREATE INDEX idx_order_items_order_id
    ON order_items(order_id);

CREATE INDEX idx_order_items_product_id
    ON order_items(product_id);


-- Order Logs

CREATE INDEX idx_order_logs_order_id
    ON order_logs(order_id);

CREATE INDEX idx_order_logs_created_at
    ON order_logs(created_at);

CREATE INDEX idx_order_logs_new_status
    ON order_logs(new_status);


-- =========================================================
-- SAMPLE DATA
-- =========================================================

-- =========================================================
-- SAMPLE CATEGORIES
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
-- SAMPLE CUSTOMERS
-- =========================================================

INSERT INTO customers (
    name,
    email,
    address
)
SELECT
    'Rahul Sharma',
    'rahul.sharma@example.com',
    'Hyderabad, Telangana'
WHERE NOT EXISTS (
    SELECT 1
    FROM customers
    WHERE email = 'rahul.sharma@example.com'
);


INSERT INTO customers (
    name,
    email,
    address
)
SELECT
    'Priya Reddy',
    'priya.reddy@example.com',
    'Bengaluru, Karnataka'
WHERE NOT EXISTS (
    SELECT 1
    FROM customers
    WHERE email = 'priya.reddy@example.com'
);


-- =========================================================
-- SAMPLE PRODUCTS
-- =========================================================
-- stock_quantity represents current inventory.
-- reorder_threshold determines when a low-stock alert
-- should be generated.
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
    'Bluetooth Keyboard',
    'Wireless Bluetooth keyboard',
    1499.00,
    15,
    5
FROM categories
WHERE name = 'Electronics'
  AND NOT EXISTS (
      SELECT 1
      FROM products
      WHERE name = 'Bluetooth Keyboard'
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
    'Air Fryer',
    'Digital air fryer for home use',
    4999.00,
    8,
    5
FROM categories
WHERE name = 'Home Appliances'
  AND NOT EXISTS (
      SELECT 1
      FROM products
      WHERE name = 'Air Fryer'
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
-- SAMPLE ORDER
-- =========================================================

INSERT INTO orders (
    customer_id,
    status,
    total_amount
)
SELECT
    customer_id,
    'PENDING',
    1598.00
FROM customers
WHERE email = 'rahul.sharma@example.com'
  AND NOT EXISTS (
      SELECT 1
      FROM orders o
      JOIN customers c
        ON o.customer_id = c.customer_id
      WHERE c.email = 'rahul.sharma@example.com'
        AND o.total_amount = 1598.00
  )
LIMIT 1;


-- =========================================================
-- SAMPLE ORDER ITEMS
-- =========================================================

INSERT INTO order_items (
    order_id,
    product_id,
    quantity,
    unit_price
)
SELECT
    o.order_id,
    p.product_id,
    2,
    p.price
FROM orders o
JOIN customers c
    ON o.customer_id = c.customer_id
JOIN products p
    ON p.name = 'Wireless Mouse'
WHERE c.email = 'rahul.sharma@example.com'
  AND o.status = 'PENDING'
  AND o.total_amount = 1598.00
  AND NOT EXISTS (
      SELECT 1
      FROM order_items oi
      WHERE oi.order_id = o.order_id
        AND oi.product_id = p.product_id
  )
LIMIT 1;


-- =========================================================
-- SAMPLE ORDER LOG
-- =========================================================

INSERT INTO order_logs (
    order_id,
    previous_status,
    new_status,
    changed_by,
    note
)
SELECT
    o.order_id,
    NULL,
    'PENDING',
    'system',
    'Sample order created'
FROM orders o
JOIN customers c
    ON o.customer_id = c.customer_id
WHERE c.email = 'rahul.sharma@example.com'
  AND o.status = 'PENDING'
  AND o.total_amount = 1598.00
  AND NOT EXISTS (
      SELECT 1
      FROM order_logs ol
      WHERE ol.order_id = o.order_id
        AND ol.new_status = 'PENDING'
        AND ol.changed_by = 'system'
  )
LIMIT 1;


-- =========================================================
-- VERIFICATION
-- =========================================================

SELECT 'TABLES' AS section;

SHOW TABLES;


SELECT
    'PRODUCT SAMPLE DATA' AS section;

SELECT
    product_id,
    category_id,
    name,
    price,
    stock_quantity,
    reorder_threshold
FROM products
ORDER BY product_id;


SELECT
    'CUSTOMER SAMPLE DATA' AS section;

SELECT
    customer_id,
    name,
    email,
    address
FROM customers
ORDER BY customer_id;


SELECT
    'ORDER SAMPLE DATA' AS section;

SELECT
    order_id,
    customer_id,
    status,
    total_amount,
    order_date
FROM orders
ORDER BY order_id;


SELECT
    'ORDER ITEM SAMPLE DATA' AS section;

SELECT
    order_item_id,
    order_id,
    product_id,
    quantity,
    unit_price
FROM order_items
ORDER BY order_item_id;


SELECT
    'ORDER LOG SAMPLE DATA' AS section;

SELECT
    order_log_id,
    order_id,
    previous_status,
    new_status,
    changed_by,
    note
FROM order_logs
ORDER BY order_log_id;