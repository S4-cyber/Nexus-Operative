"""Offline execution lifecycle invariants, including races and hostile events."""
import unittest
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from nexus.order_gateway import OrderGateway


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.gateway = OrderGateway()
        self.gateway.submit("order", "BTC-USD", "BUY", "25.00")

    def event(self, event_id="event", sequence=1, kind="FILL", amount="10", fee="0.01"):
        return self.gateway.simulate_event("order", event_id, sequence, kind, amount, fee)

    def test_partial_then_complete_and_replay(self):
        first = self.event()
        self.assertEqual(first["status"], "SIMULATED_PARTIALLY_FILLED")
        final = self.event("second", 2, amount="15", fee="0.02")
        self.assertEqual(final["status"], "SIMULATED_FILLED")
        self.assertEqual(Decimal(final["simulated_fee_total"]), Decimal("0.03"))
        replay = self.event()
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(replay["status"], "SIMULATED_FILLED")
        self.assertFalse(final["sent_to_broker"])
        with self.assertRaisesRegex(ValueError, "ORDER_TERMINAL"):
            self.event("third", 3, amount="1")

    def test_invalid_events_do_not_mutate_order(self):
        for value in (True, -1, "NaN", "Infinity", "bad", 201):
            with self.assertRaises(ValueError):
                self.event(amount=value)
        with self.assertRaisesRegex(ValueError, "OUT_OF_ORDER_EVENT"):
            self.event(sequence=2)
        with self.assertRaisesRegex(ValueError, "SIMULATED_OVERFILL"):
            self.event(amount="26")
        self.assertEqual(self.gateway.intents["order"]["lifecycle_events"], {})

    def test_duplicate_conflict_and_defensive_copy(self):
        result = self.event()
        result["lifecycle_events"]["event"]["fee"] = "199"
        self.assertTrue(self.event()["idempotent_replay"])
        with self.assertRaisesRegex(ValueError, "DUPLICATE_EVENT_CONFLICT"):
            self.event(amount="11")
        self.assertTrue(self.gateway.submit("order", "BTC-USD", "BUY", 25)["idempotent_replay"])

    def test_cancel_partial_preserves_fill_and_blocks_late_event(self):
        self.event()
        result = self.gateway.cancel("order")
        self.assertEqual(Decimal(result["simulated_filled_notional"]), 10)
        self.assertEqual(result["status"], "SIMULATED_CANCELLED")
        with self.assertRaisesRegex(ValueError, "ORDER_TERMINAL"):
            self.event("late", 2)

    def test_halt_partial_and_reset_cannot_reopen_terminal_order(self):
        self.event()
        self.gateway.emergency_halt()
        self.assertEqual(self.gateway.intents["order"]["status"], "SIMULATED_CANCELLED")
        self.assertTrue(self.event()["idempotent_replay"])
        self.gateway.reset()
        with self.assertRaisesRegex(ValueError, "ORDER_TERMINAL"):
            self.event("late", 2)

    def test_rejection_cannot_erase_partial_fill(self):
        self.event()
        with self.assertRaisesRegex(ValueError, "CANNOT_REJECT_PARTIAL_FILL"):
            self.event("reject", 2, "REJECT", "0", "0")
        self.gateway.submit("other", "ETH-USD", "SELL", 5)
        result = self.gateway.simulate_event("other", "reject", 1, "REJECT")
        self.assertEqual(result["status"], "SIMULATED_REJECTED")

    def test_concurrent_duplicate_fill_is_applied_once(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.event(), range(32)))
        self.assertEqual(sum(not r["idempotent_replay"] for r in results), 1)
        self.assertEqual(Decimal(self.gateway.intents["order"]["simulated_filled_notional"]), 10)


if __name__ == "__main__":
    unittest.main()
