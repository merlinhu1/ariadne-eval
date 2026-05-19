import sqlite3
import tempfile
import unittest
from pathlib import Path

from agent_health.adapters.hermes import HermesStateReader


def make_state_db(home: Path) -> Path:
    db_path = home / "state.db"
    con = sqlite3.connect(db_path)
    con.executescript(
        """
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY, source TEXT NOT NULL, user_id TEXT, model TEXT,
            model_config TEXT, system_prompt TEXT, parent_session_id TEXT,
            started_at REAL NOT NULL, ended_at REAL, end_reason TEXT,
            message_count INTEGER DEFAULT 0, tool_call_count INTEGER DEFAULT 0,
            input_tokens INTEGER DEFAULT 0, output_tokens INTEGER DEFAULT 0,
            reasoning_tokens INTEGER DEFAULT 0, estimated_cost_usd REAL, actual_cost_usd REAL,
            title TEXT, api_call_count INTEGER DEFAULT 0
        );
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY, session_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT,
            tool_call_id TEXT, tool_calls TEXT, tool_name TEXT, timestamp REAL NOT NULL,
            token_count INTEGER, finish_reason TEXT, reasoning TEXT, reasoning_content TEXT,
            reasoning_details TEXT, codex_reasoning_items TEXT, codex_message_items TEXT
        );
        INSERT INTO sessions (id, source, user_id, model, started_at, ended_at, message_count, tool_call_count, input_tokens, output_tokens, title, api_call_count)
        VALUES ('s1', 'discord', 'u1', 'gpt-test', 10.0, 20.0, 3, 1, 100, 50, 'Test session', 2);
        INSERT INTO messages (id, session_id, role, content, timestamp, reasoning, reasoning_content) VALUES
            (2, 's1', 'assistant', 'hi', 12.0, 'hidden', 'hidden2'),
            (1, 's1', 'user', 'hello', 11.0, NULL, NULL),
            (3, 's1', 'tool', '{"exit_code":0}', 13.0, NULL, NULL);
        """
    )
    con.commit()
    con.close()
    return db_path


class HermesStateReaderTest(unittest.TestCase):
    def test_reads_recent_sessions_and_orders_messages_without_reasoning_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            make_state_db(home)

            reader = HermesStateReader(home)

            sessions = reader.list_sessions(limit=5)
            self.assertEqual([s["id"] for s in sessions], ["s1"])
            self.assertEqual(sessions[0]["tool_call_count"], 1)

            messages = reader.get_messages("s1")
            self.assertEqual([m["id"] for m in messages], [1, 2, 3])
            self.assertEqual(messages[0]["role"], "user")
            for message in messages:
                self.assertNotIn("reasoning", message)
                self.assertNotIn("reasoning_content", message)
                self.assertNotIn("codex_message_items", message)

    def test_inspect_summary_includes_session_and_message_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            make_state_db(home)

            reader = HermesStateReader(home)
            summary = reader.inspect(limit=2)

            self.assertEqual(summary["state_db"], str(home / "state.db"))
            self.assertEqual(summary["schema_version"], None)
            self.assertEqual(summary["sessions"][0]["id"], "s1")
            self.assertEqual(summary["sessions"][0]["messages"][0]["content"], "hello")


if __name__ == "__main__":
    unittest.main()
