"""Temporal, matched-observation evaluation of forecasts of observed sales.

This module deliberately ignores undated source seasonality, growth, category,
MOQ and transit reports. Each fold rebuilds its predictors from dated history
available strictly before that forecast month. Inventory in the target month
is used only to label diagnostic slices, never as a forecast input.
"""
from __future__ import annotations

from collections import Counter
from hashlib import sha256
from typing import Any

import numpy as np
import pandas as pd

from planner import calculate_orders

METHODS = ("model", "mean_3m", "seasonal_naive")
SCOPES = ("all_observed", "without_known_zero_stock", "known_zero_stock")
MIN_HISTORY_MONTHS = 15
PREDICTION_COLUMNS = [
    "supplier", "sku", "month", "method", "actual", "prediction", "error",
    "abs_error", "target_zero_stock", "target_stock_status", "demand_pattern",
]
METRIC_COLUMNS = [
    "method", "scope", "n_observations", "mae", "wape_pct", "bias",
    "bias_pct", "actual_sum", "prediction_sum",
]
_SALES_COLUMNS = ["supplier", "sku", "name", "category", "month", "qty"]
_STOCK_COLUMNS = ["supplier", "sku", "month", "stock", "stock_basis"]
_TRANSACTION_COLUMNS = ["supplier", "sku", "date", "qty", "customer_id"]
_SEASON_COLUMNS = ["supplier", "category", "month", "factor"]
_GROWTH_COLUMNS = ["supplier", "category", "growth_rate"]


def _table(data: dict, key: str, columns: list[str]) -> pd.DataFrame:
    value = data.get(key)
    frame = value.copy() if isinstance(value, pd.DataFrame) else pd.DataFrame(value or [])
    frame = frame.reindex(columns=columns)
    for field in ("supplier", "sku"):
        if field in frame:
            frame[field] = frame[field].fillna("").astype(str).str.strip()
    return frame


def _dates(values: pd.Series) -> pd.Series:
    return pd.to_datetime(values, errors="coerce", utc=True).dt.tz_convert(None)


def _numeric(values: pd.Series) -> pd.Series:
    return pd.to_numeric(values, errors="coerce").replace([np.inf, -np.inf], np.nan)


def _normalise(data: dict) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict]:
    sales = _table(data, "sales", _SALES_COLUMNS)
    sales["month"] = _dates(sales.month).dt.to_period("M").dt.to_timestamp()
    sales["qty"] = _numeric(sales.qty)
    invalid = sales.month.isna() | sales.qty.isna() | sales.supplier.eq("") | sales.sku.eq("")
    counts = {"invalid_sales_rows": int(invalid.sum())}
    sales = sales.loc[~invalid].copy()
    # Aggregate before clipping: returns offset sales in that observed month.
    # Missing observations are absent; no calendar reindex/fill with zero occurs.
    sales = sales.groupby(["supplier", "sku", "month"], as_index=False, sort=True).qty.sum(min_count=1)
    counts["negative_net_sales_months"] = int(sales.qty.lt(0).sum())
    sales["qty"] = sales.qty.clip(lower=0)
    sales["name"] = sales.sku
    # Labels such as "category 2026" came from an undated current workbook.
    # Their historical validity is unknown, so they cannot define past models.
    sales["category"] = "Все"
    sales = sales[_SALES_COLUMNS]

    stock = _table(data, "stock", _STOCK_COLUMNS)
    stock["month"] = _dates(stock.month)
    stock["stock"] = _numeric(stock.stock)
    stock["stock_basis"] = stock.stock_basis.fillna("unknown").astype(str)
    stock = stock.loc[stock.month.notna() & stock.supplier.ne("") & stock.sku.ne("")].copy()
    snapshots = stock.stock_basis.eq("snapshot") | stock.month.dt.day.ne(1)
    counts["excluded_current_snapshot_rows"] = int(snapshots.sum())
    # Only historical monthly observations are allowed in either training or
    # the monthly inventory diagnostic. Current dated snapshots are excluded.
    stock = stock.loc[~snapshots].copy()

    transactions = _table(data, "transactions", _TRANSACTION_COLUMNS)
    transactions["date"] = _dates(transactions.date)
    transactions["qty"] = _numeric(transactions.qty)
    transactions["customer_id"] = transactions.customer_id.fillna("").astype(str)
    invalid = transactions.date.isna() | transactions.qty.isna() | transactions.supplier.eq("") | transactions.sku.eq("")
    counts["invalid_transaction_rows"] = int(invalid.sum())
    transactions = transactions.loc[~invalid].copy()
    return sales, stock, transactions, counts


def _stable_key(supplier: str, sku: str) -> str:
    return sha256(f"backtest-v1\0{supplier}\0{sku}".encode("utf-8")).hexdigest()


def _sample(training: pd.DataFrame, max_skus: int) -> tuple[list[tuple[str, str]], dict, list[dict]]:
    """Proportional supplier/incidence strata with one per stratum if feasible."""
    strata: dict[tuple[str, str], list[tuple[str, str]]] = {}
    patterns = {}
    for key, group in training.groupby(["supplier", "sku"], sort=True):
        if group.month.nunique() < MIN_HISTORY_MONTHS:
            continue
        recent = group.sort_values("month").tail(12)
        pattern = "intermittent" if recent.qty.gt(0).mean() <= .6 else "regular"
        patterns[key] = pattern
        strata.setdefault((key[0], pattern), []).append(key)
    for keys in strata.values():
        keys.sort(key=lambda key: (_stable_key(*key), key))
    labels = sorted(strata)
    limit = min(max_skus, len(patterns))
    quotas = dict.fromkeys(labels, 0)
    if limit >= len(labels):
        quotas = dict.fromkeys(labels, 1)
    elif limit:
        for label in sorted(labels, key=lambda key: (_stable_key(*key), key))[:limit]:
            quotas[label] = 1
    remaining = limit - sum(quotas.values())
    if remaining > 0:
        capacities = {label: len(strata[label]) - quotas[label] for label in labels}
        capacity_sum = sum(capacities.values())
        shares = {label: remaining * capacities[label] / capacity_sum for label in labels}
        for label in labels:
            quotas[label] += int(shares[label])
        rest = limit - sum(quotas.values())
        ranked = sorted(labels, key=lambda label: (-(shares[label] - int(shares[label])), _stable_key(*label)))
        for label in ranked[:rest]:
            quotas[label] += 1
    selected = sorted(key for label in labels for key in strata[label][:quotas[label]])
    counts = [dict(supplier=label[0], demand_pattern=label[1], eligible_skus=len(strata[label]), sampled_skus=quotas[label]) for label in labels]
    return selected, patterns, counts


def _select(frame: pd.DataFrame, keys: set[tuple[str, str]]) -> pd.DataFrame:
    if frame.empty or not keys:
        return frame.iloc[0:0].copy()
    index = pd.MultiIndex.from_frame(frame[["supplier", "sku"]])
    return frame.loc[index.isin(keys)].copy()


def _training_seasonality(sales: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Supplier factors from complete SKU/calendar-years present in training.

    Missing months never become zero. Within each supplier-year, sum only SKUs
    having all twelve observed months; divide by that year's monthly average.
    Average the resulting year profiles so catalogue growth is not seasonality.
    """
    if sales.empty:
        return pd.DataFrame(columns=_SEASON_COLUMNS), []
    frame = sales.copy()
    frame["year"] = frame.month.dt.year
    frame["month_number"] = frame.month.dt.month
    size = frame.groupby(["supplier", "sku", "year"]).month.transform("nunique")
    complete = frame.loc[size.eq(12)]
    factors = []
    neutral_suppliers = []
    for supplier in sorted(frame.supplier.unique()):
        part = complete.loc[complete.supplier.eq(supplier)]
        aggregate = part.groupby(["year", "month_number"]).qty.sum().unstack("month_number")
        annual_mean = aggregate.mean(axis=1)
        aggregate = aggregate.loc[annual_mean.gt(0)]
        if aggregate.empty:
            neutral_suppliers.append(supplier)
            profile = pd.Series(1., index=range(1, 13))
        else:
            profile = aggregate.div(aggregate.mean(axis=1), axis=0).mean(axis=0).reindex(range(1, 13))
            # Same safety bounds as the production planner, not fitted against
            # the holdout. Sparse aggregate seasons may legitimately be zero.
            profile = profile.clip(.2, 3.)
        factors.extend((supplier, "Все", month, float(profile.loc[month])) for month in range(1, 13))
    return pd.DataFrame(factors, columns=_SEASON_COLUMNS), neutral_suppliers


def _training_growth(sales: pd.DataFrame, transactions: pd.DataFrame, origin: pd.Timestamp) -> tuple[pd.DataFrame, dict[str, str]]:
    """Comparable completed months of two years; journal preferred per supplier."""
    records, sources = [], {}
    end = origin - pd.DateOffset(months=1)
    for supplier in sorted(sales.supplier.unique()):
        journal = transactions.loc[transactions.supplier.eq(supplier)]
        if not journal.empty:
            monthly = journal.assign(month=journal.date.dt.to_period("M").dt.to_timestamp()).groupby("month").qty.sum()
            source = "journal"
        else:
            monthly = sales.loc[sales.supplier.eq(supplier)].groupby("month").qty.sum()
            source = "monthly_sales"
        current = monthly[(monthly.index.year == end.year) & (monthly.index.month <= end.month)]
        previous = monthly[(monthly.index.year == end.year - 1) & (monthly.index.month <= end.month)]
        shared_months = set(current.index.month) & set(previous.index.month)
        old = float(previous.loc[previous.index.month.isin(shared_months)].sum())
        new = float(current.loc[current.index.month.isin(shared_months)].sum())
        if shared_months and old > 0:
            records.append((supplier, "Все", max(0., new) / old - 1.))
            sources[supplier] = source
        else:
            sources[supplier] = "none"
    return pd.DataFrame(records, columns=_GROWTH_COLUMNS), sources


def _stock_status(stock: pd.DataFrame) -> dict[tuple, str]:
    statuses = {}
    for key, group in stock.groupby(["supplier", "sku", "month"], sort=False):
        values = group.stock.dropna().unique()
        if len(values) != 1:
            status = "conflicting" if len(values) > 1 else "unknown"
        elif values[0] == 0:
            status = "known_zero"
        elif values[0] > 0:
            status = "known_positive"
        else:
            status = "invalid_negative"
        statuses[key] = status
    return statuses


def _metrics(predictions: pd.DataFrame) -> pd.DataFrame:
    records = []
    for scope in SCOPES:
        if scope == "known_zero_stock":
            subset = predictions.loc[predictions.target_zero_stock.eq(True)]
        elif scope == "without_known_zero_stock":
            subset = predictions.loc[~predictions.target_zero_stock.eq(True)]
        else:
            subset = predictions
        for method in METHODS:
            rows = subset.loc[subset.method.eq(method)]
            count = len(rows)
            actual_sum = float(rows.actual.abs().sum())
            pred_sum = float(rows.prediction.sum())
            abs_error = float(rows.abs_error.sum())
            error = float(rows.error.sum())
            records.append(dict(
                method=method, scope=scope, n_observations=count,
                mae=abs_error / count if count else np.nan,
                wape_pct=100 * abs_error / actual_sum if actual_sum > 0 else np.nan,
                bias=error / count if count else np.nan,
                bias_pct=100 * error / actual_sum if actual_sum > 0 else np.nan,
                actual_sum=actual_sum, prediction_sum=pred_sum,
            ))
    return pd.DataFrame(records, columns=METRIC_COLUMNS)


def run_backtest(data: dict, n_folds: int = 3, max_skus: int = 120, as_of: Any = None) -> dict:
    """Rolling one-month forecasts, compared on identical observed-sales rows.

    ``as_of='2026-09-22'`` with three folds evaluates June, July and August.
    ``mean_3m`` needs the three exact preceding calendar months; seasonal naive
    needs the same calendar month a year earlier. A missing truth or baseline
    excludes that SKU/month for every method, without changing the frozen sample.

    MAE and bias are units/observation; bias is prediction minus actual. WAPE and
    bias_pct are percentages divided by sum(abs(actual)); undefined denominators
    yield NaN. Actual is observed monthly net sales, bounded below at zero, and
    must not be interpreted as uncensored demand or an ordering cost measure.
    """
    for label, value in (("n_folds", n_folds), ("max_skus", max_skus)):
        if isinstance(value, bool) or not isinstance(value, (int, np.integer)) or value <= 0:
            raise ValueError(f"{label} должен быть положительным целым числом.")
    n_folds, max_skus = int(n_folds), int(max_skus)
    today = pd.Timestamp(as_of if as_of is not None else pd.Timestamp.today())
    if pd.isna(today):
        raise ValueError("Некорректная дата проверки.")
    if today.tzinfo is not None:
        today = today.tz_localize(None)
    current_month = today.to_period("M").to_timestamp()
    months = pd.date_range(end=current_month - pd.DateOffset(months=1), periods=n_folds, freq="MS")
    first_origin = months[0]
    sales, stock, transactions, invalid_counts = _normalise(data)
    first_training = sales.loc[sales.month.lt(first_origin)]
    selected, patterns, strata = _sample(first_training, max_skus)
    selected_set = set(selected)
    total_skus = len(sales[["supplier", "sku"]].drop_duplicates())
    warnings = [
        "Проверяется прогноз наблюдаемых чистых продаж, ограниченных снизу нулём; скрытый спрос, стоимость запасов и качество итогового заказа здесь не измеряются.",
        "Выборка SKU и её страты зафиксированы до первого проверяемого месяца. Метрики описывают эту выборку, а не весь каталог.",
        "Исходные недатированные коэффициенты сезонности/роста, категории, MOQ, путь и текущие снимки склада не используются. Сезонность и рост пересчитываются внутри каждого обучающего окна на уровне поставщика.",
        "Отсутствующие месяцы не заменяются нулями. Строка оценивается только при наличии факта, трёх предыдущих месяцев, прошлогоднего месяца и прогноза каждой модели.",
        "Нулевой месячный остаток — признак возможного ограничения продаж, а не доказательство отсутствия товара весь месяц. Срез без известных нулевых остатков включает неизвестные остатки.",
    ]
    if any("пустые" in str(w).lower() and "продаж" in str(w).lower() for w in data.get("warnings", [])):
        warnings.append("Импорт исходных файлов трактовал пустые ячейки продаж как наблюдаемые нули; проверка наследует эту конвенцию источника.")
    if len(selected) < len(patterns):
        warnings.append(f"Проверяется {len(selected)} из {len(patterns)} подходящих SKU: пропорциональная выборка по поставщику и регулярности спроса.")
    if any(s["sampled_skus"] == 0 for s in strata):
        warnings.append("Лимит SKU меньше числа страт: часть сочетаний поставщик/тип спроса в выборку не вошла.")
    lookup = sales.set_index(["supplier", "sku", "month"]).qty.to_dict()
    stock_status = _stock_status(stock)
    records, folds = [], []
    exclusions = Counter(missing_truth=0, missing_previous_3m=0, missing_previous_year=0, missing_model_forecast=0)
    neutral_folds = 0
    for origin in months:
        fold_exclusions = Counter(missing_truth=0, missing_previous_3m=0, missing_previous_year=0, missing_model_forecast=0)
        training_sales = sales.loc[sales.month.lt(origin)].copy()
        training_stock = stock.loc[stock.month.lt(origin)].copy()
        training_transactions = transactions.loc[transactions.date.lt(origin)].copy()
        seasons, neutral_suppliers = _training_seasonality(training_sales)
        growth, growth_sources = _training_growth(training_sales, training_transactions, origin)
        neutral_folds += bool(neutral_suppliers)
        train = dict(
            sales=_select(training_sales, selected_set),
            stock=_select(training_stock, selected_set),
            transactions=_select(training_transactions, selected_set),
            seasonality=seasons,
            growth=growth,
            moq=pd.DataFrame(columns=["supplier", "sku", "moq", "pack_size"]),
            transit=pd.DataFrame(columns=["supplier", "sku", "qty", "eta"]),
        )
        if selected:
            forecast = calculate_orders(train, lead_time_days=0, review_days=origin.days_in_month,
                                        safety_days=0, growth_pct=0, as_of=origin)["orders"]
            model_lookup = forecast.set_index(["supplier", "sku"]).forecast_qty.to_dict()
        else:
            model_lookup = {}
        matched = 0
        supplier_coverage = {supplier: dict(selected_skus=sum(key[0] == supplier for key in selected), matched_observations=0) for supplier in sorted({key[0] for key in selected})}
        for supplier, sku in selected:
            key = (supplier, sku)
            actual = lookup.get((*key, origin))
            previous = [lookup.get((*key, origin - pd.DateOffset(months=offset))) for offset in (1, 2, 3)]
            seasonal = lookup.get((*key, origin - pd.DateOffset(years=1)))
            model = model_lookup.get(key)
            if actual is None or not np.isfinite(actual):
                reason = "missing_truth"
            elif any(v is None or not np.isfinite(v) for v in previous):
                reason = "missing_previous_3m"
            elif seasonal is None or not np.isfinite(seasonal):
                reason = "missing_previous_year"
            elif model is None or not np.isfinite(model) or model < 0:
                reason = "missing_model_forecast"
            else:
                reason = None
            if reason:
                fold_exclusions[reason] += 1
                continue
            matched += 1
            supplier_coverage[supplier]["matched_observations"] += 1
            status = stock_status.get((*key, origin), "unknown")
            values = (float(model), float(np.mean(previous)), float(seasonal))
            for method, prediction in zip(METHODS, values):
                error = prediction - float(actual)
                records.append(dict(supplier=supplier, sku=sku, month=origin, method=method,
                                    actual=float(actual), prediction=prediction, error=error, abs_error=abs(error),
                                    target_zero_stock=status == "known_zero", target_stock_status=status,
                                    demand_pattern=patterns[key]))
        exclusions.update(fold_exclusions)
        folds.append(dict(month=origin.date().isoformat(), matched_observations=matched,
                          training_sales_through=training_sales.month.max().date().isoformat() if not training_sales.empty else None,
                          training_transactions_before=origin.date().isoformat(), growth_sources=growth_sources,
                          neutral_seasonality_suppliers=neutral_suppliers, exclusions=dict(fold_exclusions),
                          suppliers=supplier_coverage))
    predictions = pd.DataFrame(records, columns=PREDICTION_COLUMNS)
    if not predictions.empty:
        predictions = predictions.sort_values(["month", "supplier", "sku", "method"]).reset_index(drop=True)
    metrics = _metrics(predictions)
    observations = predictions.loc[predictions.method.eq("model")]
    if observations.empty:
        warnings.append("Нет общих наблюдений для сравнения: проверьте историю, даты и покрытие файлов.")
    if metrics.loc[metrics.n_observations.gt(0), "wape_pct"].isna().any():
        warnings.append("Для срезов с нулевой суммой фактических продаж WAPE и относительное смещение не определены (NaN); используйте MAE и смещение в единицах.")
    if neutral_folds:
        warnings.append("В части окон нет положительного полного календарного года для сезонности поставщика; использован нейтральный коэффициент 1.")
    coverage = dict(
        as_of=today.date().isoformat(), first_forecast_month=first_origin.date().isoformat(),
        requested_months=[month.date().isoformat() for month in months],
        evaluated_months=[fold["month"] for fold in folds if fold["matched_observations"]],
        n_folds=n_folds, sample_skus=len(selected), eligible_skus=len(patterns), total_sales_skus=total_skus,
        n_observations=len(observations), n_method_predictions=len(predictions),
        potential_observations=len(selected) * n_folds, min_history_months=MIN_HISTORY_MONTHS,
        selection_description="Не менее 15 наблюдаемых месяцев до первого окна; страты поставщик × доля положительных месяцев (>60% = регулярный спрос) за последние 12 наблюдаемых месяцев; пропорциональные квоты с минимумом 1 на страту, если лимит позволяет; внутри страты постоянный SHA-256 кода SKU. Будущие продажи не участвуют в отборе.",
        exclusions={**dict(exclusions), "insufficient_history_skus": total_skus - len(patterns), **invalid_counts},
        target_stock_status_counts=observations.target_stock_status.value_counts().to_dict(),
        strata=strata, folds=folds,
    )
    return dict(metrics=metrics, predictions=predictions, coverage=coverage, warnings=warnings)
