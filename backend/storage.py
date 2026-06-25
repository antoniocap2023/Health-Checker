"""Conversation persistence in DynamoDB.

One item per conversation, keyed by `conversation_id`. The backend writes the clean
user/assistant transcript here (see main.py / agent.py) and reads it back to resume a
conversation after a refresh.

Credentials are NEVER in the repo: boto3 resolves them from its standard chain —
locally the named profile in `settings.aws_profile` (e.g. `personal`, via
AWS_PROFILE in .env), and on AWS the task's IAM role. The table itself is
provisioned out-of-band (the AWS CLI for now; CDK in Phase 4), so this module
assumes it already exists.
"""
import logging
from datetime import datetime, timezone

import boto3

from config import settings

logger = logging.getLogger("healthchecker.storage")

# Sentinel so the constructor can tell "argument not passed" (→ use settings) apart
# from "explicitly passed None" (→ really use None, e.g. no profile in tests).
_FROM_SETTINGS = object()


class ConversationStore:
    """Read/write conversations in a DynamoDB table.

    Constructor args default to config.settings but can be overridden — tests point
    them at a moto-mocked table with `profile=None`. Building the resource does not
    call AWS; the first real network call happens in save()/get().
    """

    def __init__(self, table_name=None, region=None, profile=_FROM_SETTINGS, endpoint_url=_FROM_SETTINGS):
        table_name = table_name or settings.dynamodb_table_name
        region = region or settings.aws_region
        profile = settings.aws_profile if profile is _FROM_SETTINGS else profile
        endpoint_url = settings.dynamodb_endpoint_url if endpoint_url is _FROM_SETTINGS else endpoint_url

        session = boto3.Session(profile_name=profile, region_name=region)
        self._table = session.resource("dynamodb", endpoint_url=endpoint_url).Table(table_name)

    def save(self, conversation_id, messages):
        """Upsert the conversation's messages, stamping `updated_at` and — only on the
        first write — `created_at` (if_not_exists preserves it across later saves)."""
        now = datetime.now(timezone.utc).isoformat()
        self._table.update_item(
            Key={"conversation_id": conversation_id},
            UpdateExpression="SET #m = :m, #u = :u, #c = if_not_exists(#c, :u)",
            # ExpressionAttributeNames sidesteps any DynamoDB reserved-word clashes.
            ExpressionAttributeNames={"#m": "messages", "#u": "updated_at", "#c": "created_at"},
            ExpressionAttributeValues={":m": messages, ":u": now},
        )

    def get(self, conversation_id):
        """Return the stored `messages` list for this id, or None if there's no item."""
        resp = self._table.get_item(Key={"conversation_id": conversation_id})
        item = resp.get("Item")
        return item.get("messages") if item else None
