CloudMart Deployment Runbook
Purpose: single source for deployment, architecture understanding, application flow, verification, troubleshooting, recovery, teardown, and mentor review.
Primary region: ap-south-1
Environments: dev / prod
Deployment: GitHub Actions + OIDC + AWS CloudFormation
Architecture exclusions: no DynamoDB, no SQS, no NAT Gateway in the approved design.

Workflow: .github/workflows/deploy.yaml
Workflow name: CloudMart Infrastructure Deployment
Trigger: Manual workflow_dispatch
AWS Region: ap-south-1
Environment: dev or prod, from config/config.json
Infrastructure: AWS CloudFormation
AWS authentication: GitHub Actions OIDC with an AWS IAM deployment role
This runbook documents the deployment process and verification steps. During each run, record the actual workflow URL, commit SHA, stack outputs, test results, and evidence. This document alone is not proof of a successful fresh-account deployment.

1. Purpose
This document is the single deployment, operations, verification, and reviewer guide for CloudMart. It is intentionally broader than a command-only deployment guide: it explains what the project does, how the AWS components connect, how authentication and business flows work, what each API route is for, how data moves through RDS/EventBridge/SNS/S3, how the dashboard is populated, how CI/CD deploys the platform, and how a reviewer proves the deployed state.
The runbook describes how to deploy, verify, troubleshoot, recover, and tear down CloudMart using GitHub Actions and CloudFormation. The workflow validates configuration and source files, obtains temporary AWS credentials through GitHub OIDC, deploys infrastructure in dependency order, uploads application artifacts, initializes the database schema, refreshes the existing EC2 dashboard through Systems Manager (SSM), and performs post-deployment checks.
Important scope boundary: this runbook is self-contained for the project review, but the repository remains the implementation source of truth. When a handler or CloudFormation template contains an implementation-specific validation rule not reproduced here, verify that rule against the exact commit being deployed.
1.1 Deployment control model — 10/10 review standard
This runbook uses four mandatory control gates. A deployment is not considered complete until all applicable gates pass and evidence is recorded.
Gate	Control	Pass condition	Evidence
Gate 1 — Preflight	Source, configuration, credentials, account, region, templates, and required files	All pre-deployment checks pass before AWS changes begin	Workflow validation output/checklist
Gate 2 — Infrastructure	Five CloudFormation stacks and cross-stack dependencies	Every stack reaches CREATE_COMPLETE or UPDATE_COMPLETE; no unresolved rollback or failed resource	Stack status, events, outputs
Gate 3 — Application	Database, API, authentication, CRUD, orders, events, SNS, reports, dashboard, monitoring	All required functional/security tests pass in the deployed environment	Sanitized test results and screenshots/log references
Gate 4 — Release acceptance	Security, drift, evidence, cost/data protection, open risks	No unaccepted critical issue; evidence record is complete and reviewer approval is recorded	Section 9 sign-off


Deployment decision rules
- PASS: All mandatory gates pass and no critical or high-severity unresolved issue remains.
- CONDITIONAL PASS: Deployment is technically usable, but a documented non-critical deviation has an owner, mitigation, and approval.
- FAIL: Any mandatory gate fails, a critical security/control failure exists, or the deployed state cannot be evidenced.
- A green GitHub Actions workflow is not by itself a release approval.
- Never mark a check as passed from an expected result; it must be executed against the selected deployment and supported by evidence.
- All implementation-specific names, routes, parameters, outputs, permissions, and resource counts must be verified against the exact commit being deployed.
CloudFormation is the source of truth for AWS infrastructure. Do not manually create replacement VPCs, databases, security groups, endpoints, or dashboard instances. The supplied network design has one public subnet and two private subnets, uses VPC endpoints, and does not use a NAT Gateway. It does not include a separate monitoring subnet.
2. Architecture and stack order
Deploy the five stacks in the following order. Replace {environment} with dev or prod.
Order	Stack name	Template	Purpose
1	cloudmart-network-security-{environment}	cloudformation/network-stack.yaml	VPC, public/private subnets, internet gateway, route tables, security groups, and VPC endpoints
2	cloudmart-data-storage-{environment}	cloudformation/data-stack.yaml	RDS MySQL, S3 artifact/report buckets, database subnet group, and database SSM parameters
3	cloudmart-iam-{environment}	cloudformation/iam-stack.yaml	IAM roles and policies for Lambda and EC2, plus authentication-related parameter provisioning
4	cloudmart-application-events-{environment}	cloudformation/application-events-stack.yaml	Lambda functions/layer, schema initialization, EventBridge rules, and SNS resources
5	cloudmart-api-monitoring-ec2-{environment}	cloudformation/api-monitoring-ec2-stack.yaml	API Gateway, authorizer integration, monitoring resources, and EC2 dashboard


Why this order matters
1. Network-Security creates the VPC foundation and exports subnet/security-group identifiers consumed by later stacks.
2. Data-Storage creates the database and S3 buckets. The artifact bucket stores deployment packages and dashboard/template artifacts; the report bucket stores generated reports.
3. IAM creates the roles and policies needed by application and dashboard components.
4. Application-Events deploys Lambda and event/notification resources and applies the database schema through the schema initializer.
5. API-Monitoring-EC2 deploys the API and dashboard/monitoring resources. The workflow retrieves the CloudFormation-managed EC2 instance and refreshes it through SSM; it should not create a duplicate dashboard instance.
2.1 CloudMart resource map — what each AWS service does
This section is the quick reference for a reviewer. Every resource below exists for a specific purpose in the CloudMart architecture.
A. CI/CD and AWS authentication
Resource	Where it is defined	Why it exists
GitHub Actions workflow	.github/workflows/deploy.yaml	Runs the deployment in a repeatable, ordered way. It validates files, authenticates to AWS, deploys the five CloudFormation stacks, uploads artifacts, refreshes the dashboard, and performs verification.
GitHub OIDC provider	AWS IAM	Lets GitHub Actions exchange a short-lived GitHub identity token for temporary AWS credentials. No long-lived AWS access keys are stored in GitHub.
GitHub Actions deployment role	AWS IAM	The role assumed by GitHub Actions. It orchestrates CloudFormation, S3 artifact operations, SSM Run Command, verification, and required role passing.
CloudFormation service role	AWS IAM	Optional CloudFormation execution role used by stack operations when the workflow supplies --role-arn. This role is separate from the GitHub deployment role.
AWS_ROLE_ARN	GitHub repository secret	Contains the IAM role ARN that GitHub Actions is allowed to assume. It is not an AWS access key.
CLOUDMART_ALERT_EMAIL	GitHub repository secret	Holds the notification recipient email used when the Application-Events stack creates SNS subscriptions.
db_password	Manual workflow input	Supplies the RDS password at deployment time. It is not committed to Git and is written to SSM as a SecureString by the data stack's secure-parameter custom resource.


B. Network layer — cloudmart-network-security-{environment}
Resource	Purpose
VPC (CloudMartVPC)	Isolated network boundary for CloudMart resources. Default CIDR is 10.0.0.0/16.
Internet Gateway	Gives the public subnet internet connectivity.
Public subnet	Hosts the public dashboard EC2 instance. Default CIDR 10.0.1.0/24.
Private subnet	First private subnet for database-connected workloads. Default CIDR 10.0.2.0/24.
Secondary private subnet	Second private subnet for multi-subnet placement. Default CIDR 10.0.3.0/24.
Public route table + default route	Sends public-subnet traffic to the Internet Gateway.
Private route table	Associates both private subnets without a NAT Gateway.
Subnet route-table associations	Attach each subnet to its intended route table.
Lambda security group	Controls network access for private Lambda functions.
RDS security group	Controls MySQL access from the intended Lambda/EC2 clients.
EC2 dashboard security group	Controls inbound/outbound traffic for the public dashboard instance.
VPC endpoint security group	Allows HTTPS access to interface endpoints.
S3 Gateway VPC endpoint	Lets private workloads access S3 without a NAT Gateway.
SSM Interface endpoint	Private Systems Manager API access.
SSM Messages endpoint	Private SSM message-channel connectivity.
EC2 Messages endpoint	Private EC2 Messages connectivity used by SSM.
EventBridge interface endpoint	Lets private workloads reach EventBridge privately.
SNS interface endpoint	Lets private workloads use SNS privately.
Lambda interface endpoint	Supports private Lambda-to-Lambda/API interactions from VPC workloads.
NAT Gateway	Not used in the current architecture.


Important design point: the private subnets rely on VPC endpoints rather than a NAT Gateway for the AWS services required by the application. This keeps the network design consistent with the approved architecture.
C. Data layer — cloudmart-data-storage-{environment}
Resource	Purpose
RDS MySQL (CloudMartRDS)	Primary application database for customers, categories, products/inventory, orders, and order items.
RDS DB subnet group	Places RDS inside the two private subnets.
Report S3 bucket (CloudMartReportBucket)	Stores generated daily CSV reports.
Artifact S3 bucket (CloudMartArtifactBucket)	Stores Lambda packages, the PyMySQL layer, schema files, dashboard artifacts, and large CloudFormation templates when packaging is required.
DB endpoint SSM parameter	Stores the RDS hostname so application code does not hardcode it.
DB name SSM parameter	Stores the application database name.
DB port SSM parameter	Stores the RDS MySQL port.
DB username SSM parameter	Stores the RDS username.
DB password SecureString	Stores the RDS password encrypted in SSM Parameter Store.
DBPasswordSSMWriterFunction	Custom-resource Lambda that creates/updates the DB password as SecureString because the native CloudFormation AWS::SSM::Parameter resource does not create SecureString directly.
DBPasswordParameter custom resource	Invokes the secure-parameter writer with /cloudmart/{environment}/db/password.


D. IAM and authentication support — cloudmart-iam-{environment}
The IAM stack creates separate runtime roles instead of reusing the GitHub deployment role.
Runtime role/resource	Used by	Purpose
Lambda Authorizer role	Authorizer Lambda	CloudWatch logging, SSM token access, and VPC networking permissions.
Product Lambda role	Product Lambda	RDS access through SSM configuration, S3 artifact/report access where required, EventBridge publishing, logging, and VPC networking.
Customer Lambda role	Customer Lambda	RDS access, EventBridge publishing, logging, SSM access, and VPC networking.
Order Lambda role	Order Lambda	RDS access, EventBridge publishing, invocation of the Order Processor, logging, and VPC networking.
Order Processor role	Order Processor Lambda	RDS access, EventBridge publishing, logging, and VPC networking.
Schema Initializer role	Schema Initializer Lambda	Reads the schema artifact, reads DB configuration from SSM, connects to RDS, and writes logs.
Daily Report role	Daily Report Lambda	Reads DB configuration, connects to RDS, writes CSV to the report bucket, and logs execution.
RDS Admin EC2 role + instance profile	RDS administration EC2 workflow/resource when deployed	Provides controlled database-administration access and SSM management.
Dashboard EC2 role + instance profile	Flask/Nginx dashboard EC2	Lets the instance register with SSM and access the AWS resources required by the dashboard. It includes AmazonSSMManagedInstanceCore.
Auth SSM writer role + function	Authentication custom resource	Creates the administrator authentication token in SSM SecureString form.
Auth SSM custom resource	CloudFormation	Ensures the administrator token exists at /cloudmart/{environment}/auth/token. A legacy /auth/customer-tokens value is cleaned up rather than used for customer authentication.


E. Application and event layer — cloudmart-application-events-{environment}
Resource	Purpose
Lambda Authorizer	Reads the configured authorization token from SSM and returns the API Gateway authorization decision.
Customer Lambda	Customer onboarding and customer management operations.
Product Lambda	Product CRUD and inventory-related logic.
Order Lambda	Order API logic and synchronous invocation of the Order Processor.
Order Processor Lambda	Executes order confirmation logic, updates RDS, and publishes lifecycle events.
Schema Initializer Lambda	Applies database/schema.sql to RDS.
Daily Report Lambda	Generates the daily report CSV and stores it in the report bucket.
PyMySQL Lambda layer	Provides the MySQL client dependency to database-connected Lambdas.
Custom EventBridge bus	Central event bus for CloudMart application events.
Daily report EventBridge rule	Invokes the Daily Report Lambda on the configured schedule.
Low-stock EventBridge rule	Routes low-stock events to the product alert notification path.
Order-failed EventBridge rule	Routes failed-order events to the order alert notification path.
Product SNS topic	Sends product/low-stock notifications.
Order SNS topic	Sends order-related notifications.
SNS email subscriptions	Deliver notifications to the configured CLOUDMART_ALERT_EMAIL.
SNS topic policies	Allow the EventBridge rules to publish to the corresponding topics.


F. API, monitoring, and dashboard layer — cloudmart-api-monitoring-ec2-{environment}
Resource	Purpose
API Gateway REST API (CloudMartApi)	Public regional front door for CloudMart REST endpoints.
API Gateway TOKEN authorizer	Connects the Authorization header to the Lambda Authorizer.
Product resources/methods	Product CRUD API family.
Customer resources/methods	Customer registration and customer management API family.
Customer order resources/methods	Customer-scoped order creation, retrieval, update, and status operations.
Admin order resources/methods	Administrative order listing, read, and status operations.
Lambda invoke permissions	Allow API Gateway/EventBridge-related services to invoke the relevant Lambda functions.
API Gateway deployment	Publishes the current resource/method configuration.
API Gateway stage	Exposes the deployed API under the environment stage such as /dev or /prod.
API Gateway access log group	Stores API Gateway access logs in CloudWatch Logs.
CloudWatch operations dashboard	Gives operators a single monitoring view for API/Lambda/RDS/EC2 activity.
CloudWatch alarms	Monitor API errors/latency, Lambda errors/throttles/duration, RDS CPU/storage/connections, and EC2 health/status.
Dashboard EC2	Hosts the Flask operations dashboard.
Nginx reverse proxy	Public HTTP entry point for the dashboard and forwards traffic to the local Gunicorn/Flask application.
SSM Run Command	Lets the workflow refresh the existing dashboard EC2 without creating another instance.


G. Database layer
The schema is applied to the cloudmart MySQL database by the Schema Initializer Lambda.
Table	Purpose
categories	Product category master data.
customers	Customer identity, status, soft-delete fields, and SHA-256 hashed bearer token. customer_id is the primary key; bearer token is intentionally not unique.
products	Product catalog and inventory. stock_quantity and reorder_threshold are stored here; there is no separate inventory table.
orders	Order header, customer relationship, status, totals, and timestamps.
order_items	Products and quantities belonging to each order.


Architecture exclusions: DynamoDB and SQS are not part of this implementation. The order path is synchronous: Order Lambda invokes Order Processor Lambda and the normal success path returns the processor's final CONFIRMED result.
2.2 How the resources connect
GitHub Actions
     |
     | OIDC -> STS -> deployment IAM role
     v
CloudFormation
     |
     +------------------- 1. Network --------------------------+
     |                                                         |
     |   VPC                                                  |
     |   |-- Public subnet -------- Dashboard EC2             |
     |   |-- Private subnet -------+                          |
     |   |-- Secondary private ----+---- RDS MySQL            |
     |   |                         |                          |
     |   +-- VPC endpoints --------+---- S3 / SSM / Events   |
     |                              +---- SNS / Lambda        |
     |
     +------------------- 2. Data -----------------------------+
     |                                                         |
     |   RDS MySQL <---- SSM DB parameters                     |
     |   S3 artifact bucket                                    |
     |   S3 report bucket                                      |
     |
     +------------------- 3. IAM ------------------------------+
     |                                                         |
     |   Runtime roles for Lambdas / EC2                        |
     |   Auth SSM writer                                        |
     |
     +------------------- 4. Application / Events ------------+
     |                                                         |
     |   API-facing Lambdas <----> RDS                         |
     |          |                                               |
     |          +----> EventBridge bus ----> SNS               |
     |          |                                               |
     |          +----> Daily Report ----> S3 report bucket     |
     |
     +------------------- 5. API / Monitoring / Dashboard -----+
                                                               |
         API Gateway -> Lambda Authorizer -> application Lambdas
                     |
                     +--> CloudWatch Logs / Dashboard / Alarms
                                                               |
         Dashboard EC2 -> RDS + S3 report bucket
                    ^
                    |
                SSM Run Command
The important dependency is that later stacks import outputs from earlier stacks. For example, the application stack consumes network exports and IAM role exports, while the API/monitoring stack consumes Lambda ARNs and network/IAM outputs.
2.3 CloudFormation cross-stack contract
The five stacks are separate, but they behave like one system.
Stack	Creates	Exports/Provides to later stacks
Network-Security	VPC, subnets, security groups, endpoints	VPC ID, public/private subnet IDs, Lambda/RDS/EC2/VPC-endpoint security-group IDs, dashboard port
Data-Storage	RDS, S3 buckets, DB parameters	RDS endpoint/port/name, bucket names/ARNs, SSM parameter names
IAM	Runtime roles and instance profiles, authentication parameter writer	Lambda role ARNs, EC2 role/instance-profile information, authentication parameter location
Application-Events	Lambda functions/layer, EventBridge, SNS	Lambda ARNs/names, event bus name/ARN, SNS topic ARNs, daily report schedule
API-Monitoring-EC2	API Gateway, authorizer, CloudWatch, dashboard EC2	API URL/stage, dashboard endpoint, monitoring resource outputs


Do not manually edit one stack's exported resource and expect the downstream stack to automatically repair itself. Correct the source CloudFormation template and redeploy through the workflow.
2.4 SSM Parameter Store — complete explanation
This is the section that must be used when explaining the project to a reviewer.
What is SSM Parameter Store doing in CloudMart?
SSM Parameter Store is the centralized configuration store used by CloudMart runtime components. Instead of hardcoding the RDS hostname, database name, username, password, bucket names, or administrator token inside Lambda code, the application receives the parameter name and reads the value at runtime.
The project therefore separates:
1. Configuration values such as endpoint, port, database name, username, and bucket names.
2. Sensitive values such as the database password and administrator authentication token, which are stored as SecureString.
SSM parameter registry
Parameter path	Type	Value contains	Main consumers	Why it is needed
/cloudmart/{environment}/db/endpoint	String	RDS hostname	Product, Customer, Order, Order Processor, Schema Initializer, Daily Report, dashboard/admin components as configured	The RDS endpoint changes with the deployed database, so application code must not hardcode it.
/cloudmart/{environment}/db/name	String	cloudmart database name	Database-connected Lambdas and initialization components	Gives the runtime code the database name to connect to.
/cloudmart/{environment}/db/port	String	MySQL port	Database-connected Lambdas	Keeps connection configuration outside code.
/cloudmart/{environment}/db/username	String	RDS username	Database-connected Lambdas/admin components	Centralized runtime configuration.
/cloudmart/{environment}/db/password	SecureString	RDS password	Database-connected Lambdas/admin components	Sensitive credential is encrypted in SSM and not committed to Git.
/cloudmart/{environment}/s3/report-bucket	String	Report bucket name	Daily Report Lambda and dashboard	Allows the report destination to be discovered without hardcoding the physical bucket name.
/cloudmart/{environment}/s3/artifact-bucket	String	Artifact bucket name	Deployment workflow, schema/layer/package handling, dashboard refresh	Central location for deployment artifacts and large template packaging.
/cloudmart/{environment}/auth/token	SecureString	Administrator authentication token	Lambda Authorizer and authentication-aware components	Keeps the administrator token outside source code.
/cloudmart/{environment}/auth/customer-tokens	Legacy cleanup path	Legacy customer-token map	Not a runtime dependency	The authentication custom resource removes this legacy parameter so customer tokens remain in RDS instead.


How the DB password gets into SSM
The RDS password is not placed in a normal plaintext SSM parameter resource.
The deployment flow is:
GitHub Actions manual input: db_password
               |
               v
Data-Storage CloudFormation stack
               |
               v
DBPasswordSSMWriterFunction
               |
               v
SSM Parameter Store
/cloudmart/{environment}/db/password
Type = SecureString
               |
               v
Database-connected Lambda roles
ssm:GetParameter / WithDecryption
The custom resource exists because the native AWS::SSM::Parameter resource is used for normal strings, while the secure password path is created/managed through the dedicated writer Lambda.
How the admin authentication token is created
The IAM stack provisions a custom resource backed by AuthSSMWriterFunction.
IAM stack
   |
   v
AuthSSMWriterFunction
   |
   +---- generates administrator token
   |
   v
/cloudmart/{environment}/auth/token
Type = SecureString
   |
   v
Lambda Authorizer reads it with decryption
Customer bearer tokens are different: they are managed by the customer application and stored as SHA-256 hashes in the customers RDS table. They are not the same thing as the administrator token held in SSM.
Who can read SSM?
The GitHub deployment role has SSM management permissions for the /cloudmart/* path so the workflow can inspect and manage CloudMart parameters. Runtime Lambda roles are granted only the parameter paths they need. The dashboard/RDS-admin EC2 roles likewise have narrowly defined SSM permissions for the configuration they consume.
Never print a SecureString value, database password, administrator token, or bearer token into CloudWatch Logs, GitHub Actions logs, screenshots, or this runbook.
2.5 What the mentor should understand from the stack design
The easiest way to explain the project verbally is:
Network stack builds the private AWS network and the VPC endpoints.
Data stack builds the database and S3 storage and publishes the database/storage configuration to SSM Parameter Store.
IAM stack builds the runtime permissions and authentication parameter support so every Lambda/EC2 component has only the permissions it needs.
Application-Events stack builds the application Lambdas, their dependency layer, the database schema initializer, EventBridge event bus/rules, and SNS notifications.
API-Monitoring-EC2 stack exposes the application through API Gateway, connects the Lambda Authorizer, creates CloudWatch monitoring, and creates the EC2 Flask dashboard.
The GitHub Actions workflow is only the orchestrator. It assumes the deployment role through OIDC, deploys the five CloudFormation stacks in dependency order, uploads artifacts, refreshes the existing EC2 through SSM, and verifies the deployment.

2.6 CloudMart project overview — what the system actually does
CloudMart is an AWS-hosted e-commerce application. A customer can be onboarded, browse products, place orders, view their own orders, and update/cancel them according to the configured authorization and status rules. An administrator/operations user can manage products/customers and inspect order operations through protected APIs and the EC2 Flask dashboard.
The project is deliberately built without DynamoDB and without SQS. RDS MySQL is the system of record, EventBridge is the event-routing layer, SNS is the notification layer, S3 stores artifacts/reports, SSM provides runtime configuration and protected parameters, and CloudWatch provides operational visibility.
Core capabilities
Capability	Implementation
Customer onboarding	POST /customers, public registration route
Customer authentication	API Gateway TOKEN Lambda Authorizer + SHA-256 token hash lookup in RDS
Product catalog	Product Lambda + products table; GET /products is public in the reviewed API template
Product administration	Protected create/read-by-id/update/soft-delete routes
Customer administration	Protected listing/read/update/patch/soft-delete routes
Order placement	Customer-specific order route; synchronous RequestResponse invocation of Order Processor
Inventory deduction	Order Processor updates products.stock_quantity in RDS
Order status history	order_logs records state changes and notes
Business events	EventBridge custom bus with order/inventory/report rules
Notifications	SNS product/order alert topics and subscriptions
Daily reporting	Daily Report Lambda creates CSV and writes it to private S3 report bucket
Operations dashboard	Flask + Gunicorn/Nginx on the CloudFormation-managed EC2 instance
Monitoring	CloudWatch access logs, dashboard, custom metrics/alarms, SNS actions


End-to-end architecture in one view
                         GitHub Actions
                              |
                        GitHub OIDC role
                              |
                      CloudFormation stacks
                              |
     +------------------------+-------------------------+
     |                        |                         |
  Network                 Data + SSM                  IAM
     |                        |                         |
 VPC/subnets/SGs       RDS + S3 buckets        runtime roles/policies
     |                        |                         |
     +------------------------+-------------------------+
                              |
                    Application / Events
                              |
       +----------------------+----------------------+
       |                      |                      |
   API Gateway            EventBridge              Daily Report
       |                      |                      |
   TOKEN Authorizer     Rules -> SNS               S3 reports
       |
   +---+---------------------------+
   |        |          |            |
Products  Customers   Orders   Admin Orders
   |        |          |            |
   +--------+----------+------------+
                    RDS MySQL
          categories/customers/products
          orders/order_items/order_logs
                              |
                    Operations Dashboard EC2
                       SSM -> source/artifacts
                       RDS -> live operational data
                       S3 -> private reports
                       CloudWatch -> monitoring
What a normal order does
1. The customer sends POST /customers/{customer_id}/orders with a JSON body containing an items array.
2. API Gateway runs the TOKEN authorizer. The request is accepted only when the caller is authorized for the target customer.
3. Order Lambda validates the customer_id path value and the items array. The customer ID is taken from the URL/authorizer context, not trusted from the body.
4. Order Lambda invokes Order Processor Lambda synchronously (RequestResponse).
5. The processor reads product prices/stock, calculates the order total, creates the order/order-items records, deducts inventory, and returns the final result.
6. The normal successful path returns CONFIRMED rather than relying on a separate asynchronous queue acknowledgement.
7. Order/inventory lifecycle events are published to EventBridge; matching rules can route them to SNS.
8. The dashboard can then show the updated stock, order, status, and related event/report data.
2.7 Repository-to-runtime component map
The reviewer should be able to move from a Git file to the AWS resource it controls without guessing.
Repository path / component	Runtime responsibility	AWS resource(s)
.github/workflows/deploy.yaml	Deployment orchestration	GitHub Actions + AWS OIDC
config/config.json	Environment/project metadata	Workflow inputs/CloudFormation parameters
cloudformation/network-stack.yaml	Network foundation	VPC, subnets, routes, SGs, VPC endpoints
cloudformation/data-stack.yaml	Data/storage foundation	RDS, DB subnet group, S3 buckets, DB SSM parameters
cloudformation/iam-stack.yaml	Runtime access control	Lambda/EC2 IAM roles and auth parameter writer
cloudformation/application-events-stack.yaml	Application/event layer	Lambda functions, layer, EventBridge, SNS, schema initializer
cloudformation/api-monitoring-ec2-stack.yaml	API/operations layer	API Gateway, authorizer integration, CloudWatch, dashboard EC2
database/schema.sql	Relational data contract	RDS MySQL tables/keys/constraints/seed data
lambda/authorizer/lambda_function.py	Authentication decision	API Gateway TOKEN authorizer Lambda
lambda/customer/lambda_function.py	Customer CRUD/onboarding	Customer Lambda
lambda/product/lambda_function.py	Product/inventory CRUD	Product Lambda
lambda/order/lambda_function.py	Customer order API	Order Lambda
lambda/order-processor/lambda_function.py	Order transaction/business processing	Order Processor Lambda
lambda/daily-report/lambda_function.py	Report generation	Daily Report Lambda + S3
dashboard/app.py	Operations UI/backend	EC2 Flask application
dashboard/bootstrap-dashboard.sh	Server setup/refresh	SSM Run Command on dashboard EC2
dashboard/requirements.txt	Dashboard runtime dependencies	EC2 Python environment
docs/architecture.md	Architecture reference	Review/documentation
docs/data-model.md	Data model reference	Review/documentation
docs/crud-verification.md	Functional verification reference	Review/documentation
docs/deployment-runbook.md	This operational/reviewer guide	Review/documentation


2.8 Application flow and responsibility boundaries
2.8.1 Authentication flow
Client
  |
  | Authorization: Bearer <token>
  v
API Gateway TOKEN Authorizer
  |
  | token validation / role context
  v
Lambda request context
  |
  +--> customer routes: customer_id + token context
  |
  +--> admin routes: ADMIN role context
  v
Customer/Product/Order Lambda
  |
  v
RDS MySQL
The reviewed design uses a TOKEN authorizer whose identity source is method.request.header.Authorization. Customer bearer tokens are hashed with SHA-256 before storage. The database intentionally does not make the token hash unique. Customer-scoped authorization uses the target customer ID plus the token/authorizer context so that two active customers may share the same bearer token while remaining distinct by customer_id.
A customer token is intended to remain stable across logins; the login/onboarding flow must not silently generate a new token for every login. Customer records are soft-deleted/inactivated rather than physically removed, and inactive/deleted customers must not authorize protected requests.
The administrator token is stored separately in SSM as a SecureString under the environment-specific auth parameter. It is not the customer token store.
2.8.2 Product flow
GET /products is a public catalog-listing route in the reviewed API template. Product administration remains protected.
GET /products (public)
        |
        v
Product Lambda
        |
        v
SELECT active products from RDS
        |
        v
JSON response
For administrative changes:
POST/GET-by-id/PUT/DELETE
        |
 TOKEN Authorizer
        |
 Product Lambda
        |
 RDS products table
        |
 InventoryChanged event when applicable
        |
 EventBridge -> configured notification rule(s)
Product deletion is implemented as a soft-delete concept; the database retains the record with deletion/status metadata rather than blindly removing the row.
2.8.3 Customer flow
POST /customers is the reviewed public onboarding route. The creation request carries the customer identity details and the bearer token supplied by the customer. The Lambda stores the SHA-256 hash rather than the plaintext token.
After onboarding, protected customer operations use the Lambda Authorizer and customer ID in the URL. The customer ID in the URL is not merely informational; it is part of the authorization decision.
2.8.4 Order flow
The customer order body has the form:
{
  "items": [
    {"product_id": 101, "quantity": 2},
    {"product_id": 205, "quantity": 1}
  ]
}
Rules verified from the order handler include:
- items must be a non-empty array.
- Each item must be an object containing integer product_id and positive integer quantity.
- Duplicate product_id values are not allowed inside one order.
- A maximum of 50 product entries is accepted by the reviewed handler.
- customer_id is taken from the authenticated URL/authorizer context and is never trusted from the request body.
The Order Lambda invokes Order Processor Lambda synchronously. The processor performs the database transaction and returns the final result so the normal API response can be CONFIRMED.
2.8.5 Order cancellation and status ownership
The project review rules distinguish customer-owned actions from administrator actions:
- A customer may act on their own order only after the customer ID in the route matches the authenticated identity.
- A customer must not read/update/cancel another customer's order.
- Administrator/owner actions use the protected /orders administrative routes.
- Administrator/owner cancellation of a customer order is not allowed under the project rules; status changes must follow the current handler's role and state validation.
Always prove these rules with explicit negative tests during the mentor review.
2.8.6 Reporting flow
The Daily Report Lambda reads the RDS product inventory and recent orders, creates a CSV, and writes it under:
daily-reports/cloudmart-daily-report-YYYY-MM-DD.csv
The reviewed implementation includes product inventory fields (product_id, name, price, stock_quantity, status) and recent order fields (order_id, customer_id, status, total_amount, created_at).
The report bucket is private. The dashboard lists report objects and uses temporary presigned URLs for controlled viewing/downloads.
2.8.7 Dashboard flow
The CloudMart operations dashboard is a Flask application running on the CloudFormation-managed EC2 instance. The runtime reads configuration from SSM, queries RDS for live operational information, and lists reports from the private S3 report bucket.
The reviewed dashboard provides:
- Administrator login and logout.
- Overview/KPI widgets for products, orders, customers, low stock, and failed orders.
- Inventory value and configurable inventory budget visibility.
- Order status distribution and recent sales information.
- Product search/details and related order activity.
- Order search/details and order-item history.
- Customer search/details.
- Event History.
- Daily report calendar, report viewing, and CSV download through temporary S3 URLs.
- Links to the CloudWatch operations dashboard when the monitoring URL is configured.
- /health endpoint for deployment/operations checks.
The dashboard is refreshed through SSM on the existing CloudFormation-managed EC2 instance. The deployment workflow should not create a duplicate dashboard server.
2.9 Authentication/authorization matrix — reviewer-ready
The reviewed API template makes the authorization behavior explicit:
Method	Route	Gateway authorization	Primary rule
GET	/products	NONE	Public catalog listing
POST	/products	CUSTOM	Protected product administration
GET	/products/{id}	CUSTOM	Protected product lookup
PUT	/products/{id}	CUSTOM	Protected product update
DELETE	/products/{id}	CUSTOM	Protected product soft delete
POST	/customers	NONE	Public customer onboarding
GET	/customers	CUSTOM	Protected customer listing
GET	/customers/{customer_id}	CUSTOM	Protected customer-scoped lookup
PUT	/customers/{customer_id}	CUSTOM	Protected update
PATCH	/customers/{customer_id}	CUSTOM	Protected partial update
DELETE	/customers/{customer_id}	CUSTOM	Protected/admin soft delete
POST	/customers/{customer_id}/orders	CUSTOM	Customer must match authenticated identity
GET	/customers/{customer_id}/orders	CUSTOM	Customer-scoped order listing
GET	/customers/{customer_id}/orders/{order_id}	CUSTOM	Customer-scoped order lookup
PUT	/customers/{customer_id}/orders/{order_id}	CUSTOM	Customer-scoped order update
PATCH	/customers/{customer_id}/orders/{order_id}/status	CUSTOM	Status rule/ownership check
GET	/orders	CUSTOM	Administrative order listing
GET	/orders/{id}	CUSTOM	Administrative order lookup
PATCH	/orders/{id}/status	CUSTOM	Administrative status operation


AuthorizationType: CUSTOM means API Gateway invokes the configured Lambda Authorizer before the request reaches the protected Lambda integration. AuthorizationType: NONE means the request is not blocked at the gateway by that authorizer.
method.request.path.id: true and similar declarations mean the route path parameter is required and passed into the integration. This is a routing/request-parameter declaration, not an authentication mechanism.
Live reviewer auth test matrix
Test	Expected
GET /products with no token	200/successful product listing according to deployed handler
Protected product mutation with no token	Rejected
Protected route with malformed/wrong token	Rejected
POST /customers without token	Customer onboarding route is accepted if request body is valid
Customer accesses own customer/order route	Accepted when token/customer mapping is valid
Customer accesses another customer's route	Rejected
Soft-deleted/inactive customer uses token	Rejected
Admin token on admin route	Accepted when admin token is valid and role allows operation
Customer tries admin-only operation	Rejected


2.10 Data model and business rules
The final reviewed schema contains the following application tables:
Table	Purpose	Important relationships/behavior
categories	Product classification	Parent of products through category_id
customers	Customer identity/authentication	customer_id PK; email unique; token hash is not unique; soft-delete fields
products	Product catalog + inventory	category_id FK; stock lives here; soft delete/status fields
orders	Order header	customer_id FK; status; totals; timestamps
order_items	Product lines within an order	order_id FK and product_id FK; quantity and unit price
order_logs	Status history/failure notes	order_id FK; previous/new status, changer, note, timestamp


Key database rules
- MySQL database name is cloudmart.
- customer_id is the only customer primary key.
- customers.bearer_token stores the SHA-256 hash and has no UNIQUE constraint.
- customers.email is unique.
- Customer deletion uses deleted_at, deleted_by, delete_reason, and status rather than requiring physical row deletion.
- Product inventory is maintained by products.stock_quantity; there is no separate DynamoDB inventory table.
- products.reorder_threshold is used for low-stock evaluation.
- Product price, stock, reorder threshold, order total, quantity, and unit-price constraints are enforced at the database level.
- orders.customer_id references customers.customer_id.
- order_items.order_id references orders.order_id and cascades on delete.
- order_items.product_id references products.product_id.
- order_logs.order_id references orders.order_id and provides the status audit trail.
Relational flow
categories 1 -------- * products
customers  1 -------- * orders
orders     1 -------- * order_items * -------- 1 products
orders     1 -------- * order_logs
The schema initializer applies the repository SQL to the RDS database. The reviewer should verify all six application tables after initialization rather than checking only the five original core tables.
2.11 API contract and payload reference
The deployed API uses API Gateway AWS_PROXY integrations to Lambda. Requests are JSON-based where a body is required and Lambda returns JSON responses through the proxy integration.
Customer onboarding payload
Representative creation body aligned with the schema/application contract:
{
  "name": "Example Customer",
  "email": "customer@example.com",
  "bearer_token": "TEST-TOKEN-123",
  "address": "Hyderabad"
}
The plaintext token is accepted as input to the onboarding flow and must be hashed before database storage. Never paste a real token into runbook evidence.
Product payload
Representative product body aligned with the schema:
{
  "category_id": 1,
  "name": "Wireless Mouse",
  "description": "2.4 GHz wireless mouse",
  "price": 799.00,
  "stock_quantity": 25,
  "reorder_threshold": 5
}
Order payload
The order handler explicitly expects:
{
  "items": [
    {"product_id": 101, "quantity": 2},
    {"product_id": 205, "quantity": 1}
  ]
}
The order route takes customer_id from the URL; the body does not supply an authoritative customer ID.
Response expectations used in review
Scenario	Expected HTTP behavior
Missing/invalid JSON body	400 from validation paths
Unauthorized protected operation	401/gateway rejection or 403 from Lambda authorization/business checks, depending on the exact failure point
Successful customer/product CRUD	Successful 2xx response with JSON body
Successful order	Successful response with CONFIRMED on the normal path
Validation error such as invalid order items	400
Unexpected Lambda/runtime failure	500 with a sanitized message; correlate the request ID with CloudWatch logs


What must still be checked against the exact handler
Update/patch field allow-lists, optional response metadata, and any route-specific status transition payloads can evolve independently of the database schema. During a release, inspect the exact handler commit before declaring those fields part of the external contract.
2.12 EventBridge, SNS, reporting, and observability flow
EventBridge event families
The project uses a custom EventBridge bus. The reviewed order Lambda publishes order lifecycle events with:
Source: cloudmart.orders
DetailType: <order lifecycle detail type>
Detail:
  order_id
  customer_id
  status
  total_amount
  optional reason
  optional items
The inventory flow publishes:
Source: cloudmart.inventory
DetailType: InventoryChanged
Detail:
  product_id
  old_stock
  new_stock
Rules then match relevant events. The project contains rules for the daily report schedule, low-stock notifications, and order-failure notifications. The exact event-pattern filters remain the CloudFormation template's source of truth.
SNS notification flow
Product/Order Lambda
        |
        v
EventBridge custom bus
        |
      rule
        |
        v
SNS topic
        |
        v
confirmed email subscription
The reviewer should test at least one controlled event path in non-production and show the corresponding EventBridge rule, SNS topic/subscription, and CloudWatch evidence.
Monitoring flow
API Gateway / Lambda / application activity
                |
         CloudWatch logs
                |
      dashboard + alarms + metrics
                |
                v
             SNS action
Operational metrics should be reviewed for successful orders, failed orders, low-stock events, API/Lambda failures, and any other metrics actually published by the deployed monitoring stack. Alarm thresholds are deployment-specific and must be read from the current CloudFormation template, not guessed from this document.
2.13 Mentor-review live demo — recommended 10/10 sequence
Use this sequence to show that the project is not only deployed but understood.
Demo 1 — prove the deployment source of truth
1. Open the intended GitHub commit.
2. Show .github/workflows/deploy.yaml and the five CloudFormation templates.
3. Explain GitHub OIDC: GitHub receives short-lived AWS credentials by assuming the deployment role; no long-lived AWS access key is committed to the repository.
4. Show the five successful stack statuses.
Demo 2 — prove the infrastructure layers
5. Show the VPC, one public subnet, two private subnets, route tables, four security groups, and seven VPC endpoints.
6. Explain why there is no NAT Gateway in the current architecture.
7. Show RDS in the private network and the two S3 buckets.
8. Show the SSM DB parameters and auth parameter names/types only, never secure values.
Demo 3 — prove authentication and API behavior
9. Call GET /products without a token and show the public listing behavior.
10. Call a protected route without a token and show rejection.
11. Call a protected route with a wrong token and show rejection.
12. Use a valid test customer token on the customer/order route.
13. Attempt the same customer token against another customer's path and show rejection.
14. Demonstrate that a shared token does not collapse two different customer_id records.
Demo 4 — prove business flow
15. Create a test customer through the public onboarding route.
16. Create/update a test product and confirm inventory fields in RDS.
17. Place an order using { "items": [...] }.
18. Show the normal successful response with CONFIRMED.
19. Show the inventory decrease in products.stock_quantity and the related order/order_item records.
20. Show the order_logs entry/status history where applicable.
Demo 5 — prove event/notification/reporting/operations
21. Trigger a controlled inventory/low-stock event.
22. Show EventBridge rule matching and SNS notification evidence.
23. Invoke or wait for Daily Report Lambda and show the CSV under daily-reports/.
24. Open the dashboard and show KPIs, inventory, recent orders, event history, and report calendar.
25. Open the CloudWatch operations dashboard and show alarm/metric state.
Demo 6 — prove deployment hygiene
26. Show CloudFormation drift results.
27. Explain rollback order and why manual resource edits are forbidden.
28. State what evidence would make the deployment a PASS versus FAIL.
This demonstration sequence gives the reviewer a direct chain from source → infrastructure → API → database → business logic → events → notifications → report → dashboard → monitoring.
3. Prerequisites
3.1 Repository contents
Before deployment, confirm the selected branch contains the workflow, configuration, all five CloudFormation templates, database/schema.sql, the authorizer/product/customer/order/order-processor/daily-report Lambda code, and dashboard files such as app.py, requirements.txt, and bootstrap-dashboard.sh. Confirm that every path referenced by the workflow matches the repository layout and is committed.
3.2 AWS account and region
- Confirm the intended AWS account and set/verify the region as ap-south-1.
- Review AWS service availability, quotas, cost, and data-retention requirements.
- Inspect existing stacks and resources to ensure the deployment targets the intended environment.
- RDS and EC2 resources may continue to incur charges while running.
3.3 GitHub Actions OIDC connection and secret
The workflow authenticates to AWS through OpenID Connect (OIDC). GitHub Actions obtains an OIDC token and the AWS credentials action exchanges it for temporary credentials by assuming an AWS IAM role. This avoids storing long-lived AWS access keys in repository secrets.
Repository secret: AWS_ROLE_ARN
Configure or verify it in GitHub:
1. Open the repository Settings.
2. Select Secrets and variables → Actions.
3. Under Repository secrets, create or inspect AWS_ROLE_ARN.
4. Set the secret value to the AWS IAM role ARN that the workflow is allowed to assume, for example: arn:aws:iam::<AWS_ACCOUNT_ID>:role/<GITHUB_ACTIONS_DEPLOYMENT_ROLE>.
5. Save the secret. GitHub masks secret values in logs.
Important naming detail: The supplied workflow/runbook uses secrets.AWS_ROLE_ARN. If the secret was created with the name AWS_ROLE, either rename it to AWS_ROLE_ARN or change the workflow expression to secrets.AWS_ROLE. The GitHub secret name and YAML reference must match exactly. The secret value must be the IAM role ARN, not an AWS console URL, login URL, access key, or secret access key.
The AWS account must also have the GitHub Actions OIDC identity provider configured. The deployment role's trust policy must restrict access to the intended GitHub repository and branch/environment. The workflow should grant id-token: write and contents: read, and aws-actions/configure-aws-credentials should use the role ARN secret and region ap-south-1.
The assumed role requires permissions for the workflow's actual CloudFormation deployment/inspection, S3 artifact operations, SSM Run Command and polling, Lambda/stack verification, and narrowly scoped iam:PassRole actions where needed. Apply least privilege; do not respond to an access error by granting unrestricted permissions without review. Never store long-lived AWS access keys as a workaround for OIDC configuration.
3.3.1 GitHub Actions OIDC connection
OpenID Connect (OIDC) allows GitHub Actions to obtain short-lived AWS credentials without storing long-lived AWS access keys in GitHub.
Connection flow
GitHub repository / selected branch
        |
        v
Manual workflow_dispatch
        |
        v
Workflow requests OIDC token (id-token: write)
        |
        v
aws-actions/configure-aws-credentials
        |
        v
AWS STS AssumeRoleWithWebIdentity
        |
        v
AWS validates:
  - OIDC provider
  - audience = sts.amazonaws.com
  - repository/ref subject
        |
        v
Temporary credentials for the deployment role
OIDC provider expected in this account
arn:aws:iam::285150348844:oidc-provider/token.actions.githubusercontent.com
Audience
sts.amazonaws.com
GitHub secret
AWS_ROLE_ARN
The secret value must be the IAM role ARN that GitHub Actions is allowed to assume.
Recommended all-branches trust pattern for this repository
The subject condition should be constrained to Srihitha01/cloudmart and branch refs rather than trusting every GitHub repository. The exact subject form used by the live repository must match the repository's OIDC configuration.
Example that allows all branches while covering the standard repository subject and the immutable-subject form:
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "GitHubActionsCloudMartDeployment",
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::285150348844:oidc-provider/token.actions.githubusercontent.com"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
        },
        "StringLike": {
          "token.actions.githubusercontent.com:sub": [
            "repo:Srihitha01/cloudmart:ref:refs/heads/*",
            "repo:Srihitha01@168816985/cloudmart@1341651904:ref:refs/heads/*"
          ]
        }
      }
    }
  ]
}
This policy is intentionally branch-scoped through refs/heads/*. It is broader than allowing only main, but narrower than trusting arbitrary repositories or non-branch subjects.
Important: the IAM trust relationship shown above is an example for the current CloudMart repository configuration. During a real deployment, the live IAM role trust policy and the GitHub workflow must be checked together.
3.3.2 Deployment role and CloudFormation service role
Keep these roles distinct:
Role	Used by	Responsibility
GitHub Actions deployment role	AWS STS assumes it after validating the GitHub OIDC token	Orchestrates the workflow: stack deployment/inspection, artifact upload, SSM command/polling, verification, and passing the CloudFormation service role if configured.
CloudMart-CloudFormation-ServiceRole	CloudFormation assumes it for stack operations when supplied to the deployment	Grants CloudFormation the resource-creation/update/deletion permissions required by the templates.


The supplied permissions policy includes iam:PassRole for the CloudFormation service role. That permission is needed when the workflow submits the stack operation with that role (for example, using --role-arn). Check the workflow's deploy commands and current stack service-role configuration to confirm whether it is used. If it is used, the service role itself must have the necessary CloudFormation execution permissions. iam:PassRole only authorizes passing a role; it does not grant the caller the permissions contained in that role.
The runtime IAM roles for Lambda functions and the EC2 dashboard are separate again. They are assumed by those workloads, not by GitHub Actions. Do not use the GitHub deployment role as a Lambda execution role or EC2 instance role.
Deployment-role permissions by purpose
The attached permissions policy is the identity-based permissions policy for the GitHub Actions deployment role. Its statements cover these areas:
Policy statement (Sid)	Purpose
CloudFormationStackManagement	Create/update/delete stacks and change sets; inspect, validate, and execute deployments.
NetworkInfrastructureManagement	Manage VPCs, subnets, route tables, internet gateways, security groups, and VPC endpoints.
EC2InstanceManagement	Launch, inspect, start/stop/terminate EC2 instances and manage their network interfaces.
RDSManagement	Create, modify, delete, inspect, tag, and manage RDS instances and subnet groups.
AllowCreateRDSServiceLinkedRole	Create the RDS service-linked role, constrained to rds.amazonaws.com.
S3BucketManagement / S3ObjectManagement	Create/configure buckets and upload/read/delete deployment objects and versions.
APIGatewayManagement	Create/read/update/delete API Gateway resources.
CloudMartLambdaManagement / CloudMartLambdaInvocation	Manage CloudMart Lambda functions, versions, aliases, permissions, tags, and invoke them.
CloudMartLambdaLayerPublish / CloudMartLambdaLayerVersionManagement / CloudMartLambdaLayerList	Publish, inspect, list, and delete CloudMart Lambda layer versions.
CloudMartRoleManagement	Create/update/delete CloudMart IAM roles and manage their policies/attachments.
CloudMartInstanceProfileManagement	Create/delete CloudMart EC2 instance profiles and associate roles.
PassCloudMartRoles	Pass CloudMart runtime roles to AWS services when resources are created.
CloudWatchAlarmManagement / CloudWatchDashboardManagement	Manage alarms, dashboards, tags, and metric reads.
CloudWatchLogsManagement	Manage log groups, streams, retention, tags, and log events.
EventBridgeManagement / EventBridgeSchedulerManagement	Manage event buses, rules, targets, schedules, and schedule groups.
SNSManagement	Manage SNS topics, subscriptions, attributes, and tags.
CloudMartSSMParameterManagement / CloudMartSSMParameterDescribe	Read/write/delete CloudMart parameters and describe parameters.
EC2DashboardSSMManagement	Send SSM commands and poll command results for dashboard refresh.
ReadAmazonLinuxPublicAMI	Read the Amazon Linux 2023 public AMI SSM parameter.
PassCloudFormationServiceRole	Pass CloudMart-CloudFormation-ServiceRole if the workflow submits CloudFormation operations using that service role.
ReadCallerIdentity	Verify the effective AWS identity with STS GetCallerIdentity.


Policy review note: the supplied policy is broad rather than fully least-privilege: multiple statements use Resource: "*", and it includes destructive permissions for stacks, EC2, RDS, S3, and IAM. Before production use, scope resources/actions where supported, consider separating deploy and teardown permissions, and constrain iam:PassRole with the relevant iam:PassedToService condition. Add permissions only after checking the specific denied action/resource; do not respond to access errors with unrestricted administrator access.
The full supplied permissions policy is reproduced in Appendix A below and is also provided as a separate JSON file for convenient IAM attachment/review. The trust policy is separate: it belongs in the IAM role's Trust relationships, not inside this identity-based permissions policy.
3.4 Other secret and manual input
- CLOUDMART_ALERT_EMAIL: GitHub Actions repository secret for the notification recipient. Keep the email address out of source code and templates.
- db_password: required manual workflow input for the database deployment. The supplied workflow prompt specifies 12–41 characters and validates a minimum of 12. Use a strong unique value. Do not commit it, put it in a parameter file, or expose it in logs or evidence.
Verify the actual workflow's secret/input names before changing them.
3.4.1 Change-control and deployment authorization
Before running a deployment:
1. Confirm the commit SHA is the reviewed version intended for the selected environment.
2. Confirm the branch is authorized for that environment.
3. Review CloudFormation changes for replacement-sensitive resources, especially RDS, VPC, subnets, security groups, API Gateway, and EC2.
4. Confirm the deployment window and required reviewer/mentor approval.
5. Confirm whether the run is an update, first deployment, or recovery deployment.
6. Record the expected environment, AWS account, region, and stack names in the evidence record.
7. Do not combine unrelated infrastructure changes with an emergency fix unless the change is explicitly reviewed.
For production, a deployment should have an identified approver and a documented rollback/restore decision before execution.
3.4.2 Preflight fail-fast criteria
Stop before creating or updating AWS resources if any of the following is true:
- The intended branch/commit cannot be identified.
- The AWS account or region is wrong.
- OIDC authentication is not correctly configured.
- A required secret/input is missing or would expose a credential.
- Configuration and CloudFormation parameter names do not match.
- Required source/artifact files are missing.
- Any blocking cfn-lint error remains.
- A previously failed stack is being retried without understanding its failure.
- A proposed change would unintentionally replace or delete protected data/resources.
3.5 Configuration and network parameter file
config/config.json supplies Environment, Project, ManagedBy, and Owner. The environment must be dev or prod, and must agree with the workflow target and stack names. Runtime SSM paths follow /cloudmart/{environment}/.... Do not put credentials in this file.
The supplied network-stack.yaml uses these parameters:
Parameter	Meaning	Example dev value
Environment	Deployment environment	dev
VpcCidr	VPC CIDR	10.0.0.0/16
PublicSubnetCidr	Public subnet CIDR	10.0.1.0/24
PrivateSubnetCidr	Primary private subnet CIDR	10.0.2.0/24
SecondaryPrivateSubnetCidr	Secondary private subnet CIDR	10.0.3.0/24


If the workflow reads cloudformation/network-parameters.json, ensure the file exists and uses the CloudFormation JSON parameter-array format. Example:
[
  {"ParameterKey":"Environment","ParameterValue":"dev"},
  {"ParameterKey":"VpcCidr","ParameterValue":"10.0.0.0/16"},
  {"ParameterKey":"PublicSubnetCidr","ParameterValue":"10.0.1.0/24"},
  {"ParameterKey":"PrivateSubnetCidr","ParameterValue":"10.0.2.0/24"},
  {"ParameterKey":"SecondaryPrivateSubnetCidr","ParameterValue":"10.0.3.0/24"}
]
The workflow should convert the JSON entries into Key=Value arguments for aws cloudformation deploy --parameter-overrides. Ensure Environment matches config/config.json. Do not include MonitoringPublicSubnetCidr in this file: that parameter/resource is not part of the supplied network architecture.
3.6 SSM verification checklist
Before declaring deployment success, verify that the expected SSM parameters exist for the selected environment.
List the CloudMart parameters:
aws ssm get-parameters-by-path \
  --path /cloudmart/dev \
  --recursive \
  --with-decryption \
  --region ap-south-1
For production, replace dev with prod.
For safety, when collecting evidence do not paste decrypted secret values into the runbook. A better evidence command is:
aws ssm describe-parameters \
  --parameter-filters Key=Path,Option=Recursive,Values=/cloudmart/dev \
  --region ap-south-1 \
  --query 'Parameters[*].[Name,Type,LastModifiedDate,Version]' \
  --output table
Verify the following names:
/cloudmart/{environment}/db/endpoint
/cloudmart/{environment}/db/name
/cloudmart/{environment}/db/port
/cloudmart/{environment}/db/username
/cloudmart/{environment}/db/password
/cloudmart/{environment}/s3/report-bucket
/cloudmart/{environment}/s3/artifact-bucket
/cloudmart/{environment}/auth/token
The following path may appear only as a cleanup/legacy artifact and is not a runtime customer-token store:
/cloudmart/{environment}/auth/customer-tokens
To verify a specific non-secret parameter:
aws ssm get-parameter \
  --name /cloudmart/dev/db/name \
  --region ap-south-1 \
  --query 'Parameter.[Name,Type,Value]' \
  --output table
Do not use the same output pattern for the password or administrator token because SecureString values must not be copied into evidence.
4. Pre-deployment checklist
Check	What to confirm
Branch and commit	Intended, reviewed branch and commit are selected
Workflow	.github/workflows/deploy.yaml has correct paths, stack order, secret references, and environment handling
Configuration	config/config.json is valid and targets the intended environment
Network parameters	Parameter keys exactly match network-stack.yaml
Templates	All five templates pass cfn-lint without blocking errors
Source files	Lambda handlers, schema, layer, and dashboard files referenced by workflow exist
OIDC secret	AWS_ROLE_ARN exists and contains the intended role ARN; YAML references the same name
OIDC trust	AWS trust policy is scoped to the intended repository and branch/environment
Alert secret	CLOUDMART_ALERT_EMAIL is configured
Database input	Strong db_password is ready and meets workflow validation
AWS target	Correct account and ap-south-1 region
Existing resources	Current stack states/outputs reviewed; no unintended replacement or deletion
Cost/data	Costs, backups, retention, and production approval reviewed


Optional local lint commands from the repository root:
python -m pip install --quiet cfn-lint
cfn-lint -t cloudformation/network-stack.yaml --non-zero-exit-code error
cfn-lint -t cloudformation/data-stack.yaml --non-zero-exit-code error
cfn-lint -t cloudformation/iam-stack.yaml --non-zero-exit-code error
cfn-lint -t cloudformation/application-events-stack.yaml --non-zero-exit-code error
cfn-lint -t cloudformation/api-monitoring-ec2-stack.yaml --non-zero-exit-code error
Resolve errors before deployment. Review warnings, especially unused parameters, invalid references, and replacement-sensitive properties.
5. Execute the GitHub Actions workflow
The workflow is manually triggered with workflow_dispatch; pushing code alone does not start it.
1. Commit and push the reviewed changes to the intended branch.
2. Open the GitHub repository and select Actions.
3. Choose CloudMart Infrastructure Deployment.
4. Click Run workflow and select the branch.
5. Enter the required db_password input without exposing it elsewhere.
6. Start the run and monitor each job/step.
7. Save the workflow run URL/ID, commit SHA, environment, and final status for review.
What the workflow does
1. Validates configuration and source: checks configuration and required files.
2. Authenticates to AWS: uses GitHub OIDC and the configured IAM role to obtain temporary AWS credentials.
3. Deploys Network-Security: creates or updates VPC networking resources.
4. Deploys Data-Storage: provisions RDS, S3 buckets, and database parameters.
5. Deploys IAM: creates execution roles and policies.
6. Packages and uploads artifacts: uploads the PyMySQL layer, Lambda packages, schema, dashboard source, and required template artifacts using commit-specific S3 keys.
7. Deploys Application-Events: provisions functions, schema initialization, EventBridge, and SNS.
8. Deploys API-Monitoring-EC2: provisions API/monitoring/dashboard resources and refreshes the existing dashboard EC2 through SSM.
9. Verifies deployment: checks stack/function outputs and dashboard health as implemented in the workflow.
Large CloudFormation templates and artifacts
Templates larger than 51,200 bytes must be deployed using an S3 template bucket. The Application-Events and API-Monitoring-EC2 deploy commands should use the existing artifact bucket created by Data-Storage via --s3-bucket. Do not create another bucket manually. Keep the commit-SHA artifact keys used by the workflow aligned with the values passed to CloudFormation.
The API-Monitoring-EC2 template parameters identified in the supplied runbook are Environment, EC2InstanceType, DashboardPort, and NginxPort. Do not pass Lambda S3 keys or database SSM parameter names to that stack unless its template is intentionally updated to declare them.
Deployment lifecycle and artifact timing
Reviewed code is committed and pushed
        |
        v
Actions > CloudMart Infrastructure Deployment > Run workflow
        |
        v
Select branch and supply db_password input
        |
        v
Validate configuration, parameter files, templates, and source paths
        |
        v
GitHub OIDC token -> AWS STS -> temporary deployment-role credentials
        |
        v
1. Network-Security stack
        |
        v
2. Data-Storage stack: RDS + artifact/report S3 buckets + DB SSM parameters
        |
        v
3. IAM stack: runtime roles, policies, instance profile/auth parameters
        |
        v
Build/package and upload commit-specific Lambda/layer/schema artifacts
to the artifact bucket
        |
        v
4. Application-Events stack: Lambda resources, schema initialization,
EventBridge rules, and SNS resources
        |
        v
Upload dashboard source; 5. API-Monitoring-EC2 stack; refresh existing
dashboard EC2 via SSM
        |
        v
Run configured report/health checks; record outputs and evidence
When objects are stored in S3
- The Data-Storage stack creates the deployment artifact bucket and the separate report bucket. Downstream workflow steps use the artifact bucket name from that stack's outputs.
- Lambda ZIPs, the PyMySQL layer, schema SQL, and dashboard source are uploaded to the artifact bucket after the bucket exists and before the relevant stack/resource or dashboard refresh consumes them. The workflow uses commit-specific object keys to associate artifacts with the source revision.
- For CloudFormation templates larger than 51,200 bytes, the deployment command uses the existing artifact bucket with --s3-bucket; template packaging/upload occurs as part of submitting that stack deployment.
- The report bucket stores generated CSV reports. The Daily Report Lambda writes those after it is invoked (including the workflow's configured post-deployment invocation and the scheduled run), not as part of uploading the source code.
- The dashboard reads its application files from the artifact bucket and report files from the report bucket. Do not treat these as one bucket or one deployment phase.
Deployment failure/retry: stop at the first failed step, inspect its error and CloudFormation events, fix the source/configuration in Git, run validation, and start a fresh manual workflow run. Do not manually create replacement AWS resources or repeatedly rerun without resolving the underlying failure.
6. Post-deployment verification
Record actual values from CloudFormation outputs and test results. Do not invent endpoint URLs, resource IDs, or bucket names.
6.1 Stack status
Example for the development network stack:
aws cloudformation describe-stacks \
  --stack-name cloudmart-network-security-dev \
  --region ap-south-1 \
  --query 'Stacks[0].[StackName,StackStatus]' \
  --output table
Repeat for cloudmart-data-storage-dev, cloudmart-iam-dev, cloudmart-application-events-dev, and cloudmart-api-monitoring-ec2-dev. Replace dev with prod for production. Successful statuses include CREATE_COMPLETE and UPDATE_COMPLETE. Investigate any in-progress, failed, or rollback state before declaring completion.
6.2 Stack outputs
aws cloudformation describe-stacks \
  --stack-name cloudmart-api-monitoring-ec2-dev \
  --region ap-south-1 \
  --query 'Stacks[0].Outputs[*].[OutputKey,OutputValue]' \
  --output table
Record the verified API invoke URL, dashboard URL, artifact/report bucket names, and relevant IDs from actual outputs.
6.3 Lambda and CloudWatch Logs
Confirm the authorizer, product, customer, order, order-processor, and daily-report functions exist and their deployments succeeded. For errors, inspect the relevant CloudWatch log groups and correlate timestamps, request IDs, exceptions, and duration. Check Lambda configuration, execution role, VPC settings, SSM parameter access, database connectivity, and schema as relevant.
Never place bearer tokens, passwords, customer secrets, or other credentials in logs, screenshots, or evidence.
6.3.1 API resource, route, and authorization inventory
The reviewed API-Monitoring-EC2 template exposes 19 methods across four functional route families.
Route family	Methods	Gateway authorization	Notes
/products + /products/{id}	5	GET collection = public; other methods = CUSTOM	Product listing is intentionally public in the reviewed template
/customers + /customers/{customer_id}	6	POST collection = public; other methods = CUSTOM	POST is public onboarding; customer administration is protected
/customers/{customer_id}/orders + nested routes	5	CUSTOM	Every customer-order route is protected and customer-scoped
/orders + /orders/{id}/status	3	CUSTOM	Administrative order operations


Products
Method	Route	Authorization	Purpose
GET	/products	NONE	Public product catalog listing
POST	/products	CUSTOM	Create product
GET	/products/{id}	CUSTOM	Read one product
PUT	/products/{id}	CUSTOM	Update product
DELETE	/products/{id}	CUSTOM	Soft-delete product


Customers
Method	Route	Authorization	Purpose
POST	/customers	NONE	Customer onboarding
GET	/customers	CUSTOM	Administrative customer listing
GET	/customers/{customer_id}	CUSTOM	Customer lookup
PUT	/customers/{customer_id}	CUSTOM	Full update
PATCH	/customers/{customer_id}	CUSTOM	Partial update
DELETE	/customers/{customer_id}	CUSTOM	Protected/admin soft delete


Customer orders
Method	Route	Authorization	Purpose
POST	/customers/{customer_id}/orders	CUSTOM	Place order
GET	/customers/{customer_id}/orders	CUSTOM	List customer orders
GET	/customers/{customer_id}/orders/{order_id}	CUSTOM	Read one customer order
PUT	/customers/{customer_id}/orders/{order_id}	CUSTOM	Update configured order fields
PATCH	/customers/{customer_id}/orders/{order_id}/status	CUSTOM	Status change under role/ownership rules


Administrative orders
Method	Route	Authorization	Purpose
GET	/orders	CUSTOM	Administrative order listing
GET	/orders/{id}	CUSTOM	Administrative order lookup
PATCH	/orders/{id}/status	CUSTOM	Administrative status update


All Lambda-backed routes use AWS_PROXY integration. The API is REGIONAL. The Lambda Authorizer is an API Gateway TOKEN authorizer whose identity source is the Authorization header. The reviewed template sets the authorizer cache TTL to 0 seconds.
method.request.path.id: true, method.request.path.customer_id: true, and method.request.path.order_id: true declare required path parameters to API Gateway; they do not perform user authentication themselves.
6.4 Authentication tests
Use non-production test credentials. Record sanitized requests, expected/actual results, and evidence.
Test	Expected result
Protected request without Authorization header	Rejected
Protected request with invalid bearer token	Rejected
Valid active customer token	Accepted only for permitted resources
Same customer logs in again	Existing token remains stable, per application behavior
Multiple customers share a token	Customer identities remain distinct
Inactive/soft-deleted customer authenticates	Rejected
Customer accesses another customer's order	Denied


Use Authorization: Bearer <test-token> where required. Do not include real tokens in documentation.
6.5 CRUD and order tests
The runbook now contains the reviewed route matrix and the principal request shapes. The exact commit's handlers remain authoritative for optional fields and route-specific validation. Record method, route, sanitized payload, response/status, and evidence in docs/crud-verification.md.
- Products: create, list, get, update, and soft delete. Verify that GET /products works without a bearer token in the reviewed deployment.
- Customers: public create, protected administrative list, protected get/update/patch, and protected soft delete.
- Orders: place an order with an items array, list/read customer orders, exercise configured update/status routes, and test cross-customer denial.
- Order processing: the normal successful placement path returns CONFIRMED; verify the database transaction, inventory deduction, order items, and order log/event evidence.
- Authorization: customers must not read/update/cancel another customer's order. Administrative/owner cancellation of customer orders must be rejected under the project rules; status changes must follow the current handler's role/state validation.
Do not mark tests passed until executed against the deployed environment and recorded.
6.6 Events, SNS, reports, dashboard, and alarms
- Confirm EventBridge rules are enabled and targets are present.
- In non-production, trigger a controlled low-stock scenario and verify event matching and SNS delivery.
- Test order event/notification behavior with test orders.
- Confirm the scheduled Daily Report Lambda writes an object to the report bucket.
- Confirm SNS email subscriptions are confirmed and delivery works.
- Open the dashboard URL from stack outputs and verify inventory, recent orders, navigation/search/details where implemented, and latest report access.
- Check dashboard health on the configured service and Nginx ports.
- Review the CloudWatch operations dashboard, metrics, alarm states, thresholds, and SNS actions after suitable test activity.
- Confirm the dashboard EC2 is SSM-managed and was refreshed successfully by the workflow.
Avoid unnecessary test orders or alerts in production.
6.7 Drift and source hygiene
Run CloudFormation drift detection for all five stacks and record each result. Drift detection does not necessarily detect application-level divergence, so also review workflow and application deployment evidence. Confirm documentation paths resolve and no credentials or secrets were committed.
7. Troubleshooting
Symptom	Checks and corrective action
OIDC role assumption fails	Verify AWS_ROLE_ARN secret name/value, workflow role-to-assume, AWS OIDC provider, trust conditions, repository/branch identity, and role permissions. The secret value must be an IAM role ARN, not a console URL.
AccessDenied	Identify the denied action/resource from the error or CloudTrail; add only the required least-privilege permission after review.
Configuration validation fails	Check JSON syntax, required keys, allowed environment, and consistency across config, parameter file, and stack name.
Network lint: undefined parameter	Ensure each !Ref matches a declared parameter/resource. Use the names actually defined in the network template.
Network lint: unused parameter	Remove a parameter not in the architecture or reference it in the intended resource. Do not add a monitoring subnet parameter when no such subnet exists in the design.
Template exceeds 51,200 bytes	Use the existing Data-Storage artifact bucket with the deploy command's --s3-bucket option.
Artifact bucket output missing	Inspect Data-Storage stack status, outputs, and events before downstream deployment.
Lambda package/layer unavailable	Verify commit-specific S3 key, object existence, upload result, template reference, and S3 permissions.
Lambda cannot reach RDS	Check VPC/subnets, Lambda-to-RDS security-group ingress, DB endpoint/port SSM parameters, credentials, and schema.
API returns 401	Check bearer header format, authorizer/cache settings, token hash, customer active/deleted state, and admin-token configuration.
API returns 5xx/timeout	Inspect API Gateway and Lambda logs; check integration/Lambda timeouts, DB latency, and processor invocation results.
Order does not confirm	Inspect Order/Order Processor logs, transaction and stock checks, schema, invocation result, and event errors.
SNS email absent	Check subscription confirmation, topic/rule target, event pattern, alarm action, and CLOUDMART_ALERT_EMAIL.
Dashboard/SSM refresh fails	Check EC2 managed-instance status, instance role, S3 access, bootstrap script, service logs, and health endpoint.
Stack update fails/rolls back	Review CloudFormation events/change set, dependencies, exports/imports, permissions, and replacement-sensitive changes. Correct code and redeploy through the workflow; avoid manual resource edits.


Safe retry process
1. Open the failed Actions run and identify the first failing step.
2. Capture the error, stack name, and logical resource ID if shown.
3. Inspect CloudFormation events and relevant service logs.
4. Fix the source, template, or configuration in Git.
5. Run local validation.
6. Push the reviewed fix and start a new manual workflow run.
7. Verify the selected branch, environment, account, and region before retrying.
Do not repeatedly rerun without addressing the cause, and do not manually create resources to bypass CloudFormation.
7.1 Rollback and recovery strategy
Rollback must be driven by the type of failure. Do not treat every failure as a reason to delete all five stacks.
A. Workflow/preflight failure
If validation or authentication fails before infrastructure changes:
1. Stop the workflow.
2. Correct the repository configuration, secret reference, trust policy, or source issue.
3. Re-run validation.
4. Start a new workflow run from the reviewed commit.
No AWS rollback is required if no infrastructure mutation occurred.
B. CloudFormation stack failure
If a stack enters *_FAILED, ROLLBACK_*, or an unexpected in-progress state:
1. Stop downstream deployment.
2. Capture the first failing resource and CloudFormation event.
3. Determine whether the failure is configuration, permission, dependency, quota, artifact, or application-related.
4. Fix the source template/workflow/configuration in Git.
5. Check exports/imports and replacement-sensitive resources.
6. Retry only after the cause is understood.
Do not manually edit the failed resource to make the stack appear healthy.
C. Application verification failure after successful stacks
If all stacks succeed but API/application verification fails:
1. Keep the infrastructure state available for diagnosis unless there is an explicit rollback decision.
2. Inspect API Gateway, Lambda, CloudWatch, SSM, RDS, EventBridge, SNS, and dashboard evidence as applicable.
3. Identify whether the defect is in code, configuration, schema, permissions, or runtime connectivity.
4. Fix the source and deploy a reviewed update.
5. Re-run the complete affected verification gate.
D. Data-risk or destructive change
If a change may replace/delete RDS, remove data, or break production traffic:
- Do not continue solely because CloudFormation can perform the change.
- Verify backup/restore readiness and deletion/retention policies.
- Obtain the required approval.
- Prefer a safe forward fix or controlled restoration procedure over ad-hoc manual changes.
- Record the decision and evidence.
E. Recovery acceptance
A recovery deployment is complete only when the same mandatory acceptance gates are passed again. A successful rollback/recovery action without application verification is not considered a successful release.
8. Teardown
Destructive: Teardown may permanently remove RDS data and S3 objects depending on deletion/retention policies. Back up required data, verify account/region/environment, and obtain approval before proceeding.

Delete stacks through CloudFormation in reverse dependency order:
1. cloudmart-api-monitoring-ec2-{environment}
2. cloudmart-application-events-{environment}
3. cloudmart-iam-{environment}
4. cloudmart-data-storage-{environment}
5. cloudmart-network-security-{environment}
Before deletion, check termination protection, deletion/retention policies, RDS backups, S3 contents, exports/imports, and environment names. Resolve failures through CloudFormation events and the approved infrastructure process rather than manually deleting individual resources.
After teardown, verify stack deletion, inspect retained resources/data, and review any continuing AWS charges.
9. Deployment evidence and sign-off
Complete this record for each deployment using actual run details and outputs.
Evidence	Value to record
Repository / branch	<fill in>
Commit SHA	<fill in>
Actions run URL / ID and status	<fill in>
AWS account / region	<account identifier> / ap-south-1
Environment	<dev / prod>
Assumed OIDC IAM role	<role name or ARN; never credentials>
Network stack status	<fill in>
Data-Storage stack status	<fill in>
IAM stack status	<fill in>
Application-Events stack status	<fill in>
API-Monitoring-EC2 stack status	<fill in>
API URL / dashboard URL	<verified outputs>
Artifact/report bucket names	<verified outputs>
Authentication tests	<pass/fail and evidence location>
CRUD/order tests	<pass/fail and evidence location>
Event/SNS/report tests	<pass/fail and evidence location>
Dashboard/alarms	<pass/fail and evidence location>
Drift results	<result for each stack>
Open issues / accepted risks	<none or details>
Reviewer / approval	<name/date per team process>
Teardown	<completed / not applicable>


What “deployment succeeded” actually means
A green GitHub Actions run is necessary but not sufficient.
A complete successful deployment has all of the following:
1. GitHub Actions successfully authenticated to AWS through OIDC.
2. All five CloudFormation stacks reached CREATE_COMPLETE or UPDATE_COMPLETE.
3. Required exports and SSM parameters exist.
4. RDS is available and the schema initializer successfully created the expected database tables.
5. Lambda functions and their layers exist and have the expected configuration.
6. API Gateway has the expected resources, methods, authorizer, deployment, and stage.
7. EventBridge rules are enabled and their targets are present.
8. SNS topics/subscriptions exist and notification delivery has been tested in a controlled environment.
9. The dashboard EC2 is managed by SSM and the dashboard health check succeeds.
10. CloudWatch logs/dashboard/alarms exist and evidence has been recorded.
11. CRUD, authentication, order, event, report, and dashboard tests have actual results attached to the review.
12. No secrets, tokens, or passwords were exposed in source code or deployment evidence.
9.1 Reviewer acceptance matrix
Review question	Required evidence	Status
Was the exact commit identified?	Branch + commit SHA	<PASS/FAIL>
Did GitHub authenticate through OIDC?	Workflow log + assumed role	<PASS/FAIL>
Were all five stacks successful?	Stack status/output evidence	<PASS/FAIL>
Were cross-stack exports/parameters verified?	Export/SSM evidence	<PASS/FAIL>
Was the database schema verified?	Schema/table evidence	<PASS/FAIL>
Were authentication controls tested?	Sanitized auth test results	<PASS/FAIL>
Were CRUD and order flows tested?	CRUD/order evidence	<PASS/FAIL>
Were EventBridge/SNS/report flows tested?	Event/report/notification evidence	<PASS/FAIL>
Was the dashboard verified through SSM?	Health check + SSM result	<PASS/FAIL>
Were CloudWatch alarms/logs reviewed?	Dashboard/alarm/log evidence	<PASS/FAIL>
Was drift checked?	Drift results for five stacks	<PASS/FAIL>
Were secrets protected?	Repository/log/evidence review	<PASS/FAIL>
Were open risks formally accepted?	Risk/approval record	<PASS/FAIL>
Is the deployment reproducible from source?	Workflow + CloudFormation evidence	<PASS/FAIL>


9.1.1 10/10 scoring map — what this runbook now proves
Use this table as the mentor-facing index. The evidence can be collected during the live review rather than spread across unrelated documents.
Likely scoring area	Where this runbook proves it
Project understanding	Sections 2.6–2.13 explain architecture, responsibilities, flows, and demo sequence
CloudFormation/IaC	Sections 2, 3, 5 and Appendix B
CI/CD + OIDC	Sections 2.1(A), 3.3, 5 and Demo 1
Networking	Section 2.1(B) and Demo 2
Database design	Sections 2.1(C), 2.10 and 6.2/6.5
Authentication/security	Sections 2.8.1, 2.9, 6.4 and Demo 3
API Gateway	Sections 2.1(F), 2.9, 6.3.1 and Demo 3
Lambda/business logic	Sections 2.8 and 2.11
Order/inventory flow	Sections 2.8.4–2.8.5, 2.10, 2.11 and Demo 4
EventBridge/SNS	Section 2.12 and Demo 5
Reporting/S3	Sections 2.8.6, 2.12 and Demo 5
Dashboard/EC2/SSM	Section 2.8.7 and Demo 5
CloudWatch/alarms	Sections 2.1(F), 2.12 and Demo 5
Troubleshooting/recovery	Sections 7 and 7.1
Teardown/data safety	Section 8
Evidence and reproducibility	Section 9 and Section 10


The reviewer can therefore ask “what does this component do, why is it there, how is it connected, how do you test it, and what happens if it fails?” and the runbook has a direct section for each answer.
9.2 Reviewer sign-off statement
The reviewer should approve the deployment only after confirming that the evidence above corresponds to the exact repository commit and AWS environment under review.
Reviewer decision: <APPROVED / CONDITIONALLY APPROVED / REJECTED>
Reviewer: <name>
Date: <date>
Comments / accepted deviations: <none or details>
Completion criteria
The deployment is ready for review when the intended workflow run succeeds, all five stacks reach successful completion states, actual outputs are recorded, application/security/event/report/dashboard checks have evidence, and deviations are resolved or formally accepted. Ensure no credentials or sensitive tokens appear in the repository or evidence.
10. Runbook quality and maintenance controls
This runbook is a controlled operational document, not a substitute for the deployed infrastructure or source repository.
Source-alignment checklist used for this revision
This revision was aligned against the reviewed project materials available with the project: README-cloudmart-revised.md, cloudmart-final-crud-verification.md, the reviewed MySQL schema, the reviewed API-Monitoring-EC2 template that makes GET /products public and POST /customers public, the reviewed order Lambda, the Daily Report Lambda, and the EC2 app.py dashboard implementation. These sources should be rechecked whenever the repository changes.
- Update this runbook when stack names, resource contracts, workflow inputs, authentication behavior, routes, deployment order, artifact handling, or rollback behavior changes.
- Keep implementation-specific claims synchronized with the reviewed repository commit.
- Do not copy real passwords, bearer tokens, secret values, customer data, or private credentials into the runbook.
- Keep deployment evidence separate from reusable procedure text when evidence contains environment-specific identifiers.
- Review IAM permissions periodically and reduce wildcard/destructive permissions where the implementation permits.
- Re-run the complete acceptance checklist after material architecture or deployment-workflow changes.
10/10 quality principle: every important claim in this runbook should be either (a) directly verifiable from the repository/deployed AWS environment or (b) explicitly labeled as an example, expectation, or reviewer-controlled decision.
End of runbook.
Appendix A — Full supplied GitHub Actions deployment-role permissions policy
This is the permissions policy from the accompanying uploaded text file, formatted as JSON. Review its scope before attaching it, especially wildcard resources and destructive permissions.
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "CloudFormationStackManagement",
      "Effect": "Allow",
      "Action": [
        "cloudformation:CreateStack",
        "cloudformation:UpdateStack",
        "cloudformation:DeleteStack",
        "cloudformation:CreateChangeSet",
        "cloudformation:DeleteChangeSet",
        "cloudformation:DescribeChangeSet",
        "cloudformation:ExecuteChangeSet",
        "cloudformation:DescribeStacks",
        "cloudformation:DescribeStackEvents",
        "cloudformation:DescribeStackResources",
        "cloudformation:GetTemplate",
        "cloudformation:GetTemplateSummary",
        "cloudformation:ListChangeSets",
        "cloudformation:ListStackResources",
        "cloudformation:ValidateTemplate"
      ],
      "Resource": "*"
    },
    {
      "Sid": "NetworkInfrastructureManagement",
      "Effect": "Allow",
      "Action": [
        "ec2:CreateVpc",
        "ec2:DeleteVpc",
        "ec2:DescribeVpcs",
        "ec2:DescribeAvailabilityZones",
        "ec2:ModifyVpcAttribute",
        "ec2:CreateTags",
        "ec2:DeleteTags",
        "ec2:DescribeTags",
        "ec2:CreateSubnet",
        "ec2:DeleteSubnet",
        "ec2:DescribeSubnets",
        "ec2:ModifySubnetAttribute",
        "ec2:CreateRouteTable",
        "ec2:DeleteRouteTable",
        "ec2:DescribeRouteTables",
        "ec2:AssociateRouteTable",
        "ec2:DisassociateRouteTable",
        "ec2:CreateRoute",
        "ec2:ReplaceRoute",
        "ec2:DeleteRoute",
        "ec2:CreateInternetGateway",
        "ec2:DeleteInternetGateway",
        "ec2:AttachInternetGateway",
        "ec2:DetachInternetGateway",
        "ec2:DescribeInternetGateways",
        "ec2:CreateSecurityGroup",
        "ec2:DeleteSecurityGroup",
        "ec2:DescribeSecurityGroups",
        "ec2:AuthorizeSecurityGroupIngress",
        "ec2:AuthorizeSecurityGroupEgress",
        "ec2:RevokeSecurityGroupIngress",
        "ec2:RevokeSecurityGroupEgress",
        "ec2:CreateVpcEndpoint",
        "ec2:DeleteVpcEndpoints",
        "ec2:DescribeVpcEndpoints",
        "ec2:ModifyVpcEndpoint",
        "ec2:DescribeNetworkInterfaces",
        "ec2:DescribeVolumes",
        "ec2:DescribeVolumeStatus"
      ],
      "Resource": "*"
    },
    {
      "Sid": "EC2InstanceManagement",
      "Effect": "Allow",
      "Action": [
        "ec2:RunInstances",
        "ec2:TerminateInstances",
        "ec2:DescribeInstances",
        "ec2:DescribeInstanceStatus",
        "ec2:DescribeImages",
        "ec2:DescribeInstanceTypes",
        "ec2:ModifyInstanceAttribute",
        "ec2:StopInstances",
        "ec2:StartInstances",
        "ec2:CreateNetworkInterface",
        "ec2:DeleteNetworkInterface",
        "ec2:AttachNetworkInterface",
        "ec2:DetachNetworkInterface"
      ],
      "Resource": "*"
    },
    {
      "Sid": "RDSManagement",
      "Effect": "Allow",
      "Action": [
        "rds:CreateDBInstance",
        "rds:ModifyDBInstance",
        "rds:DeleteDBInstance",
        "rds:DescribeDBInstances",
        "rds:CreateDBSubnetGroup",
        "rds:ModifyDBSubnetGroup",
        "rds:DeleteDBSubnetGroup",
        "rds:DescribeDBSubnetGroups",
        "rds:ListTagsForResource",
        "rds:AddTagsToResource",
        "rds:RemoveTagsFromResource"
      ],
      "Resource": "*"
    },
    {
      "Sid": "AllowCreateRDSServiceLinkedRole",
      "Effect": "Allow",
      "Action": [
        "iam:CreateServiceLinkedRole"
      ],
      "Resource": "*",
      "Condition": {
        "StringEquals": {
          "iam:AWSServiceName": "rds.amazonaws.com"
        }
      }
    },
    {
      "Sid": "S3BucketManagement",
      "Effect": "Allow",
      "Action": [
        "s3:CreateBucket",
        "s3:DeleteBucket",
        "s3:GetBucketLocation",
        "s3:ListBucket",
        "s3:GetBucketVersioning",
        "s3:PutBucketVersioning",
        "s3:GetEncryptionConfiguration",
        "s3:PutEncryptionConfiguration",
        "s3:GetBucketPublicAccessBlock",
        "s3:PutBucketPublicAccessBlock",
        "s3:GetBucketTagging",
        "s3:PutBucketTagging",
        "s3:GetLifecycleConfiguration",
        "s3:PutLifecycleConfiguration",
        "s3:GetBucketPolicy",
        "s3:PutBucketPolicy",
        "s3:DeleteBucketPolicy"
      ],
      "Resource": "*"
    },
    {
      "Sid": "S3ObjectManagement",
      "Effect": "Allow",
      "Action": [
        "s3:GetObject",
        "s3:PutObject",
        "s3:DeleteObject",
        "s3:GetObjectVersion",
        "s3:DeleteObjectVersion"
      ],
      "Resource": "*"
    },
    {
      "Sid": "APIGatewayManagement",
      "Effect": "Allow",
      "Action": [
        "apigateway:GET",
        "apigateway:POST",
        "apigateway:PUT",
        "apigateway:PATCH",
        "apigateway:DELETE"
      ],
      "Resource": "*"
    },
    {
      "Sid": "CloudMartLambdaManagement",
      "Effect": "Allow",
      "Action": [
        "lambda:CreateFunction",
        "lambda:GetFunction",
        "lambda:GetFunctionConfiguration",
        "lambda:UpdateFunctionCode",
        "lambda:UpdateFunctionConfiguration",
        "lambda:DeleteFunction",
        "lambda:PublishVersion",
        "lambda:ListVersionsByFunction",
        "lambda:CreateAlias",
        "lambda:UpdateAlias",
        "lambda:DeleteAlias",
        "lambda:GetAlias",
        "lambda:AddPermission",
        "lambda:RemovePermission",
        "lambda:TagResource",
        "lambda:UntagResource",
        "lambda:ListTags"
      ],
      "Resource": "arn:aws:lambda:ap-south-1:285150348844:function:cloudmart-*"
    },
    {
      "Sid": "CloudMartLambdaInvocation",
      "Effect": "Allow",
      "Action": [
        "lambda:InvokeFunction"
      ],
      "Resource": "arn:aws:lambda:ap-south-1:285150348844:function:cloudmart-*"
    },
    {
      "Sid": "CloudMartLambdaLayerPublish",
      "Effect": "Allow",
      "Action": [
        "lambda:PublishLayerVersion"
      ],
      "Resource": "*"
    },
    {
      "Sid": "CloudMartLambdaLayerVersionManagement",
      "Effect": "Allow",
      "Action": [
        "lambda:GetLayerVersion",
        "lambda:DeleteLayerVersion"
      ],
      "Resource": "arn:aws:lambda:ap-south-1:285150348844:layer:cloudmart-*:*"
    },
    {
      "Sid": "CloudMartLambdaLayerList",
      "Effect": "Allow",
      "Action": [
        "lambda:ListLayerVersions"
      ],
      "Resource": "*"
    },
    {
      "Sid": "CloudMartRoleManagement",
      "Effect": "Allow",
      "Action": [
        "iam:CreateRole",
        "iam:GetRole",
        "iam:UpdateRole",
        "iam:UpdateAssumeRolePolicy",
        "iam:DeleteRole",
        "iam:TagRole",
        "iam:UntagRole",
        "iam:PutRolePolicy",
        "iam:GetRolePolicy",
        "iam:DeleteRolePolicy",
        "iam:ListRolePolicies",
        "iam:AttachRolePolicy",
        "iam:DetachRolePolicy",
        "iam:ListAttachedRolePolicies"
      ],
      "Resource": "arn:aws:iam::285150348844:role/cloudmart-*"
    },
    {
      "Sid": "CloudMartInstanceProfileManagement",
      "Effect": "Allow",
      "Action": [
        "iam:CreateInstanceProfile",
        "iam:GetInstanceProfile",
        "iam:DeleteInstanceProfile",
        "iam:AddRoleToInstanceProfile",
        "iam:RemoveRoleFromInstanceProfile"
      ],
      "Resource": "arn:aws:iam::285150348844:instance-profile/cloudmart-*"
    },
    {
      "Sid": "PassCloudMartRoles",
      "Effect": "Allow",
      "Action": [
        "iam:PassRole"
      ],
      "Resource": "arn:aws:iam::285150348844:role/cloudmart-*"
    },
    {
      "Sid": "CloudWatchAlarmManagement",
      "Effect": "Allow",
      "Action": [
        "cloudwatch:PutMetricAlarm",
        "cloudwatch:DeleteAlarms",
        "cloudwatch:DescribeAlarms",
        "cloudwatch:DescribeAlarmsForMetric",
        "cloudwatch:GetMetricData",
        "cloudwatch:GetMetricStatistics",
        "cloudwatch:ListMetrics",
        "cloudwatch:TagResource",
        "cloudwatch:UntagResource",
        "cloudwatch:ListTagsForResource"
      ],
      "Resource": "*"
    },
    {
      "Sid": "CloudWatchDashboardManagement",
      "Effect": "Allow",
      "Action": [
        "cloudwatch:PutDashboard",
        "cloudwatch:GetDashboard",
        "cloudwatch:DeleteDashboards",
        "cloudwatch:ListDashboards"
      ],
      "Resource": "*"
    },
    {
      "Sid": "CloudWatchLogsManagement",
      "Effect": "Allow",
      "Action": [
        "logs:CreateLogGroup",
        "logs:DeleteLogGroup",
        "logs:DescribeLogGroups",
        "logs:PutRetentionPolicy",
        "logs:DeleteRetentionPolicy",
        "logs:TagResource",
        "logs:UntagResource",
        "logs:ListTagsForResource",
        "logs:CreateLogStream",
        "logs:DeleteLogStream",
        "logs:DescribeLogStreams",
        "logs:PutLogEvents"
      ],
      "Resource": "*"
    },
    {
      "Sid": "EventBridgeManagement",
      "Effect": "Allow",
      "Action": [
        "events:CreateEventBus",
        "events:DeleteEventBus",
        "events:DescribeEventBus",
        "events:PutRule",
        "events:DeleteRule",
        "events:DescribeRule",
        "events:EnableRule",
        "events:DisableRule",
        "events:PutTargets",
        "events:RemoveTargets",
        "events:TagResource",
        "events:UntagResource",
        "events:ListTagsForResource",
        "events:ListRules",
        "events:ListTargetsByRule"
      ],
      "Resource": "*"
    },
    {
      "Sid": "EventBridgeSchedulerManagement",
      "Effect": "Allow",
      "Action": [
        "scheduler:CreateSchedule",
        "scheduler:UpdateSchedule",
        "scheduler:DeleteSchedule",
        "scheduler:GetSchedule",
        "scheduler:CreateScheduleGroup",
        "scheduler:DeleteScheduleGroup",
        "scheduler:GetScheduleGroup",
        "scheduler:TagResource",
        "scheduler:UntagResource",
        "scheduler:ListTagsForResource"
      ],
      "Resource": "*"
    },
    {
      "Sid": "SNSManagement",
      "Effect": "Allow",
      "Action": [
        "sns:CreateTopic",
        "sns:DeleteTopic",
        "sns:GetTopicAttributes",
        "sns:SetTopicAttributes",
        "sns:Subscribe",
        "sns:Unsubscribe",
        "sns:GetSubscriptionAttributes",
        "sns:SetSubscriptionAttributes",
        "sns:ListSubscriptionsByTopic",
        "sns:ListTagsForResource",
        "sns:TagResource",
        "sns:UntagResource"
      ],
      "Resource": "*"
    },
    {
      "Sid": "CloudMartSSMParameterManagement",
      "Effect": "Allow",
      "Action": [
        "ssm:GetParameter",
        "ssm:GetParameters",
        "ssm:GetParametersByPath",
        "ssm:PutParameter",
        "ssm:DeleteParameter",
        "ssm:DeleteParameters",
        "ssm:AddTagsToResource",
        "ssm:RemoveTagsFromResource",
        "ssm:ListTagsForResource"
      ],
      "Resource": "arn:aws:ssm:ap-south-1:285150348844:parameter/cloudmart/*"
    },
    {
      "Sid": "CloudMartSSMParameterDescribe",
      "Effect": "Allow",
      "Action": [
        "ssm:DescribeParameters"
      ],
      "Resource": "*"
    },
    {
      "Sid": "EC2DashboardSSMManagement",
      "Effect": "Allow",
      "Action": [
        "ssm:SendCommand",
        "ssm:GetCommandInvocation",
        "ssm:ListCommandInvocations",
        "ssm:ListCommands"
      ],
      "Resource": "*"
    },
    {
      "Sid": "ReadAmazonLinuxPublicAMI",
      "Effect": "Allow",
      "Action": [
        "ssm:GetParameter",
        "ssm:GetParameters"
      ],
      "Resource": "arn:aws:ssm:ap-south-1::parameter/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
    },
    {
      "Sid": "PassCloudFormationServiceRole",
      "Effect": "Allow",
      "Action": [
        "iam:PassRole"
      ],
      "Resource": "arn:aws:iam::285150348844:role/CloudMart-CloudFormation-ServiceRole"
    },
    {
      "Sid": "ReadCallerIdentity",
      "Effect": "Allow",
      "Action": [
        "sts:GetCallerIdentity"
      ],
      "Resource": "*"
    }
  ]
}
Appendix B — Resource-by-resource review evidence checklist
Use this table during the mentor review. The reviewer can trace each resource back to its stack and then to an observable AWS object.
Review area	Evidence to show
GitHub Actions	Workflow run URL, branch, commit SHA, success status
OIDC	Successful Configure AWS Credentials step and assumed role identity
Network	VPC ID, three subnet IDs, route tables, four security groups, seven VPC endpoints, no NAT Gateway
RDS	DB identifier, endpoint, subnet group, security group, status available
S3	Artifact bucket and report bucket names; sample deployment artifact and sample report object
SSM	Parameter names/types under /cloudmart/{environment} without revealing SecureString values
IAM	Lambda runtime role names, EC2 dashboard role/instance profile, authentication writer role
Lambda	Authorizer, Customer, Product, Order, Order Processor, Schema Initializer, Daily Report, plus PyMySQL layer
Database	categories, customers, products, orders, order_items, order_logs tables
EventBridge	Custom event bus, daily report rule, low-stock rule, order-failed rule, targets
SNS	Product alert topic, order alert topic, email subscriptions, topic policies
API Gateway	REST API, TOKEN authorizer, resources, methods, deployment ID, stage
CloudWatch	API access log group, operations dashboard, alarms
EC2 dashboard	Instance ID, public URL, SSM managed status, application health
End-to-end	Authentication tests, CRUD tests, order test, event/SNS test, daily report test, dashboard test


Final reviewer explanation
The project is intentionally split into five CloudFormation stacks so that the network foundation, data services, IAM, application/event layer, and API/monitoring/dashboard layer have clear responsibilities and dependency boundaries. SSM Parameter Store is the configuration bridge between infrastructure and application runtime: non-sensitive values are stored as strings, while the RDS password and administrator authentication token are SecureString values. The workflow never creates a parallel/manual version of these resources; CloudFormation remains the source of truth.