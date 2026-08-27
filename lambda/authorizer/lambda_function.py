import json
import os
import boto3

ssm = boto3.client("ssm")


def get_parameter(name):
    response = ssm.get_parameter(
        Name=name,
        WithDecryption=True
    )
    return response["Parameter"]["Value"]


def generate_policy(principal_id, effect, resource):
    return {
        "principalId": principal_id,
        "policyDocument": {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Action": "execute-api:Invoke",
                    "Effect": effect,
                    "Resource": resource
                }
            ]
        }
    }


def lambda_handler(event, context):
    try:
        token_parameter = os.environ["AUTH_TOKEN_PARAMETER"]

        expected_token = get_parameter(token_parameter)

        authorization_header = event.get("headers", {}).get("Authorization")

        if not authorization_header:
            print(json.dumps({
                "operation": "AUTHORIZE",
                "result": "DENY",
                "reason": "TOKEN_MISSING"
            }))
            return generate_policy(
                "unauthorized",
                "Deny",
                event["methodArn"]
            )

        token = authorization_header

        if token != expected_token:
            print(json.dumps({
                "operation": "AUTHORIZE",
                "result": "DENY",
                "reason": "TOKEN_INVALID"
            }))
            return generate_policy(
                "unauthorized",
                "Deny",
                event["methodArn"]
            )

        print(json.dumps({
            "operation": "AUTHORIZE",
            "result": "ALLOW"
        }))

        return generate_policy(
            "authorized-user",
            "Allow",
            event["methodArn"]
        )

    except Exception as error:
        print(json.dumps({
            "operation": "AUTHORIZE",
            "result": "ERROR",
            "error_type": type(error).__name__
        }))

        return generate_policy(
            "unauthorized",
            "Deny",
            event.get("methodArn", "*")
        )