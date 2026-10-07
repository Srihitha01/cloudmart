# CloudMart Infrastructure Deployment Runbook
**Workflow:** `.github/workflows/deploy.yaml`  
**Workflow name:** `CloudMart Infrastructure Deployment`  
**Trigger:** Manual `workflow_dispatch`  
**AWS Region:** `ap-south-1`  
**Environment:** `dev` or `prod`, from `config/config.json`  
**Infrastructure:** AWS CloudFormation  
**AWS authentication:** GitHub Actions OIDC with an AWS IAM deployment role
> This runbook documents the deployment process and verification steps. During each run, record the actual workflow URL, commit SHA, stack outputs, test results, and evidence. This document alone is not proof of a successful fresh-account deployment.
## 1. Purpose
This runbook describes how to deploy, verify, troubleshoot, and tear down CloudMart using GitHub Actions and CloudFormation. The workflow validates configuration and source files, obtains temporary AWS credentials through GitHub OIDC, deploys infrastructure in dependency order, uploads application artifacts, initializes the database schema, refreshes the existing EC2 dashboard through Systems Manager (SSM), and performs post-deployment checks.
CloudFormation is the source of truth for AWS infrastructure. Do not manually create replacement VPCs, databases, security groups, endpoints, or dashboard instances. The supplied network design has one public subnet and two private subnets, uses VPC endpoints, includes a dedicated database-client EC2 and a separate Flask dashboard EC2, and does not use a NAT Gateway. It does not include a separate monitoring subnet.
## 2. Architecture and stack order
Deploy the five stacks in the following order. Replace `{environment}` with `dev` or `prod`.
| Order | Stack name | Template | Purpose |
|---|---|---|---|
| 1 | `cloudmart-network-security-{environment}` | `cloudformation/network-stack.yaml` | VPC, public/private subnets, internet gateway, route tables, security groups, and VPC endpoints |
| 2 | `cloudmart-data-storage-{environment}` | `cloudformation/data-stack.yaml` | RDS MySQL, S3 artifact/report buckets, database subnet group, and database SSM parameters |
| 3 | `cloudmart-iam-{environment}` | `cloudformation/iam-stack.yaml` | IAM roles and policies for Lambda and EC2, plus authentication-related parameter provisioning |
| 4 | `cloudmart-application-events-{environment}` | `cloudformation/application-events-stack.yaml` | Lambda functions/layer, schema initialization, EventBridge rules, and SNS resources |
| 5 | `cloudmart-api-monitoring-ec2-{environment}` | `cloudformation/api-monitoring-ec2-stack.yaml` | API Gateway, authorizer integration, monitoring resources, and EC2 dashboard |
### Why this order matters
1. **Network-Security** creates the VPC foundation and exports subnet/security-group identifiers consumed by later stacks.
2. **Data-Storage** creates the database and S3 buckets. The artifact bucket stores deployment packages and dashboard/template artifacts; the report bucket stores generated reports.
3. **IAM** creates the roles and policies needed by application and dashboard components.
4. **Application-Events** deploys Lambda and event/notification resources and applies the database schema through the schema initializer.
5. **API-Monitoring-EC2** deploys the API and dashboard/monitoring resources. The workflow retrieves the CloudFormation-managed EC2 instance and refreshes it through SSM; it should not create a duplicate dashboard instance.
## 2.1 Complete CloudFormation resource inventory
The five stacks create more resources than the stack-purpose summary above. The following inventory should remain aligned with the CloudFormation templates used by the deployment. If a template revision changes a logical ID, verify the deployed revision before treating an older name as authoritative.
### Network and security stack — cloudmart-network-security-{environment}
Resource	Type	Purpose
CloudMartVPC	AWS::EC2::VPC	CloudMart VPC; default CIDR 10.0.0.0/16.
InternetGateway	AWS::EC2::InternetGateway	Internet connectivity for the public subnet.
InternetGatewayAttachment	AWS::EC2::VPCGatewayAttachment	Attaches the IGW to the CloudMart VPC.
PublicSubnet	AWS::EC2::Subnet	Public subnet for public-facing EC2 resources.
PrivateSubnet	AWS::EC2::Subnet	Primary private subnet for RDS/Lambda workloads.
SecondaryPrivateSubnet	AWS::EC2::Subnet	Second private subnet used by RDS and private Lambda placement.
PublicRouteTable	AWS::EC2::RouteTable	Public subnet routing table.
PublicRoute	AWS::EC2::Route	0.0.0.0/0 route through the Internet Gateway.
PrivateRouteTable	AWS::EC2::RouteTable	Routing table for both private subnets.
PublicSubnetRouteTableAssociation	AWS::EC2::SubnetRouteTableAssociation	Associates the public subnet with the public route table.
PrivateSubnetRouteTableAssociation	AWS::EC2::SubnetRouteTableAssociation	Associates the primary private subnet with the private route table.
SecondaryPrivateSubnetRouteTableAssociation	AWS::EC2::SubnetRouteTableAssociation	Associates the secondary private subnet with the private route table.
LambdaSecurityGroup	AWS::EC2::SecurityGroup	Network security group for private Lambda functions.
DatabaseClientSecurityGroup	AWS::EC2::SecurityGroup	SSH and network access control for the database client EC2.
RDSSecurityGroup	AWS::EC2::SecurityGroup	Allows MySQL traffic from the intended Lambda and database-client security groups.
EC2SecurityGroup	AWS::EC2::SecurityGroup	Security group for the Flask dashboard EC2.
VPCEndpointSecurityGroup	AWS::EC2::SecurityGroup	HTTPS access control for interface VPC endpoints.
DatabaseClientEC2	AWS::EC2::Instance	Separate database client/admin host in the public subnet; it is not the Flask dashboard instance.
S3GatewayEndpoint	AWS::EC2::VPCEndpoint	Private S3 access without a NAT Gateway.
SSMEndpoint	AWS::EC2::VPCEndpoint	Private Systems Manager API access.
SSMMessagesEndpoint	AWS::EC2::VPCEndpoint	Private SSM message-channel access.
EC2MessagesEndpoint	AWS::EC2::VPCEndpoint	Private EC2 Messages connectivity for SSM.
EventBridgeEndpoint	AWS::EC2::VPCEndpoint	Private EventBridge API access.
SNSEndpoint	AWS::EC2::VPCEndpoint	Private SNS API access.
LambdaVPCEndpoint	AWS::EC2::VPCEndpoint	Private Lambda API access.


Network parameters that must not be omitted: DatabaseClientKeyName, DatabaseClientInstanceType, DatabaseClientAMI, and AllowedSSHCidr. Restrict the SSH CIDR to the administrator's actual source IP/range; do not knowingly deploy the default 0.0.0.0/0 SSH rule in production.
### Data-storage stack — cloudmart-data-storage-{environment}
Resource	Type	Purpose
CloudMartDBSubnetGroup	AWS::RDS::DBSubnetGroup	Places the RDS instance in both private subnets.
CloudMartRDS	AWS::RDS::DBInstance	Private, encrypted MySQL database for CloudMart.
CloudMartReportBucket	AWS::S3::Bucket	Stores generated daily CSV reports; retained and versioned.
CloudMartArtifactBucket	AWS::S3::Bucket	Stores Lambda/layer/schema/dashboard deployment artifacts and large CloudFormation templates; retained, versioned, encrypted, and lifecycle-managed.
DBEndpointParameter	AWS::SSM::Parameter	RDS hostname under /cloudmart/{environment}/db/endpoint.
DBNameParameter	AWS::SSM::Parameter	Database name under /cloudmart/{environment}/db/name.
DBPortParameter	AWS::SSM::Parameter	RDS port under /cloudmart/{environment}/db/port.
DBUsernameParameter	AWS::SSM::Parameter	RDS username under /cloudmart/{environment}/db/username.
DBPasswordSSMWriterRole	AWS::IAM::Role	Execution role for the secure password writer Lambda.
DBPasswordSSMWriterFunction	AWS::Lambda::Function	Writes the RDS password to SSM as SecureString.
DBPasswordParameter	Custom::CloudMartSecureParameter	CloudFormation custom resource for /cloudmart/{environment}/db/password.


Source-of-truth warning: an older data-stack.yaml revision in the project history contains only the RDS/report-bucket portion, while the final data-stack design includes the artifact bucket and SSM password/configuration resources above. The repository file actually deployed by GitHub Actions must match the final design before release.
### IAM and authentication-support stack — cloudmart-iam-{environment}
Resource	Type	Purpose
LambdaAuthorizerRole	AWS::IAM::Role	Authorizer execution permissions.
ProductLambdaRole	AWS::IAM::Role	Product Lambda execution permissions.
CustomerLambdaRole	AWS::IAM::Role	Customer Lambda execution permissions.
OrderLambdaRole	AWS::IAM::Role	Order Lambda permissions, including processor invocation.
OrderProcessorLambdaRole	AWS::IAM::Role	Order Processor database/event permissions.
SchemaInitializerLambdaRole	AWS::IAM::Role	Schema initializer permissions.
DailyReportLambdaRole	AWS::IAM::Role	Daily report database/S3 permissions.
RDSAdminEC2Role	AWS::IAM::Role	RDS administration workflow/instance role.
RDSAdminEC2InstanceProfile	AWS::IAM::InstanceProfile	Instance profile for RDS administration EC2.
EC2DashboardRole	AWS::IAM::Role	Dashboard EC2 runtime permissions.
EC2DashboardInstanceProfile	AWS::IAM::InstanceProfile	Instance profile attached to the Flask dashboard.
AuthSSMWriterRole	AWS::IAM::Role	Role for the authentication SSM writer.
AuthSSMWriterFunction	AWS::Lambda::Function	Creates/updates the admin authentication token in SSM SecureString form.
AuthSSMParameters	Custom::CloudMartAuthParameters	CloudFormation custom resource for the authentication parameter path.


Keep the GitHub OIDC deployment role, CloudFormation service role, and runtime roles separate.
### Application and event stack — cloudmart-application-events-{environment}
Resource	Type	Purpose
CloudMartEventBus	AWS::Events::EventBus	Central CloudMart application event bus.
PyMySQLLayer	AWS::Lambda::LayerVersion	Shared MySQL dependency layer.
LambdaAuthorizer	AWS::Lambda::Function	Authorization logic.
CustomerLambda	AWS::Lambda::Function	Customer management/onboarding and soft-delete logic.
ProductLambda	AWS::Lambda::Function	Product CRUD and inventory logic.
OrderLambda	AWS::Lambda::Function	Customer order API and synchronous processor invocation.
OrderProcessorLambda	AWS::Lambda::Function	Order confirmation, inventory deduction, and order event publishing.
OrderProcessorInvokePermission	AWS::Lambda::Permission	Allows Order Lambda to invoke the Order Processor.
DailyReportLambda	AWS::Lambda::Function	Generates daily CSV reports.
DailyReportSchedule	AWS::Events::Rule	Scheduled daily report trigger.
DailyReportLambdaInvokePermission	AWS::Lambda::Permission	Allows EventBridge to invoke Daily Report Lambda.
SchemaInitializerLambda	AWS::Lambda::Function	Applies database/schema.sql to RDS.
DatabaseSchemaInitialization	Custom::CloudMartDatabaseSchema	CloudFormation custom resource for schema initialization.


SNS resources
Resource	Type	Purpose
ProductAlertTopic	AWS::SNS::Topic	Operational product/low-stock alerts.
ProductNotificationTopic	AWS::SNS::Topic	Product notification stream.
OrderAlertTopic	AWS::SNS::Topic	Operational order alerts.
OrderNotificationTopic	AWS::SNS::Topic	Customer order notification stream.
ProductAlertEmailSubscription	AWS::SNS::Subscription	Email subscription for product alerts.
ProductNotificationEmailSubscription	AWS::SNS::Subscription	Email subscription for product notifications.
OrderAlertEmailSubscription	AWS::SNS::Subscription	Email subscription for order alerts.
OrderNotificationEmailSubscription	AWS::SNS::Subscription	Email subscription for order notifications.
ProductAlertTopicPolicy	AWS::SNS::TopicPolicy	Publish policy for product alerts.
ProductNotificationTopicPolicy	AWS::SNS::TopicPolicy	Publish policy for product notifications.
OrderAlertTopicPolicy	AWS::SNS::TopicPolicy	Publish policy for order alerts.
OrderNotificationTopicPolicy	AWS::SNS::TopicPolicy	Publish policy for order notifications.


EventBridge rules
The reviewed application-events template contains these event rules:
- DailyReportSchedule
- LowStockEventRule
- ProductInventoryLowStockEventRule
- ProductLowStockAlertEventRule
- OrderFailedEventsToSNSRule
- OrderPendingEventsToSNSRule
- OrderPlacedEventsToSNSRule
- OrderConfirmedEventsToSNSRule
- OrderCancelledEventsToSNSRule
- OrderDeliveredEventsToSNSRule
### API, monitoring, and dashboard stack — cloudmart-api-monitoring-ec2-{environment}
API Gateway resource families
- /products
- /products/{id}
- /customers
- /customers/{customer_id}
- /customers/{customer_id}/orders
- /customers/{customer_id}/orders/{order_id}
- /customers/{customer_id}/orders/{order_id}/status
- /orders
- /orders/{id}
- /orders/{id}/status
The reviewed template contains 10 API Gateway resources and 19 HTTP methods.
API/integration/logging resources
Resource	Type	Purpose
CloudMartApi	AWS::ApiGateway::RestApi	Regional REST API.
AuthorizerLambdaInvokePermission	AWS::Lambda::Permission	API Gateway permission for the authorizer.
CloudMartAuthorizer	AWS::ApiGateway::Authorizer	TOKEN authorizer using Authorization.
ProductLambdaInvokePermission	AWS::Lambda::Permission	API Gateway permission for Product Lambda.
OrderLambdaInvokePermission	AWS::Lambda::Permission	API Gateway permission for Order Lambda.
CustomerLambdaInvokePermission	AWS::Lambda::Permission	API Gateway permission for Customer Lambda.
API deployment resource	AWS::ApiGateway::Deployment	Captures the API method configuration into a deployment.
ApiStage	AWS::ApiGateway::Stage	Publishes the environment stage.
ApiGatewayLogGroup	AWS::Logs::LogGroup	API Gateway access log storage.


Monitoring resources
Resource	Type	Purpose
CloudMartMonitoringDashboard	AWS::CloudWatch::Dashboard	CloudMart operations dashboard.
Api5XXErrorAlarm	AWS::CloudWatch::Alarm	API 5XX server errors.
ProductLambdaErrorAlarm	AWS::CloudWatch::Alarm	Product Lambda failures.
OrderLambdaErrorAlarm	AWS::CloudWatch::Alarm	Order Lambda failures.
Api4XXErrorAlarm	AWS::CloudWatch::Alarm	Elevated API 4XX errors.
ApiLatencyAlarm	AWS::CloudWatch::Alarm	Elevated API latency.
ProductLambdaThrottleAlarm	AWS::CloudWatch::Alarm	Product Lambda throttles.
OrderLambdaThrottleAlarm	AWS::CloudWatch::Alarm	Order Lambda throttles.
ProductLambdaDurationAlarm	AWS::CloudWatch::Alarm	High Product Lambda duration.
OrderLambdaDurationAlarm	AWS::CloudWatch::Alarm	High Order Lambda duration.
RdsHighCpuAlarm	AWS::CloudWatch::Alarm	High RDS CPU utilization.
RdsLowFreeStorageAlarm	AWS::CloudWatch::Alarm	Low RDS free storage.
RdsHighConnectionsAlarm	AWS::CloudWatch::Alarm	High RDS connections.
EC2HighCPUAlarm	AWS::CloudWatch::Alarm	High dashboard EC2 CPU.
EC2StatusCheckAlarm	AWS::CloudWatch::Alarm	Dashboard EC2 status-check failure.


Dashboard resources
Resource	Type	Purpose
CloudMartDashboardAccessPolicy	AWS::IAM::Policy	Restricted dashboard access to artifacts, reports, and required SSM parameters.
CloudMartDashboardInstance	AWS::EC2::Instance	Flask/Gunicorn/Nginx operations dashboard host.


DatabaseClientEC2 and CloudMartDashboardInstance are separate instances with different purposes.
### Database schema inventory
Table	Purpose	Key relationship
categories	Product category master data.	Referenced by products.category_id.
customers	Customer identity, token hash, lifecycle/status, soft-delete metadata.	Referenced by orders.customer_id.
products	Product catalog and inventory. stock_quantity is current stock and reorder_threshold drives low-stock logic.	References categories.
orders	Order header and lifecycle status.	References customers.
order_items	Products and quantities within an order.	References orders and products.
order_logs	Order status history and failure/transition notes.	References orders.


Customer bearer_token is stored as a SHA-256 hash, is intentionally not unique, and customer_id remains the customer primary key.
## 3. Prerequisites
### 3.1 Repository contents
Before deployment, confirm the selected branch contains the workflow, configuration, all five CloudFormation templates, `database/schema.sql`, the authorizer/product/customer/order/order-processor/daily-report Lambda code, and dashboard files such as `app.py`, `requirements.txt`, and `bootstrap-dashboard.sh`. Confirm that every path referenced by the workflow matches the repository layout and is committed.
### 3.2 AWS account and region
- Confirm the intended AWS account and set/verify the region as `ap-south-1`.
- Review AWS service availability, quotas, cost, and data-retention requirements.
- Inspect existing stacks and resources to ensure the deployment targets the intended environment.
- RDS and EC2 resources may continue to incur charges while running.
### 3.3 GitHub Actions OIDC connection and secret
The workflow authenticates to AWS through **OpenID Connect (OIDC)**. GitHub Actions obtains an OIDC token and the AWS credentials action exchanges it for temporary credentials by assuming an AWS IAM role. This avoids storing long-lived AWS access keys in repository secrets.
**Repository secret:** `AWS_ROLE_ARN`
Configure or verify it in GitHub:
1. Open the repository **Settings**.
2. Select **Secrets and variables → Actions**.
3. Under **Repository secrets**, create or inspect `AWS_ROLE_ARN`.
4. Set the secret value to the AWS IAM role ARN that the workflow is allowed to assume, for example: `arn:aws:iam::<AWS_ACCOUNT_ID>:role/<GITHUB_ACTIONS_DEPLOYMENT_ROLE>`.
5. Save the secret. GitHub masks secret values in logs.
**Important naming detail:** The supplied workflow/runbook uses `secrets.AWS_ROLE_ARN`. If the secret was created with the name `AWS_ROLE`, either rename it to `AWS_ROLE_ARN` or change the workflow expression to `secrets.AWS_ROLE`. The GitHub secret name and YAML reference must match exactly. The secret value must be the **IAM role ARN**, not an AWS console URL, login URL, access key, or secret access key.
The AWS account must also have the GitHub Actions OIDC identity provider configured. The deployment role's trust policy must restrict access to the intended GitHub repository and branch/environment. The workflow should grant `id-token: write` and `contents: read`, and `aws-actions/configure-aws-credentials` should use the role ARN secret and region `ap-south-1`.
The assumed role requires permissions for the workflow's actual CloudFormation deployment/inspection, S3 artifact operations, SSM Run Command and polling, Lambda/stack verification, and narrowly scoped `iam:PassRole` actions where needed. Apply least privilege; do not respond to an access error by granting unrestricted permissions without review. Never store long-lived AWS access keys as a workaround for OIDC configuration.
### 3.3.1 GitHub Actions OIDC connection
OpenID Connect (OIDC) allows GitHub Actions to obtain short-lived AWS credentials by exchanging a GitHub-issued identity token through AWS STS. This avoids storing long-lived AWS access keys in GitHub.
**Connection flow**
```text
GitHub repository / selected branch
        |
        v
Manual GitHub Actions workflow_dispatch
        |
        v
Workflow requests an OIDC token (id-token: write)
        |
        v
GitHub token identifies repository + ref/environment and audience
        |
        v
aws-actions/configure-aws-credentials
        |
        v
AWS STS AssumeRoleWithWebIdentity
        |
        v
AWS checks the account OIDC provider + IAM role trust policy
        |
        v
STS returns temporary credentials for the deployment role
        |
        v
AWS CLI uses those credentials for CloudFormation, S3, SSM,
verification, and other authorized deployment steps
```
The account OIDC provider for this repository/account is expected to be:
`arn:aws:iam::285150348844:oidc-provider/token.actions.githubusercontent.com`
Its client ID/audience must include `sts.amazonaws.com`. In the workflow, grant `id-token: write` and `contents: read`; configure `aws-actions/configure-aws-credentials` with region `ap-south-1` and `role-to-assume: ${{ secrets.AWS_ROLE_ARN }}`.
**GitHub secret:** create `AWS_ROLE_ARN` under **Repository Settings → Secrets and variables → Actions → Repository secrets**. The value must be the IAM deployment role ARN, such as:
`arn:aws:iam::285150348844:role/<GITHUB_ACTIONS_DEPLOYMENT_ROLE>`
The secret is not the OIDC provider ARN, AWS console URL, access key, or secret access key. The secret name used in GitHub must match the workflow expression exactly.
**Trust policy supplied for this setup — corrected subject format**
The provided trust policy's `sub` pattern, `repo:Srihitha01@*/cloudmart@*:*`, uses `@` separators that do not match GitHub's repository subject format. For this repository, use the `OWNER/REPOSITORY` form and restrict it to the deployment branches. This example allows `main` and `feature/order-flow`; change the branch entries to match the branches from which you actually run deployments.
```json
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
```
If the workflow uses a GitHub **Environment** instead of a branch-based subject, the subject is environment-based (for example, `repo:Srihitha01/cloudmart:environment:prod`); configure the job's `environment` and trust condition to match. Do not broaden the subject to every repository/branch unless that is deliberate and reviewed. The role trust relationship and the identity-based permissions policy are two separate IAM configurations.
### 3.3.2 Deployment role and CloudFormation service role
Keep these roles distinct:
| Role | Used by | Responsibility |
|---|---|---|
| GitHub Actions deployment role | AWS STS assumes it after validating the GitHub OIDC token | Orchestrates the workflow: stack deployment/inspection, artifact upload, SSM command/polling, verification, and passing the CloudFormation service role if configured. |
| `CloudMart-CloudFormation-ServiceRole` | CloudFormation assumes it for stack operations when supplied to the deployment | Grants CloudFormation the resource-creation/update/deletion permissions required by the templates. |
The supplied permissions policy includes `iam:PassRole` for the CloudFormation service role. That permission is needed when the workflow submits the stack operation with that role (for example, using `--role-arn`). Check the workflow's deploy commands and current stack service-role configuration to confirm whether it is used. If it is used, the service role itself must have the necessary CloudFormation execution permissions. `iam:PassRole` only authorizes passing a role; it does not grant the caller the permissions contained in that role.
The runtime IAM roles for Lambda functions and the EC2 dashboard are separate again. They are assumed by those workloads, not by GitHub Actions. Do not use the GitHub deployment role as a Lambda execution role or EC2 instance role.
### Deployment-role permissions by purpose
The attached permissions policy is the **identity-based permissions policy** for the GitHub Actions deployment role. Its statements cover these areas:
| Policy statement (`Sid`) | Purpose |
|---|---|
| `CloudFormationStackManagement` | Create/update/delete stacks and change sets; inspect, validate, and execute deployments. |
| `NetworkInfrastructureManagement` | Manage VPCs, subnets, route tables, internet gateways, security groups, and VPC endpoints. |
| `EC2InstanceManagement` | Launch, inspect, start/stop/terminate EC2 instances and manage their network interfaces. |
| `RDSManagement` | Create, modify, delete, inspect, tag, and manage RDS instances and subnet groups. |
| `AllowCreateRDSServiceLinkedRole` | Create the RDS service-linked role, constrained to `rds.amazonaws.com`. |
| `S3BucketManagement` / `S3ObjectManagement` | Create/configure buckets and upload/read/delete deployment objects and versions. |
| `APIGatewayManagement` | Create/read/update/delete API Gateway resources. |
| `CloudMartLambdaManagement` / `CloudMartLambdaInvocation` | Manage CloudMart Lambda functions, versions, aliases, permissions, tags, and invoke them. |
| `CloudMartLambdaLayerPublish` / `CloudMartLambdaLayerVersionManagement` / `CloudMartLambdaLayerList` | Publish, inspect, list, and delete CloudMart Lambda layer versions. |
| `CloudMartRoleManagement` | Create/update/delete CloudMart IAM roles and manage their policies/attachments. |
| `CloudMartInstanceProfileManagement` | Create/delete CloudMart EC2 instance profiles and associate roles. |
| `PassCloudMartRoles` | Pass CloudMart runtime roles to AWS services when resources are created. |
| `CloudWatchAlarmManagement` / `CloudWatchDashboardManagement` | Manage alarms, dashboards, tags, and metric reads. |
| `CloudWatchLogsManagement` | Manage log groups, streams, retention, tags, and log events. |
| `EventBridgeManagement` / `EventBridgeSchedulerManagement` | Manage event buses, rules, targets, schedules, and schedule groups. |
| `SNSManagement` | Manage SNS topics, subscriptions, attributes, and tags. |
| `CloudMartSSMParameterManagement` / `CloudMartSSMParameterDescribe` | Read/write/delete CloudMart parameters and describe parameters. |
| `EC2DashboardSSMManagement` | Send SSM commands and poll command results for dashboard refresh. |
| `ReadAmazonLinuxPublicAMI` | Read the Amazon Linux 2023 public AMI SSM parameter. |
| `PassCloudFormationServiceRole` | Pass `CloudMart-CloudFormation-ServiceRole` if the workflow submits CloudFormation operations using that service role. |
| `ReadCallerIdentity` | Verify the effective AWS identity with STS `GetCallerIdentity`. |
**Policy review note:** the supplied policy is broad rather than fully least-privilege: multiple statements use `Resource: "*"`, and it includes destructive permissions for stacks, EC2, RDS, S3, and IAM. Before production use, scope resources/actions where supported, consider separating deploy and teardown permissions, and constrain `iam:PassRole` with the relevant `iam:PassedToService` condition. Add permissions only after checking the specific denied action/resource; do not respond to access errors with unrestricted administrator access.
The full supplied permissions policy is reproduced in **Appendix A** below and is also provided as a separate JSON file for convenient IAM attachment/review. The trust policy is separate: it belongs in the IAM role's **Trust relationships**, not inside this identity-based permissions policy.
### 3.4 Other secret and manual input
- **`CLOUDMART_ALERT_EMAIL`**: GitHub Actions repository secret for the notification recipient. Keep the email address out of source code and templates.
- **`db_password`**: required manual workflow input for the database deployment. The supplied workflow prompt specifies 12–41 characters and validates a minimum of 12. Use a strong unique value. Do not commit it, put it in a parameter file, or expose it in logs or evidence.
Verify the actual workflow's secret/input names before changing them.
### 3.5 Configuration and network parameter file
`config/config.json` supplies `Environment`, `Project`, `ManagedBy`, and `Owner`. The environment must be `dev` or `prod`, and must agree with the workflow target and stack names. Runtime SSM paths follow `/cloudmart/{environment}/...`. Do not put credentials in this file.
The supplied `network-stack.yaml` uses these parameters:
| Parameter | Meaning | Example `dev` value |
|---|---|---|
| `Environment` | Deployment environment | `dev` |
| `VpcCidr` | VPC CIDR | `10.0.0.0/16` |
| `PublicSubnetCidr` | Public subnet CIDR | `10.0.1.0/24` |
| `PrivateSubnetCidr` | Primary private subnet CIDR | `10.0.2.0/24` |
| `SecondaryPrivateSubnetCidr` | Secondary private subnet CIDR | `10.0.3.0/24` |
If the workflow reads `cloudformation/network-parameters.json`, ensure the file exists and uses the CloudFormation JSON parameter-array format. Example:
```json
[
  {"ParameterKey":"Environment","ParameterValue":"dev"},
  {"ParameterKey":"VpcCidr","ParameterValue":"10.0.0.0/16"},
  {"ParameterKey":"PublicSubnetCidr","ParameterValue":"10.0.1.0/24"},
  {"ParameterKey":"PrivateSubnetCidr","ParameterValue":"10.0.2.0/24"},
  {"ParameterKey":"SecondaryPrivateSubnetCidr","ParameterValue":"10.0.3.0/24"}
]
```
The workflow should convert the JSON entries into `Key=Value` arguments for `aws cloudformation deploy --parameter-overrides`. Ensure `Environment` matches `config/config.json`. Do not include `MonitoringPublicSubnetCidr` in this file: that parameter/resource is not part of the supplied network architecture.
### 3.6 Resource-specific deployment inputs
Input	Used by	Requirement
DatabaseClientKeyName	Network-Security	Existing EC2 key-pair name for the database client host.
DatabaseClientInstanceType	Network-Security	EC2 instance type for the database client.
DatabaseClientAMI	Network-Security	Approved Amazon Linux AMI/SSM parameter.
AllowedSSHCidr	Network-Security	Source CIDR for SSH; restrict for production.
EC2InstanceType	API-Monitoring-EC2	Dashboard EC2 instance type.
DashboardPort	API-Monitoring-EC2	Dashboard application port.
NginxPort	API-Monitoring-EC2	Public reverse-proxy port.
Lambda/layer/schema S3 keys	Application-Events	Commit-specific artifact locations.


Do not pass parameters to CloudFormation unless the selected template declares them. Do not omit a required declared parameter because an older runbook revision did not list it.
## 4. Pre-deployment checklist
| Check | What to confirm |
|---|---|
| Branch and commit | Intended, reviewed branch and commit are selected |
| Workflow | `.github/workflows/deploy.yaml` has correct paths, stack order, secret references, and environment handling |
| Configuration | `config/config.json` is valid and targets the intended environment |
| Network parameters | Parameter keys exactly match `network-stack.yaml` |
| Templates | All five templates pass `cfn-lint` without blocking errors |
| Source files | Lambda handlers, schema, layer, and dashboard files referenced by workflow exist |
| OIDC secret | `AWS_ROLE_ARN` exists and contains the intended role ARN; YAML references the same name |
| OIDC trust | AWS trust policy is scoped to the intended repository and branch/environment |
| Alert secret | `CLOUDMART_ALERT_EMAIL` is configured |
| Database input | Strong `db_password` is ready and meets workflow validation |
| AWS target | Correct account and `ap-south-1` region |
| Existing resources | Current stack states/outputs reviewed; no unintended replacement or deletion |
| Cost/data | Costs, backups, retention, and production approval reviewed |
Optional local lint commands from the repository root:
```bash
python -m pip install --quiet cfn-lint
cfn-lint -t cloudformation/network-stack.yaml --non-zero-exit-code error
cfn-lint -t cloudformation/data-stack.yaml --non-zero-exit-code error
cfn-lint -t cloudformation/iam-stack.yaml --non-zero-exit-code error
cfn-lint -t cloudformation/application-events-stack.yaml --non-zero-exit-code error
cfn-lint -t cloudformation/api-monitoring-ec2-stack.yaml --non-zero-exit-code error
```
Resolve errors before deployment. Review warnings, especially unused parameters, invalid references, and replacement-sensitive properties.
## 5. Execute the GitHub Actions workflow
The workflow is manually triggered with `workflow_dispatch`; pushing code alone does not start it.
1. Commit and push the reviewed changes to the intended branch.
2. Open the GitHub repository and select **Actions**.
3. Choose **CloudMart Infrastructure Deployment**.
4. Click **Run workflow** and select the branch.
5. Enter the required `db_password` input without exposing it elsewhere.
6. Start the run and monitor each job/step.
7. Save the workflow run URL/ID, commit SHA, environment, and final status for review.
### What the workflow does
1. **Validates configuration and source:** checks configuration and required files.
2. **Authenticates to AWS:** uses GitHub OIDC and the configured IAM role to obtain temporary AWS credentials.
3. **Deploys Network-Security:** creates or updates VPC networking resources.
4. **Deploys Data-Storage:** provisions RDS, S3 buckets, and database parameters.
5. **Deploys IAM:** creates execution roles and policies.
6. **Packages and uploads artifacts:** uploads the PyMySQL layer, Lambda packages, schema, dashboard source, and required template artifacts using commit-specific S3 keys.
7. **Deploys Application-Events:** provisions functions, schema initialization, EventBridge, and SNS.
8. **Deploys API-Monitoring-EC2:** provisions API/monitoring/dashboard resources and refreshes the existing dashboard EC2 through SSM.
9. **Verifies deployment:** checks stack/function outputs and dashboard health as implemented in the workflow.
### Large CloudFormation templates and artifacts
Templates larger than 51,200 bytes must be deployed using an S3 template bucket. The Application-Events and API-Monitoring-EC2 deploy commands should use the existing artifact bucket created by Data-Storage via `--s3-bucket`. Do not create another bucket manually. Keep the commit-SHA artifact keys used by the workflow aligned with the values passed to CloudFormation.
The API-Monitoring-EC2 template parameters identified in the supplied runbook are `Environment`, `EC2InstanceType`, `DashboardPort`, and `NginxPort`. Do not pass Lambda S3 keys or database SSM parameter names to that stack unless its template is intentionally updated to declare them.
### Deployment lifecycle and artifact timing
```text
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
```
**When objects are stored in S3**
- The Data-Storage stack creates the deployment artifact bucket and the separate report bucket. Downstream workflow steps use the artifact bucket name from that stack's outputs.
- Lambda ZIPs, the PyMySQL layer, schema SQL, and dashboard source are uploaded to the artifact bucket after the bucket exists and before the relevant stack/resource or dashboard refresh consumes them. The workflow uses commit-specific object keys to associate artifacts with the source revision.
- For CloudFormation templates larger than 51,200 bytes, the deployment command uses the existing artifact bucket with `--s3-bucket`; template packaging/upload occurs as part of submitting that stack deployment.
- The report bucket stores generated CSV reports. The Daily Report Lambda writes those after it is invoked (including the workflow's configured post-deployment invocation and the scheduled run), not as part of uploading the source code.
- The dashboard reads its application files from the artifact bucket and report files from the report bucket. Do not treat these as one bucket or one deployment phase.
**Deployment failure/retry:** stop at the first failed step, inspect its error and CloudFormation events, fix the source/configuration in Git, run validation, and start a fresh manual workflow run. Do not manually create replacement AWS resources or repeatedly rerun without resolving the underlying failure.
## 6. Post-deployment verification
Record actual values from CloudFormation outputs and test results. Do not invent endpoint URLs, resource IDs, or bucket names.
### 6.1 Stack status
Example for the development network stack:
```bash
aws cloudformation describe-stacks \
  --stack-name cloudmart-network-security-dev \
  --region ap-south-1 \
  --query 'Stacks[0].[StackName,StackStatus]' \
  --output table
```
Repeat for `cloudmart-data-storage-dev`, `cloudmart-iam-dev`, `cloudmart-application-events-dev`, and `cloudmart-api-monitoring-ec2-dev`. Replace `dev` with `prod` for production. Successful statuses include `CREATE_COMPLETE` and `UPDATE_COMPLETE`. Investigate any in-progress, failed, or rollback state before declaring completion.
### 6.2 Stack outputs
```bash
aws cloudformation describe-stacks \
  --stack-name cloudmart-api-monitoring-ec2-dev \
  --region ap-south-1 \
  --query 'Stacks[0].Outputs[*].[OutputKey,OutputValue]' \
  --output table
```
Record the verified API invoke URL, dashboard URL, artifact/report bucket names, and relevant IDs from actual outputs.
### 6.3 Lambda and CloudWatch Logs
Confirm the authorizer, product, customer, order, order-processor, and daily-report functions exist and their deployments succeeded. For errors, inspect the relevant CloudWatch log groups and correlate timestamps, request IDs, exceptions, and duration. Check Lambda configuration, execution role, VPC settings, SSM parameter access, database connectivity, and schema as relevant.
Never place bearer tokens, passwords, customer secrets, or other credentials in logs, screenshots, or evidence.
### 6.3.1 Full infrastructure resource verification
For a resource-level sign-off, verify the deployed stacks contain the resource families listed in Section 2.1. At minimum, confirm:
- Network: VPC, three subnets, two route tables, public route/associations, five security groups, database-client EC2, and seven VPC endpoints.
- Data: RDS subnet group, encrypted private MySQL, report bucket, artifact bucket, four regular DB SSM parameters, secure password writer role/function, and password custom resource.
- IAM: seven Lambda runtime roles, RDS administration role/profile, dashboard role/profile, and authentication SSM writer resources.
- Application: event bus, PyMySQL layer, seven Lambda functions, processor invoke permission, report schedule/permission, schema initializer/custom resource, four SNS topics, four subscriptions, four topic policies, and all event-routing rules.
- API/monitoring: REST API, authorizer, ten API resources, nineteen methods, Lambda invoke permissions, deployment/stage, access-log group, monitoring dashboard, dashboard access policy, dashboard EC2, and the full CloudWatch alarm set.
- EC2: confirm DatabaseClientEC2 and CloudMartDashboardInstance are two separate instances.
Useful verification commands:
aws cloudformation list-stack-resources \
  --stack-name cloudmart-network-security-dev \
  --region ap-south-1 \
  --query 'StackResourceSummaries[*].[LogicalResourceId,ResourceType,ResourceStatus]' \
  --output table

aws cloudformation list-stack-resources \
  --stack-name cloudmart-data-storage-dev \
  --region ap-south-1 \
  --query 'StackResourceSummaries[*].[LogicalResourceId,ResourceType,ResourceStatus]' \
  --output table

aws cloudformation list-stack-resources \
  --stack-name cloudmart-iam-dev \
  --region ap-south-1 \
  --query 'StackResourceSummaries[*].[LogicalResourceId,ResourceType,ResourceStatus]' \
  --output table

aws cloudformation list-stack-resources \
  --stack-name cloudmart-application-events-dev \
  --region ap-south-1 \
  --query 'StackResourceSummaries[*].[LogicalResourceId,ResourceType,ResourceStatus]' \
  --output table

aws cloudformation list-stack-resources \
  --stack-name cloudmart-api-monitoring-ec2-dev \
  --region ap-south-1 \
  --query 'StackResourceSummaries[*].[LogicalResourceId,ResourceType,ResourceStatus]' \
  --output table
Replace dev with prod when validating production. A resource is not considered verified until its actual stack resource status is observed.
### 6.4 Authentication tests
Use non-production test credentials. Record sanitized requests, expected/actual results, and evidence.
| Test | Expected result |
|---|---|
| Protected request without Authorization header | Rejected |
| Protected request with invalid bearer token | Rejected |
| Valid active customer token | Accepted only for permitted resources |
| Same customer logs in again | Existing token remains stable, per application behavior |
| Multiple customers share a token | Customer identities remain distinct |
| Inactive/soft-deleted customer authenticates | Rejected |
| Customer accesses another customer's order | Denied |
Use `Authorization: Bearer <test-token>` where required. Do not include real tokens in documentation.
### 6.5 CRUD and order tests
Use the current Lambda handlers and `database/schema.sql` as the authority for route names and payload shapes. Record method, route, sanitized payload, response/status, and evidence in `docs/crud-verification.md`.
- **Products:** create, list, get, update, and delete.
- **Customers:** create, administrative list, get, update/patch, and soft delete, where supported by the handlers.
- **Orders:** place an order, list/read customer orders, and exercise configured update/status routes.
- **Order processing:** successful placement should return the processor's final `CONFIRMED` status rather than an asynchronous `PENDING` acknowledgement, as specified in the supplied runbook.
- **Authorization:** customers must not read or cancel another customer's order. Administrative/owner cancellation should be rejected; status changes must follow the current code's role and status rules.
Do not mark tests passed until executed against the deployed environment and recorded.
### 6.6 Events, SNS, reports, dashboard, and alarms
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
### 6.7 Drift and source hygiene
Run CloudFormation drift detection for all five stacks and record each result. Drift detection does not necessarily detect application-level divergence, so also review workflow and application deployment evidence. Confirm documentation paths resolve and no credentials or secrets were committed.
## 7. Troubleshooting
| Symptom | Checks and corrective action |
|---|---|
| OIDC role assumption fails | Verify `AWS_ROLE_ARN` secret name/value, workflow `role-to-assume`, AWS OIDC provider, trust conditions, repository/branch identity, and role permissions. The secret value must be an IAM role ARN, not a console URL. |
| AccessDenied | Identify the denied action/resource from the error or CloudTrail; add only the required least-privilege permission after review. |
| Configuration validation fails | Check JSON syntax, required keys, allowed environment, and consistency across config, parameter file, and stack name. |
| Network lint: undefined parameter | Ensure each `!Ref` matches a declared parameter/resource. Use the names actually defined in the network template. |
| Network lint: unused parameter | Remove a parameter not in the architecture or reference it in the intended resource. Do not add a monitoring subnet parameter when no such subnet exists in the design. |
| Template exceeds 51,200 bytes | Use the existing Data-Storage artifact bucket with the deploy command's `--s3-bucket` option. |
| Artifact bucket output missing | Inspect Data-Storage stack status, outputs, and events before downstream deployment. |
| Lambda package/layer unavailable | Verify commit-specific S3 key, object existence, upload result, template reference, and S3 permissions. |
| Lambda cannot reach RDS | Check VPC/subnets, Lambda-to-RDS security-group ingress, DB endpoint/port SSM parameters, credentials, and schema. |
| API returns 401 | Check bearer header format, authorizer/cache settings, token hash, customer active/deleted state, and admin-token configuration. |
| API returns 5xx/timeout | Inspect API Gateway and Lambda logs; check integration/Lambda timeouts, DB latency, and processor invocation results. |
| Order does not confirm | Inspect Order/Order Processor logs, transaction and stock checks, schema, invocation result, and event errors. |
| SNS email absent | Check subscription confirmation, topic/rule target, event pattern, alarm action, and `CLOUDMART_ALERT_EMAIL`. |
| Dashboard/SSM refresh fails | Check EC2 managed-instance status, instance role, S3 access, bootstrap script, service logs, and health endpoint. |
| Stack update fails/rolls back | Review CloudFormation events/change set, dependencies, exports/imports, permissions, and replacement-sensitive changes. Correct code and redeploy through the workflow; avoid manual resource edits. |
### Safe retry process
1. Open the failed Actions run and identify the first failing step.
2. Capture the error, stack name, and logical resource ID if shown.
3. Inspect CloudFormation events and relevant service logs.
4. Fix the source, template, or configuration in Git.
5. Run local validation.
6. Push the reviewed fix and start a new manual workflow run.
7. Verify the selected branch, environment, account, and region before retrying.
Do not repeatedly rerun without addressing the cause, and do not manually create resources to bypass CloudFormation.
## 8. Teardown
> **Destructive:** Teardown may permanently remove RDS data and S3 objects depending on deletion/retention policies. Back up required data, verify account/region/environment, and obtain approval before proceeding.
Delete stacks through CloudFormation in reverse dependency order:
1. `cloudmart-api-monitoring-ec2-{environment}`
2. `cloudmart-application-events-{environment}`
3. `cloudmart-iam-{environment}`
4. `cloudmart-data-storage-{environment}`
5. `cloudmart-network-security-{environment}`
Before deletion, check termination protection, deletion/retention policies, RDS backups, S3 contents, exports/imports, and environment names. Resolve failures through CloudFormation events and the approved infrastructure process rather than manually deleting individual resources.
After teardown, verify stack deletion, inspect retained resources/data, and review any continuing AWS charges.
## 9. Deployment evidence and sign-off
Complete this record for each deployment using actual run details and outputs.
| Evidence | Value to record |
|---|---|
| Repository / branch | `<fill in>` |
| Commit SHA | `<fill in>` |
| Actions run URL / ID and status | `<fill in>` |
| AWS account / region | `<account identifier> / ap-south-1` |
| Environment | `<dev / prod>` |
| Assumed OIDC IAM role | `<role name or ARN; never credentials>` |
| Network stack status | `<fill in>` |
| Data-Storage stack status | `<fill in>` |
| IAM stack status | `<fill in>` |
| Application-Events stack status | `<fill in>` |
| API-Monitoring-EC2 stack status | `<fill in>` |
| API URL / dashboard URL | `<verified outputs>` |
| Artifact/report bucket names | `<verified outputs>` |
| Authentication tests | `<pass/fail and evidence location>` |
| CRUD/order tests | `<pass/fail and evidence location>` |
| Event/SNS/report tests | `<pass/fail and evidence location>` |
| Dashboard/alarms | `<pass/fail and evidence location>` |
| Drift results | `<result for each stack>` |
| Open issues / accepted risks | `<none or details>` |
| Reviewer / approval | `<name/date per team process>` |
| Teardown | `<completed / not applicable>` |
### Completion criteria
The deployment is ready for review when the intended workflow run succeeds, all five stacks reach successful completion states, actual outputs are recorded, application/security/event/report/dashboard checks have evidence, and deviations are resolved or formally accepted. Ensure no credentials or sensitive tokens appear in the repository or evidence.
---
**End of runbook.**
## Appendix A — Full supplied GitHub Actions deployment-role permissions policy
This is the permissions policy from the accompanying uploaded text file, formatted as JSON. Review its scope before attaching it, especially wildcard resources and destructive permissions.
```json
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
```