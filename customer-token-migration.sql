UPDATE customers
SET bearer_token = 'CUSTOMER_TEST_001',
    status = 'ACTIVE',
    deleted_at = NULL
WHERE email = 'testcustomer@gmail.com';

SELECT customer_id, name, email, bearer_token, status, deleted_at
FROM customers
WHERE email = 'testcustomer@gmail.com';
