CloudMart
CloudMart is an AWS-hosted e-commerce application built with CloudFormation-managed infrastructure and a repeatable GitHub Actions deployment workflow.
The application exposes REST APIs through Amazon API Gateway, runs business logic in AWS Lambda, stores application data in Amazon RDS for MySQL, uses Amazon S3 for deployment artifacts and generated reports, uses AWS Systems Manager Parameter Store for runtime configuration and protected values, routes application events through Amazon EventBridge, sends notifications through Amazon SNS, and provides operational visibility through CloudWatch and an EC2-hosted Flask dashboard.
Deployment model: GitHub Actions + GitHub OIDC + AWS IAM deployment role + AWS CloudFormation
AWS Region: ap-south-1
Supported environments: dev, prod
Platform exclusions: no DynamoDB, no SQS, and no NAT Gateway are used in the approved CloudMart setup.

1. Project Objectives
CloudMart is designed to demonstrate an end-to-end AWS e-commerce platform with:
- Infrastructure as Code using AWS CloudFormation.
- CI/CD using GitHub Actions and AWS OIDC.
- A REST API exposed through Amazon API Gateway.
- Lambda-based product, customer, order, authorization, schema, and reporting services.
- MySQL persistence in Amazon RDS.
- Secure runtime configuration through AWS Systems Manager Parameter Store.
- Event-driven notifications through EventBridge and SNS.
- Daily CSV reporting to Amazon S3.
- Operational visibility through CloudWatch and a Flask dashboard running on EC2.
- Repeatable deployment, verification, troubleshooting, recovery, and teardown procedures.
2. CloudFormation Stack Model
CloudMart uses five CloudFormation stacks and deploys them in dependency order.
Order	Stack	Template	Responsibility
1	cloudmart-network-security-{environment}	cloudformation/network-stack.yaml	VPC, subnets, routes, security groups, VPC endpoints
2	cloudmart-data-storage-{environment}	cloudformation/data-stack.yaml	RDS MySQL, DB subnet group, S3 buckets, DB SSM parameters
3	cloudmart-iam-{environment}	cloudformation/iam-stack.yaml	Runtime IAM roles, policies, instance profiles, authentication parameter provisioning
4	cloudmart-application-events-{environment}	cloudformation/application-events-stack.yaml	Lambda functions, PyMySQL layer, schema initialization, EventBridge, SNS
5	cloudmart-api-monitoring-ec2-{environment}	cloudformation/api-monitoring-ec2-stack.yaml	API Gateway, Lambda Authorizer integration, CloudWatch, EC2 dashboard


Why the order matters
- The network stack creates the VPC resources consumed by later stacks.
- The data stack creates RDS, S3 buckets, and DB configuration parameters used by application services.
- The IAM stack creates runtime roles and permissions used by Lambda and EC2.
- The application-events stack creates the Lambda and event/notification layer.
- The API-monitoring-EC2 stack creates API Gateway, monitoring, and the operational dashboard.
3. AWS Resource Map
4.1 Network layer
The network stack creates:
- VPC with default CIDR 10.0.0.0/16.
- One public subnet for the dashboard EC2 instance.
- Two private subnets for private workloads and RDS placement.
- Internet Gateway and public routing.
- Private routing without a NAT Gateway.
- Lambda, RDS, dashboard EC2, and VPC endpoint security groups.
- VPC endpoints for services required by the private workloads, including S3, SSM, EventBridge, SNS, and Lambda-related connectivity.
4.2 Data layer
The data stack creates:
- Amazon RDS for MySQL.
- RDS DB subnet group across the private subnets.
- CloudMart artifact S3 bucket.
- CloudMart report S3 bucket.
- SSM parameters for database endpoint, name, port, username, and protected password.
- Secure-parameter provisioning for the database password.
4.3 IAM and authentication layer
Separate runtime IAM roles are used for the Lambda services and EC2 dashboard. The runtime roles provide only the AWS permissions needed by the specific service.
The authentication layer includes:
- Lambda Authorizer runtime role.
- Customer authentication using SHA-256 bearer-token hashes stored in RDS.
- Administrator authentication using a separate SSM SecureString token.
- Customer records supporting soft deletion through deleted_at and status.
4.4 Application layer
The Application-Events stack creates:
Component	Responsibility
Lambda Authorizer	Authorizes protected API requests
Product Lambda	Product CRUD and inventory operations
Customer Lambda	Customer onboarding and customer management
Order Lambda	Order API logic and customer-order access rules
Order Processor Lambda	Order confirmation and database inventory/order processing
Schema Initializer Lambda	Applies the database schema to RDS
Daily Report Lambda	Generates the daily CSV report and writes it to S3
PyMySQL Layer	Supplies MySQL client dependencies to database Lambdas
EventBridge	Routes application events
SNS	Sends configured notifications


4.5 API, monitoring, and dashboard layer
The API-monitoring stack creates:
- Regional Amazon API Gateway REST API.
- TOKEN-type API Gateway Lambda Authorizer.
- Lambda integrations for product, customer, and order APIs.
- API Gateway access logging.
- CloudWatch dashboard and alarms.
- CloudFormation-managed EC2 instance for the Flask operations dashboard.
- Nginx/Gunicorn-based dashboard serving model where configured.
- SSM-managed dashboard refresh through the deployment workflow.
4. Repository Structure
cloudmart/
├── .github/
│   └── workflows/
│       └── deploy.yaml
│
├── config/
│   └── config.json
│
├── cloudformation/
│   ├── network-stack.yaml
│   ├── network-parameters.json
│   ├── data-stack.yaml
│   ├── iam-stack.yaml
│   ├── application-events-stack.yaml
│   └── api-monitoring-ec2-stack.yaml
│
├── database/
│   └── schema.sql
│
├── lambda/
│   ├── authorizer/
│   │   └── lambda_function.py
│   ├── product/
│   │   └── lambda_function.py
│   ├── customer/
│   │   └── lambda_function.py
│   ├── order/
│   │   └── lambda_function.py
│   ├── order-processor/
│   │   └── lambda_function.py
│   └── daily-report/
│       └── lambda_function.py
│
├── dashboard/
│   ├── app.py
│   ├── requirements.txt
│   └── bootstrap-dashboard.sh
│
├── readme/
│   ├── README.md
│   ├── deployment-runbook.md
│   └── ...
│
└── README.md
The exact repository contents should always be verified against the deployed commit before a release is approved.
5. Authentication and Authorization
6.1 API Gateway Lambda Authorizer
Protected API routes use an API Gateway TOKEN authorizer. The identity source is the Authorization header.
The authorization flow is:
Client
  |
  | Authorization: Bearer <token>
  v
API Gateway
  |
  v
Lambda Authorizer
  |
  | validate configured authentication data
  v
API method allowed / denied
  |
  v
Application Lambda
6.2 Customer bearer tokens
The customer authentication design has the following rules:
- Customer tokens are SHA-256 hashed before being stored in RDS.
- The database stores the 64-character hexadecimal hash.
- The bearer_token value is intentionally not unique.
- customer_id is the customer primary key.
- A customer's token is intended to remain stable across logins.
- Multiple active customers may use the same bearer token.
- Customer-scoped handlers use the customer identity and route customer_id together when authorizing access.
- Soft-deleted/inactive customers must not authenticate to protected resources.
6.3 Administrator authentication
The administrator token is stored separately as an SSM SecureString parameter under the CloudMart environment path.
The administrator token must not be committed to the repository or exposed in logs.
6. Database Design
The primary application database is MySQL on Amazon RDS.
Tables
Table	Purpose
categories	Product categories
customers	Customer identity and authentication metadata
products	Product data and inventory
orders	Order header and status
order_items	Products and quantities belonging to an order
order_logs	Order status history and failure/transition details


Main relationships
categories
    |
    +------< products
                |
                +------< order_items >------ orders >------ customers
Important data rules
- customers.customer_id is the primary key.
- customers.email is unique.
- customers.bearer_token stores a SHA-256 hash and is not unique.
- Customers support soft deletion.
- Products maintain their own stock_quantity and reorder_threshold.
- Products support soft deletion.
- Orders reference customers through customer_id.
- Order items reference both orders and products.
- Order logs preserve order status history.
7. API Documentation
The API is a regional REST API exposed through Amazon API Gateway. API methods use AWS Lambda proxy integrations.
8.1 Products
Method	Route	Purpose	Authorization
GET	/products	List products	Public in the current design
POST	/products	Create product	Protected
GET	/products/{id}	Read product	Protected
PUT	/products/{id}	Update product	Protected
DELETE	/products/{id}	Soft-delete product	Protected


The current architecture intentionally allows the product listing endpoint to be accessed without a bearer token.
8.2 Customers
Method	Route	Purpose	Authorization
POST	/customers	Create customer	Public onboarding
GET	/customers	Administrative customer listing	Protected
GET	/customers/{customer_id}	Read customer	Protected
PUT	/customers/{customer_id}	Update customer	Protected
PATCH	/customers/{customer_id}	Partial update	Protected
DELETE	/customers/{customer_id}	Soft delete customer	Protected / role dependent


8.3 Customer orders
Method	Route	Purpose	Authorization
POST	/customers/{customer_id}/orders	Place order	Customer protected
GET	/customers/{customer_id}/orders	List customer's orders	Customer protected
GET	/customers/{customer_id}/orders/{order_id}	Read one customer order	Customer protected
PUT	/customers/{customer_id}/orders/{order_id}	Update configured order fields	Customer protected
PATCH	/customers/{customer_id}/orders/{order_id}/status	Change order status	Role/ownership rules apply


8.4 Administrative order routes
Method	Route	Purpose	Authorization
GET	/orders	List orders	Admin/protected
GET	/orders/{id}	Read order	Admin/protected
PATCH	/orders/{id}/status	Administrative status update	Admin/protected


Authorization rules for orders
- A customer may access only their own customer-scoped routes.
- A customer cannot read another customer's order.
- A customer may cancel only their own order according to the application's status rules.
- The administrator/owner must not cancel customer orders where that restriction is enforced by the current application logic.
- The deployed Lambda handlers remain the authority for detailed validation and payload behavior.
8. Order Processing Flow
CloudMart uses synchronous order processing through the Order Lambda and Order Processor Lambda.
POST /customers/{customer_id}/orders
              |
              v
        Order Lambda
              |
              | validate customer identity
              | validate items
              v
     Order Processor Lambda
              |
              | validate stock
              | create/update order data
              | update inventory
              | write order items/logs
              v
           RDS MySQL
              |
              v
      CONFIRMED / failure result
              |
              v
        Order Lambda response
The normal successful order path returns CONFIRMED.
The order request uses an items collection. Each item contains product_id and quantity; the current order Lambda validates that the list is non-empty, quantities are positive, product IDs are valid integers, and duplicate product IDs are not allowed.
9. EventBridge and Notifications
CloudMart uses an EventBridge event bus to route application events.
Typical application event sources include:
- Product/inventory changes.
- Order placement and order processing.
- Order failures.
- Other configured application events from the deployed Lambda functions.
SNS topics and EventBridge rules are provisioned by CloudFormation.
Example order event shape
The order service publishes event details containing values such as:
{
  "order_id": 123,
  "customer_id": 10,
  "status": "CONFIRMED",
  "total_amount": "499.00"
}
Additional information such as reason and order items may be included for relevant events.
Inventory event
Inventory changes can publish an event with fields such as:
{
  "product_id": 101,
  "old_stock": 10,
  "new_stock": 4
}
The deployed EventBridge rules determine which events are routed to configured SNS notification targets.
10. Daily Reporting
The Daily Report Lambda reads application data from RDS and generates a CSV report.
The report contains:
- Report generation time.
- Product inventory information.
- Recent orders.
- Order status and amount information.
Reports are written to the CloudMart report S3 bucket under a path similar to:
 daily-reports/cloudmart-daily-report-YYYY-MM-DD.csv
The dashboard reads the private report objects and uses temporary presigned URLs where configured for secure report access.
11. Operations Dashboard
The Flask dashboard runs on the CloudFormation-managed EC2 instance.
The dashboard is intended for operational visibility rather than customer-facing commerce.
Dashboard capabilities
- Overview of products, customers, and orders.
- Current inventory visibility.
- Low-stock visibility based on stock_quantity <= reorder_threshold.
- Failed-order visibility.
- Order status distribution.
- Inventory value and configured inventory budget visibility where enabled.
- Product, order, and customer search/navigation.
- Product and order detail views.
- Event history view.
- Daily report calendar and report access.
- Link to the CloudWatch monitoring dashboard where configured.
- Health endpoint for operational checks.
The deployment workflow refreshes the existing CloudFormation-managed EC2 instance through SSM rather than creating a duplicate dashboard instance manually.
12. Monitoring and Logging
CloudMart uses CloudWatch for operational monitoring.
The monitoring layer includes:
- Lambda logging.
- API Gateway access logs.
- CloudWatch dashboard resources.
- Configured alarms.
- SNS alarm actions where configured.
- Application metrics and failure visibility.
Logging principles
Application logs should provide enough context to troubleshoot:
- Request failures.
- Database connectivity problems.
- SSM configuration failures.
- Order processing errors.
- Event publication failures.
- Dashboard/runtime problems.
Do not place bearer tokens, passwords, customer secrets, or other credentials in logs or deployment evidence.
13. Configuration and Secrets
Configuration source
config/config.json is the workflow configuration source for environment/project metadata used by the deployment process.
GitHub secrets
Secret / input	Purpose
AWS_ROLE_ARN	IAM role assumed by GitHub Actions through OIDC
CLOUDMART_ALERT_EMAIL	SNS notification recipient configuration
db_password workflow input	RDS password supplied at deployment time


Runtime configuration in SSM
Database and authentication runtime values are stored under the CloudMart environment path, for example:
/cloudmart/{environment}/db/endpoint
/cloudmart/{environment}/db/name
/cloudmart/{environment}/db/port
/cloudmart/{environment}/db/username
/cloudmart/{environment}/db/password
/cloudmart/{environment}/auth/token
Secret values are stored as SecureString where configured.
Never commit passwords, bearer tokens, AWS credentials, or account-specific secrets to Git.

14. CI/CD Workflow
The deployment workflow is:
Manual workflow_dispatch
        |
        v
Validate config and source files
        |
        v
Authenticate through GitHub OIDC
        |
        v
Assume AWS deployment role
        |
        v
Deploy Network stack
        |
        v
Deploy Data stack
        |
        v
Deploy IAM stack
        |
        v
Package/upload application artifacts
        |
        v
Deploy Application-Events stack
        |
        v
Deploy API-Monitoring-EC2 stack
        |
        v
Refresh dashboard through SSM
        |
        v
Run post-deployment verification
        |
        v
Record evidence and approve release
The workflow should be run from a reviewed commit and should record:
- GitHub Actions run URL/ID.
- Commit SHA.
- AWS account and region.
- Environment.
- CloudFormation stack statuses.
- Stack outputs.
- API and dashboard URLs.
- Verification results.
15. Deployment
The full deployment procedure is documented in:
[`readme/deployment-runbook.md`](deployment-runbook.md)
The runbook covers:
- Preflight checks.
- GitHub OIDC and IAM prerequisites.
- Five-stack deployment order.
- Artifact upload.
- Database initialization.
- API verification.
- Authentication tests.
- CRUD verification.
- Order verification.
- EventBridge/SNS/report verification.
- Dashboard verification.
- CloudWatch verification.
- Troubleshooting.
- Rollback and recovery.
- Teardown.
- Evidence and release sign-off.
16. Verification Checklist
Infrastructure
- [ ] GitHub Actions authenticated through OIDC.
- [ ] Network stack is CREATE_COMPLETE or UPDATE_COMPLETE.
- [ ] Data stack is CREATE_COMPLETE or UPDATE_COMPLETE.
- [ ] IAM stack is CREATE_COMPLETE or UPDATE_COMPLETE.
- [ ] Application-Events stack is CREATE_COMPLETE or UPDATE_COMPLETE.
- [ ] API-Monitoring-EC2 stack is CREATE_COMPLETE or UPDATE_COMPLETE.
Database
- [ ] RDS is available.
- [ ] Required SSM database parameters exist.
- [ ] Database schema initialization succeeded.
- [ ] Expected tables exist.
API and authentication
- [ ] GET /products works without a bearer token according to the current public endpoint design.
- [ ] Protected routes reject missing authentication.
- [ ] Invalid bearer tokens are rejected.
- [ ] Valid customer access is accepted where authorized.
- [ ] Cross-customer access is rejected.
- [ ] Soft-deleted/inactive customers cannot access protected customer resources.
CRUD and orders
- [ ] Product CRUD verified.
- [ ] Customer CRUD/soft delete verified.
- [ ] Customer order creation verified.
- [ ] Customer order listing/read verified.
- [ ] Order ownership restrictions verified.
- [ ] Normal successful order path returns CONFIRMED.
- [ ] Inventory deduction/stock behavior verified.
Events, notifications, and reporting
- [ ] EventBridge rules are enabled.
- [ ] SNS subscriptions are configured/confirmed as required.
- [ ] Controlled low-stock/event notification tested.
- [ ] Daily report Lambda succeeds.
- [ ] Daily CSV report appears in the report bucket.
Dashboard and monitoring
- [ ] EC2 is SSM managed.
- [ ] Dashboard health check succeeds.
- [ ] Dashboard shows current inventory and recent orders.
- [ ] Search/detail navigation works.
- [ ] Report access works.
- [ ] CloudWatch dashboard is available.
- [ ] Required alarms exist and have expected actions.
Security and release acceptance
- [ ] No secrets are committed.
- [ ] No bearer tokens are exposed in evidence.
- [ ] CloudFormation drift is reviewed.
- [ ] Open risks are documented and accepted or resolved.
- [ ] Reviewer approval is recorded.
17. Troubleshooting Guide
Problem	First checks
GitHub OIDC failure	OIDC provider, role trust policy, AWS_ROLE_ARN, workflow role configuration
CloudFormation AccessDenied	IAM action/resource denied in the error and CloudFormation events
Template too large	Use the artifact bucket and CloudFormation S3 deployment packaging path
Lambda cannot reach RDS	VPC/subnets, security groups, SSM DB parameters, credentials, schema
API returns 401	Authorization header, token, authorizer configuration, customer status, deployment stage
API returns 5xx	API Gateway logs, Lambda logs, RDS connectivity, processor invocation
Order fails	Order Lambda logs, Order Processor logs, stock, schema, transaction behavior
SNS notification missing	EventBridge rule, target, SNS subscription confirmation, email configuration
Dashboard unavailable	EC2 status, SSM management, bootstrap/service logs, Nginx/Gunicorn, security group
Report missing	Daily Report Lambda logs, S3 report bucket, EventBridge schedule, SSM DB configuration


Never bypass CloudFormation by manually creating replacement infrastructure just to make a deployment appear successful.
18. Rollback and Recovery
The recovery approach depends on the failure type.
Workflow/preflight failure
Fix the source/configuration problem and rerun from a reviewed commit. No infrastructure rollback is required when no AWS mutation occurred.
CloudFormation failure
Stop downstream deployment, inspect the first failing resource and CloudFormation events, fix the source template/workflow/configuration, and retry only after the underlying cause is understood.
Application verification failure
Keep the deployed infrastructure available for diagnosis unless there is a specific rollback decision. Inspect API Gateway, Lambda, RDS, SSM, EventBridge, SNS, dashboard, and CloudWatch evidence as applicable.
Destructive/data-risk change
Do not continue solely because CloudFormation can perform the change. Verify retention/backup requirements and obtain the required approval before making changes that could replace or delete production data.
19. Teardown
Warning: Teardown is destructive and may remove infrastructure and data depending on deletion/retention policies.

Delete CloudFormation stacks in reverse dependency order:
cloudmart-api-monitoring-ec2-{environment}
cloudmart-application-events-{environment}
cloudmart-iam-{environment}
cloudmart-data-storage-{environment}
cloudmart-network-security-{environment}
Before teardown:
- Back up required database/report data.
- Verify the target account, region, and environment.
- Check termination protection and retention settings.
- Review S3 contents and RDS data retention requirements.
- Confirm exports/imports and stack dependencies.
Do not manually delete individual resources to bypass a failed CloudFormation stack.
20. Documentation Set
Recommended CloudMart documentation:
- README: project overview and deployment/service summary.
- Deployment Runbook: deployment, verification, recovery, and teardown.
- Architecture document: detailed diagrams and resource relationships.
- Data model document: database tables, relationships, constraints, and design decisions.
- CRUD verification: executed API test evidence and expected/actual results.
21. Final Submission Checklist
Before submitting CloudMart for review, confirm:
- [ ] Repository is clean and the intended commit is pushed.
- [ ] GitHub Actions deployment is green.
- [ ] All five CloudFormation stacks are healthy.
- [ ] API endpoint and dashboard URL are recorded from actual stack outputs.
- [ ] Public/product and protected/authentication behavior matches the deployed design.
- [ ] Product CRUD is demonstrated.
- [ ] Customer CRUD/soft delete is demonstrated.
- [ ] Customer order flow is demonstrated.
- [ ] Cross-customer access is blocked.
- [ ] Order confirmation/inventory behavior is demonstrated.
- [ ] EventBridge and SNS flows are demonstrated.
- [ ] Daily report generation and S3 storage are demonstrated.
- [ ] Dashboard and CloudWatch monitoring are demonstrated.
- [ ] No secrets are committed or shown in screenshots.
- [ ] CloudFormation drift is reviewed.
- [ ] Deployment evidence and reviewer sign-off are complete.
22. Repository Links
Resource	Path
Deployment workflow	.github/workflows/deploy.yaml
Configuration	config/config.json
Network stack	cloudformation/network-stack.yaml
Data stack	cloudformation/data-stack.yaml
IAM stack	cloudformation/iam-stack.yaml
Application/Event stack	cloudformation/application-events-stack.yaml
API/Monitoring/EC2 stack	cloudformation/api-monitoring-ec2-stack.yaml
Database schema	database/schema.sql
Deployment runbook	readme/deployment-runbook.md
CRUD verification	readme/crud-verification.md
Architecture	readme/architecture.md
Data model	readme/data-model.md


23. Project Status
Update the values below only after verifying the live deployment.

Item	Value
AWS Region	ap-south-1
Environment	dev / prod
API Invoke URL	<verified live URL>
Dashboard URL	<verified live URL>
Git Commit SHA	<verified commit>
GitHub Actions Run	<verified run URL/ID>
Reviewer	<name>
Review Date	<date>
