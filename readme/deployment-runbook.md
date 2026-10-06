Workflow: .github/workflows/deploy.yaml
Workflow name: CloudMart Infrastructure Deployment
Trigger: Manual workflow_dispatch
AWS Region: ap-south-1
Environment: dev or prod, from config/config.json
Infrastructure: AWS CloudFormation
AWS authentication: GitHub Actions OIDC with an AWS IAM deployment role
This runbook documents the deployment process and verification steps. During each run, record the actual workflow URL, commit SHA, stack outputs, test results, and evidence. This document alone is not proof of a successful fresh-account deployment.

1. Purpose
This runbook describes how to deploy, verify, troubleshoot, and tear down CloudMart using GitHub Actions and CloudFormation. The workflow validates configuration and source files, obtains temporary AWS credentials through GitHub OIDC, deploys infrastructure in dependency order, uploads application artifacts, initializes the database schema, refreshes the existing EC2 dashboard through Systems Manager (SSM), and performs post-deployment checks.
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
6.3.1 API resource and route inventory
The API-Monitoring-EC2 template defines four functional route families:
Route family	Methods implemented	Purpose
/products and /products/{id}	5	Product create, list, read, update, delete.
/customers and /customers/{customer_id}	6	Customer registration, list/read, update/patch, and soft delete.
/customers/{customer_id}/orders and nested order routes	5	Customer-scoped order creation, listing, read, update, and status change.
/orders and /orders/{id}/status	3	Administrative order list/read/status operations.


The deployed template uses API Gateway AWS_PROXY integrations to Lambda. API Gateway passes request data to Lambda, and Lambda performs the business logic.
method.request.path.id: true or equivalent path-parameter declarations tell API Gateway that the route contains a required path parameter and ensure the path value is available to the integration.
The API is a regional API (EndpointConfiguration: REGIONAL).
The Lambda Authorizer is an API Gateway TOKEN authorizer. Its identity source is the Authorization header.
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
Use the current Lambda handlers and database/schema.sql as the authority for route names and payload shapes. Record method, route, sanitized payload, response/status, and evidence in docs/crud-verification.md.
- Products: create, list, get, update, and delete.
- Customers: create, administrative list, get, update/patch, and soft delete, where supported by the handlers.
- Orders: place an order, list/read customer orders, and exercise configured update/status routes.
- Order processing: successful placement should return the processor's final CONFIRMED status rather than an asynchronous PENDING acknowledgement, as specified in the supplied runbook.
- Authorization: customers must not read or cancel another customer's order. Administrative/owner cancellation should be rejected; status changes must follow the current code's role and status rules.
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
Completion criteria
The deployment is ready for review when the intended workflow run succeeds, all five stacks reach successful completion states, actual outputs are recorded, application/security/event/report/dashboard checks have evidence, and deviations are resolved or formally accepted. Ensure no credentials or sensitive tokens appear in the repository or evidence.
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
Database	categories, customers, products, orders, order_items tables
EventBridge	Custom event bus, daily report rule, low-stock rule, order-failed rule, targets
SNS	Product alert topic, order alert topic, email subscriptions, topic policies
API Gateway	REST API, TOKEN authorizer, resources, methods, deployment ID, stage
CloudWatch	API access log group, operations dashboard, alarms
EC2 dashboard	Instance ID, public URL, SSM managed status, application health
End-to-end	Authentication tests, CRUD tests, order test, event/SNS test, daily report test, dashboard test

