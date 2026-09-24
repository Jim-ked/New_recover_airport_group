from __future__ import annotations

import unittest

from original_algorithm_overlay.model.inventory_flow import (
    InventoryFlowError,
    calculate_inventory_timeline,
)


class InventoryFlowTests(unittest.TestCase):
    def test_window_order_damage_then_consumption_then_end_supply(self):
        rows = calculate_inventory_timeline(
            initial_quantity=10,
            storage_capacity=[6, 8, 8],
            replenishment_throughput=[10, 2, 10],
            planned_replenishment=[5, 4, 3],
            sortie_consumption=[2, 1, 0],
        )

        self.assertEqual([4, 0, 0], [row.damage_loss for row in rows])
        self.assertEqual([2, 2, 1], [row.received for row in rows])
        self.assertEqual([3, 0, 2], [row.storage_overflow for row in rows])
        self.assertEqual([0, 2, 0], [row.throughput_rejected for row in rows])
        self.assertEqual([6, 7, 8], [row.ending_quantity for row in rows])
        # Capacity repair from 6 to 8 at window 1 does not recreate the four units lost.
        self.assertEqual(6, rows[1].opening_quantity)

    def test_storage_capacity_and_replenishment_throughput_are_not_interchangeable(self):
        throughput_limited = calculate_inventory_timeline(
            initial_quantity=0,
            storage_capacity=[100],
            replenishment_throughput=[1],
            planned_replenishment=[5],
            sortie_consumption=[0],
        )[0]
        storage_limited = calculate_inventory_timeline(
            initial_quantity=5,
            storage_capacity=[5],
            replenishment_throughput=[100],
            planned_replenishment=[5],
            sortie_consumption=[0],
        )[0]
        self.assertEqual((1, 4, 0), (
            throughput_limited.received,
            throughput_limited.throughput_rejected,
            throughput_limited.storage_overflow,
        ))
        self.assertEqual((0, 0, 5), (
            storage_limited.received,
            storage_limited.throughput_rejected,
            storage_limited.storage_overflow,
        ))

    def test_sortie_consumption_cannot_exceed_stock_after_window_start_damage(self):
        with self.assertRaisesRegex(InventoryFlowError, "window=0"):
            calculate_inventory_timeline(
                initial_quantity=10,
                storage_capacity=[4],
                replenishment_throughput=[10],
                planned_replenishment=[10],
                sortie_consumption=[5],
            )


if __name__ == "__main__":
    unittest.main()
