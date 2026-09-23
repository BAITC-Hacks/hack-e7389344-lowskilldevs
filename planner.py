"""Auditable supplier replenishment; calculations never depend on an AI model.

Monthly inventory is a stockout proxy, not a record of actual unavailable days.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

DAYS_PER_MONTH = 365.25 / 12
ORDER_COLUMNS = [
    "supplier", "sku", "name", "category", "monthly_demand", "forecast_qty",
    "stock", "in_transit", "moq", "pack_size", "order_qty", "urgency", "reason",
    "spikes_removed", "lost_demand", "season_factor", "growth_rate", "manual_review",
    "unknown_transit", "lead_demand", "stock_month", "potential_order_qty",
    "spike_units_removed", "demand_months", "demand_pattern", "model_label",
    "arrival_gap_qty", "arrival_gap_date",
]
HISTORY_COLUMNS = [
    "supplier", "sku", "month", "qty", "clean_qty", "adjusted_qty", "season_factor",
    "spike", "client_spike", "transaction_spike", "stockout_proxy", "lost_demand",
]


def _frame(data: dict, name: str, columns: list[str]) -> pd.DataFrame:
    value = data.get(name)
    result = value.copy() if isinstance(value, pd.DataFrame) else pd.DataFrame(value or [])
    for column in columns:
        if column not in result:
            result[column] = pd.Series(index=result.index, dtype="object")
    for column in ("supplier", "sku", "category", "customer_id"):
        if column in result:
            result[column] = result[column].fillna("").astype(str).str.strip()
    if "supplier" in result and "sku" in result:
        result = result[(result.supplier != "") & (result.sku != "")].copy()
    return result


def _numeric(series: pd.Series, default: float | None = None) -> pd.Series:
    result = pd.to_numeric(series, errors="coerce").replace([np.inf, -np.inf], np.nan).astype(float)
    return result.fillna(default) if default is not None else result


def _date(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce", utc=True).dt.tz_convert(None)


def _outliers(values: np.ndarray) -> np.ndarray:
    """High-only robust detection; low sales are never removed as anomalies."""
    if len(values) < 4:
        return np.zeros(len(values), dtype=bool)
    center = float(np.median(values))
    deviation = float(np.median(np.abs(values - center))) * 1.4826
    return values > max(center + 3.5 * deviation, center * 2.5, center + 3)


def _demand_pattern(values: np.ndarray) -> str:
    """Classify observed incidence; absent months are never invented as zeros."""
    positive_count = int(np.count_nonzero(values > 0))
    if len(values) < 4:
        return "insufficient"
    if positive_count == 0:
        return "no_demand"
    if positive_count == 1:
        return "insufficient"
    return "intermittent" if positive_count / len(values) <= .6 else "regular"


def _monthly_outliers(values: np.ndarray, pattern: str) -> tuple[np.ndarray, float]:
    """Sparse incidence is not an anomaly: compare positive event sizes only.

    Three or more positive months are required for a magnitude comparison. One
    event alone cannot establish whether a sale was exceptional, so it is kept
    in the audit history and referred to a manager instead of being erased.
    """
    flags = np.zeros(len(values), dtype=bool)
    if pattern in ("intermittent", "insufficient"):
        positive = values[values > 0]
        if len(positive) >= 3:
            center = float(np.median(positive))
            scale = float(np.median(np.abs(positive - center))) * 1.4826
            flags = values > max(center + 3.5 * scale, 4 * center, center + 3)
        remaining = values[(values > 0) & ~flags]
    else:
        flags = _outliers(values)
        remaining = values[~flags]
    return flags, float(np.median(remaining)) if len(remaining) else 0.


def _round_order(need: float, moq: int, pack: int) -> int:
    return 0 if need <= 1e-8 else int(math.ceil((max(need, moq) - 1e-9) / pack) * pack)


def _number(value: Any, default: float = 0) -> float:
    try:
        value = float(value)
        return value if math.isfinite(value) else default
    except (ValueError, TypeError):
        return default


def _quantity_label(value: float) -> str:
    return f"{value:.3f}".rstrip("0").rstrip(".") if pd.notna(value) else "неизвестно"


def _transaction_reductions(transactions: pd.DataFrame) -> tuple[dict, set]:
    """Use customer-month history if identified, else conservative SKU-day spikes.

    This reduces the corresponding monthly aggregate, never adds a second sales
    source. Customers with fewer than four months are not labelled anomalous.
    """
    reductions, client_keys = {}, set()
    for (supplier, sku), sku_group in transactions.groupby(["supplier", "sku"]):
        identified = sku_group.customer_id.ne("").all()
        if identified:
            groups = sku_group.groupby(["customer_id", "month"], as_index=False).qty.sum().groupby("customer_id")
            for _, group in groups:
                values = group.qty.to_numpy(dtype=float)
                flags = _outliers(values)
                reference = float(np.median(values[~flags])) if (~flags).any() else 0
                for row, flagged in zip(group.itertuples(), flags):
                    if flagged:
                        key = (supplier, sku, row.month)
                        reductions[key] = reductions.get(key, 0) + max(0, row.qty - reference)
                        client_keys.add(key)
        else:
            daily = sku_group.groupby(sku_group.date.dt.normalize()).qty.sum().sort_index()
            if len(daily) < 8:
                continue
            values = daily.to_numpy(dtype=float)
            reference = float(np.median(values))
            flags = _outliers(values) & (values > max(5 * reference, 10))
            for day, qty, flagged in zip(daily.index, values, flags):
                if flagged:
                    key = (supplier, sku, day.to_period("M").to_timestamp())
                    reductions[key] = reductions.get(key, 0) + max(0, qty - reference)
    return reductions, client_keys


def calculate_orders(
    data: dict,
    lead_time_days: int = 30,
    review_days: int = 30,
    safety_days: int = 14,
    growth_pct: float = 0,
    as_of: Any = None,
    category_filter: Any = None,
    clean_spikes: bool = True,
) -> dict[str, Any]:
    """Return orders/history DataFrames, warnings and summary.

    Incomplete months and future data are excluded. Missing months are not filled
    with invented zeros. Forecast integrates calendar days, seasonality and a
    bounded robust annual trend. Missing/stale stock or uncertain arrivals make
    order_qty NaN; potential_order_qty is only an indicative manager estimate.
    clean_spikes=False disables both transaction and monthly spike reductions;
    seasonality, stockout correction, model selection and growth stay unchanged.
    """
    durations = [lead_time_days, review_days, safety_days]
    if any(not math.isfinite(float(v)) or float(v) < 0 or int(v) != float(v) for v in durations):
        raise ValueError("Сроки должны быть целыми неотрицательными днями.")
    lead_time_days, review_days, safety_days = map(int, durations)
    horizon = lead_time_days + review_days + safety_days
    if horizon <= 0 or horizon > 730:
        raise ValueError("Горизонт заказа должен составлять от 1 до 730 дней.")
    if not math.isfinite(float(growth_pct)) or not -80 <= float(growth_pct) <= 100:
        raise ValueError("Корректировка роста должна быть от −80 до 100 процентов.")
    today = pd.Timestamp(as_of if as_of is not None else pd.Timestamp.today()).tz_localize(None).normalize()
    if pd.isna(today):
        raise ValueError("Некорректная дата расчёта.")
    current_month = today.to_period("M").to_timestamp()
    warnings: list[str] = []
    sales = _frame(data, "sales", ["supplier", "sku", "name", "category", "month", "qty"])
    stock = _frame(data, "stock", ["supplier", "sku", "month", "stock"])
    moqs = _frame(data, "moq", ["supplier", "sku", "moq", "pack_size"])
    transit = _frame(data, "transit", ["supplier", "sku", "qty", "eta"])
    seasons = _frame(data, "seasonality", ["supplier", "category", "month", "factor"])
    growth = _frame(data, "growth", ["supplier", "category", "growth_rate"])
    transactions = _frame(data, "transactions", ["supplier", "sku", "date", "qty", "customer_id"])
    sales["month"] = _date(sales.month).dt.to_period("M").dt.to_timestamp()
    stock["month"] = _date(stock.month)
    sales["qty"] = _numeric(sales.qty)
    stock["stock"] = _numeric(stock.stock)
    if (sales.qty < 0).any():
        warnings.append("Отрицательные месячные продажи (возвраты) ограничены нулём для прогноза спроса.")
    sales["qty"] = sales.qty.clip(lower=0)
    sales = sales.dropna(subset=["month", "qty"])
    if (sales.month >= current_month).any():
        warnings.append("Неполный текущий и будущие месяцы продаж исключены из оценки спроса.")
    sales = sales[sales.month < current_month].copy()
    stock = stock[stock.month <= today].dropna(subset=["month"]).copy()
    transit["qty"] = _numeric(transit.qty, 0).clip(lower=0)
    transit["eta"] = _date(transit.eta)
    transactions["date"] = _date(transactions.date)
    transactions["qty"] = _numeric(transactions.qty, 0).clip(lower=0)
    transactions = transactions[transactions.date < current_month].copy()
    transactions["month"] = transactions.date.dt.to_period("M").dt.to_timestamp()
    transaction_reductions, client_keys = _transaction_reductions(transactions) if clean_spikes else ({}, set())
    seasons["month"] = _numeric(seasons.month)
    seasons["factor"] = _numeric(seasons.factor)
    seasons = seasons[(seasons.month >= 1) & (seasons.month <= 12) & (seasons.factor > 0)].copy()
    if ((seasons.factor < .2) | (seasons.factor > 3)).any():
        warnings.append("Экстремальные коэффициенты сезонности ограничены диапазоном 0,2–3.")
    seasons["factor"] = seasons.factor.clip(.2, 3)
    growth["growth_rate"] = _numeric(growth.growth_rate)

    product_frames = []
    for frame in (stock, sales):
        subset = frame.copy()
        for field, default in (("name", ""), ("category", "Все")):
            if field not in subset:
                subset[field] = default
            subset[field] = subset[field].fillna(default).astype(str).str.strip().replace("", default)
        product_frames.append(subset[["supplier", "sku", "name", "category"]])
    products = pd.concat(product_frames, ignore_index=True).drop_duplicates(["supplier", "sku"], keep="last")
    if category_filter is not None:
        selected = [category_filter] if isinstance(category_filter, str) else list(category_filter)
        products = products[products.category.isin(selected)]
    sales_groups = {key: group for key, group in sales.groupby(["supplier", "sku"])}
    stock_groups = {key: group for key, group in stock.groupby(["supplier", "sku"])}
    transit_groups = {key: group for key, group in transit.groupby(["supplier", "sku"])}
    moq_lookup = {(row.supplier, row.sku): row for row in moqs.itertuples()}
    season_lookup = {(row.supplier, row.category, int(row.month)): float(row.factor) for row in seasons.itertuples()}
    growth_lookup = {(row.supplier, row.category): row.growth_rate for row in growth.itertuples() if pd.notna(row.growth_rate)}
    rows, history = [], []
    dates = pd.date_range(today, periods=horizon, freq="D")
    missing_season = False
    for product in products.itertuples(index=False):
        key = (product.supplier, product.sku)
        reasons, review = [], False
        factors = {month: season_lookup.get((product.supplier, product.category, month), season_lookup.get((product.supplier, "Все", month), 1.)) for month in range(1, 13)}
        if not any((product.supplier, cat, m) in season_lookup for cat in [product.category, "Все"] for m in range(1, 13)):
            missing_season = True
        monthly = sales_groups.get(key, sales.iloc[0:0]).groupby("month", as_index=False).qty.sum().sort_values("month").tail(24)
        inventory = stock_groups.get(key, stock.iloc[0:0]).sort_values("month")
        stock_value = float(inventory.iloc[-1].stock) if not inventory.empty and pd.notna(inventory.iloc[-1].stock) else np.nan
        stock_date = inventory.iloc[-1].month if not inventory.empty else pd.NaT
        stock_basis = str(inventory.iloc[-1].get("stock_basis", "unknown")) if not inventory.empty else "unknown"
        if stock_basis == "opening":
            reasons.append("Входящий остаток на начало месяца: движения после снимка не учтены; подтвердите фактический остаток")
            review = True
        elif stock_basis != "snapshot" and pd.notna(stock_date):
            reasons.append("Месячный остаток: точный момент снимка не подтверждён; подтвердите фактический остаток")
            review = True
        if pd.isna(stock_value):
            reasons.append("Нет подтверждённого остатка: количество требует проверки")
            review = True
        elif stock_value < 0:
            reasons.append("Отрицательный остаток: требуется сверка учёта")
            review = True
        elif (today - stock_date).days > 45:
            reasons.append("Остаток старше 45 дней: требуется обновление")
            review = True
        month_stock = inventory.copy()
        month_stock["period"] = month_stock.month.dt.to_period("M").dt.to_timestamp()
        month_stock = month_stock.drop_duplicates("period", keep="last").set_index("period").stock.to_dict()
        if monthly.empty:
            review = True
            reasons.append("Нет завершённых месяцев продаж для оценки спроса")
            monthly_demand, local_growth = 0., 0.
            spikes_removed, spike_units, lost_total = 0, 0., 0.
            pattern = "insufficient"
            model_label = "Недостаточно истории для прогноза"
        else:
            monthly["season_factor"] = monthly.month.dt.month.map(factors)
            # Choose the model on the same observed incidence in both toggle
            # modes so before/after compares cleaning, not different model rules.
            pattern = _demand_pattern(monthly.qty.to_numpy(dtype=float))
            monthly["event_reduction"] = [min(float(qty), transaction_reductions.get((*key, month), 0)) for month, qty in zip(monthly.month, monthly.qty)]
            monthly["clean_qty"] = (monthly.qty - monthly.event_reduction).clip(lower=0)
            deseason = (monthly.clean_qty / monthly.season_factor).to_numpy(dtype=float)
            flags, replacement = _monthly_outliers(deseason, pattern) if clean_spikes else (np.zeros(len(deseason), dtype=bool), 0.)
            monthly.loc[flags, "clean_qty"] = replacement * monthly.loc[flags, "season_factor"]
            monthly["spike"] = flags | (monthly.event_reduction > 0).to_numpy()
            monthly["client_spike"] = [(*key, month) in client_keys for month in monthly.month]
            monthly["transaction_spike"] = monthly.event_reduction > 0
            monthly["stockout_proxy"] = monthly.month.map(month_stock).le(0)
            normal = monthly.loc[~monthly.stockout_proxy, "clean_qty"] / monthly.loc[~monthly.stockout_proxy, "season_factor"]
            reference = float(normal.tail(12).median()) if len(normal) else 0.
            monthly["adjusted_qty"] = monthly.clean_qty
            for index in monthly.index[monthly.stockout_proxy]:
                expected = reference * monthly.at[index, "season_factor"]
                uplift = min(max(expected - monthly.at[index, "clean_qty"], 0), .5 * expected)
                monthly.at[index, "adjusted_qty"] += uplift
            monthly["lost_demand"] = monthly.adjusted_qty - monthly.clean_qty
            recent = monthly.tail(12)
            levels = (recent.adjusted_qty / recent.season_factor).to_numpy(dtype=float)
            times = (recent.month.dt.year * 12 + recent.month.dt.month).to_numpy(dtype=float)
            if pattern == "regular":
                slopes = []
                if len(recent) >= 6:
                    slopes = [(levels[j] - levels[i]) / (times[j] - times[i]) for i in range(len(times)) for j in range(i + 1, len(times))]
                slope = float(np.median(slopes)) if slopes else 0.
                raw_level = float(np.median(levels[-6:]))
                local_growth = float(np.clip(12 * slope / max(raw_level, 1), -.5, .5))
                bounded_slope = local_growth * raw_level / 12
                monthly_demand = max(0., float(np.median(levels[-6:] + bounded_slope * (times[-1] - times[-6:]))))
                model_label = "Робастный уровень и тренд"
            else:
                # Expected units/month = average positive size × observed event
                # frequency. Unlike the median, this preserves recurring rare
                # demand. No SKU trend is extrapolated from sparse occurrences.
                monthly_demand = max(0., float(np.mean(levels)))
                local_growth = 0.
                model_label = {"intermittent": "Средняя частота за 12 месяцев", "insufficient": "Предварительная средняя · мало наблюдений", "no_demand": "Нулевой наблюдаемый спрос"}[pattern]
                if pattern == "intermittent":
                    reasons.append(f"Прерывистый спрос: {int((recent.qty > 0).sum())} ненулевых из {len(recent)} месяцев; средняя частота сохраняет регулярные редкие покупки")
                elif pattern == "insufficient" and int((monthly.qty > 0).sum()) == 1:
                    reasons.append("Только одна положительная продажа: регулярность не подтверждена; средняя предварительная, требуется классификация менеджером")
                    review = True
                elif pattern == "no_demand":
                    reasons.append("В наблюдаемой истории продаж нет; это не доказывает отсутствие потенциального спроса")
            spikes_removed = int(monthly.spike.sum())
            spike_units = float((monthly.qty - monthly.clean_qty).sum())
            lost_total = float(monthly.lost_demand.sum())
            if spikes_removed:
                reasons.append(f"Сглажено всплесков: {spikes_removed}")
            if lost_total > 0:
                reasons.append("Спрос скорректирован по нулевым месячным остаткам (оценка)")
            if len(monthly) < 4:
                reasons.append("Короткая история: менее 4 месяцев")
                review = True
            if (today.to_period("M") - monthly.month.max().to_period("M")).n > 3:
                reasons.append("История продаж устарела более чем на 3 месяца")
                review = True
            for hist in monthly.itertuples():
                history.append(dict(supplier=product.supplier, sku=product.sku, month=hist.month, qty=hist.qty, clean_qty=hist.clean_qty, adjusted_qty=hist.adjusted_qty, season_factor=hist.season_factor, spike=bool(hist.spike), client_spike=bool(hist.client_spike), transaction_spike=bool(hist.transaction_spike), stockout_proxy=bool(hist.stockout_proxy), lost_demand=hist.lost_demand))
        source_growth = growth_lookup.get((product.supplier, product.category), growth_lookup.get((product.supplier, "Все")))
        estimated_growth = local_growth if source_growth is None else .6 * local_growth + .4 * float(np.clip(source_growth, -.8, 1))
        final_growth = float(np.clip(estimated_growth + float(growth_pct) / 100, -.8, 1.))
        anchor = monthly.month.max() + pd.Timedelta(days=DAYS_PER_MONTH / 2) if not monthly.empty else today
        future_factors = np.array([factors[date.month] for date in dates])
        years = np.maximum((dates - anchor).total_seconds().to_numpy() / (86400 * 365.25), 0)
        daily_forecast = monthly_demand / DAYS_PER_MONTH * future_factors * np.power(1 + final_growth, years)
        forecast = float(daily_forecast.sum())
        lead_demand = float(daily_forecast[:lead_time_days].sum())
        incoming = transit_groups.get(key, transit.iloc[0:0])
        unknown = float(incoming.loc[incoming.eta.isna(), "qty"].sum())
        due = incoming[incoming.eta.notna() & (incoming.eta < today + pd.Timedelta(days=horizon))]
        in_transit = float(due.qty.sum())
        if unknown > 0:
            reasons.append("Есть товар в пути без даты: проверьте срок поступления")
            review = True
        if (due.eta < today - pd.Timedelta(days=7)).any():
            reasons.append("Есть просроченные поступления: подтвердите поставку")
            review = True
        moq_row = moq_lookup.get(key)
        moq = max(1, math.ceil(_number(getattr(moq_row, "moq", None), 1)))
        pack = max(1, math.ceil(_number(getattr(moq_row, "pack_size", None), 1)))
        if moq_row is None:
            reasons.append("MOQ/кратность не заданы: принято 1, подтвердите у поставщика")
        potential = _round_order(forecast - stock_value - in_transit, moq, pack) if pd.notna(stock_value) else np.nan
        # A positive balance at the horizon must not hide a shortage while
        # waiting for existing transit. Include the proposed new order at its
        # configured lead time. When that new order is positive, its ordinary
        # pre-lead shortage is already represented by urgency, not a new block.
        arrival_gap_qty, arrival_gap_date = 0., pd.NaT
        future_due = due[(due.eta.dt.normalize() > today) & (due.qty > 0)]
        if pd.notna(stock_value) and stock_value >= 0 and not future_due.empty:
            receipts = np.zeros(horizon, dtype=float)
            for delivery in due.itertuples():
                arrival_day = max(0, int((delivery.eta.normalize() - today).days))
                if arrival_day < horizon:
                    receipts[arrival_day] += delivery.qty
            if potential > 0 and lead_time_days < horizon:
                receipts[lead_time_days] += potential
            balances = stock_value + np.cumsum(receipts) - np.cumsum(daily_forecast)
            start_day = lead_time_days if potential > 0 else 0
            last_arrival_day = int((future_due.eta.max().normalize() - today).days)
            before_arrival = balances[start_day:last_arrival_day]
            shortage_days = np.flatnonzero(before_arrival < -1e-8)
            if len(shortage_days):
                arrival_gap_qty = float(-before_arrival.min())
                arrival_gap_date = today + pd.Timedelta(days=start_day + int(shortage_days[0]))
                reasons.append(
                    f"До поступления товара в пути возможен дефицит до {_quantity_label(arrival_gap_qty)} ед. "
                    f"с {arrival_gap_date:%d.%m.%Y}; проверьте сроки и объём поступлений"
                )
                review = True
        order_qty = np.nan if review else potential
        urgency = "Проверить данные" if review else ("Срочно" if potential > 0 and stock_value < lead_demand else "Планово" if potential > 0 else "Запас достаточен")
        demand_known = not monthly.empty
        demand_label = _quantity_label(monthly_demand if demand_known else np.nan)
        forecast_label = _quantity_label(forecast if demand_known else np.nan)
        stock_label = _quantity_label(stock_value)
        date_label = stock_date.strftime("%d.%m.%Y") if pd.notna(stock_date) else "дата неизвестна"
        need = max(0., forecast - stock_value - in_transit) if demand_known and pd.notna(stock_value) else np.nan
        rounded_label = _quantity_label(potential if demand_known else np.nan)
        season_factor = float(future_factors.mean())
        reasons.append(
            f"База {demand_label} ед./мес.; сезонность {season_factor:.1%} от базы; рост {final_growth:+.1%}/год. "
            f"Прогноз {horizon} дн. {forecast_label} − остаток (снимок {date_label}) {stock_label} "
            f"− путь с датой в горизонте {_quantity_label(in_transit)} → потребность max(0, разница) {_quantity_label(need)} "
            f"→ {'предварительно ' if review else ''}{rounded_label} ед. после MOQ {moq} и кратности {pack}"
        )
        if not review and potential == 0:
            reasons.append("Остаток и поступления покрывают горизонт")
        rows.append(dict(supplier=product.supplier, sku=product.sku, name=product.name or product.sku, category=product.category,
                         monthly_demand=round(monthly_demand, 3), forecast_qty=round(forecast, 3), stock=stock_value,
                         in_transit=in_transit, moq=moq, pack_size=pack, order_qty=order_qty, urgency=urgency,
                         reason="; ".join(reasons), spikes_removed=spikes_removed, lost_demand=round(lost_total, 3),
                         season_factor=round(season_factor, 4), growth_rate=round(final_growth, 4),
                         manual_review=review, unknown_transit=unknown, lead_demand=round(lead_demand, 3), stock_month=stock_date,
                         potential_order_qty=potential, spike_units_removed=round(spike_units, 3), demand_months=len(monthly), demand_pattern=pattern, model_label=model_label,
                         arrival_gap_qty=round(arrival_gap_qty, 3), arrival_gap_date=arrival_gap_date))
    orders = pd.DataFrame(rows, columns=ORDER_COLUMNS)
    history_frame = pd.DataFrame(history, columns=HISTORY_COLUMNS)
    if len(history_frame) and history_frame.stockout_proxy.any():
        warnings.append("Нулевой остаток на месячную дату — только признак возможного дефицита. Дни отсутствия неизвестны; добавка спроса ограничена 50% типичного месячного спроса.")
    if missing_season:
        warnings.append("Для части категорий нет сезонности: для отсутствующих месяцев принят коэффициент 1.")
    if transaction_reductions:
        warnings.append("Всплески оценены по покупкам клиентов (от 4 месяцев) либо продажам SKU за день (от 8 дней, более 5× медианы); проверьте их разовый характер.")
    if not clean_spikes:
        warnings.append("Сглаживание всплесков отключено для сравнения; сезонность, поправка на возможный дефицит и правила роста сохранены.")
    if len(orders) and orders.demand_pattern.eq("intermittent").any():
        warnings.append("Для прерывистого спроса используется средняя частота по наблюдаемым месяцам: нули сохраняются, большие продажи сравниваются с положительными месяцами.")
    if len(orders) and orders.manual_review.any():
        warnings.append("Строки «Проверить данные» не имеют автоматического заказа; предварительная оценка вынесена в отдельную колонку.")
    if len(orders) and orders.arrival_gap_qty.gt(0).any():
        warnings.append("Суммарный товар в пути может не покрыть спрос до даты прихода. Проверка по дням учитывает известные приходы и предполагает поступление нового заказа через заданный срок поставки; выявленные разрывы требуют решения менеджера.")
    if not stock.empty:
        bases = set(stock.stock_basis.dropna().astype(str)) if "stock_basis" in stock else {"unknown"}
        if "opening" in bases:
            warnings.append("Входящий остаток отражает начало месяца. Продажи и поступления после снимка не восстановлены; перед заказом подтвердите текущий остаток.")
        if "unknown" in bases:
            warnings.append("Для части месячных остатков не подтверждено, начало или конец месяца они отражают; расчёт использует последнюю указанную дату.")
    summary = dict(products=len(orders), recommended_skus=int((orders.order_qty.fillna(0) > 0).sum()),
                   total_order_qty=float(orders.order_qty.fillna(0).sum()), manual_review_skus=int(orders.manual_review.sum()) if len(orders) else 0,
                   spikes_removed=int(orders.spikes_removed.sum()) if len(orders) else 0,
                   lost_demand=float(orders.lost_demand.sum()) if len(orders) else 0,
                   as_of=today.date().isoformat(), horizon_days=horizon)
    return dict(orders=orders, history=history_frame, warnings=warnings, summary=summary)
