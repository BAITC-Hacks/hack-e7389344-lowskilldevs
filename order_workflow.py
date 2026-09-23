"""Manager decisions stay separate from calculated demand and unknown inputs."""
from __future__ import annotations

import copy
import hashlib

import numpy as np
import pandas as pd

STATUS_LABELS = {"ready": "Готово", "review": "Проверить", "deferred": "Отложено"}
DEMAND_LABELS = {"regular": "Регулярный", "intermittent": "Редкий / прерывистый",
                 "insufficient": "Недостаточно истории", "no_demand": "Нет наблюдаемого спроса"}
EDITABLE_COLUMNS = ["include_in_order", "order_qty", "decision_reason", "defer"]


def prepare_decisions(orders: pd.DataFrame) -> pd.DataFrame:
    rows = orders.copy().reset_index(drop=True)
    quantity = pd.to_numeric(rows["order_qty"], errors="coerce")
    review = rows.get("manual_review", pd.Series(False, index=rows.index)).fillna(True).astype(bool) | quantity.isna()
    rows["recommended_order_qty"] = quantity
    rows["order_qty"] = quantity
    rows["include_in_order"] = (~review & quantity.gt(0)).astype(bool)
    rows["defer"] = (~review & quantity.eq(0)).astype(bool)
    rows["decision_reason"] = ""
    rows["workflow_status"] = np.where(review, "review", np.where(quantity.gt(0), "ready", "deferred"))
    return rows


def assess_decisions(decisions: pd.DataFrame) -> dict:
    """Only invalid *selected* rows block this order; unknown rows stay unknown."""
    rows = decisions.copy()
    quantity = pd.to_numeric(rows["order_qty"], errors="coerce")
    selected = rows["include_in_order"].fillna(False).astype(bool)
    deferred = rows["defer"].fillna(False).astype(bool)
    review = rows.get("manual_review", pd.Series(False, index=rows.index)).fillna(True).astype(bool)
    review |= pd.to_numeric(rows["recommended_order_qty"], errors="coerce").isna()
    reason = rows["decision_reason"].fillna("").astype(str).str.strip()
    valid_quantity = quantity.notna() & np.isfinite(quantity) & quantity.gt(0) & quantity.mod(1).eq(0)
    invalid_quantity = selected & ~valid_quantity
    missing_reason = selected & review & reason.eq("")
    conflict = selected & deferred
    invalid = invalid_quantity | missing_reason | conflict
    ready = selected & ~invalid
    rows["order_qty"] = quantity
    rows["decision_reason"] = reason
    rows["workflow_status"] = np.where(
        ready, "ready", np.where((deferred | ~review) & ~selected, "deferred", "review")
    )
    rows["decision_error"] = ""
    rows.loc[invalid_quantity, "decision_error"] = "Укажите целое количество больше нуля либо снимите «В заказ»."
    rows.loc[missing_reason, "decision_error"] += " Для ручного решения укажите причину."
    rows.loc[conflict, "decision_error"] += " Выберите одно: «В заказ» или «Отложить»."
    rows["decision_error"] = rows["decision_error"].str.strip()
    return {
        "rows": rows,
        "selected": rows.loc[ready].copy(),
        "errors": rows.loc[invalid, ["supplier", "sku", "decision_error"]].copy(),
        "can_approve": bool(ready.any() and not invalid.any()),
        "counts": {status: int(rows["workflow_status"].eq(status).sum()) for status in STATUS_LABELS},
    }


def approval_signature(calculation_signature: str, decisions: pd.DataFrame) -> str:
    # All decisions, including excluded rows and their reasons, are part of the
    # signed snapshot. This revokes approval when any manager decision changes.
    return hashlib.sha256(
        calculation_signature.encode("utf-8") + decisions.to_csv(index=False).encode("utf-8")
    ).hexdigest()


def csv_bytes(frame: pd.DataFrame, labels: dict[str, str] | None = None) -> bytes:
    def inert_text(value):
        if isinstance(value, str) and (
            value.lstrip().startswith(("=", "+", "-", "@"))
            or value.startswith(("\t", "\r", "\n"))
        ):
            return "'" + value
        return value

    result = frame.copy()
    if "workflow_status" in result:
        result["workflow_status"] = result["workflow_status"].map(STATUS_LABELS)
    if "demand_pattern" in result:
        result["demand_pattern"] = result["demand_pattern"].map(DEMAND_LABELS).fillna(result["demand_pattern"])
    for column in result.columns:
        result[column] = result[column].map(inert_text)
    return result.rename(columns=labels or {}).to_csv(index=False, sep=";").encode("utf-8-sig")


def apply_input_overrides(data: dict, overrides: list[dict]) -> dict:
    """Apply scoped, explained input corrections without editing source tables."""
    result = {key: value.copy() if isinstance(value, pd.DataFrame) else copy.deepcopy(value) for key, value in data.items()}
    for change in overrides:
        supplier, sku = change["supplier"], change["sku"]
        if not str(change.get("reason", "")).strip():
            raise ValueError("Уточнение исходных данных требует причины.")
        if "stock" in change:
            stock = float(change["stock"])
            if not np.isfinite(stock) or stock < 0:
                raise ValueError("Уточнённый остаток должен быть неотрицательным числом.")
            snapshot_date = pd.Timestamp(change["stock_date"])
            if pd.isna(snapshot_date):
                raise ValueError("Укажите дату подтверждённого остатка.")
            current = result.get("stock", pd.DataFrame(columns=["supplier", "sku", "month", "stock", "stock_basis"]))
            exact = current["supplier"].eq(supplier) & current["sku"].eq(sku) & pd.to_datetime(current["month"]).eq(snapshot_date)
            result["stock"] = pd.concat([
                current.loc[~exact], pd.DataFrame([{"supplier": supplier, "sku": sku, "month": snapshot_date,
                                                  "stock": stock, "stock_basis": "snapshot"}]),
            ], ignore_index=True)
        if "unknown_eta" in change:
            eta = pd.Timestamp(change["unknown_eta"])
            if pd.isna(eta):
                raise ValueError("Укажите подтверждённую дату прихода.")
            current = result.get("transit", pd.DataFrame(columns=["supplier", "sku", "qty", "eta"]))
            target = current["supplier"].eq(supplier) & current["sku"].eq(sku) & pd.to_datetime(current["eta"]).isna()
            current.loc[target, "eta"] = eta
            result["transit"] = current
        result.setdefault("warnings", []).append(f"Уточнение менеджера · {supplier} · {sku}: {change['reason']}")
    return result
