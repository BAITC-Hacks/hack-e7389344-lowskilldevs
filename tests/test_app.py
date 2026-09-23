"""Approval scope, uncertainty preservation, audit trail, and lazy UI tools."""
from __future__ import annotations

import io
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd
import streamlit as st
from streamlit.testing.v1 import AppTest

from demo_data import load_demo_data
from order_workflow import apply_input_overrides, approval_signature, assess_decisions, csv_bytes, prepare_decisions

APP = str(Path(__file__).resolve().parents[1] / "app.py")


class OrderWorkflowTests(unittest.TestCase):
    def decisions(self, unknown_count=1):
        rows = [{"supplier": "A", "sku": "0001", "order_qty": 12.0, "manual_review": False}]
        rows.extend({"supplier": "A", "sku": f"X{index}", "order_qty": np.nan, "manual_review": True}
                    for index in range(unknown_count))
        return prepare_decisions(pd.DataFrame(rows))

    def test_thousand_unresolved_rows_do_not_block_ready_selection(self):
        decisions = self.decisions(1000)
        result = assess_decisions(decisions)
        self.assertTrue(result["can_approve"])
        self.assertEqual(len(result["selected"]), 1)
        self.assertEqual(result["counts"], {"ready": 1, "review": 1000, "deferred": 0})
        self.assertTrue(result["rows"].iloc[1:]["order_qty"].isna().all())

    def test_manual_inclusion_requires_positive_quantity_and_reason(self):
        decisions = self.decisions()
        decisions.loc[1, "include_in_order"] = True
        self.assertFalse(assess_decisions(decisions)["can_approve"])
        decisions.loc[1, "order_qty"] = 10
        self.assertFalse(assess_decisions(decisions)["can_approve"])
        decisions.loc[1, "decision_reason"] = "Остаток проверен менеджером"
        result = assess_decisions(decisions)
        self.assertTrue(result["can_approve"])
        self.assertEqual(result["counts"]["ready"], 2)
        self.assertTrue(pd.isna(result["selected"].loc[1, "recommended_order_qty"]))
        decisions.loc[1, "defer"] = True
        self.assertFalse(assess_decisions(decisions)["can_approve"])

    def test_deferral_keeps_unknown_quantity_and_excluded_errors_do_not_block(self):
        decisions = self.decisions()
        decisions.loc[1, "defer"] = True
        result = assess_decisions(decisions)
        self.assertTrue(result["can_approve"])
        self.assertEqual(result["counts"]["deferred"], 1)
        self.assertTrue(pd.isna(result["rows"].loc[1, "order_qty"]))
        decisions.loc[1, "order_qty"] = -10
        self.assertTrue(assess_decisions(decisions)["can_approve"])
        decisions.loc[0, "order_qty"] = -1
        self.assertFalse(assess_decisions(decisions)["can_approve"])

    def test_signature_covers_selection_reasons_and_parameters(self):
        decisions = self.decisions()
        original = approval_signature("calculation-1", decisions)
        for field, value in [("include_in_order", False), ("decision_reason", "Отложено до сверки"), ("order_qty", 24)]:
            changed = decisions.copy()
            changed.loc[0, field] = value
            self.assertNotEqual(original, approval_signature("calculation-1", changed))
        self.assertNotEqual(original, approval_signature("calculation-2", decisions))

    def test_input_corrections_are_scoped_and_leave_sources_unchanged(self):
        data = load_demo_data()
        data["transit"].loc[0, "eta"] = pd.NaT
        supplier, sku = data["transit"].loc[0, ["supplier", "sku"]]
        original_stock = data["stock"].copy()
        corrected = apply_input_overrides(data, [{
            "supplier": supplier, "sku": sku, "stock": 17, "stock_date": "2026-09-22",
            "unknown_eta": "2026-10-05", "reason": "Подтверждено учётной системой и поставщиком",
        }])
        pd.testing.assert_frame_equal(data["stock"], original_stock)
        self.assertTrue(pd.isna(data["transit"].loc[0, "eta"]))
        self.assertEqual(corrected["transit"].loc[0, "eta"], pd.Timestamp("2026-10-05"))
        pd.testing.assert_frame_equal(data["transit"].iloc[1:], corrected["transit"].iloc[1:])
        snapshot = corrected["stock"].loc[
            corrected["stock"].supplier.eq(supplier) & corrected["stock"].sku.eq(sku)
            & corrected["stock"].month.eq(pd.Timestamp("2026-09-22"))
        ]
        self.assertEqual(len(snapshot), 1)
        self.assertEqual(snapshot.iloc[0].stock, 17)
        self.assertEqual(snapshot.iloc[0].stock_basis, "snapshot")
        with self.assertRaises(ValueError):
            apply_input_overrides(data, [{"supplier": supplier, "sku": sku, "stock": 0, "stock_date": "2026-09-22", "reason": " "}])

    def test_export_neutralizes_formula_text_in_reason_and_retains_unknown_original(self):
        rows = self.decisions()
        rows.loc[1, ["include_in_order", "order_qty", "decision_reason"]] = [True, 10, "  =1+1"]
        content = csv_bytes(assess_decisions(rows)["selected"])
        self.assertTrue(content.startswith(b"\xef\xbb\xbf"))
        exported = pd.read_csv(io.BytesIO(content), sep=";", encoding="utf-8-sig", dtype={"sku": str})
        self.assertEqual(exported.loc[0, "sku"], "0001")
        self.assertEqual(exported.loc[1, "decision_reason"], "'=1+1")
        self.assertTrue(pd.isna(exported.loc[1, "recommended_order_qty"]))


class ApprovalFlowTests(unittest.TestCase):
    def new_app(self):
        app = AppTest.from_file(APP, default_timeout=90).run()
        self.assertEqual(len(app.exception), 0, [item.message for item in app.exception])
        return app

    def button(self, app, label):
        return next(button for button in app.button if button.label == label)

    def approve(self, app):
        return self.button(app, "Подтвердить итоговый заказ")

    def export_disabled(self, app):
        return app.get("download_button")[0].disabled

    def edit(self, app, key, row, values):
        # AppTest has no editable-dataframe frontend. Repeat this state with
        # subsequent events to simulate the actual browser's widget payload.
        app.session_state[key] = {"edited_rows": {row: values}, "added_rows": [], "deleted_rows": []}

    def test_demo_filters_and_cleaning_toggle_revoke_approval(self):
        app = self.new_app()
        expected_count = len(load_demo_data()["sales"][["supplier", "sku"]].drop_duplicates())
        self.assertEqual(len(app.session_state["current_plan"]["orders"]), expected_count)
        self.assertTrue(self.export_disabled(app))
        self.approve(app).click().run()
        self.assertFalse(self.export_disabled(app))
        next(item for item in app.checkbox if item.label == "Очищать разовые всплески").uncheck().run()
        self.assertTrue(self.export_disabled(app))
        self.assertEqual(app.session_state["current_plan"]["orders"]["spikes_removed"].sum(), 0)
        self.approve(app).click().run()
        app.slider[0].set_value(20).run()
        self.assertTrue(self.export_disabled(app))
        self.approve(app).click().run()
        app.multiselect[0].set_value(["Systeme Electric"]).run()
        self.assertTrue(self.export_disabled(app))
        self.assertEqual(set(app.session_state["current_plan"]["orders"]["supplier"]), {"Systeme Electric"})
        self.assertEqual(len(app.exception), 0)

    def test_unknown_position_can_be_skipped_or_manually_resolved_with_reason(self):
        data = load_demo_data()
        missing_sku = data["sales"].iloc[0]["sku"]
        data["stock"] = data["stock"].loc[data["stock"]["sku"] != missing_sku].copy()
        with patch("demo_data.load_demo_data", return_value=data), patch("streamlit.data_editor", wraps=st.data_editor) as editors, patch("streamlit.download_button", wraps=st.download_button) as download:
            app = self.new_app()
            self.assertFalse(self.approve(app).disabled)
            self.approve(app).click().run()
            exported = pd.read_csv(io.BytesIO(download.call_args.kwargs["data"]), sep=";")
            self.assertNotIn(missing_sku, exported["Артикул"].tolist())
            target = next(call for call in editors.call_args_list if missing_sku in call.args[0]["sku"].values)
            key, row = target.kwargs["key"], target.args[0]["sku"].tolist().index(missing_sku)
            values = {"include_in_order": True, "order_qty": 10}
            self.edit(app, key, row, values)
            app.run()
            self.assertTrue(self.approve(app).disabled)
            self.assertTrue(self.export_disabled(app))
            values["decision_reason"] = "Проверено менеджером, заказ по подтверждённой потребности"
            self.edit(app, key, row, values)
            app.run()
            self.assertFalse(self.approve(app).disabled)
            self.edit(app, key, row, values)
            self.approve(app).click().run()
            self.assertFalse(self.export_disabled(app))
            exported = pd.read_csv(io.BytesIO(download.call_args.kwargs["data"]), sep=";")
            manual = exported.loc[exported["Артикул"].eq(missing_sku)].iloc[0]
            self.assertEqual(manual["К заказу, ед."], 10)
            self.assertTrue(pd.isna(manual["Расчётный заказ, ед."]))
            self.assertEqual(manual["Решение менеджера"], values["decision_reason"])
            self.assertEqual(len(app.exception), 0)

    def test_selection_and_reason_changes_revoke_approval(self):
        with patch("streamlit.data_editor", wraps=st.data_editor) as editors:
            app = self.new_app()
            key = editors.call_args_list[0].kwargs["key"]
            self.approve(app).click().run()
            self.edit(app, key, 0, {"include_in_order": False, "defer": True})
            app.run()
            self.assertTrue(self.export_disabled(app))
            self.assertFalse(self.approve(app).disabled)
            self.edit(app, key, 0, {"include_in_order": False, "defer": True})
            self.approve(app).click().run()
            self.assertFalse(self.export_disabled(app))
            self.edit(app, key, 0, {"include_in_order": False, "defer": True, "decision_reason": "Ждём сверку"})
            app.run()
            self.assertTrue(self.export_disabled(app))
            self.assertEqual(len(app.exception), 0)

    def test_optional_tools_run_only_on_explicit_request(self):
        empty_result = {"coverage": {}, "metrics": pd.DataFrame(), "predictions": pd.DataFrame(), "warnings": []}
        answer = {"mode": "local", "answer": "Расчёт готов", "trace": [], "comparison": pd.DataFrame()}
        with patch("evaluation.run_backtest", return_value=empty_result) as backtest, patch("ai_agent.run_procurement_agent", return_value=answer) as agent:
            app = self.new_app()
            backtest.assert_not_called()
            agent.assert_not_called()
            self.button(app, "Запустить проверку на истории").click().run()
            self.assertEqual(backtest.call_count, 1)
            app.run()
            self.assertEqual(backtest.call_count, 1)
            app.text_area[0].set_value("Объясни заказ").run()
            self.button(app, "Разобрать сценарий").click().run()
            self.assertEqual(agent.call_count, 1)
            self.assertFalse(agent.call_args.kwargs["use_api"])
            self.assertEqual(agent.call_args.kwargs["api_key"], "")
            self.assertEqual(len(app.exception), 0)

    def test_input_correction_form_recalculates_and_preserves_audit_reason(self):
        app = self.new_app()
        original_signature = app.session_state["calculation_signature"]
        self.approve(app).click().run()
        next(item for item in app.checkbox if item.label == "Подтвердить фактический остаток").check()
        next(item for item in app.number_input if item.label.startswith("Остаток на")).set_value(1)
        next(item for item in app.text_input if item.label == "Основание уточнения").set_value("Сверено в системе")
        self.button(app, "Применить уточнение и пересчитать").click().run()
        self.assertEqual(len(app.session_state["input_overrides"]), 1)
        self.assertNotEqual(app.session_state["calculation_signature"], original_signature)
        self.assertTrue(self.export_disabled(app))
        self.assertEqual(app.session_state["input_overrides"][0]["reason"], "Сверено в системе")
        self.assertEqual(len(app.exception), 0)


if __name__ == "__main__":
    unittest.main()
