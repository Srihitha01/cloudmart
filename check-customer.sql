SELECT customer_id, name, email, bearer_token, status, deleted_at
FROM customers
WHERE email = 'testcustomer@gmail.com';
