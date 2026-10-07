CloudMart Deployment Runbook

1. Document Purpose

This runbook is the operational procedure for deploying, validating, troubleshooting, updating, and tearing down the CloudMart application using GitHub Actions, GitHub OIDC, AWS IAM, and AWS CloudFormation.

Source of truth: the CloudMart repository, especially:

.github/workflows/deploy.yaml

config/config.json

config/network-parameters.json (if present/used by the selected revision)

cloudformation/*.yaml

lambda/**

database/schema.sql

dashboard/**

This runbook intentionally does not contain an architecture section. It documents deployment, resources, configuration, security, verification, troubleshooting, and teardown.

2. Deployment Baseline

Item

Value

Project

CloudMart

AWS Region

ap-south-1

Environments

dev, prod

Deployment mechanism

GitHub Actions + AWS CloudFormation

Workflow

.github/workflows/deploy.yaml

Workflow trigger

Manual workflow_dispatch

AWS authentication

GitHub Actions OIDC

Deployment role secret

AWS_ROLE_ARN

Notification secret

CLOUDMART_ALERT_EMAIL

Database password

Manual workflow_dispatch input: db_password

IaC source of truth

CloudFormation templates

Database

Amazon RDS MySQL

Application runtime

Python 3.12 Lambda

Dashboard runtime

EC2 + Flask/Gunicorn + Nginx

API

API Gateway REST API

Database connectivity

VPC-enabled Lambda functions

Eventing

EventBridge

Notifications

SNS

Monitoring

CloudWatch

Operational commands

AWS Systems Manager (SSM)

3. Repository Components Required for Deployment

Before running the deployment, verify that the selected branch contains these components.

3.1 GitHub Actions

.github/
└── workflows/
    └── deploy.yaml

The workflow contains these deployment jobs:

load-config

deploy-network

deploy-data

deploy-iam

deploy-application-events

deploy-api-monitoring-ec2

The workflow is manually triggered. A normal push does not automatically start deployment unless the workflow is changed to add another trigger.

3.2 CloudFormation templates

cloudformation/
├── network-stack.yaml
├── data-stack.yaml
├── iam-stack.yaml
├── application-events-stack.yaml
└── api-monitoring-ec2-stack.yaml

3.3 Lambda source

lambda/
├── authorizer/
│   └── lambda_function.py
├── customer/
│   ├── lambda_function.py
│   └── requirements.txt
├── product/
│   ├── lambda_function.py
│   └── requirements.txt
├── order/
│   └── lambda_function.py
├── order-processor/
│   └── lambda_function.py
└── daily-report/
    └── lambda_function.py

The deployment workflow also checks for requirements files where applicable and packages the Lambda artifacts.

3.4 Database

database/
└── schema.sql

The schema initializer consumes this SQL through the application-events deployment.

3.5 Dashboard

dashboard/
├── app.py
├── requirements.txt
├── bootstrap-dashboard.sh
└── templates/
    └── index.html

3.6 Configuration

config/
└── config.json

Do not place passwords, bearer tokens, access keys, or other secrets in configuration files.

4. GitHub OIDC Setup

4.1 Why OIDC is required

CloudMart uses GitHub Actions OIDC instead of storing long-lived AWS access keys in GitHub.

The authentication sequence is:

GitHub Actions
    |
    | requests OIDC identity token
    v
GitHub OIDC provider
    |
    | token presented to AWS STS
    v
AWS STS AssumeRoleWithWebIdentity
    |
    v
GitHub Actions deployment IAM role
    |
    v
Temporary AWS credentials
    |
    v
CloudFormation / S3 / SSM / Lambda / verification commands

4.2 AWS OIDC provider

The repository deployment account is configured to use:

arn:aws:iam::285150348844:oidc-provider/token.actions.githubusercontent.com

The OIDC provider audience must include:

sts.amazonaws.com

4.3 GitHub repository secret: AWS_ROLE_ARN

Create:

GitHub → Repository → Settings → Secrets and variables → Actions → Repository secrets

Secret name:

AWS_ROLE_ARN

Secret value:

arn:aws:iam::285150348844:role/<GITHUB_ACTIONS_DEPLOYMENT_ROLE>

The value must be the IAM role ARN.

It must not be:

AWS console URL

AWS login URL

access key ID

secret access key

OIDC provider ARN

The workflow references:

role-to-assume: ${{ secrets.AWS_ROLE_ARN }}

The secret name and workflow expression must match exactly.

4.4 GitHub workflow permissions

The workflow must have permissions equivalent to:

permissions:
  id-token: write
  contents: read

id-token: write is required so GitHub can issue the OIDC token.

contents: read is required so the workflow can check out the repository.

4.5 IAM trust policy

The deployment role trust relationship must trust GitHub's OIDC provider and restrict the subject to the intended CloudMart repository and deployment branch/environment.

For branch-based deployments, the repository subject format is:

repo:Srihitha01/cloudmart:ref:refs/heads/main

and, if required:

repo:Srihitha01/cloudmart:ref:refs/heads/feature/order-flow

Example trust relationship:

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
            "repo:Srihitha01/cloudmart:ref:refs/heads/main",
            "repo:Srihitha01/cloudmart:ref:refs/heads/feature/order-flow"
          ]
        }
      }
    }
  ]
}

If GitHub Environments are used, use the environment subject required by the workflow, for example:

repo:Srihitha01/cloudmart:environment:prod

Do not broaden the trust relationship unnecessarily.

5. GitHub Actions Deployment Role

The GitHub Actions deployment role is responsible for orchestration. It is not a Lambda execution role and it is not the EC2 runtime role.

The deployment role requires permissions for the actions actually performed by the workflow, including:

CloudFormation stack deployment and inspection

VPC/network resource management

EC2 resource management

RDS management

S3 bucket/object operations

API Gateway management

Lambda deployment and invocation

Lambda layer publishing

IAM role/instance-profile management

iam:PassRole for approved CloudMart roles

CloudWatch alarms and dashboards

CloudWatch Logs

EventBridge rules and schedules

SNS topics/subscriptions

SSM Parameter Store

SSM Run Command

STS GetCallerIdentity

The deployment policy should be reviewed for least privilege before production use. In particular, avoid unnecessarily broad Resource: "*" permissions and tightly constrain iam:PassRole.

6. Required GitHub Secret and Input Values

6.1 AWS_ROLE_ARN

Required for OIDC authentication.

AWS_ROLE_ARN=<IAM deployment role ARN>

6.2 CLOUDMART_ALERT_EMAIL

Required for SNS notification subscriptions.

Create:

CLOUDMART_ALERT_EMAIL

under GitHub repository Actions secrets.

Do not hard-code the notification email in CloudFormation source.

6.3 db_password

The workflow asks for:

db_password

at manual workflow execution.

Requirements from the workflow:

Must be provided.

Minimum length: 12 characters.

Use a strong unique password.

Do not commit it.

Do not put it in a parameter JSON file.

Do not print it in logs.

The workflow masks the input before continuing.

7. Configuration

7.1 config/config.json

This file supplies deployment metadata including:

Environment

Project

ManagedBy

Owner

The environment must be one of:

dev
prod

The environment must match the stack names and workflow target.

Runtime SSM parameter paths use:

/cloudmart/{environment}/...

No credentials belong in this file.

7.2 Network parameters

The network stack declares:

Parameter

Example

Environment

dev

VpcCidr

10.0.0.0/16

PublicSubnetCidr

10.0.1.0/24

PrivateSubnetCidr

10.0.2.0/24

SecondaryPrivateSubnetCidr

10.0.3.0/24

If a network parameter JSON file is used by the selected workflow revision, it must use CloudFormation parameter-array syntax:

[
  {"ParameterKey":"Environment","ParameterValue":"dev"},
  {"ParameterKey":"VpcCidr","ParameterValue":"10.0.0.0/16"},
  {"ParameterKey":"PublicSubnetCidr","ParameterValue":"10.0.1.0/24"},
  {"ParameterKey":"PrivateSubnetCidr","ParameterValue":"10.0.2.0/24"},
  {"ParameterKey":"SecondaryPrivateSubnetCidr","ParameterValue":"10.0.3.0/24"}
]

8. CloudFormation Stack Inventory

CloudMart deploys these five CloudFormation stacks.

Order

Stack

Template

Main responsibility

1

cloudmart-network-security-{environment}

cloudformation/network-stack.yaml

VPC, subnets, routing, security groups, VPC endpoints

2

cloudmart-data-storage-{environment}

cloudformation/data-stack.yaml

RDS, S3 buckets, database parameters

3

cloudmart-iam-{environment}

cloudformation/iam-stack.yaml

Lambda/EC2 IAM roles, instance profiles, authentication parameter resources

4

cloudmart-application-events-{environment}

cloudformation/application-events-stack.yaml

Lambda functions, layer, schema initialization, EventBridge, SNS

5

cloudmart-api-monitoring-ec2-{environment}

cloudformation/api-monitoring-ec2-stack.yaml

API Gateway, authorizer integration, CloudWatch monitoring, EC2 dashboard

Use the same environment value consistently.

For dev, the stack names are:

cloudmart-network-security-dev
cloudmart-data-storage-dev
cloudmart-iam-dev
cloudmart-application-events-dev
cloudmart-api-monitoring-ec2-dev

For prod, replace dev with prod.

9. Complete AWS Resource Inventory

9.1 Network stack resources

CloudFormation resource definitions in network-stack.yaml:

Resource

AWS type

CloudMartVPC

AWS::EC2::VPC

InternetGateway

AWS::EC2::InternetGateway

InternetGatewayAttachment

AWS::EC2::VPCGatewayAttachment

PublicSubnet

AWS::EC2::Subnet

PrivateSubnet

AWS::EC2::Subnet

SecondaryPrivateSubnet

AWS::EC2::Subnet

PublicRouteTable

AWS::EC2::RouteTable

PublicRoute

AWS::EC2::Route

PublicSubnetRouteTableAssociation

AWS::EC2::SubnetRouteTableAssociation

PrivateRouteTable

AWS::EC2::RouteTable

PrivateSubnetRouteTableAssociation

AWS::EC2::SubnetRouteTableAssociation

SecondaryPrivateSubnetRouteTableAssociation

AWS::EC2::SubnetRouteTableAssociation

LambdaSecurityGroup

AWS::EC2::SecurityGroup

EC2SecurityGroup

AWS::EC2::SecurityGroup

RDSSecurityGroup

AWS::EC2::SecurityGroup

VPCEndpointSecurityGroup

AWS::EC2::SecurityGroup

S3GatewayEndpoint

AWS::EC2::VPCEndpoint

SSMEndpoint

AWS::EC2::VPCEndpoint

SSMMessagesEndpoint

AWS::EC2::VPCEndpoint

EC2MessagesEndpoint

AWS::EC2::VPCEndpoint

EventBridgeEndpoint

AWS::EC2::VPCEndpoint

SNSEndpoint

AWS::EC2::VPCEndpoint

CloudWatchLogsEndpoint

AWS::EC2::VPCEndpoint

LambdaVPCEndpoint

AWS::EC2::VPCEndpoint

The network stack exports identifiers consumed by downstream stacks.

Network stack outputs

VpcId

PublicSubnetId

PrivateSubnetId

SecondaryPrivateSubnetId

LambdaSecurityGroupId

RDSSecurityGroupId

EC2SecurityGroupId

VPCEndpointSecurityGroupId

S3EndpointId

SSMEndpointId

SSMMessagesEndpointId

EC2MessagesEndpointId

EventBridgeEndpointId

SNSEndpointId

CloudWatchLogsEndpointId

LambdaEndpointId

9.2 Data storage stack resources

data-stack.yaml creates:

Resource

AWS type

Purpose

CloudMartDBSubnetGroup

AWS::RDS::DBSubnetGroup

RDS subnet group

CloudMartRDS

AWS::RDS::DBInstance

CloudMart MySQL database

CloudMartReportBucket

AWS::S3::Bucket

Generated reports

CloudMartArtifactBucket

AWS::S3::Bucket

Deployment/Lambda/dashboard/CFN artifacts

DBEndpointParameter

AWS::SSM::Parameter

DB endpoint

DBNameParameter

AWS::SSM::Parameter

DB name

DBPortParameter

AWS::SSM::Parameter

DB port

DBUsernameParameter

AWS::SSM::Parameter

DB username

DBPasswordSSMWriterRole

AWS::IAM::Role

Secure parameter writer

DBPasswordSSMWriterFunction

AWS::Lambda::Function

Writes DB password to SSM

DBPasswordParameter

Custom::CloudMartSecureParameter

Secure DB password parameter

ReportBucketParameter

AWS::SSM::Parameter

Report bucket name

ArtifactBucketParameter

AWS::SSM::Parameter

Artifact bucket name

Database SSM parameter paths

/cloudmart/{environment}/db/endpoint
/cloudmart/{environment}/db/name
/cloudmart/{environment}/db/port
/cloudmart/{environment}/db/username
/cloudmart/{environment}/db/password

The password is stored as a secure parameter through the custom resource mechanism.

S3 bucket responsibilities

Artifact bucket

Stores deployment artifacts such as:

PyMySQL Lambda layer

Authorizer Lambda package

Product Lambda package

Customer Lambda package

Order Lambda package

Order Processor Lambda package

Daily Report Lambda package

Database schema

Dashboard source

Large CloudFormation templates when --s3-bucket deployment is required

Report bucket

Stores generated Daily Report CSV files.

Do not treat these two buckets as interchangeable.

Data stack outputs

DBEndpoint

DBPort

DBNameOutput

DBUsernameOutput

DBInstanceIdentifier

RDSSecurityGroupId

ReportBucketName

ReportBucketArn

ArtifactBucketName

ArtifactBucketArn

DBEndpointParameterName

DBNameParameterName

DBPortParameterName

DBUsernameParameterName

DBPasswordParameterName

ReportBucketParameterName

ArtifactBucketParameterName

9.3 IAM stack resources

iam-stack.yaml creates:

Resource

AWS type

LambdaAuthorizerRole

AWS::IAM::Role

ProductLambdaRole

AWS::IAM::Role

CustomerLambdaRole

AWS::IAM::Role

OrderLambdaRole

AWS::IAM::Role

OrderProcessorLambdaRole

AWS::IAM::Role

SchemaInitializerLambdaRole

AWS::IAM::Role

DailyReportLambdaRole

AWS::IAM::Role

RDSAdminEC2Role

AWS::IAM::Role

RDSAdminEC2InstanceProfile

AWS::IAM::InstanceProfile

EC2DashboardRole

AWS::IAM::Role

EC2DashboardInstanceProfile

AWS::IAM::InstanceProfile

AuthSSMWriterRole

AWS::IAM::Role

AuthSSMWriterFunction

AWS::Lambda::Function

AuthSSMParameters

Custom::CloudMartAuthParameters

Authentication SSM paths

The IAM stack provisions authentication-related parameters under:

/cloudmart/{environment}/auth

The code references authentication parameters including:

/cloudmart/{environment}/auth/token
/cloudmart/{environment}/auth/customer-tokens

IAM outputs

LambdaAuthorizerRoleArn

ProductLambdaRoleArn

CustomerLambdaRoleArn

OrderLambdaRoleArn

OrderProcessorLambdaRoleArn

SchemaInitializerLambdaRoleArn

DailyReportLambdaRoleArn

RDSAdminEC2RoleArn

RDSAdminEC2InstanceProfile

EC2DashboardRoleArn

EC2DashboardInstanceProfile

10. Application and Event Resources

application-events-stack.yaml creates the following resources.

10.1 EventBridge event bus

Resource

Type

CloudMartEventBus

AWS::Events::EventBus

10.2 Lambda layer

Resource

Type

PyMySQLLayer

AWS::Lambda::LayerVersion

The workflow builds the layer with:

PyMySQL==1.1.1
cryptography==45.0.6

and uploads the package to the artifact bucket.

10.3 Lambda functions

Logical resource

Function purpose

LambdaAuthorizer

Validates bearer-token authorization

CustomerLambda

Customer creation and customer management

ProductLambda

Product CRUD

OrderLambda

Customer/admin order operations

OrderProcessorLambda

Order processing and confirmation/failure handling

DailyReportLambda

Generates scheduled CSV reports

SchemaInitializerLambda

Applies database/schema.sql

Function naming convention:

cloudmart-{environment}-lambda-authorizer
cloudmart-{environment}-customer-lambda
cloudmart-{environment}-product-lambda
cloudmart-{environment}-order-lambda
cloudmart-{environment}-order-processor-lambda
cloudmart-{environment}-daily-report-lambda
cloudmart-{environment}-schema-initializer-lambda

10.4 Lambda permissions

The stack also contains:

OrderProcessorInvokePermission

DailyReportLambdaInvokePermission

These allow the required AWS services to invoke the corresponding Lambda functions.

10.5 Daily report schedule

Resource:

DailyReportSchedule

Type:

AWS::Events::Rule

It invokes the Daily Report Lambda on its configured schedule.

The generated report is written to the report S3 bucket.

10.6 Database schema initialization

Resource:

DatabaseSchemaInitialization

Type:

Custom::CloudMartDatabaseSchema

The schema initializer uses the SQL stored in:

database/schema.sql

Do not manually run a different schema against the deployed database unless the project procedure explicitly requires it.

11. SNS Notification Resources

The application-events stack creates four SNS topics:

Resource

Purpose

ProductAlertTopic

Product/low-stock alert notification

ProductNotificationTopic

Product event notification

OrderAlertTopic

Order alert notification

OrderNotificationTopic

Order notification

It also creates four email subscriptions:

ProductAlertEmailSubscription

ProductNotificationEmailSubscription

OrderAlertEmailSubscription

OrderNotificationEmailSubscription

The corresponding topic policies are:

ProductAlertTopicPolicy

ProductNotificationTopicPolicy

OrderAlertTopicPolicy

OrderNotificationTopicPolicy

The notification recipient comes from:

CLOUDMART_ALERT_EMAIL

SNS email subscriptions must be confirmed before email delivery can be considered verified.

12. EventBridge Rules

The application-events stack creates these rules:

Rule

Purpose

LowStockEventRule

Handles low-stock events

ProductInventoryLowStockEventRule

Product inventory low-stock event processing

ProductLowStockAlertEventRule

Low-stock alert routing

OrderFailedEventsToSNSRule

Sends failed-order events toward SNS

OrderPendingEventsToSNSRule

Sends pending-order events toward SNS

OrderPlacedEventsToSNSRule

Sends placed-order events toward SNS

OrderConfirmedEventsToSNSRule

Sends confirmed-order events toward SNS

OrderCancelledEventsToSNSRule

Sends cancelled-order events toward SNS

OrderDeliveredEventsToSNSRule

Sends delivered-order events toward SNS

After deployment, verify that all required rules are enabled and have their expected targets.

13. API Gateway and API Resources

api-monitoring-ec2-stack.yaml creates the REST API:

cloudmart-{environment}-api

API endpoint type:

REGIONAL

The stack creates a TOKEN Lambda Authorizer using the Authorization header.

Authorizer result caching is configured with:

AuthorizerResultTtlInSeconds: 0

13.1 API resources

The API contains these route groups:

Products

/products
/products/{id}

Customers

/customers
/customers/{customer_id}

Customer orders

/customers/{customer_id}/orders
/customers/{customer_id}/orders/{order_id}
/customers/{customer_id}/orders/{order_id}/status

Administrative orders

/orders
/orders/{id}
/orders/{id}/status

13.2 API methods

Method

Route

Authorization

GET

/products

Public

POST

/products

Lambda Authorizer

GET

/products/{id}

Public

PUT

/products/{id}

Lambda Authorizer

DELETE

/products/{id}

Lambda Authorizer

POST

/customers

Public

GET

/customers

Lambda Authorizer

GET

/customers/{customer_id}

Lambda Authorizer

PUT

/customers/{customer_id}

Lambda Authorizer

PATCH

/customers/{customer_id}

Lambda Authorizer

DELETE

/customers/{customer_id}

Lambda Authorizer

POST

/customers/{customer_id}/orders

Lambda Authorizer

GET

/customers/{customer_id}/orders

Lambda Authorizer

GET

/customers/{customer_id}/orders/{order_id}

Lambda Authorizer

PUT

/customers/{customer_id}/orders/{order_id}

Lambda Authorizer

PATCH

/customers/{customer_id}/orders/{order_id}/status

Lambda Authorizer

GET

/orders

Lambda Authorizer

GET

/orders/{id}

Lambda Authorizer

PATCH

/orders/{id}/status

Lambda Authorizer

Important public endpoints

The deployed template explicitly makes these product reads public:

GET /products
GET /products/{id}

Customer creation is also public:

POST /customers

Do not add a bearer-token requirement to these routes unless the CloudFormation template is intentionally changed.

14. API Gateway Supporting Resources

The API stack also creates:

AuthorizerLambdaInvokePermission

ProductLambdaInvokePermission

OrderLambdaInvokePermission

CustomerLambdaInvokePermission

ApiDeploymentV12

ApiGatewayLogGroup

ApiStage

The stage name is the selected environment:

dev

or:

prod

The API stage has:

tracing enabled

CloudWatch metrics enabled

INFO logging

data trace disabled

API Gateway logs are written to:

/aws/apigateway/cloudmart-{environment}

with the template-configured retention period.

15. CloudWatch Monitoring Resources

The API/monitoring stack creates:

CloudMartMonitoringDashboard

Dashboard name:

cloudmart-{environment}-operations

The dashboard includes monitoring for API Gateway, Lambda, EC2, and RDS activity.

15.1 CloudWatch alarms

The stack defines:

Alarm resource

Api5XXErrorAlarm

ProductLambdaErrorAlarm

OrderLambdaErrorAlarm

EC2HighCPUAlarm

Api4XXErrorAlarm

ApiLatencyAlarm

ProductLambdaThrottleAlarm

OrderLambdaThrottleAlarm

ProductLambdaDurationAlarm

OrderLambdaDurationAlarm

RdsHighCpuAlarm

RdsLowFreeStorageAlarm

RdsHighConnectionsAlarm

EC2StatusCheckAlarm

After deployment, inspect alarm state and confirm alarm actions point to the intended SNS notification resources where configured.

16. EC2 Dashboard Resources

The API/monitoring stack creates:

CloudMartDashboardInstance

Type:

AWS::EC2::Instance

The stack also creates:

CloudMartDashboardAccessPolicy

Type:

AWS::IAM::Policy

The EC2 instance uses the CloudMart dashboard instance profile created by the IAM stack.

Dashboard defaults

EC2InstanceType: t3.micro
DashboardPort: 5000
NginxPort: 80

The dashboard deployment uses:

Flask

Gunicorn

Nginx

AWS SSM

artifact S3 bucket

report S3 bucket

The workflow refreshes the CloudFormation-managed existing instance using SSM. It must not create a duplicate dashboard instance manually.

The workflow verifies:

cloudmart-dashboard.service
nginx.service
http://127.0.0.1:5000/health
http://127.0.0.1:80/health

The dashboard artifacts expected in S3 are:

dashboard/app.py
dashboard/requirements.txt
dashboard/bootstrap-dashboard.sh
dashboard/templates/index.html

17. CloudFormation Outputs to Record

Network

Record:

VpcId
PublicSubnetId
PrivateSubnetId
SecondaryPrivateSubnetId
LambdaSecurityGroupId
RDSSecurityGroupId
EC2SecurityGroupId
VPCEndpointSecurityGroupId
S3EndpointId
SSMEndpointId
SSMMessagesEndpointId
EC2MessagesEndpointId
EventBridgeEndpointId
SNSEndpointId
CloudWatchLogsEndpointId
LambdaEndpointId

Data

Record:

DBEndpoint
DBPort
DBNameOutput
DBUsernameOutput
DBInstanceIdentifier
ReportBucketName
ArtifactBucketName
DBEndpointParameterName
DBNameParameterName
DBPortParameterName
DBUsernameParameterName
DBPasswordParameterName
ReportBucketParameterName
ArtifactBucketParameterName

IAM

Record role/profile outputs where required for evidence.

Application Events

Record:

EventBusName
EventBusArn
LambdaAuthorizerArn
LambdaAuthorizerName
CustomerLambdaArn
CustomerLambdaName
ProductLambdaArn
ProductLambdaName
OrderLambdaArn
OrderLambdaName
OrderProcessorLambdaArn
OrderProcessorLambdaName
DailyReportLambdaArn
DailyReportLambdaName
DailyReportScheduleArn
SchemaInitializerLambdaArn
SchemaInitializerLambdaName
PyMySQLLayerArn
ProductAlertTopicArn
ProductNotificationTopicArn
OrderAlertTopicArn
OrderNotificationTopicArn
LowStockEventRuleArn
OrderPendingEventsToSNSRuleArn
OrderPlacedEventsToSNSRuleArn
OrderFailedEventsToSNSRuleArn

API/Monitoring/EC2

Record:

ApiId
ApiEndpoint
AuthorizerId
MonitoringDashboardName
DashboardInstanceId
DashboardUrl
DashboardPublicDns

Never record passwords, bearer tokens, or other credentials in the evidence file.

18. Database Schema Verification

The schema is initialized from:

database/schema.sql

The deployed database contains the core tables defined by that schema, including:

categories
customers
products
orders
order_items
order_logs

The schema uses product stock fields for inventory management:

products.stock_quantity
products.reorder_threshold

Customer bearer tokens are stored as hashes by the schema seed/application behavior.

The schema includes soft-delete/status concepts for customers and products.

Database verification

After deployment, verify that:

RDS is available.

The database endpoint is present in SSM.

The schema initializer completed successfully.

The expected tables exist.

Seed/test data is present only where intended.

The application Lambdas can connect to the database.

Do not expose database credentials in logs.

19. Deployment Sequence

Run deployment through GitHub Actions.

The intended dependency sequence is:

1. load-config
       |
2. deploy-network
       |
3. deploy-data
       |
4. deploy-iam
       |
5. deploy-application-events
       |
6. deploy-api-monitoring-ec2
       |
7. final verification

Do not manually skip a failed dependency and deploy downstream resources.

20. Pre-Deployment Checklist

Before clicking Run workflow, confirm:

Correct GitHub repository selected.

Correct branch selected.

Correct commit reviewed.

deploy.yaml exists.

config/config.json is valid.

Environment is dev or prod.

AWS region is ap-south-1.

AWS_ROLE_ARN GitHub secret exists.

AWS_ROLE_ARN contains the correct IAM role ARN.

GitHub OIDC provider exists in AWS.

OIDC audience is sts.amazonaws.com.

IAM trust policy matches the repository and branch/environment.

CLOUDMART_ALERT_EMAIL exists.

SNS email recipient is correct.

Database password is ready.

Database password has at least 12 characters.

No password is committed to Git.

All five CloudFormation templates exist.

All Lambda source files exist.

database/schema.sql exists.

Dashboard files exist.

No unintended manual AWS changes are expected.

Existing CloudFormation stacks have been reviewed.

Production approval/cost review is complete where applicable.

21. Optional Local CloudFormation Validation

From the repository root:

python -m pip install --quiet cfn-lint

cfn-lint -t cloudformation/network-stack.yaml --non-zero-exit-code error
cfn-lint -t cloudformation/data-stack.yaml --non-zero-exit-code error
cfn-lint -t cloudformation/iam-stack.yaml --non-zero-exit-code error
cfn-lint -t cloudformation/application-events-stack.yaml --non-zero-exit-code error
cfn-lint -t cloudformation/api-monitoring-ec2-stack.yaml --non-zero-exit-code error

Resolve blocking errors before deployment.

22. Starting a Deployment

Commit the reviewed changes.

Push them to the intended branch.

Open GitHub.

Open Actions.

Select CloudMart Infrastructure Deployment.

Select Run workflow.

Select the intended branch.

Enter db_password.

Start the workflow.

Monitor each job.

Record the workflow URL, run ID, commit SHA, branch, environment, and final status.

23. What Each Workflow Job Does

23.1 load-config

Loads deployment configuration such as:

environment

project

managed-by value

owner value

The values are passed to downstream jobs.

23.2 deploy-network

The workflow:

Checks out the repository.

Configures AWS credentials through OIDC.

Runs aws sts get-caller-identity.

Validates network-stack.yaml.

Deploys the network CloudFormation stack.

Verifies stack status.

Displays stack outputs.

23.3 deploy-data

The workflow:

Checks out the repository.

Authenticates through OIDC.

Verifies AWS identity.

Masks db_password.

Validates the password.

Validates data-stack.yaml.

Deploys the data stack.

Verifies /cloudmart/{environment}/db parameters.

Verifies stack status.

Displays outputs.

23.4 deploy-iam

The workflow:

Authenticates through OIDC.

Verifies AWS identity.

Runs cfn-lint.

Deploys iam-stack.yaml.

Waits for IAM policy propagation.

Verifies the required Lambda roles.

Verifies authentication SSM parameters.

Displays IAM outputs.

The workflow checks roles for:

product-lambda-role
customer-lambda-role
order-lambda-role
order-processor-role
schema-initializer-role
daily-report-role

23.5 deploy-application-events

The workflow:

Authenticates through OIDC.

Verifies required Lambda/schema/dashboard source files.

Validates the CloudFormation template.

Retrieves the artifact bucket from the data stack.

Creates a clean build directory.

Builds the PyMySQL layer.

Builds the Lambda ZIP packages.

Uploads application artifacts to S3.

Uploads schema/dashboard artifacts.

Deploys the application-events stack.

Verifies stack status and outputs.

Runs configured post-deployment checks.

23.6 deploy-api-monitoring-ec2

The workflow:

Authenticates through OIDC.

Validates the API/monitoring template.

Retrieves the existing CloudFormation-managed artifact bucket.

Uses the artifact bucket for large-template deployment when required.

Deploys the API/monitoring/EC2 stack.

Retrieves the existing dashboard EC2 instance ID.

Verifies dashboard artifacts exist in S3.

Sends an SSM Run Command to the existing instance.

Waits for the SSM command result.

Verifies dashboard and Nginx health endpoints.

Verifies stack status.

Displays API/monitoring outputs.

Displays final status for all five stacks.

24. Large CloudFormation Template Handling

CloudFormation templates larger than 51,200 bytes must be submitted through an S3 template bucket.

CloudMart uses the artifact bucket created by the Data Storage stack.

For example:

aws cloudformation deploy \
  --template-file cloudformation/api-monitoring-ec2-stack.yaml \
  --stack-name "$API_MONITORING_EC2_STACK_NAME" \
  --s3-bucket "$CFN_ARTIFACT_BUCKET" \
  --parameter-overrides Environment="$ENVIRONMENT" \
  --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM \
  --region "$AWS_REGION" \
  --no-fail-on-empty-changeset

Do not create a second manual artifact bucket merely to work around the template-size limit.

25. Artifact Deployment

The workflow packages and uploads artifacts to the CloudFormation-managed artifact bucket.

Expected Lambda artifact categories include:

authorizer-lambda
product-lambda
customer-lambda
order-lambda
order-processor-lambda
daily-report-lambda
pymysql-layer
schema
dashboard

The workflow uses commit-specific artifact keys so the deployed resources can be associated with the source revision.

Before downstream deployment, verify the required object exists in S3.

26. Post-Deployment Verification

26.1 Verify all stack statuses

Example:

aws cloudformation describe-stacks \
  --stack-name cloudmart-network-security-dev \
  --region ap-south-1 \
  --query 'Stacks[0].[StackName,StackStatus]' \
  --output table

Repeat for:

cloudmart-data-storage-dev
cloudmart-iam-dev
cloudmart-application-events-dev
cloudmart-api-monitoring-ec2-dev

Successful states include:

CREATE_COMPLETE
UPDATE_COMPLETE

Do not declare success if any stack is in a failed, rollback, or unresolved state.

27. Verify CloudFormation Outputs

Use:

aws cloudformation describe-stacks \
  --stack-name cloudmart-api-monitoring-ec2-dev \
  --region ap-south-1 \
  --query 'Stacks[0].Outputs[*].[OutputKey,OutputValue]' \
  --output table

Record actual values for:

API endpoint

dashboard URL

dashboard public DNS

API ID

authorizer ID

bucket names

Lambda ARNs

event resources

monitoring dashboard name

Never invent these values in documentation.

28. Verify Lambda Functions

Confirm the following functions exist and are deployed:

cloudmart-{environment}-lambda-authorizer
cloudmart-{environment}-customer-lambda
cloudmart-{environment}-product-lambda
cloudmart-{environment}-order-lambda
cloudmart-{environment}-order-processor-lambda
cloudmart-{environment}-daily-report-lambda
cloudmart-{environment}-schema-initializer-lambda

Check:

runtime

handler

execution role

VPC configuration where applicable

layer attachment

environment variables

S3 code package

timeout/memory settings

CloudWatch logs

29. Verify CloudWatch Logs

Inspect Lambda log groups and API Gateway logs.

Check:

request timestamps

request IDs

exceptions

database errors

authorization failures

timeout errors

order processing failures

report-generation errors

Never place the following in screenshots or evidence:

database password

bearer token

access key

secret key

customer secrets

30. Authentication Verification

Use only test credentials.

Protected endpoint tests

Test

Expected result

Protected route with no Authorization header

Rejected

Protected route with invalid bearer token

Rejected

Protected route with valid active customer token

Accepted when permitted

Soft-deleted/inactive customer token

Rejected

Customer accesses another customer's order

Denied

Same customer logs in again

Existing token behavior is preserved

Multiple customers share the same bearer token

Customer identities remain distinguishable according to application logic

Authorization header format:

Authorization: Bearer <test-token>

Do not put real tokens in this runbook.

31. Product API Verification

Test:

GET    /products
GET    /products/{id}
POST   /products
PUT    /products/{id}
DELETE /products/{id}

Verify:

public GET behavior

authenticated write behavior

product creation

product retrieval

product update

product soft-delete behavior where implemented

stock quantity

reorder threshold

database persistence

event generation where configured

32. Customer API Verification

Test:

POST   /customers
GET    /customers
GET    /customers/{customer_id}
PUT    /customers/{customer_id}
PATCH  /customers/{customer_id}
DELETE /customers/{customer_id}

Verify:

customer creation

customer lookup

update/patch

soft deletion

authentication rules

deleted customer rejection

token behavior

33. Customer Order API Verification

Test:

POST  /customers/{customer_id}/orders
GET   /customers/{customer_id}/orders
GET   /customers/{customer_id}/orders/{order_id}
PUT   /customers/{customer_id}/orders/{order_id}
PATCH /customers/{customer_id}/orders/{order_id}/status

Verify:

correct customer ownership

order creation

order retrieval

order update

status update

inventory deduction

order processing

successful confirmation

failed-order handling

event publication

notification behavior

A customer must not be able to access or cancel another customer's order.

34. Administrative Order API Verification

Test:

GET   /orders
GET   /orders/{id}
PATCH /orders/{id}/status

Verify the role/status restrictions implemented by the deployed Lambda code.

Do not assume that an administrative route automatically grants permission to perform every status transition.

35. EventBridge Verification

Verify rules:

aws events list-rules \
  --region ap-south-1 \
  --query 'Rules[?contains(Name, `cloudmart`)].[Name,State]' \
  --output table

Verify that required rules are:

ENABLED

where expected.

Inspect targets:

aws events list-targets-by-rule \
  --rule <RULE_NAME> \
  --region ap-south-1

Test controlled non-production events for:

low stock

product inventory changes

order placed

order pending

order confirmed

order failed

order cancelled

order delivered

36. SNS Verification

Verify topic existence:

aws sns list-topics --region ap-south-1

Verify subscriptions:

aws sns list-subscriptions \
  --region ap-south-1

Confirm:

correct email address

subscription confirmation

topic policy

EventBridge target

message delivery

Do not mark notification verification as passed until an actual test notification is received/confirmed.

37. Daily Report Verification

Confirm:

DailyReportLambda exists.

DailyReportSchedule exists and is enabled.

The Lambda can read the database.

The Lambda can write to the report bucket.

A CSV report is created.

List report objects:

aws s3 ls s3://<REPORT_BUCKET>/ \
  --region ap-south-1

The actual bucket name must come from CloudFormation output.

38. Dashboard Verification

Get the dashboard output:

aws cloudformation describe-stacks \
  --stack-name cloudmart-api-monitoring-ec2-dev \
  --region ap-south-1 \
  --query 'Stacks[0].Outputs[*].[OutputKey,OutputValue]' \
  --output table

Verify:

DashboardInstanceId

DashboardUrl

DashboardPublicDns

The workflow also verifies:

cloudmart-dashboard.service
nginx.service
/health on the Flask/Gunicorn port
/health through Nginx

Verify dashboard functionality:

inventory display

recent orders

product navigation/details where implemented

customer/order views where implemented

search where implemented

latest report access

page health

report availability

39. SSM Dashboard Refresh Verification

The workflow uses:

AWS-RunShellScript

through Systems Manager.

Verify the EC2 instance is managed by SSM.

Check the command result:

aws ssm get-command-invocation \
  --command-id <COMMAND_ID> \
  --instance-id <INSTANCE_ID> \
  --region ap-south-1

Successful status:

Success

Failure states include:

Failed
Cancelled
TimedOut
Cancelling

If refresh fails, inspect both standard output and standard error before retrying.

40. CloudWatch Monitoring Verification

Open the CloudWatch dashboard:

cloudmart-{environment}-operations

Verify metrics for:

API Gateway latency

API Gateway request count

API Gateway 4XX/5XX errors

Lambda invocations

Lambda errors

Lambda duration

Lambda throttles

Lambda concurrency

EC2 CPU/status/network

RDS IOPS/network activity

Verify alarm state for:

API 4XX

API 5XX

API latency

Product Lambda errors

Order Lambda errors

Product Lambda throttles

Order Lambda throttles

Product Lambda duration

Order Lambda duration

RDS CPU

RDS free storage

RDS connections

EC2 CPU

EC2 status check

41. Database Connectivity Verification

If a Lambda cannot reach RDS, check:

Lambda subnet configuration.

Lambda security group.

RDS security group.

RDS endpoint.

RDS port.

Database name.

Database username.

Secure database password parameter.

SSM access permissions.

VPC endpoints.

Database availability.

Schema initialization.

Use the SSM parameter names generated by CloudFormation rather than hard-coded credentials.

42. Failure Handling and Safe Retry

When a deployment fails:

Stop at the first failed workflow step.

Record the workflow URL/run ID.

Identify the stack and logical resource that failed.

Read CloudFormation stack events.

Read the relevant service logs.

Identify the actual denied action/error.

Fix the source code, CloudFormation template, configuration, or IAM policy.

Run validation again.

Commit and push the fix.

Start a fresh workflow run.

Verify the branch, environment, AWS account, and region before retrying.

Do not repeatedly rerun a failing workflow without fixing the underlying cause.

Do not manually create duplicate AWS resources to bypass CloudFormation.

43. Troubleshooting Guide

Problem

Checks

OIDC AssumeRoleWithWebIdentity failure

AWS_ROLE_ARN, OIDC provider, audience, trust policy, repository/branch subject

Secret not found

GitHub secret name must exactly match AWS_ROLE_ARN

AccessDenied

Identify exact action/resource; add only required permission

iam:PassRole failure

Verify the caller can pass the exact runtime/CloudFormation role

CloudFormation rollback

Inspect stack events and failed logical resource

Template > 51,200 bytes

Use the CloudFormation-managed artifact bucket with --s3-bucket

Artifact missing

Check S3 object key, upload step, bucket output, and IAM permission

Lambda deployment failure

Check package, S3 key, execution role, layer, runtime, handler

Lambda VPC permission error

Check Lambda execution role EC2 network-interface permissions

Lambda cannot reach RDS

Check security groups, subnets, endpoint, port, SSM values

Database table missing

Check schema initializer and database/schema.sql

SQL syntax error

Inspect schema initializer logs and exact SQL statement

API returns 401

Check route authorization, Authorization header, token hash, customer status

Public product GET returns 401

Confirm GET /products and GET /products/{id} remain AuthorizationType: NONE

API returns 5XX

Inspect API Gateway and Lambda logs

API timeout

Check Lambda timeout, DB latency, network path, and downstream invocation

Order remains/ends in wrong status

Inspect Order Lambda, Order Processor Lambda, DB transaction and event logs

Inventory not deducted

Check order transaction, product stock, database row, and processor logs

SNS email absent

Confirm subscription, email confirmation, EventBridge target, topic policy

Low-stock alert absent

Check product stock threshold, emitted event, EventBridge rule, SNS target

Daily report absent

Check schedule, Lambda logs, S3 write permissions, report bucket

Dashboard unavailable

Check EC2 instance, security group, Nginx, Gunicorn, dashboard service

SSM refresh fails

Check SSM managed status, instance role, S3 access, bootstrap script, command output

CloudWatch alarm not changing

Verify metric dimensions, threshold, evaluation period, and action

Stack update unexpectedly replaces resource

Review CloudFormation change set and replacement-sensitive properties

44. OIDC Troubleshooting Checklist

If the workflow fails at AWS authentication, check in this order:

GitHub

AWS_ROLE_ARN exists.

Secret value is an IAM role ARN.

Workflow contains id-token: write.

role-to-assume references secrets.AWS_ROLE_ARN.

Correct branch was selected.

AWS

OIDC provider exists.

Provider URL is GitHub's token endpoint.

Audience contains sts.amazonaws.com.

Trust relationship contains the correct repository subject.

Branch/environment condition matches the workflow.

Deployment role exists.

Deployment role permissions are sufficient.

Never replace OIDC with long-lived AWS access keys merely to bypass an OIDC problem.

45. Drift Detection

After a successful deployment, run CloudFormation drift detection for all five stacks.

The purpose is to identify resources changed outside CloudFormation.

Review:

cloudmart-network-security-{environment}
cloudmart-data-storage-{environment}
cloudmart-iam-{environment}
cloudmart-application-events-{environment}
cloudmart-api-monitoring-ec2-{environment}

Drift detection does not replace application-level verification.

Also confirm:

no unexpected manual resources were created

no credentials were committed

repository artifacts match the deployed commit

stack outputs match the recorded deployment evidence

46. Teardown

WARNING: DESTRUCTIVE OPERATION

Teardown can delete infrastructure and may permanently remove database/S3 data depending on the configured deletion and retention policies. Obtain approval and back up required data before proceeding.

Delete the stacks in reverse dependency order:

1. cloudmart-api-monitoring-ec2-{environment}
2. cloudmart-application-events-{environment}
3. cloudmart-iam-{environment}
4. cloudmart-data-storage-{environment}
5. cloudmart-network-security-{environment}

Before deletion verify:

account

region

environment

termination protection

RDS deletion/backup behavior

S3 deletion/retention behavior

exports/imports

retained resources

required evidence

After teardown:

verify stack deletion

verify expected retained resources

verify required backups

check for continuing AWS charges

47. Deployment Evidence Record

Complete this section for every deployment.

Evidence

Value

Repository

<repository>

Branch

<branch>

Commit SHA

<commit>

GitHub Actions run ID

<run id>

GitHub Actions URL

<URL>

AWS account

<account>

AWS region

ap-south-1

Environment

<dev/prod>

Assumed IAM role

<role name/ARN>

Network stack

<status>

Data stack

<status>

IAM stack

<status>

Application-events stack

<status>

API/monitoring/EC2 stack

<status>

API endpoint

<verified output>

Dashboard URL

<verified output>

Dashboard instance ID

<verified output>

Artifact bucket

<verified output>

Report bucket

<verified output>

Authentication tests

<pass/fail + evidence>

Product CRUD tests

<pass/fail + evidence>

Customer tests

<pass/fail + evidence>

Order tests

<pass/fail + evidence>

EventBridge tests

<pass/fail + evidence>

SNS tests

<pass/fail + evidence>

Daily report test

<pass/fail + evidence>

Dashboard test

<pass/fail + evidence>

CloudWatch alarms

<pass/fail + evidence>

Drift results

<per-stack result>

Open issues

<none/details>

Reviewer

<name/date>

Approval

<name/date>

Teardown

<completed/not applicable>

48. Final Deployment Acceptance Criteria

A CloudMart deployment is ready for review only when all applicable conditions below are satisfied:

GitHub Actions OIDC authentication succeeds.

Correct IAM deployment role is assumed.

Configuration validation succeeds.

All five CloudFormation stacks complete successfully.

All required stack outputs are recorded.

RDS is available.

Database schema initialization succeeds.

Required SSM parameters exist.

All required Lambda functions exist.

Lambda packages/layer are available.

API Gateway is deployed.

Public product GET endpoints behave as configured.

Protected endpoints reject missing/invalid tokens.

Valid authorization succeeds.

Customer ownership checks work.

Product CRUD works.

Customer operations work.

Order operations work.

Inventory updates work.

Order processing works.

EventBridge rules are enabled and tested.

SNS subscriptions are confirmed.

Notification tests succeed.

Daily Report Lambda runs successfully.

Report CSV is written to S3.

EC2 dashboard is running.

SSM dashboard refresh succeeds.

Dashboard health endpoint succeeds.

Dashboard URL is accessible.

CloudWatch dashboard exists.

CloudWatch alarms are present.

Relevant alarms and metrics are reviewed.

CloudFormation drift results are reviewed.

No credentials or bearer tokens are present in repository/evidence.

Open issues are documented or formally accepted.

49. Operational Rules

CloudFormation is the source of truth for AWS infrastructure.

Do not manually create replacement infrastructure.

Do not manually modify CloudFormation-managed resources to bypass deployment problems.

Do not commit AWS credentials, database passwords, bearer tokens, or SNS email secrets.

Use GitHub OIDC for GitHub-to-AWS authentication.

Use SSM Parameter Store for runtime configuration/secrets as implemented by the stacks.

Use CloudFormation outputs for actual resource identifiers and URLs.

Use the existing artifact bucket for deployment artifacts.

Do not create duplicate dashboard EC2 instances.

Investigate the first deployment failure before retrying.

Use sanitized test data for verification.

Record deployment evidence for every release.

Review destructive permissions and iam:PassRole permissions before production use.

Review cost and data-retention implications before production deployment or teardown.
