"""Eval-table access — write tagged evidence records and read a whole run back.

Thin wrapper over the backend's ConversationStore pointed at the SEPARATE eval
table (`settings.eval_dynamodb_table_name`), so eval records are written by the
same code (and look like) production records, just with extra tags
(`run_id`/`question_id`/`repeat`). That keeps one persistence module.
"""
import _pathsetup  # noqa: F401  -- side effect: backend on path + backend/.env loaded

from config import settings
from storage import ConversationStore


class EvalStore:
    def __init__(self, table_name=None, **kwargs):
        table_name = table_name or settings.eval_dynamodb_table_name
        self._store = ConversationStore(table_name=table_name, **kwargs)

    def save_record(self, run_id, question_id, repeat, conversation_id, messages, evidence):
        """Persist one agent run as an evidence record tagged for this eval run."""
        extra = {
            **(evidence or {}),
            "run_id": run_id,
            "question_id": question_id,
            "repeat": repeat,
        }
        self._store.save(conversation_id, messages, extra)

    def read_run(self, run_id):
        """Every stored record for one eval run (the input Phase 3 scores)."""
        return self._store.items_by("run_id", run_id)
