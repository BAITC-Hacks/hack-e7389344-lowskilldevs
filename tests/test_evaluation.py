"""Evaluation tests use synthetic observations; no partner files are fixtures."""
import copy
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

import evaluation
from evaluation import run_backtest


def make_data(suppliers=1, skus=2, zero=False):
    sales, stock, transactions = [], [], []
    for supplier_number in range(suppliers):
        supplier = f"Supplier{supplier_number}"
        for item in range(skus):
            sku = f"{item:04d}"
            for month in pd.date_range("2024-01-01", "2026-08-01", freq="MS"):
                qty = 0. if zero else (float(30 + 2 * month.month + item) if item % 2 == 0 else (60. if month.month % 3 == 0 else 0.))
                sales.append(dict(supplier=supplier, sku=sku, name="Synthetic", category="Current category", month=month, qty=qty))
                stock.append(dict(supplier=supplier, sku=sku, month=month, stock=100., stock_basis="opening"))
                transactions.append(dict(supplier=supplier, sku=sku, date=month + pd.Timedelta(days=9), qty=qty, customer_id=""))
            stock.append(dict(supplier=supplier, sku=sku, month=pd.Timestamp("2026-09-22"), stock=1_000_000., stock_basis="snapshot"))
    return dict(
        sales=pd.DataFrame(sales), stock=pd.DataFrame(stock), transactions=pd.DataFrame(transactions),
        seasonality=pd.DataFrame([dict(supplier="Supplier0", category="Current category", month=month, factor=2.) for month in range(1, 13)]),
        growth=pd.DataFrame([dict(supplier="Supplier0", category="Current category", growth_rate=999.)]),
        moq=pd.DataFrame([dict(supplier="Supplier0", sku="0000", moq=999, pack_size=999)]),
        transit=pd.DataFrame([dict(supplier="Supplier0", sku="0000", eta=pd.Timestamp("2026-01-01"), qty=999_999.)]),
    )


class BacktestTests(unittest.TestCase):
    def evaluate(self, data=None, **kwargs):
        return run_backtest(data if data is not None else make_data(), as_of="2026-09-22", **kwargs)

    def test_months_horizon_and_strict_training_dates(self):
        original = evaluation.calculate_orders
        calls = []

        def inspect(train, **kwargs):
            origin = kwargs["as_of"]
            self.assertTrue(train["sales"].month.lt(origin).all())
            self.assertTrue(train["stock"].month.lt(origin).all())
            self.assertTrue(train["transactions"].date.lt(origin).all())
            self.assertFalse(train["stock"].stock_basis.eq("snapshot").any())
            self.assertEqual(set(train["sales"].category), {"Все"})
            self.assertTrue(train["moq"].empty)
            self.assertTrue(train["transit"].empty)
            self.assertEqual(kwargs["lead_time_days"], 0)
            self.assertEqual(kwargs["review_days"], origin.days_in_month)
            self.assertEqual(kwargs["safety_days"], 0)
            self.assertEqual(kwargs["growth_pct"], 0)
            calls.append(origin)
            return original(train, **kwargs)

        with patch("evaluation.calculate_orders", side_effect=inspect):
            result = self.evaluate()
        self.assertEqual(calls, list(pd.date_range("2026-06-01", "2026-08-01", freq="MS")))
        self.assertEqual(result["coverage"]["evaluated_months"], ["2026-06-01", "2026-07-01", "2026-08-01"])
        self.assertEqual(result["coverage"]["n_observations"], 6)
        self.assertEqual(len(result["predictions"]), 18)

    def test_future_mutations_do_not_change_earlier_forecasts(self):
        data = make_data()
        original = self.evaluate(data)
        changed = copy.deepcopy(data)
        changed["sales"].loc[changed["sales"].month.ge("2026-07-01"), "qty"] = 9_999_999.
        changed["transactions"].loc[changed["transactions"].date.ge("2026-07-01"), "qty"] = 9_999_999.
        changed["stock"].loc[changed["stock"].month.ge("2026-07-01"), "stock"] = 0.
        updated = self.evaluate(changed)
        before = original["predictions"].loc[original["predictions"].month.eq(pd.Timestamp("2026-06-01"))].reset_index(drop=True)
        after = updated["predictions"].loc[updated["predictions"].month.eq(pd.Timestamp("2026-06-01"))].reset_index(drop=True)
        pd.testing.assert_frame_equal(before, after)

    def test_undated_reports_categories_and_current_snapshots_cannot_leak(self):
        data = make_data()
        original = self.evaluate(data)
        changed = copy.deepcopy(data)
        changed["seasonality"]["factor"] = .000001
        changed["growth"]["growth_rate"] = -999
        changed["sales"]["category"] = "Future reassignment"
        changed["moq"]["moq"] = 1
        changed["transit"]["qty"] = 0
        # Even an old timestamp does not make a current snapshot a historical
        # monthly observation when its source explicitly labels it snapshot.
        snapshots = changed["stock"].stock_basis.eq("snapshot")
        changed["stock"].loc[snapshots, "month"] = pd.Timestamp("2025-05-01")
        changed["stock"].loc[snapshots, "stock"] = 0
        updated = self.evaluate(changed)
        pd.testing.assert_frame_equal(original["predictions"], updated["predictions"])

    def test_baselines_and_every_method_share_exact_observations(self):
        data = make_data()
        sales = data["sales"]
        # A missing month is unknown: it must not become a baseline zero.
        data["sales"] = sales.loc[~(sales.sku.eq("0000") & sales.month.eq("2026-05-01"))].copy()
        data["sales"] = data["sales"].loc[~(data["sales"].sku.eq("0001") & data["sales"].month.eq("2026-07-01"))]
        result = self.evaluate(data)
        predictions = result["predictions"]
        observations = {}
        for method in evaluation.METHODS:
            part = predictions.loc[predictions.method.eq(method)]
            observations[method] = set(part[["supplier", "sku", "month"]].itertuples(index=False, name=None))
        self.assertEqual(observations["model"], observations["mean_3m"])
        self.assertEqual(observations["model"], observations["seasonal_naive"])
        self.assertEqual(len(observations["model"]), 1)
        self.assertEqual(result["coverage"]["exclusions"]["missing_truth"], 1)
        self.assertGreater(result["coverage"]["exclusions"]["missing_previous_3m"], 0)
        # The retained intermittent June observation has Mar=60, Apr=0, May=0.
        mean = predictions.loc[predictions.method.eq("mean_3m")].iloc[0]
        seasonal = predictions.loc[predictions.method.eq("seasonal_naive")].iloc[0]
        self.assertEqual(mean.prediction, 20)
        self.assertEqual(seasonal.prediction, 60)

    def test_metric_formula_and_zero_stock_diagnostic_slices(self):
        data = make_data()
        data["stock"].loc[data["stock"].sku.eq("0000") & data["stock"].month.eq("2026-06-01"), "stock"] = 0.
        data["stock"].loc[data["stock"].sku.eq("0001") & data["stock"].month.eq("2026-08-01"), "stock"] = np.nan
        result = self.evaluate(data)
        predictions = result["predictions"]
        for row in result["metrics"].itertuples():
            part = predictions.loc[predictions.method.eq(row.method)]
            if row.scope == "known_zero_stock":
                part = part.loc[part.target_zero_stock]
            elif row.scope == "without_known_zero_stock":
                part = part.loc[~part.target_zero_stock]
            self.assertEqual(row.n_observations, len(part))
            self.assertAlmostEqual(row.mae, (part.prediction - part.actual).abs().mean())
            self.assertAlmostEqual(row.bias, (part.prediction - part.actual).mean())
            self.assertAlmostEqual(row.wape_pct, 100 * (part.prediction - part.actual).abs().sum() / part.actual.abs().sum())
        self.assertEqual(result["coverage"]["target_stock_status_counts"]["known_zero"], 1)
        self.assertEqual(result["coverage"]["target_stock_status_counts"]["unknown"], 1)

    def test_wape_zero_denominator_is_undefined_not_fake_zero_accuracy(self):
        result = self.evaluate(make_data(zero=True))
        observed = result["metrics"].loc[result["metrics"].n_observations.gt(0)]
        self.assertTrue(observed.wape_pct.isna().all())
        self.assertTrue(observed.bias_pct.isna().all())
        self.assertTrue(observed.mae.eq(0).all())
        self.assertTrue(observed.bias.eq(0).all())
        self.assertTrue(any("не определены" in warning for warning in result["warnings"]))

    def test_target_inventory_only_changes_diagnostic_not_same_month_forecast(self):
        data = make_data(skus=4)
        baseline = self.evaluate(data)
        stock = data["stock"]
        target = stock.month.eq("2026-06-01")
        stock.loc[target & stock.sku.eq("0000"), "stock"] = 0
        stock.loc[target & stock.sku.eq("0001"), "stock"] = -1
        stock.loc[target & stock.sku.eq("0002"), "stock"] = np.nan
        # Contradictory observations cannot prove zero stock.
        conflict = stock.loc[target & stock.sku.eq("0003")].copy()
        conflict["stock"] = 0
        data["stock"] = pd.concat([stock, conflict], ignore_index=True)
        changed = self.evaluate(data)
        before = baseline["predictions"].loc[baseline["predictions"].month.eq("2026-06-01"), "prediction"].reset_index(drop=True)
        after = changed["predictions"].loc[changed["predictions"].month.eq("2026-06-01"), "prediction"].reset_index(drop=True)
        pd.testing.assert_series_equal(before, after)
        targets = changed["predictions"].loc[changed["predictions"].month.eq("2026-06-01") & changed["predictions"].method.eq("model")].set_index("sku")
        self.assertEqual(targets.target_stock_status.to_dict(), {
            "0000": "known_zero", "0001": "invalid_negative", "0002": "unknown", "0003": "conflicting",
        })
        self.assertEqual(int(targets.target_zero_stock.sum()), 1)

    def test_missing_model_output_excludes_row_for_all_methods(self):
        original = evaluation.calculate_orders

        def missing_one(train, **kwargs):
            result = original(train, **kwargs)
            if kwargs["as_of"] == pd.Timestamp("2026-06-01"):
                result["orders"].loc[result["orders"].sku.eq("0000"), "forecast_qty"] = np.nan
            return result

        with patch("evaluation.calculate_orders", side_effect=missing_one):
            result = self.evaluate()
        self.assertEqual(result["coverage"]["exclusions"]["missing_model_forecast"], 1)
        self.assertEqual(result["coverage"]["n_observations"], 5)
        self.assertEqual(result["predictions"].groupby("method").size().to_dict(), dict.fromkeys(evaluation.METHODS, 5))

    def test_sampling_is_deterministic_stratified_and_does_not_inspect_holdout(self):
        data = make_data(suppliers=2, skus=10)
        first = self.evaluate(data, max_skus=5)
        changed = copy.deepcopy(data)
        changed["sales"] = changed["sales"].sample(frac=1, random_state=8).reset_index(drop=True)
        changed["sales"].loc[changed["sales"].month.ge("2026-06-01"), "qty"] = 99_999.
        second = self.evaluate(changed, max_skus=5)
        keys = lambda result: set(result["predictions"][["supplier", "sku"]].itertuples(index=False, name=None))
        self.assertEqual(keys(first), keys(second))
        self.assertEqual(first["coverage"]["strata"], second["coverage"]["strata"])
        self.assertEqual(first["coverage"]["eligible_skus"], 20)
        self.assertEqual(first["coverage"]["sample_skus"], 5)
        self.assertEqual(len(first["coverage"]["strata"]), 4)
        self.assertTrue(all(s["sampled_skus"] >= 1 for s in first["coverage"]["strata"]))

    def test_missing_future_truth_does_not_change_sample(self):
        data = make_data(suppliers=2, skus=4)
        first = self.evaluate(data, max_skus=5)
        first_key = first["predictions"].iloc[0]
        sales = data["sales"]
        data["sales"] = sales.loc[~(sales.supplier.eq(first_key.supplier) & sales.sku.eq(first_key.sku) & sales.month.ge("2026-06-01"))]
        changed = self.evaluate(data, max_skus=5)
        self.assertEqual(first["coverage"]["strata"], changed["coverage"]["strata"])
        self.assertEqual(changed["coverage"]["sample_skus"], 5)
        self.assertEqual(changed["coverage"]["exclusions"]["missing_truth"], 3)

    def test_model_forecast_retained_when_orders_require_manual_review(self):
        data = make_data()
        data["stock"] = data["stock"].iloc[0:0]
        result = self.evaluate(data)
        self.assertEqual(result["coverage"]["n_observations"], 6)
        self.assertTrue(np.isfinite(result["predictions"].prediction).all())

    def test_exact_previous_year_month_is_required(self):
        data = make_data(skus=1)
        data["sales"] = data["sales"].loc[data["sales"].month.ne("2025-06-01")]
        result = self.evaluate(data)
        self.assertEqual(result["coverage"]["exclusions"]["missing_previous_year"], 1)
        self.assertEqual(result["coverage"]["n_observations"], 2)

    def test_insufficient_history_empty_and_bad_parameters(self):
        data = make_data(skus=1)
        data["sales"] = data["sales"].loc[data["sales"].month.ge("2025-04-01")]
        result = self.evaluate(data)
        self.assertEqual(result["coverage"]["eligible_skus"], 0)
        self.assertTrue(result["predictions"].empty)
        self.assertEqual(list(result["predictions"].columns), evaluation.PREDICTION_COLUMNS)
        self.assertTrue(result["metrics"].n_observations.eq(0).all())
        empty = self.evaluate({})
        self.assertTrue(empty["predictions"].empty)
        self.assertEqual(empty["coverage"]["sample_skus"], 0)
        for kwargs in ({"max_skus": 0}, {"n_folds": 0}, {"n_folds": 1.5}, {"max_skus": True}):
            with self.assertRaises(ValueError):
                self.evaluate(**kwargs)

    def test_returns_are_netted_then_clipped_and_input_is_not_mutated(self):
        data = make_data(skus=1)
        data["sales"].loc[data["sales"].month.eq("2026-06-01"), "qty"] = -10.
        original = data["sales"].copy(deep=True)
        result = self.evaluate(data)
        actuals = result["predictions"].loc[result["predictions"].month.eq("2026-06-01"), "actual"]
        self.assertTrue(actuals.eq(0).all())
        self.assertEqual(result["coverage"]["exclusions"]["negative_net_sales_months"], 1)
        pd.testing.assert_frame_equal(original, data["sales"])

    def test_history_requirement_counts_unique_months_not_duplicate_rows(self):
        data = make_data(skus=1)
        data["sales"] = data["sales"].loc[data["sales"].month.ge("2025-04-01")]
        data["sales"] = pd.concat([data["sales"], data["sales"], data["sales"]], ignore_index=True)
        result = self.evaluate(data)
        self.assertEqual(result["coverage"]["eligible_skus"], 0)


if __name__ == "__main__":
    unittest.main()
