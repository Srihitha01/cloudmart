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

CloudFormation is the source of truth for AWS infrastructure. Do not manually create replacement VPCs, databases, security groups, endpoints, or dashboard instances. The supplied network design has one public subnet and two private subnets, uses VPC endpoints, and does not use a NAT Gateway. It does not include a separate monitoring subnet.

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
