"""Create the eval DynamoDB table if it doesn't exist (idempotent).

Run once before the first populate:
    backend/venv/bin/python evals/ensure_eval_table.py

Same schema ConversationStore expects (PK `conversation_id`, on-demand billing).
Provisioned out-of-band for now; this moves into CDK later (Phase 7).
"""
import _pathsetup  # noqa: F401  -- side effect: backend on path + backend/.env loaded

import boto3

from config import settings


def ensure_table():
    session = boto3.Session(profile_name=settings.aws_profile, region_name=settings.aws_region)
    dynamodb = session.resource("dynamodb", endpoint_url=settings.dynamodb_endpoint_url)
    name = settings.eval_dynamodb_table_name
    try:
        dynamodb.create_table(
            TableName=name,
            AttributeDefinitions=[{"AttributeName": "conversation_id", "AttributeType": "S"}],
            KeySchema=[{"AttributeName": "conversation_id", "KeyType": "HASH"}],
            BillingMode="PAY_PER_REQUEST",
        )
        dynamodb.Table(name).wait_until_exists()
        print(f"created eval table: {name}")
    except dynamodb.meta.client.exceptions.ResourceInUseException:
        print(f"eval table already exists: {name}")


if __name__ == "__main__":
    ensure_table()
