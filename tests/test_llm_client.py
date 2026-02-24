import unittest

from src.llm_client import BudgetedLLMClient


class _DummyLLM:
    def __init__(self, should_fail: bool = False) -> None:
        self.should_fail = bool(should_fail)
        self.calls = 0

    def chat(self, prompt: str) -> str:
        _ = prompt
        self.calls += 1
        if self.should_fail:
            raise RuntimeError("dummy failure")
        return '{"ok":"yes"}'


class BudgetedLLMClientTests(unittest.TestCase):
    def test_disabled_mode_blocks_calls(self) -> None:
        base = _DummyLLM()
        client = BudgetedLLMClient(base_client=base, enabled=False, max_calls=10)

        self.assertEqual(client.chat("a"), "{}")
        self.assertEqual(client.chat("b"), "{}")
        self.assertEqual(base.calls, 0)

        usage = client.usage()
        self.assertFalse(usage.enabled)
        self.assertEqual(usage.calls_used, 0)
        self.assertEqual(usage.calls_blocked, 2)
        self.assertEqual(usage.calls_failed, 0)

    def test_budget_limit_blocks_after_max_calls(self) -> None:
        base = _DummyLLM()
        client = BudgetedLLMClient(base_client=base, enabled=True, max_calls=2)

        self.assertEqual(client.chat("a"), '{"ok":"yes"}')
        self.assertEqual(client.chat("b"), '{"ok":"yes"}')
        self.assertEqual(client.chat("c"), "{}")
        self.assertEqual(base.calls, 2)

        usage = client.usage()
        self.assertEqual(usage.calls_used, 2)
        self.assertEqual(usage.calls_blocked, 1)
        self.assertEqual(usage.calls_failed, 0)

    def test_gateway_failure_returns_empty_json_and_counts_failure(self) -> None:
        base = _DummyLLM(should_fail=True)
        client = BudgetedLLMClient(base_client=base, enabled=True, max_calls=3)

        self.assertEqual(client.chat("a"), "{}")
        self.assertEqual(base.calls, 1)

        usage = client.usage()
        self.assertEqual(usage.calls_used, 0)
        self.assertEqual(usage.calls_blocked, 0)
        self.assertEqual(usage.calls_failed, 1)


if __name__ == "__main__":
    unittest.main()
