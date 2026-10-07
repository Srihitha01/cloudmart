# CloudMart Deployment Runbook

| Item | Value |
|---|---|
| Workflow | `CloudMart Infrastructure Deployment` (`.github/workflows/deploy.yaml`) |
| Trigger | Manual (`workflow_dispatch`), one required input: `db_password` |
| Region | `ap-south-1` |
| Environment | `dev` or `prod`, read from `config/config.json` |
| Deployment method | Five CloudFormation stacks deployed in sequence by one workflow run |

Replace `{env}` with `dev` or `prod` throughout this document.

---

## 1. Deployment Overview

A single workflow run deploys everything. The jobs run in this order, and each job waits for the previous ones:

| # | Workflow job | Stack deployed | Template |
|---|---|---|---|
| 0 | Load CloudMart Configuration | none (reads and validates config) | `config/config.json` |
| 1 | Deploy Network Security Stack | `cloudmart-network-security-{env}` | `cloudformation/network-stack.yaml` |
| 2 | Deploy Data Storage Stack | `cloudmart-data-storage-{env}` | `cloudformation/data-stack.yaml` |
| 3 | Deploy IAM Stack | `cloudmart-iam-{env}` | `cloudformation/iam-stack.yaml` |
| 4 | Deploy Application Events Stack | `cloudmart-application-events-{env}` | `cloudformation/application-events-stack.yaml` |
| 5 | Deploy API Monitoring EC2 Stack | `cloudmart-api-monitoring-ec2-{env}` | `cloudformation/api-monitoring-ec2-stack.yaml` |

Rules for this deployment:

- All infrastructure changes go through the workflow and CloudFormation. Do not create or edit AWS resources by hand to get past an error.
- Stacks depend on each other's outputs. Fix a problem in the template or code, commit it, and re-run the workflow. Editing a resource in the console will not repair downstream stacks.
- Artifacts are uploaded under keys that contain the commit SHA (`GITHUB_SHA`). The commit you deploy must be pushed to the branch you select.

---

## 2. Prerequisites

### 2.1 Tools and access

- Access to the GitHub repository with permission to run workflows and edit Actions secrets.
- AWS CLI v2 configured for the target account, with read access to CloudFormation, SSM, Lambda, S3, CloudWatch and Logs (used for verification only).
- `jq`, `curl`, Python 3 and `cfn-lint` for optional local checks.

### 2.2 AWS account setup (one-time per account)

1. Confirm you are in the intended AWS account and region `ap-south-1`.
2. An IAM OIDC identity provider for GitHub Actions exists, using the default STS audience that the AWS credentials action requests.
3. An IAM role for GitHub Actions exists whose trust policy:
   - allows `sts:AssumeRoleWithWebIdentity` from that OIDC provider,
   - requires the `aud` claim to match that STS audience,
   - restricts the `sub` claim to your repository and the branches allowed to deploy.
4. The role's permissions cover what the workflow actually does: CloudFormation stack operations, EC2/VPC and RDS management, S3 buckets and objects, API Gateway, Lambda (functions and layers) named `cloudmart-*`, IAM roles and instance profiles named `cloudmart-*` with `iam:PassRole`, CloudWatch alarms/dashboards/logs, EventBridge, SNS, SSM parameters under `/cloudmart/*`, SSM `SendCommand`/`GetCommandInvocation`, and `sts:GetCallerIdentity`.
5. The workflow does **not** pass a CloudFormation service role, so the GitHub Actions role itself must hold every permission the templates need.
6. Stacks are deployed with `CAPABILITY_IAM` and `CAPABILITY_NAMED_IAM`, so the role must be allowed to create named IAM roles.

### 2.3 GitHub repository secrets

Set these under Repository Settings > Secrets and variables > Actions.

| Secret | Value | Used by |
|---|---|---|
| `AWS_ROLE_ARN` | ARN of the IAM role from 2.2 (not an access key, not a console address) | Every AWS-facing job |
| `CLOUDMART_ALERT_EMAIL` | Mailbox that should receive alerts | Application Events stack (SNS subscriptions) |

The secret names must match the workflow exactly. Never store long-lived AWS access keys.

### 2.4 Manual workflow input

| Input | Rules |
|---|---|
| `db_password` | Required. 12 to 41 characters (the workflow rejects fewer than 12, and the Data Storage template enforces 12 to 41). Use a strong, unique value. It is masked in logs and written to SSM as a SecureString. Never commit it or paste it into tickets or chat. |

### 2.5 Configuration files

**`config/config.json`** is a list of Key/Value pairs. All four keys are required:

```json
[
  { "Key": "Environment", "Value": "dev" },
  { "Key": "Project",     "Value": "CloudMart" },
  { "Key": "ManagedBy",   "Value": "GitHubActions" },
  { "Key": "Owner",       "Value": "platform-team" }
]
```

`Environment` must be exactly `dev` or `prod`; the workflow fails otherwise.

**`cloudformation/network-parameters.json`** is a list of ParameterKey/ParameterValue pairs:

| ParameterKey | Current value |
|---|---|
| `Environment` | `dev` |
| `VpcCidr` | `10.0.0.0/16` |
| `PublicSubnetCidr` | `10.0.1.0/24` |
| `PrivateSubnetCidr` | `10.0.2.0/24` |
| `SecondaryPrivateSubnetCidr` | `10.0.3.0/24` |

The `Environment` value in this file must match `config/config.json`, or the Network job stops with an error. The keys must match the parameters declared in `network-stack.yaml`; do not add parameters the template does not declare.

### 2.6 Repository files the workflow requires

The Application Events job fails if any of these is missing:

```
lambda/authorizer/lambda_function.py
lambda/product/lambda_function.py
lambda/customer/lambda_function.py
lambda/order/lambda_function.py
lambda/order-processor/lambda_function.py
lambda/daily-report/lambda_function.py
database/schema.sql
dashboard/app.py
dashboard/requirements.txt
```

The API Monitoring EC2 job also requires these to be **committed** (not just present locally) and non-empty:

```
dashboard/app.py
dashboard/requirements.txt
dashboard/bootstrap-dashboard.sh
dashboard/templates/index.html
```

---

## 3. Pre-Deployment Checklist

Complete every item before starting a run.

- [ ] Target branch and commit are reviewed, pushed, and are the ones you intend to deploy.
- [ ] `config/config.json` has the intended `Environment`.
- [ ] `network-parameters.json` `Environment` matches `config/config.json`.
- [ ] `AWS_ROLE_ARN` and `CLOUDMART_ALERT_EMAIL` secrets exist and are correct.
- [ ] A `db_password` meeting the rules in 2.4 is ready.
- [ ] AWS account and region (`ap-south-1`) are confirmed.
- [ ] Existing `cloudmart-*-{env}` stacks are in a stable state (`CREATE_COMPLETE` or `UPDATE_COMPLETE`), not `*_IN_PROGRESS`, `*_FAILED` or `ROLLBACK_*`.
- [ ] No other workflow run for the same environment is in progress.
- [ ] Stray or non-project files are removed from the branch before deploying (for example the Windows shortcut in `.github/`, and the `authorizer-deployed/` folder if it is not intentionally part of the release).
- [ ] No credentials, tokens or passwords exist in the repository or its history.
- [ ] For `prod`: approval obtained, change window agreed, and the current database state reviewed.

Optional local template lint (the workflow runs the same checks per stack):

```bash
python -m pip install --quiet cfn-lint
for t in network data iam application-events api-monitoring-ec2; do
  cfn-lint -t cloudformation/$t-stack.yaml --non-zero-exit-code error
done
```

---

## 4. Deployment Procedure

### 4.1 Start the run

1. Push the reviewed commit to the branch you will deploy.
2. In GitHub, open **Actions** and select **CloudMart Infrastructure Deployment**.
3. Choose **Run workflow** and select the branch.
4. Enter `db_password`.
5. Start the run and keep the run page open.
6. Record the run URL or ID, the commit SHA, the environment, and the start time in the sign-off record (section 10).

### 4.2 What each job does and what success looks like

**Job 0 – Load CloudMart Configuration**
Reads `config/config.json`, checks that `Environment`, `Project`, `ManagedBy` and `Owner` are present, and that `Environment` is `dev` or `prod`.
*Success:* the log prints the four values.

**Job 1 – Network Security Stack**
Assumes the AWS role, prints the caller identity, lints `network-stack.yaml`, checks the parameter file, and deploys `cloudmart-network-security-{env}`.
*Success:* stack status `CREATE_COMPLETE` or `UPDATE_COMPLETE`, and the outputs table lists the VPC, subnet, security-group and endpoint IDs.

**Job 2 – Data Storage Stack**
Masks and validates `db_password`, lints `data-stack.yaml`, and deploys `cloudmart-data-storage-{env}`. Then lists the SSM parameters under `/cloudmart/{env}/db`.
*Success:* stack complete; outputs include `ArtifactBucketName`, `ReportBucketName`, `DBEndpoint`; the SSM listing shows the database parameters. Allow extra time on the first run because the RDS instance is created here.

**Job 3 – IAM Stack**
Lints and deploys `cloudmart-iam-{env}`, waits 15 seconds for IAM propagation, then confirms these roles exist: product, customer, order, order-processor, schema-initializer and daily-report (`cloudmart-{env}-<name>-role`). Finally checks that `/cloudmart/{env}/auth/token` exists.
*Success:* stack complete, all six roles found, auth token parameter listed.

**Job 4 – Application Events Stack**
Verifies the required source files, lints the template, reads `ArtifactBucketName` from the Data Storage stack, builds and uploads these artifacts to the artifact bucket under commit-SHA keys:

- PyMySQL layer (PyMySQL 1.1.1 with cryptography 45.0.6)
- Lambda zips: authorizer, product, customer, order, order-processor, daily-report
- `database/schema.sql`

It confirms each object exists, deploys `cloudmart-application-events-{env}` (template is uploaded through the artifact bucket), invokes the daily-report Lambda once, and checks the six main functions.
*Success:*

- All uploaded objects verified.
- Stack complete.
- The log shows `Daily report created: <s3 key>` (the run fails unless the function returns status 200 and an S3 key).
- Each function listed in the verification step shows `Active` and `Successful`.

**Job 5 – API Monitoring EC2 Stack**
Syncs the `dashboard/` folder to `dashboard/` in the artifact bucket and verifies the four required files, lints the template, deploys `cloudmart-api-monitoring-ec2-{env}` (parameters: `Environment`; `EC2InstanceType` defaults to `t3.micro`, `DashboardPort` to `5000`, `NginxPort` to `80`), then refreshes the existing dashboard instance over SSM Run Command. The refresh downloads and runs `bootstrap-dashboard.sh`, checks that the dashboard service and Nginx are active, and calls `/health` on both the dashboard port and the Nginx port.
*Success:* log shows `Dashboard refresh completed successfully`, followed by the stack status and outputs table, then the final summary listing all five stacks.

The run is green only if every job passes. A green run is necessary but not sufficient; complete section 5 before declaring success.

### 4.3 Typical run time

The first run in a new account takes the longest, mainly because of RDS creation, the VPC interface endpoints, and the EC2 instance. Later runs that only change code finish much faster. If a job appears stalled, check the CloudFormation events for the stack before cancelling anything.

---

## 5. Post-Deployment Verification

Record real values and results. Do not fill in anything you have not observed.

### 5.1 Stack status

```bash
for s in network-security data-storage iam application-events api-monitoring-ec2; do
  aws cloudformation describe-stacks \
    --stack-name cloudmart-$s-{env} --region ap-south-1 \
    --query 'Stacks[0].[StackName,StackStatus]' --output text
done
```

All five must show `CREATE_COMPLETE` or `UPDATE_COMPLETE`.

### 5.2 Collect outputs

```bash
aws cloudformation describe-stacks --stack-name cloudmart-api-monitoring-ec2-{env} \
  --region ap-south-1 --query 'Stacks[0].Outputs[*].[OutputKey,OutputValue]' --output table

aws cloudformation describe-stacks --stack-name cloudmart-data-storage-{env} \
  --region ap-south-1 --query 'Stacks[0].Outputs[*].[OutputKey,OutputValue]' --output table
```

Record these: `ApiEndpoint`, `DashboardUrl`, `DashboardInstanceId`, `ArtifactBucketName`, `ReportBucketName`, `DBInstanceIdentifier`. The API stage name equals the environment (`dev` or `prod`).

Set shell variables for the checks below:

```bash
API_URL="<ApiEndpoint output value>"
DASHBOARD_URL="<DashboardUrl output value>"
REPORT_BUCKET="<ReportBucketName output value>"
```

### 5.3 SSM parameters

Confirm names and types without showing secret values:

```bash
aws ssm describe-parameters --region ap-south-1 \
  --parameter-filters Key=Path,Option=Recursive,Values=/cloudmart/{env} \
  --query 'Parameters[*].[Name,Type]' --output table
```

Expected under `/cloudmart/{env}/`:

| Parameter | Type |
|---|---|
| `db/endpoint`, `db/name`, `db/port`, `db/username` | String |
| `db/password` | SecureString |
| `s3/report-bucket`, `s3/artifact-bucket` | String |
| `auth/token` | SecureString |

Do not use `--with-decryption` on `db/password` or `auth/token` when collecting evidence.

### 5.4 Database schema

The schema is applied during the Application Events stack deployment. Confirm that the schema initializer finished without errors:

```bash
aws logs tail /aws/lambda/cloudmart-{env}-schema-initializer --since 1h --region ap-south-1
```

Expected tables: `categories`, `customers`, `products`, `orders`, `order_items`, `order_logs`.

### 5.5 Lambda functions

Expected functions: `cloudmart-{env}-lambda-authorizer`, `-customer-lambda`, `-product-lambda`, `-order-lambda`, `-order-processor`, `-daily-report`, `-schema-initializer`.

```bash
aws lambda get-function-configuration --function-name cloudmart-{env}-order-lambda \
  --region ap-south-1 --query '[FunctionName,State,LastUpdateStatus]' --output text
```

Repeat for the others. Each should be `Active` / `Successful`.

### 5.6 API authentication tests

Use a test token only. Never paste a real token into evidence.

| # | Test | Expected |
|---|---|---|
| 1 | Call a protected route with no `Authorization` header | Rejected (401/403) |
| 2 | Call a protected route with an invalid bearer token | Rejected |
| 3 | Call a protected route with the administrator token | Accepted |
| 4 | Customer token reads its own customer record | Accepted |
| 5 | Customer token reads or cancels another customer's order | Denied |
| 6 | Soft-deleted customer uses their old token | Rejected |
| 7 | Administrator tries to cancel a customer order | Rejected |

Example request shape (substitute your own values in the shell, not in documents):

```bash
curl -s -o /dev/null -w "%{http_code}\n" "$API_URL/products"                                   # test 1
curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer invalid" "$API_URL/products" # test 2
```

Also check how `POST /customers` is configured in the deployed API: the project treats customer creation as an onboarding route, so confirm its authorization setting is what you intend for this environment.

### 5.7 Functional tests

Run against the deployed environment and record sanitized request, status code and result.

- **Products:** create, list, get, update, delete.
- **Customers:** create, administrative list, get own record, update, soft delete.
- **Orders:** place an order for a product with sufficient stock. The normal path must return status `CONFIRMED` (the order Lambda waits for the order processor). Then list orders, read one order, update an allowed field on a `CONFIRMED` order, and cancel your own `CONFIRMED` order.
- **Negative cases:** order more than available stock; use a non-existent product; confirm only `CONFIRMED` orders can be updated or cancelled.

### 5.8 Events, notifications and reports

1. **SNS subscriptions:** the address in `CLOUDMART_ALERT_EMAIL` receives subscription confirmation emails. Each must be confirmed or no alerts will arrive.
2. **Low stock:** in a non-production environment, reduce a product's stock to or below its reorder threshold and confirm an alert arrives.
3. **Order events:** place and fail/cancel test orders and confirm the matching notifications.
4. **Daily report:** the workflow already created one report. Confirm it:

```bash
aws s3 ls "s3://$REPORT_BUCKET/" --recursive --region ap-south-1
```

   The scheduled run is `cron(0 0 * * ? *)`, which is 00:00 UTC daily. Confirm a new report appears after the next schedule.

Avoid unnecessary test orders and alerts in production.

### 5.9 Dashboard

1. Check health:

```bash
curl -s "$DASHBOARD_URL/health"
```

   Expected: a JSON body with `"status": "healthy"`.
2. Open the `DashboardUrl` output in a browser. Sign in with the administrator token (read it from SSM into your own session when needed; do not display it in logs, screenshots or documents).
3. Confirm these pages load with real data: home summary, products, orders, customers, events, reports (view and download), and search.
4. Confirm the instance is managed by SSM:

```bash
aws ssm describe-instance-information --region ap-south-1 \
  --filters Key=InstanceIds,Values=<DashboardInstanceId> \
  --query 'InstanceInformationList[0].PingStatus' --output text
```

   Expected: `Online`.

### 5.10 CloudWatch

Confirm the operations dashboard (output `MonitoringDashboardName`) shows data, and that these alarms exist and are not in an unexpected state after some test traffic:

```
cloudmart-{env}-api-5xx-errors          cloudmart-{env}-api-4xx-errors
cloudmart-{env}-api-high-latency        cloudmart-{env}-product-lambda-errors
cloudmart-{env}-order-lambda-errors     cloudmart-{env}-product-lambda-throttles
cloudmart-{env}-order-lambda-throttles  cloudmart-{env}-product-lambda-duration
cloudmart-{env}-order-lambda-duration   cloudmart-{env}-rds-high-cpu
cloudmart-{env}-rds-low-free-storage    cloudmart-{env}-rds-high-connections
cloudmart-{env}-dashboard-high-cpu      cloudmart-{env}-dashboard-status-check
```

```bash
aws cloudwatch describe-alarms --alarm-name-prefix cloudmart-{env}- \
  --region ap-south-1 --query 'MetricAlarms[*].[AlarmName,StateValue]' --output table
```

New alarms may show `INSUFFICIENT_DATA` until metrics arrive.

### 5.11 Drift and hygiene

Run drift detection on all five stacks and record the results:

```bash
for s in network-security data-storage iam application-events api-monitoring-ec2; do
  ID=$(aws cloudformation detect-stack-drift --stack-name cloudmart-$s-{env} \
        --region ap-south-1 --query StackDriftDetectionId --output text)
  echo "$s: $ID"
done
# then, for each ID:
aws cloudformation describe-stack-drift-detection-status \
  --stack-drift-detection-id <ID> --region ap-south-1
```

Also confirm no secrets appear in the repository, workflow logs, or collected evidence.

### 5.12 Definition of done

The deployment is complete only when all of the following are true:

1. The workflow run is green.
2. All five stacks are in a `*_COMPLETE` state (not rollback).
3. Required SSM parameters exist with the correct types.
4. The database tables exist.
5. All seven Lambda functions are `Active`.
6. Authentication tests behave as expected.
7. Functional and order tests pass, with orders returning `CONFIRMED`.
8. SNS subscriptions are confirmed and test notifications arrive.
9. A daily report exists in the report bucket.
10. Dashboard health returns healthy and its pages load; the instance is SSM `Online`.
11. CloudWatch dashboard and alarms are present.
12. Drift results are recorded and any findings resolved or accepted.
13. Evidence is recorded and contains no secrets.

---

## 6. Redeploying and Updating

**Code-only change (Lambda, dashboard, schema):**
Commit, push, then run the workflow again with the same `db_password` policy. New artifacts are uploaded under the new commit SHA, and the stacks update to reference them. The dashboard is refreshed in place over SSM; no new instance is created.

**Template change:**
Run `cfn-lint` locally, commit, and re-run the workflow. Before running in `prod`, read the change carefully for properties that cause resource replacement (RDS, EC2, security groups, IAM names).

**Changing the password:**
Running the workflow with a different `db_password` updates the Data Storage stack's password parameter. Confirm that the database and every dependent Lambda and the dashboard can still connect afterwards.

**Switching environment:**
Changing `Environment` in both `config/config.json` and `network-parameters.json` deploys a separate set of `cloudmart-*-{env}` stacks. Existing stacks for the other environment are not modified.

**API changes:**
The API Gateway deployment resource is versioned in the template. If you add or change routes, make sure the deployment resource is updated as well so the new configuration reaches the stage.

---

## 7. Failure Handling and Rollback

### 7.1 Safe retry procedure

1. Open the failed run and find the **first** failing step.
2. Record the stack name, logical resource ID and error message.
3. Open the CloudFormation events for that stack and read the failure reason.
4. Check the relevant service logs (Lambda log group, SSM command output, bootstrap log).
5. Fix the cause in the repository, never in the console.
6. Run local lint, commit and push.
7. Start a new workflow run and verify branch, environment, account and region first.

Do not simply re-run without a fix, and do not create replacement resources manually.

### 7.2 Rollback

- CloudFormation rolls back a failed **update** automatically. Wait for `UPDATE_ROLLBACK_COMPLETE`, then fix and redeploy.
- A failed **first create** ends in `ROLLBACK_COMPLETE` and must be deleted before it can be created again. Delete only that failed stack, and only after confirming nothing depends on it.
- A stack in `UPDATE_ROLLBACK_FAILED` needs the specific blocking resource resolved, then use "Continue update rollback" in CloudFormation. Do not delete resources underneath the stack.
- To return to earlier application code, check out the earlier known-good commit on the deploy branch (or revert the change) and run the workflow again. Artifacts for older commits remain in the artifact bucket.

### 7.3 Stack failure in the middle of a multi-stack run

Stacks completed earlier in the run stay deployed. After fixing the problem, a full re-run is safe: unchanged stacks report no changes and are skipped.

---

## 8. Troubleshooting

| Symptom | Likely cause and checks |
|---|---|
| **Configure AWS Credentials fails** | `AWS_ROLE_ARN` missing, misspelled, or not a role ARN. Check the OIDC provider, the role's trust policy `aud` and `sub` conditions (repository and branch), and that the workflow has `id-token: write`. |
| **AccessDenied during a stack deploy** | Read the denied action and resource in the error or CloudTrail, then add only that permission to the deployment role. Remember the workflow does not use a CloudFormation service role. |
| **"Environment ... does not match"** | `network-parameters.json` and `config/config.json` disagree. Make them identical. |
| **"Invalid Environment"** | `config/config.json` value is not exactly `dev` or `prod`. |
| **Password validation fails** | `db_password` shorter than 12 characters, or longer than 41 (rejected by the Data Storage template). |
| **cfn-lint fails** | Undefined or unused parameter, bad `!Ref`, invalid property. Fix the template named in the log. |
| **"Required project files are missing"** | A path listed in section 2.6 is not committed on the selected branch. |
| **"ArtifactBucketName output was not found"** | Data Storage stack did not finish or its outputs changed. Check its status and outputs. |
| **IAM role not found in propagation step** | IAM stack incomplete or role names changed. Check the IAM stack outputs and events. |
| **Lambda creation fails on VPC permissions** | IAM changes not yet propagated or role policy missing network-interface permissions. Re-run after confirming the role policy. |
| **Daily report step fails** | Function returned a non-200 status or no `key`. Check the daily-report log group, the database connection parameters in SSM, and write access to the report bucket. |
| **Lambda cannot reach the database** | Check Lambda and RDS security groups, the DB endpoint/port parameters in SSM, the password parameter, and that the schema initializer ran successfully. |
| **API returns 401/403 unexpectedly** | Header must be `Authorization: Bearer <token>`. Check authorizer logs, the admin token parameter, and whether the customer is active and not soft-deleted. The authorizer result cache is set to 0 seconds, so changes take effect immediately. |
| **API returns 5xx or times out** | Inspect the API access log group and the Lambda logs. Check database latency and the order-processor invocation. |
| **Order does not return CONFIRMED** | Check order and order-processor logs, stock levels, and the `order_logs` table. |
| **No SNS emails** | Subscription not confirmed, `CLOUDMART_ALERT_EMAIL` secret wrong, or event pattern not matching. Check the topic subscriptions and the EventBridge rule targets. |
| **Dashboard refresh fails or times out** | Check the instance is SSM `Online`, the instance role can read the artifact bucket, and the `bootstrap-dashboard.sh` output printed in the failed step. On the instance, the bootstrap log is in `/var/log/cloudmart-dashboard-bootstrap.log`. |
| **Dashboard health fails** | Check the `cloudmart-dashboard` service and Nginx status, and their journals, over SSM Session Manager. |
| **Dashboard login rejected** | Token must match `/cloudmart/{env}/auth/token`; check for stray whitespace. |
| **Stack update rolls back** | Review the CloudFormation events for the first failed resource, correct the template, and redeploy. |
| **Deletion fails during teardown** | See section 9. |

---

## 9. Teardown

> **Destructive.** Teardown can permanently remove the database and stored data. Confirm the account, region and environment, and obtain approval before starting.

### 9.1 Before you delete

- Verify account, region and `{env}`.
- Take a manual RDS snapshot if you need data after teardown. (The database has a delete-time snapshot policy, but take one explicitly if the data matters.)
- Copy anything needed from the report bucket and artifact bucket. Both buckets are set to be **retained** when the stack is deleted.
- Stop traffic to the API and dashboard.
- Check termination protection on each stack.

### 9.2 Delete order

Delete in reverse dependency order, waiting for each to finish before starting the next:

1. `cloudmart-api-monitoring-ec2-{env}`
2. `cloudmart-application-events-{env}`
3. `cloudmart-iam-{env}`
4. `cloudmart-data-storage-{env}`
5. `cloudmart-network-security-{env}`

```bash
aws cloudformation delete-stack --stack-name cloudmart-api-monitoring-ec2-{env} --region ap-south-1
aws cloudformation wait stack-delete-complete --stack-name cloudmart-api-monitoring-ec2-{env} --region ap-south-1
# repeat for each stack in the order above
```

### 9.3 After deletion

- Confirm all five stacks are gone.
- The report and artifact buckets remain. Empty and delete them manually only after you are sure the contents are no longer needed.
- Review remaining RDS snapshots and decide whether to keep them (they continue to incur storage charges).
- Review the account for leftover `/cloudmart/{env}` parameters, log groups and network interfaces.
- If a stack fails to delete, read its events and remove the specific blocker (for example a network interface still attached or a non-empty resource). Do not delete resources individually to bypass the stack.

---

## 10. Deployment Sign-Off Record

Copy this table for each deployment and fill it in with actual results.

| Item | Value |
|---|---|
| Date / operator | |
| Branch / commit SHA | |
| Workflow run ID and result | |
| AWS account / region | / `ap-south-1` |
| Environment | |
| Network stack status | |
| Data Storage stack status | |
| IAM stack status | |
| Application Events stack status | |
| API Monitoring EC2 stack status | |
| `ApiEndpoint` output | |
| `DashboardUrl` output | |
| Artifact bucket / report bucket | |
| SSM parameters verified | |
| Database tables verified | |
| Lambda functions verified | |
| Authentication tests | |
| Functional and order tests | |
| SNS / event tests | |
| Daily report verified | |
| Dashboard verified | |
| Alarms verified | |
| Drift results (all five stacks) | |
| Open issues / accepted risks | |
| Reviewer / approval | |

---

## 11. Handling Secrets

- Never print or store the database password, administrator token or customer tokens in logs, screenshots, tickets, this runbook, or the repository.
- When reading SecureString parameters for operational use, send the value into a shell variable and clear it afterwards.
- If a secret is exposed, rotate it immediately: update the `db_password` through a new workflow run, and regenerate the administrator token according to your team's process, then re-verify authentication.
- If a secret was committed, treat it as compromised, rotate it, and remove it from history.