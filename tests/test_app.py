"""Exercise the manager's approval boundary through Streamlit's real UI runner."""
from __future__ import annotations

import io
from pathlib import Path
import unittest
from unittest.mock import patch

import pandas as pd
import streamlit as st
from streamlit.testing.v1 import AppTest

from demo_data import load_demo_data


APP = str(Path(__file__).resolve().parents[1] / "app.py")


class ApprovalFlowTests(unittest.TestCase):
    def new_app(self):
        app = AppTest.from_file(APP, default_timeout=60).run()
        self.assertEqual(len(app.exception), 0, [item.message for item in app.exception])
        return app

    def approve_button(self, app):
        return next(button for button in app.button if button.label == "Подтвердить итоговый заказ")

    def export_disabled(self, app):
        return app.get("download_button")[0].disabled

    def test_demo_approval_and_recalculation_filters(self):
        app = self.new_app()
        self.assertEqual(len(app.session_state["current_plan"]["orders"]), 8)
        self.assertTrue(self.export_disabled(app))
        self.approve_button(app).click().run()
        self.assertFalse(self.export_disabled(app))

        app.slider[0].set_value(20).run()
        self.assertTrue(self.export_disabled(app))
        self.assertIsNone(app.session_state["approved_signature"])
        self.approve_button(app).click().run()
        self.assertFalse(self.export_disabled(app))

        app.multiselect[0].set_value(["Systeme Electric"]).run()
        self.assertTrue(self.export_disabled(app))
        self.assertEqual(set(app.session_state["current_plan"]["orders"]["supplier"]), {"Systeme Electric"})
        self.assertEqual(len(app.exception), 0)

    def test_edit_revokes_approval_and_negative_quantity_blocks_it(self):
        with patch("streamlit.data_editor", wraps=st.data_editor) as editors:
            app = self.new_app()
            editor_key = editors.call_args_list[0].kwargs["key"]
            self.approve_button(app).click().run()
            self.assertFalse(self.export_disabled(app))

            app.session_state[editor_key] = {
                "edited_rows": {0: {"order_qty": 1}}, "added_rows": [], "deleted_rows": [],
            }
            app.run()
            self.assertTrue(self.export_disabled(app))
            # AppTest has no data-editor frontend: each subsequent UI event must
            # carry the edit again, just as the browser sends widget state.
            app.session_state[editor_key] = {
                "edited_rows": {0: {"order_qty": 1}}, "added_rows": [], "deleted_rows": [],
            }
            self.approve_button(app).click().run()
            self.assertFalse(self.export_disabled(app))
            manual_metric = next(metric for metric in app.metric if metric.label == "Ручных корректировок")
            self.assertEqual(manual_metric.value, "1")

            app.session_state[editor_key] = {
                "edited_rows": {0: {"order_qty": -1}}, "added_rows": [], "deleted_rows": [],
            }
            app.run()
            self.assertTrue(self.export_disabled(app))
            self.assertTrue(self.approve_button(app).disabled)
            self.assertGreater(len(app.error), 0)
            self.assertEqual(len(app.exception), 0)

    def test_missing_stock_requires_explicit_manager_quantity(self):
        data = load_demo_data()
        first_sku = data["sales"].iloc[0]["sku"]
        data["stock"] = data["stock"].loc[data["stock"]["sku"] != first_sku].copy()
        with patch("demo_data.load_demo_data", return_value=data), patch(
            "streamlit.data_editor", wraps=st.data_editor
        ) as editors:
            app = self.new_app()
            orders = app.session_state["current_plan"]["orders"]
            self.assertTrue(orders.loc[orders["sku"] == first_sku, "order_qty"].isna().all())
            self.assertTrue(self.approve_button(app).disabled)
            self.assertTrue(self.export_disabled(app))
            target_editor = next(call for call in editors.call_args_list if first_sku in call.args[0]["sku"].values)
            displayed = target_editor.args[0]
            row_number = displayed["sku"].tolist().index(first_sku)
            app.session_state[target_editor.kwargs["key"]] = {
                "edited_rows": {row_number: {"order_qty": 0}}, "added_rows": [], "deleted_rows": [],
            }
            app.run()
            self.assertFalse(self.approve_button(app).disabled)
            self.assertTrue(self.export_disabled(app))
            app.session_state[target_editor.kwargs["key"]] = {
                "edited_rows": {row_number: {"order_qty": 0}}, "added_rows": [], "deleted_rows": [],
            }
            self.approve_button(app).click().run()
            self.assertFalse(self.export_disabled(app))
            self.assertEqual(len(app.exception), 0)

    def test_export_is_excel_readable_and_formula_text_is_inert(self):
        data = load_demo_data()
        first_sku = data["sales"].iloc[0]["sku"]
        data["sales"].loc[data["sales"]["sku"] == first_sku, "name"] = "  =1+1"
        with patch("demo_data.load_demo_data", return_value=data), patch(
            "streamlit.download_button", wraps=st.download_button
        ) as download:
            app = self.new_app()
            self.approve_button(app).click().run()
            self.assertFalse(self.export_disabled(app))
            content = download.call_args.kwargs["data"]
            self.assertTrue(content.startswith(b"\xef\xbb\xbf"))
            exported = pd.read_csv(io.BytesIO(content), sep=";", encoding="utf-8-sig")
            self.assertTrue((exported["К заказу, ед."] > 0).all())
            self.assertEqual(exported.loc[exported["Артикул"] == first_sku, "Наименование"].iloc[0], "'=1+1")
            self.assertIn("Расчётный заказ, ед.", exported.columns)


if __name__ == "__main__":
    unittest.main()
