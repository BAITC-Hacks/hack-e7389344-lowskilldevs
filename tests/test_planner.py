import unittest

import numpy as np
import pandas as pd

from planner import calculate_orders


class ReplenishmentTests(unittest.TestCase):
    def data(self, quantities=None):
        quantities = quantities if quantities is not None else [30.] * 12
        months = pd.date_range(end="2026-08-01", periods=len(quantities), freq="MS")
        return {
            "sales": pd.DataFrame([dict(supplier="S", sku="A", name="Товар", category="C", month=m, qty=q) for m, q in zip(months, quantities)]),
            "stock": pd.DataFrame([dict(supplier="S", sku="A", month=pd.Timestamp("2026-09-01"), stock=0.)]),
            "moq": pd.DataFrame([dict(supplier="S", sku="A", moq=1, pack_size=1)]),
            "transit": pd.DataFrame(columns=["supplier", "sku", "qty", "eta"]),
            "seasonality": pd.DataFrame([dict(supplier="S", category="C", month=m, factor=1.) for m in range(1, 13)]),
            "growth": pd.DataFrame(columns=["supplier", "category", "growth_rate"]),
        }

    def calculate(self, data=None, **kwargs):
        return calculate_orders(data if data is not None else self.data(), as_of="2026-09-23", **kwargs)

    def row(self, data=None, **kwargs):
        return self.calculate(data, **kwargs)["orders"].iloc[0]

    def test_constant_demand_formula_and_horizon(self):
        row = self.row()
        self.assertAlmostEqual(row.forecast_qty, 30 / (365.25 / 12) * 74, places=3)
        self.assertEqual(row.order_qty, 73)
        self.assertTrue(f"Прогноз 74 дн. {row.forecast_qty}" in row.reason and "остаток (снимок 01.09.2026) 0" in row.reason and "73 ед. после MOQ 1 и кратности 1" in row.reason)
        self.assertGreater(self.row(safety_days=44).forecast_qty, row.forecast_qty)
        self.assertGreater(self.row(lead_time_days=60).lead_demand, row.lead_demand)
        self.assertGreater(self.row(review_days=60).forecast_qty, row.forecast_qty)

    def test_monthly_spike_does_not_distort_regular_demand(self):
        result = self.calculate(self.data([30.] * 11 + [9000.]))
        self.assertEqual(result["orders"].iloc[0].spikes_removed, 1)
        self.assertEqual(result["orders"].iloc[0].monthly_demand, 30)
        self.assertEqual(result["history"].iloc[-1].clean_qty, 30)

    def test_seasonal_peak_is_not_a_spike(self):
        data = self.data([30.] * 11 + [90.])
        data["seasonality"].loc[data["seasonality"].month == 8, "factor"] = 3
        row = self.row(data)
        self.assertEqual(row.spikes_removed, 0)
        self.assertEqual(row.monthly_demand, 30)

    def test_integer_inputs_accept_fractional_adjustments(self):
        data = self.data([31] * 11 + [9000])
        data["seasonality"].loc[data["seasonality"].month == 8, "factor"] = 1.1
        result = self.calculate(data)
        self.assertAlmostEqual(result["history"].iloc[-1].clean_qty, 34.1)

    def test_opening_inventory_basis_disclosed(self):
        data = self.data()
        data["stock"]["stock_basis"] = "opening"
        result = self.calculate(data)
        self.assertIn("Входящий остаток", result["orders"].iloc[0].reason)
        self.assertTrue(any("начало месяца" in warning for warning in result["warnings"]))

    def test_stockout_correction_is_bounded_and_disclosed(self):
        data = self.data([30.] * 10 + [0., 0.])
        data["stock"] = pd.concat([data["stock"], pd.DataFrame([dict(supplier="S", sku="A", month=pd.Timestamp(m), stock=0) for m in ["2026-07-31", "2026-08-31"]])])
        result = self.calculate(data)
        affected = result["history"].tail(2)
        self.assertTrue(affected.stockout_proxy.all())
        self.assertTrue((affected.adjusted_qty == 15).all())
        self.assertEqual(result["summary"]["lost_demand"], 30)
        self.assertTrue(any("Дни отсутствия неизвестны" in warning for warning in result["warnings"]))

    def test_zero_sales_with_available_stock_stays_zero(self):
        result = self.calculate(self.data([30.] * 10 + [0., 0.]))
        self.assertEqual(result["summary"]["lost_demand"], 0)
        self.assertEqual(result["history"].iloc[-1].adjusted_qty, 0)

    def test_increasing_trend_is_bounded(self):
        row = self.row(self.data(list(range(10, 34, 2))))
        self.assertGreater(row.growth_rate, 0)
        self.assertLessEqual(row.growth_rate, .5)
        self.assertGreater(row.monthly_demand, 20)

    def test_growth_file_and_manual_growth_change_forecast(self):
        data = self.data()
        base = self.row(data)
        data["growth"] = pd.DataFrame([dict(supplier="S", category="C", growth_rate=.5)])
        changed = self.row(data)
        manual = self.row(data, growth_pct=20)
        self.assertGreater(changed.forecast_qty, base.forecast_qty)
        self.assertAlmostEqual(changed.growth_rate, .2)
        self.assertGreater(manual.forecast_qty, changed.forecast_qty)

    def test_future_seasonality_and_supplier_fallback(self):
        data = self.data()
        base = self.row(data)
        data["seasonality"]["category"] = "Все"
        data["seasonality"].loc[data["seasonality"].month.isin([10, 11, 12]), "factor"] = 2
        changed = self.row(data)
        self.assertGreater(changed.forecast_qty, base.forecast_qty)
        self.assertGreater(changed.season_factor, base.season_factor)
        data["growth"] = pd.DataFrame([dict(supplier="S", category="Все", growth_rate=.5)])
        self.assertAlmostEqual(self.row(data).growth_rate, .2)

    def test_stock_reduces_need(self):
        data = self.data()
        data["stock"].loc[0, "stock"] = 100
        self.assertEqual(self.row(data).order_qty, 0)

    def test_missing_stale_and_negative_stock_require_review(self):
        for kind in ("missing", "stale", "negative"):
            with self.subTest(kind=kind):
                data = self.data()
                if kind == "missing":
                    data["stock"] = data["stock"].iloc[0:0]
                elif kind == "stale":
                    data["stock"].loc[0, "month"] = pd.Timestamp("2026-01-01")
                else:
                    data["stock"].loc[0, "stock"] = -1
                row = self.row(data)
                self.assertTrue(row.manual_review)
                self.assertTrue(pd.isna(row.order_qty))

    def test_latest_stock_and_missing_latest_not_replaced_by_old(self):
        data = self.data()
        data["stock"] = pd.concat([data["stock"], pd.DataFrame([dict(supplier="S", sku="A", month=pd.Timestamp("2026-10-01"), stock=999)])])
        self.assertEqual(self.row(data).stock, 0)
        data["stock"] = pd.concat([data["stock"], pd.DataFrame([dict(supplier="S", sku="A", month=pd.Timestamp("2026-09-22"), stock=np.nan)])])
        self.assertTrue(pd.isna(self.row(data).order_qty))

    def test_moq_and_pack_rounding(self):
        data = self.data()
        data["moq"].loc[0, ["moq", "pack_size"]] = [101, 12]
        self.assertEqual(self.row(data).order_qty, 108)

    def test_transit_only_within_horizon_reduces_order(self):
        data = self.data()
        data["transit"] = pd.DataFrame([dict(supplier="S", sku="A", qty=20, eta=pd.Timestamp("2026-10-01")), dict(supplier="S", sku="A", qty=9999, eta=pd.Timestamp("2027-01-01"))])
        row = self.row(data)
        self.assertEqual(row.in_transit, 20)
        self.assertEqual(row.order_qty, 53)

    def test_unknown_or_overdue_eta_requires_review(self):
        for eta in [pd.NaT, pd.Timestamp("2026-01-01")]:
            data = self.data()
            data["transit"] = pd.DataFrame([dict(supplier="S", sku="A", qty=20, eta=eta)])
            row = self.row(data)
            self.assertTrue(row.manual_review)
            self.assertTrue(pd.isna(row.order_qty))

    def test_client_spike_detectable_without_monthly_spike(self):
        data = self.data([100.] * 12)
        data["transactions"] = pd.DataFrame([dict(supplier="S", sku="A", customer_id="anon1", date=pd.Timestamp(m), qty=q) for m, q in zip(["2026-05-10", "2026-06-10", "2026-07-10", "2026-08-10"], [1, 1, 1, 50])])
        result = self.calculate(data)
        self.assertTrue(result["history"].iloc[-1].client_spike)
        self.assertEqual(result["history"].iloc[-1].clean_qty, 51)
        self.assertEqual(result["orders"].iloc[0].spike_units_removed, 49)

    def test_daily_spike_without_client_and_no_double_counting(self):
        data = self.data([30.] * 11 + [120.])
        data["transactions"] = pd.DataFrame([dict(supplier="S", sku="A", date=d, qty=q) for d, q in zip(pd.date_range("2026-08-01", periods=10), [3.] * 9 + [93.])])
        result = self.calculate(data)
        self.assertTrue(result["history"].iloc[-1].transaction_spike)
        self.assertFalse(result["history"].iloc[-1].client_spike)
        self.assertEqual(result["history"].iloc[-1].clean_qty, 30)
        self.assertEqual(result["orders"].iloc[0].spike_units_removed, 90)

    def test_current_month_omitted_and_missing_month_not_zero_filled(self):
        data = self.data()
        data["sales"] = data["sales"].drop(index=4)
        data["sales"] = pd.concat([data["sales"], pd.DataFrame([dict(supplier="S", sku="A", name="Товар", category="C", month=pd.Timestamp("2026-09-01"), qty=100000)])])
        result = self.calculate(data)
        self.assertEqual(len(result["history"]), 11)
        self.assertEqual(result["orders"].iloc[0].monthly_demand, 30)

    def test_stock_only_product_visible_but_not_automatic(self):
        data = self.data()
        data["stock"] = pd.concat([data["stock"], pd.DataFrame([dict(supplier="S", sku="B", month=pd.Timestamp("2026-09-01"), stock=5)])])
        result = self.calculate(data)
        self.assertEqual(result["summary"]["products"], 2)
        self.assertTrue(result["orders"].set_index("sku").loc["B", "manual_review"])

    def test_category_filter_and_empty_frames(self):
        self.assertEqual(self.calculate(category_filter=["C"])["summary"]["products"], 1)
        self.assertEqual(self.calculate(category_filter=["other"])["summary"]["products"], 0)
        self.assertEqual(self.calculate({})["summary"]["products"], 0)

    def test_suppliers_with_identical_sku_are_separate(self):
        data = self.data()
        for name in ["sales", "stock", "moq"]:
            second = data[name].copy()
            second["supplier"] = "T"
            data[name] = pd.concat([data[name], second])
        self.assertEqual(self.calculate(data)["summary"]["products"], 2)

    def test_returns_are_clipped_not_absolute(self):
        result = self.calculate(self.data([30.] * 11 + [-100.]))
        self.assertEqual(result["history"].iloc[-1].qty, 0)
        self.assertEqual(result["orders"].iloc[0].spikes_removed, 0)

    def test_invalid_settings_rejected(self):
        for kwargs in [dict(lead_time_days=-1), dict(review_days=1.5), dict(growth_pct=np.inf), dict(growth_pct=101), dict(lead_time_days=1000)]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.calculate(**kwargs)


if __name__ == "__main__":
    unittest.main()
