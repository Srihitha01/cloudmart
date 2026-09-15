UPDATE customers
SET bearer_token = 'CUSTOMER_TEST_001',
    status = 'ACTIVE',
    deleted_at = NULL
WHERE email = 'testcustomer@gmail.com';
