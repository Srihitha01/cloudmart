# CloudMart Deployment Runbook

**Workflow:** `.github/workflows/deploy.yaml`  
**Workflow name:** `CloudMart Infrastructure Deployment`  
**Trigger:** Manual `workflow_dispatch`  
**Region:** `ap-south-1`  
**Environment:** `dev` or `prod`, read from `config/config.json`

> This is an operational guide based on the supplied workflow and architecture. Record actual run IDs, outputs, URLs, and test evidence during execution; do not treat this document alone as proof of a fresh-account deployment.

## 1. Purpose and deployment flow

Deploy CloudMart from a fresh AWS environment through GitHub Actions and CloudFormation. The workflow loads and validates configuration, deploys the network, data, IAM, application/events, and API/monitoring/EC2 stacks in dependency order, uploads versioned application artifacts, refreshes the existing dashboard EC2 through SSM, and verifies stack/function outputs.

CloudFormation owns AWS infrastructure. The workflow uploads artifacts and invokes CloudFormation/SSM; it must not create a second artifact bucket or another dashboard EC2 instance.

## 2. Prerequisites

### Repository files

Confirm the branch contains the workflow, `config/config.json`, all five CloudFormation templates, `database/schema.sql`, the authorizer/product/customer/order/order-processor/daily-report Lambda handlers, and dashboard `app.py`, `requirements.txt`, and `bootstrap-dashboard.sh`. The workflow validates and packages these project files.

### AWS and GitHub OIDC

The AWS account must have a deployment IAM role whose trust policy permits the repository’s GitHub Actions OIDC identity. Configure the role ARN as GitHub Actions secret `AWS_ROLE_ARN`. The workflow has `id-token: write` permission and assumes this role using `aws-actions/configure-aws-credentials`.

Review the role’s permissions for CloudFormation, S3 artifact operations, SSM Run Command/polling, Lambda and stack verification. Apply least privilege; do not use long-lived AWS access keys.

### Other secret and workflow input

Configure GitHub Actions secret `CLOUDMART_ALERT_EMAIL` for the notification subscription. When running the workflow, supply the required `db_password` input. The workflow masks the value in logs and passes it to the Data-Storage stack. Its prompt specifies 12–41 characters; the workflow validates a minimum of 12. Never commit the password or place it in source code.

### Configuration

`config/config.json` supplies `Environment`, `Project`, `ManagedBy`, and `Owner`. Environment must be `dev` or `prod`. The workflow region is `ap-south-1`; runtime SSM paths follow `/cloudmart/{environment}/...`.

Review AWS quotas, service availability, costs, and data-retention requirements before deployment. RDS and EC2 can continue to incur charges while running.

## 3. Run the workflow

1. Commit and push the reviewed source to the intended branch.
2. Open the repository’s **Actions** tab.
3. Select **CloudMart Infrastructure Deployment**.
4. Click **Run workflow**, choose the branch, and enter the `db_password` input.
5. Start the run and monitor each job and stack deployment.
6. Save the workflow URL/run ID and commit SHA for final-review evidence.

The workflow is manual by design; a normal code push does not itself start this deployment.

## 4. Stack order

| Order | Stack | Template | Main responsibility |
|---|---|---|---|
| 1 | `cloudmart-network-security-{environment}` | `cloudformation/network-stack.yaml` | VPC, public/private subnets, routes, security groups, endpoints |
| 2 | `cloudmart-data-storage-{environment}` | `cloudformation/data-stack.yaml` | RDS MySQL, S3 artifact/report buckets, database SSM parameters |
| 3 | `cloudmart-iam-{environment}` | `cloudformation/iam-stack.yaml` | Lambda/EC2 roles and policies, authentication parameter provisioning |
| 4 | `cloudmart-application-events-{environment}` | `cloudformation/application-events-stack.yaml` | Lambda functions/layer, schema initialization, EventBridge, SNS |
| 5 | `cloudmart-api-monitoring-ec2-{environment}` | `cloudformation/api-monitoring-ec2-stack.yaml` | API Gateway, authorizer integration, CloudWatch, EC2 dashboard |

The Data-Storage stack creates the artifact bucket used for Lambda packages, dashboard source, and CloudFormation template packaging. It also creates the report bucket used by the report function/dashboard.

### Large CloudFormation templates

Templates exceeding 51,200 bytes must be packaged through S3. Ensure the Application-Events deploy command includes `--s3-bucket "$LAMBDA_ARTIFACT_BUCKET"`. The API-Monitoring-EC2 deployment must likewise use the artifact bucket retrieved from the Data-Storage stack. Do not create a bucket manually for this purpose.

### Application artifacts

The workflow builds and uploads the PyMySQL layer, Lambda packages, and schema file using commit-SHA-specific S3 keys, then verifies the uploaded objects. Keep the artifact keys passed to CloudFormation aligned with the current commit.

### API and dashboard deployment

The API-Monitoring-EC2 template currently uses `Environment`, `EC2InstanceType`, `DashboardPort`, and `NginxPort` parameters. Do not pass Lambda S3-key or database SSM parameters to this stack unless its template is changed to define them.

The workflow publishes dashboard source to the artifact bucket, deploys the stack, retrieves the existing instance ID and bucket/port outputs, and uses SSM to refresh the dashboard. It checks service status and health endpoints. This updates the CloudFormation-managed instance; it does not create another one.

## 5. Post-deployment verification

### CloudFormation status and outputs

Check each stack and record its status. Example:

```bash
aws cloudformation describe-stacks \
  --stack-name cloudmart-network-security-dev \
  --region ap-south-1 \
  --query 'Stacks[0].[StackName,StackStatus]' \
  --output table
```

Repeat for the other four stack names and the selected environment. A successful stack should report a complete status such as `CREATE_COMPLETE` or `UPDATE_COMPLETE`.

Inspect outputs to obtain actual endpoints and resource names:

```bash
aws cloudformation describe-stacks \
  --stack-name cloudmart-api-monitoring-ec2-dev \
  --region ap-south-1 \
  --query 'Stacks[0].Outputs[*].[OutputKey,OutputValue]' \
  --output table
```

Record the verified API invoke URL, dashboard URL, artifact bucket, and report bucket. Never invent or hardcode these values in documentation.

### Lambda and CloudWatch Logs

Confirm authorizer, product, customer, order, order-processor, and daily-report functions are active and their updates succeeded. For errors, inspect the relevant CloudWatch log group and correlate request IDs, error messages, and duration. Do not log bearer tokens, passwords, or customer secrets.

### Authentication checks

Using test credentials and the deployed API:

- Protected request with no Authorization header: must be rejected.
- Protected request with invalid bearer token: must be rejected.
- Valid active customer token: succeeds only for permitted customer-scoped resources.
- Same customer token remains stable across logins.
- Multiple customers sharing a token are not collapsed into one customer identity.
- Inactive or soft-deleted customers cannot authenticate.
- Customer-scoped paths enforce customer ownership.

Use `Authorization: Bearer <test-token>` where required. Never place a real token in docs or screenshots.

### CRUD and order checks

Use the request schemas in the current handlers and `database/schema.sql`; record method, route, payload, response, and evidence in `docs/crud-verification.md`.

- Products: create, list, get, update, delete.
- Customers: create, administrative list, get, update/patch, soft delete.
- Orders: create through `/customers/{customer_id}/orders`, list/read customer orders, and test configured update/status routes.
- Normal successful order placement should return the processor’s final `CONFIRMED` status, not an asynchronous `PENDING` acknowledgement.
- A customer must not read or cancel another customer’s order.
- Admin/owner cancellation must be rejected; administrative status changes must follow the roles and statuses enforced by the current code.

### Events, notifications, reporting, dashboard

- Confirm configured EventBridge rules are enabled and targets are present.
- In a non-production environment, perform a controlled low-stock test and confirm event matching and SNS delivery.
- Verify order event/notification behavior.
- Confirm the scheduled Daily Report Lambda creates an object in the report bucket.
- Confirm SNS subscriptions are confirmed and email delivery works.
- Open the dashboard URL from stack outputs; verify inventory, recent orders, customer/product/order navigation, search/details, and latest report access.
- Check dashboard `/health` on the configured service and Nginx ports.
- Inspect the CloudWatch operations dashboard, metrics, alarm states, and SNS actions after generating test activity.

### Drift and source hygiene

Run CloudFormation drift detection for all five stacks and record the results. Confirm the latest intended-branch workflow run is green, documentation links resolve, and repository history contains no credentials or secrets.

## 6. Troubleshooting

| Symptom | Checks |
|---|---|
| Template exceeds 51,200 bytes | Add `--s3-bucket` to the deploy command using the existing Data-Storage artifact bucket. |
| Artifact bucket output missing | Inspect Data-Storage stack status, outputs, and events. |
| OIDC role assumption fails | Check `AWS_ROLE_ARN`, OIDC trust conditions, repository/branch identity, and role permissions. |
| Lambda package/layer unavailable | Verify the commit-SHA S3 key, object existence, and access permissions. |
| Lambda cannot reach RDS | Check VPC/subnet configuration, Lambda-to-RDS security-group ingress, endpoint/port SSM parameters, credentials, and schema. |
| API returns 401 | Check bearer header format, authorizer/cache configuration, token hash, customer active/deleted state, and admin token configuration. |
| API returns 5xx/timeout | Inspect API Gateway and Lambda logs; check integration/Lambda timeouts, DB latency, and processor invocation result. |
| Order does not confirm | Inspect Order and Order Processor logs, DB transaction/stock checks, schema, and event errors. |
| SNS email absent | Check subscription confirmation, topic/rule target, event pattern, and alarm action. |
| Dashboard/SSM refresh fails | Check EC2 managed-instance status, instance role, S3 access, bootstrap script, service logs, and health endpoint. |
| Stack update fails | Review CloudFormation events and dependency references; fix code/templates and redeploy. Avoid manual resource edits. |

## 7. Teardown

**Warning:** Teardown may permanently delete RDS data and S3 objects. Back up/export anything that must be retained and verify the target environment first.

Delete through CloudFormation in reverse dependency order:

1. `cloudmart-api-monitoring-ec2-{environment}`
2. `cloudmart-application-events-{environment}`
3. `cloudmart-iam-{environment}`
4. `cloudmart-data-storage-{environment}`
5. `cloudmart-network-security-{environment}`

Before deletion, check termination protection, retention policies, S3 bucket contents, database backups, and environment names. Resolve stack deletion failures through CloudFormation events rather than manually deleting individual resources. Afterward, verify stacks are deleted and review remaining resources/costs.

## 8. Final-review evidence record

| Evidence | Value to record |
|---|---|
| Branch / commit SHA | `<fill in>` |
| GitHub Actions run URL and status | `<fill in>` |
| Region / environment | `ap-south-1` / `<dev or prod>` |
| Five stack statuses | `<fill in>` |
| API invoke URL | `<fill in verified output>` |
| Dashboard URL | `<fill in verified output>` |
| Authentication tests | `<pass/fail with evidence>` |
| CRUD/order tests | `<pass/fail with evidence>` |
| Event/SNS/report tests | `<pass/fail with evidence>` |
| Dashboard/alarms | `<pass/fail with evidence>` |
| Drift results | `<fill in>` |
| Teardown | `<done / not applicable>` |
