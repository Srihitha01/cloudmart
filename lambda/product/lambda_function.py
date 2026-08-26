import json
import os
import boto3
import pymysql


ssm = boto3.client("ssm")


def get_parameter(name):
    response = ssm.get_parameter(
        Name=name,
        WithDecryption=True
    )
    return response["Parameter"]["Value"]


def lambda_handler(event, context):
    try:
        db_host = get_parameter(os.environ["DB_ENDPOINT_PARAMETER"])
        db_name = get_parameter(os.environ["DB_NAME_PARAMETER"])
        db_port = int(get_parameter(os.environ["DB_PORT_PARAMETER"]))
        db_user = get_parameter(os.environ["DB_USERNAME_PARAMETER"])
        db_password = get_parameter(os.environ["DB_PASSWORD_PARAMETER"])

        connection = pymysql.connect(
            host=db_host,
            user=db_user,
            password=db_password,
            database=db_name,
            port=db_port,
            connect_timeout=5,
            read_timeout=5,
            write_timeout=5,
            cursorclass=pymysql.cursors.DictCursor
        )

        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1 AS connection_test")
                result = cursor.fetchone()

            return {
                "statusCode": 200,
                "headers": {
                    "Content-Type": "application/json"
                },
                "body": json.dumps({
                    "message": "Successfully connected to RDS",
                    "database": db_name,
                    "result": result
                })
            }

        finally:
            connection.close()

    except Exception as exc:
        print(json.dumps({
            "level": "ERROR",
            "message": "RDS connection failed",
            "error": str(exc)
        }))

        return {
            "statusCode": 500,
            "headers": {
                "Content-Type": "application/json"
            },
            "body": json.dumps({
                "message": "RDS connection failed"
            })
        }