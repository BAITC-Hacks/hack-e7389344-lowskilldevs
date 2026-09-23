from __future__ import annotations

import hashlib
import json
import math
from datetime import date

import pandas as pd
import streamlit as st

from ai_agent import explain_plan, run_procurement_agent
from data_loader import combine_datasets, load_partner_files
from demo_data import load_demo_data
from planner import calculate_orders
from order_workflow import (
    DEMAND_LABELS, EDITABLE_COLUMNS, STATUS_LABELS, apply_input_overrides, approval_signature,
    assess_decisions, csv_bytes, prepare_decisions,
)


st.set_page_config(page_title="LogiPilot · Заказы поставщикам", page_icon="📦", layout="wide")

LABELS = {
    "supplier": "Поставщик", "sku": "Артикул", "name": "Наименование",
    "category": "Категория", "monthly_demand": "Спрос / месяц",
    "forecast_qty": "Потребность", "stock": "Остаток", "in_transit": "В пути",
    "moq": "MOQ", "pack_size": "Кратность", "order_qty": "К заказу, ед.",
    "recommended_order_qty": "Расчётный заказ, ед.", "urgency": "Приоритет",
    "reason": "Обоснование", "spikes_removed": "Очищенные всплески",
    "lost_demand": "Поправка на дефицит", "season_factor": "Сезонный коэффициент",
    "growth_rate": "Темп роста", "manual_review": "Проверить данные",
    "unknown_transit": "В пути без даты", "potential_order_qty": "Предварительный заказ",
    "stock_month": "Дата остатка", "lead_demand": "Спрос за срок поставки",
    "spike_units_removed": "Исключено разовых продаж, ед.", "demand_months": "Месяцев истории",
    "include_in_order": "В заказ", "defer": "Отложить", "decision_reason": "Решение менеджера",
    "workflow_status": "Статус", "decision_error": "Что проверить",
    "demand_pattern": "Тип спроса", "model_label": "Модель спроса",
    "input_correction_reason": "Уточнение исходных данных",
    "arrival_gap_qty": "Дефицит до поступления", "arrival_gap_date": "Дата риска дефицита",
}
TABLE_COLUMNS = [
    "include_in_order", "sku", "name", "recommended_order_qty", "order_qty",
    "decision_reason", "defer", "stock", "in_transit", "moq", "pack_size", "manual_review",
]


def fingerprint(data: dict) -> str:
    """Identify the complete input snapshot without retaining another file copy."""
    digest = hashlib.sha256()
    for key in sorted(data):
        value = data[key]
        digest.update(key.encode("utf-8"))
        if isinstance(value, pd.DataFrame):
            digest.update(value.to_csv(index=False).encode("utf-8"))
        else:
            digest.update(json.dumps(value, ensure_ascii=False, default=str, sort_keys=True).encode("utf-8"))
    return digest.hexdigest()


def supplier_subset(data: dict, suppliers: list[str]) -> dict:
    return {
        key: value.loc[value["supplier"].isin(suppliers)].copy()
        if isinstance(value, pd.DataFrame) and "supplier" in value.columns else value
        for key, value in data.items()
    }


def number(value, decimals: int = 0) -> str:
    if pd.isna(value) or not math.isfinite(float(value)):
        return "—"
    return f"{float(value):,.{decimals}f}".replace(",", " ")


def metadata_items(data: dict, key: str) -> list[str]:
    items = data.get(key, [])
    metadata = data.get("metadata", {})
    if isinstance(metadata, dict):
        additional = metadata.get(key, [])
        items = ([items] if isinstance(items, str) else list(items or [])) + (
            [additional] if isinstance(additional, str) else list(additional or [])
        )
    elif isinstance(items, str):
        items = [items]
    return list(dict.fromkeys(str(item) for item in items or []))


st.session_state.setdefault("imported_suppliers", {})
st.session_state.setdefault("source_mode", "Демонстрация")
st.session_state.setdefault("approved_signature", None)
st.session_state.setdefault("input_overrides", [])

with st.sidebar:
    st.markdown("### 📦 LogiPilot")
    st.caption("Планирование закупок · Электрокомплект")
    with st.expander("Загрузить Excel поставщика"):
        upload_supplier = st.selectbox("Поставщик отчётов", ["IEK", "Systeme Electric"])
        st.caption("Выберите 6 файлов: продажи по месяцам, остатки, товар в пути, MOQ, сезонность и динамика продаж.")
        uploads = st.file_uploader(
            "Отчёты одного поставщика", type=["xlsx"], accept_multiple_files=True,
            help="Затем таким же способом можно добавить второго поставщика.",
            key=f"uploads-{upload_supplier}",
        )
        if st.button("Импортировать отчёты", width="stretch", disabled=not uploads):
            try:
                files = {item.name: item.getvalue() for item in uploads}
                if len(files) != len(uploads):
                    raise ValueError("Имена загружаемых файлов должны отличаться.")
                with st.spinner("Читаем таблицы и сопоставляем артикулы…"):
                    imported = load_partner_files(files, upload_supplier)
                st.session_state.imported_suppliers[upload_supplier] = imported
                st.session_state.source_mode = "Мои Excel"
                st.success(f"{upload_supplier}: отчёты импортированы.")
            except Exception as exc:
                st.error(f"Не удалось импортировать: {exc}")
        if st.session_state.imported_suppliers:
            st.caption("Загружены: " + ", ".join(st.session_state.imported_suppliers))
            if st.button("Очистить загруженные данные", width="stretch"):
                st.session_state.imported_suppliers = {}
                st.session_state.source_mode = "Демонстрация"
                st.session_state.approved_signature = None
                st.rerun()
        st.caption("Файлы обрабатываются сервером приложения в текущей сессии. Внешний API вызывается только по вашему выбору во вкладке помощника.")

    source_mode = st.radio("Источник данных", ["Демонстрация", "Мои Excel"], key="source_mode")

if source_mode == "Мои Excel":
    if not st.session_state.imported_suppliers:
        st.session_state.approved_signature = None
        st.title("Заказы поставщикам")
        st.info("Загрузите отчёты в боковой панели или выберите демонстрацию, чтобы сразу увидеть расчёт.")
        st.stop()
    dataset = combine_datasets(list(st.session_state.imported_suppliers.values()))
else:
    dataset = load_demo_data()

source_signature = fingerprint(dataset)
if st.session_state.get("override_source") != source_signature:
    st.session_state.input_overrides = []
    st.session_state.override_source = source_signature
dataset = apply_input_overrides(dataset, st.session_state.input_overrides)

sales = dataset.get("sales", pd.DataFrame())
if sales.empty:
    st.session_state.approved_signature = None
    st.error("Нет истории продаж для расчёта. Проверьте импортированные отчёты.")
    st.stop()

with st.sidebar:
    st.divider()
    st.markdown("#### Параметры пополнения")
    as_of = st.date_input("Дата расчёта", value=date(2026, 9, 22), format="DD.MM.YYYY")
    lead_time = st.number_input("Срок поставки, дней", min_value=0, max_value=365, value=30, step=1)
    review_days = st.number_input("Период между заказами, дней", min_value=1, max_value=365, value=30, step=1)
    safety_days = st.number_input("Страховой запас, дней", min_value=0, max_value=365, value=14, step=1)
    clean_spikes = st.checkbox("Очищать разовые всплески", value=True,
                               help="Отключите, чтобы посчитать потребность без исключения разовых крупных продаж.")
    growth_pct = st.slider(
        "Дополнительный рост спроса, %", min_value=-50, max_value=100, value=0, step=5,
        help="Ручная поправка к прогнозу. Динамика из загруженных отчётов учитывается отдельно.",
    )
    st.caption(f"Покрытие потребности: {lead_time + review_days + safety_days} дней.")
    st.caption("Изменение параметров пересчитывает рекомендации и сбрасывает ручные правки.")
    st.divider()
    suppliers = sorted(sales["supplier"].dropna().astype(str).unique().tolist())
    selected_suppliers = st.multiselect("Поставщики в расчёте", suppliers, default=suppliers)
    available_categories = sorted(
        sales.loc[sales["supplier"].isin(selected_suppliers), "category"].dropna().astype(str).unique().tolist()
    )
    selected_categories = st.multiselect("Категории", available_categories, default=available_categories)

st.caption("ЭЛЕКТРОКОМПЛЕКТ / HACKALEM AI")
st.title("Заказы поставщикам")
st.write("Регулярный спрос, остатки и товар в пути — в одном проверяемом расчёте.")
st.caption(("Демонстрационные данные" if source_mode == "Демонстрация" else "Ваши отчёты Excel") + f" · На {as_of:%d.%m.%Y}")

if source_mode == "Демонстрация":
    st.info("Демо на синтетических данных двух поставщиков: разовая крупная продажа, дефицит и товар в пути. Для расчёта по исходным отчётам загрузите Excel.")
if not selected_suppliers or not selected_categories:
    st.session_state.approved_signature = None
    st.info("Выберите хотя бы одного поставщика и одну категорию в боковой панели.")
    st.stop()

parameters = {
    "lead_time_days": int(lead_time), "review_days": int(review_days),
    "safety_days": int(safety_days), "growth_pct": int(growth_pct),
    "as_of": as_of.isoformat(), "category_filter": selected_categories,
    "clean_spikes": clean_spikes,
}
calculation_data = supplier_subset(dataset, selected_suppliers)
calculation_signature = hashlib.sha256(json.dumps(
    {"data": fingerprint(dataset), "suppliers": selected_suppliers, "parameters": parameters},
    sort_keys=True,
).encode("utf-8")).hexdigest()
approval_invalidated = False
try:
    if st.session_state.get("calculation_signature") != calculation_signature:
        approval_invalidated = st.session_state.approved_signature is not None
        st.session_state.approved_signature = None
        with st.spinner("Считаем регулярную потребность и заказ…"):
            plan = calculate_orders(calculation_data, **parameters)
        st.session_state.current_plan = plan
        st.session_state.calculation_signature = calculation_signature
    else:
        plan = st.session_state.current_plan
except Exception as exc:
    st.error(f"Не удалось рассчитать заказ: {exc}")
    st.stop()

orders = plan["orders"].copy().reset_index(drop=True)
orders["input_correction_reason"] = [
    "; ".join(change["reason"] for change in st.session_state.input_overrides
              if change["supplier"] == row.supplier and change["sku"] == row.sku)
    for row in orders.itertuples()
]
if orders.empty:
    st.info("Для выбранных параметров нет позиций. Проверьте фильтры и дату расчёта.")
    st.stop()

metrics = st.columns(4)
metrics[0].metric("SKU в расчёте", number(len(orders)))
metrics[1].metric("SKU к пополнению", number((orders["order_qty"] > 0).sum()))
metrics[2].metric("Заказ, ед.", number(orders["order_qty"].sum()))
metrics[3].metric("SKU со всплесками", number((orders["spikes_removed"] > 0).sum()))
st.caption("Показатели отражают расчётную рекомендацию до ручных корректировок.")
missing_qty = orders["order_qty"].isna().sum()
if missing_qty:
    st.info(f"{missing_qty} SKU требуют проверки. Они не включены в заказ и не мешают подтвердить готовые позиции. Для ручного включения нужны количество и причина решения.")

order_tab, analysis_tab, backtest_tab, agent_tab, data_tab = st.tabs([
    "Заказ", "Спрос и сравнение", "Проверка на истории", "AI-помощник", "Данные",
])
with order_tab:
    st.subheader("Подтвердите готовые позиции")
    st.caption("«В заказ» включает позицию в итоговый файл. Для спорной позиции заполните количество и причину. «Отложить» сохраняет её вне заказа без подмены неизвестного количества нулём.")
    edited_orders = prepare_decisions(orders)
    supplier_names = orders["supplier"].drop_duplicates().tolist()
    supplier_tabs = st.tabs([str(value) for value in supplier_names])
    table_columns = [column for column in TABLE_COLUMNS if column in edited_orders]
    for supplier_tab, supplier in zip(supplier_tabs, supplier_names):
        with supplier_tab:
            subset = edited_orders.loc[edited_orders["supplier"] == supplier, table_columns].copy()
            st.caption(f"{len(subset)} SKU · поиск по артикулу доступен в панели таблицы.")
            edited = st.data_editor(
                subset, width="stretch", hide_index=True, height=380,
                disabled=[column for column in table_columns if column not in EDITABLE_COLUMNS],
                column_config={
                    **{column: st.column_config.Column(label) for column, label in LABELS.items() if column in table_columns},
                    "name": st.column_config.TextColumn("Наименование", width="medium"),
                    "include_in_order": st.column_config.CheckboxColumn("В заказ", help="В файл войдут только выбранные и проверенные позиции."),
                    "defer": st.column_config.CheckboxColumn("Отложить", help="Явно отложить решение. Снимите «В заказ» для этой позиции."),
                    "decision_reason": st.column_config.TextColumn("Решение менеджера", width="large", help="Обязательно при включении позиции, которая требует ручной проверки."),
                    "recommended_order_qty": st.column_config.NumberColumn("Расчёт, ед.", help="Пустое значение означает, что данных недостаточно."),
                    "order_qty": st.column_config.NumberColumn("К заказу, ед.", min_value=0, step=1),
                    "moq": st.column_config.NumberColumn("MOQ", help="Минимальный объём заказа при положительной потребности."),
                    "pack_size": st.column_config.NumberColumn("Кратность", help="Размер упаковки или шаг заказа."),
                },
                key=f"order-editor-{calculation_signature[:16]}-{supplier}",
            )
            for column in EDITABLE_COLUMNS:
                edited_orders.loc[edited.index, column] = edited[column]

    decisions = assess_decisions(edited_orders)
    decided_rows = decisions["rows"]
    final_order = decisions["selected"]
    status_cols = st.columns(4)
    status_cols[0].metric("Готово · в заказ", number(decisions["counts"]["ready"]))
    status_cols[1].metric("Проверить · вне заказа", number(decisions["counts"]["review"]))
    status_cols[2].metric("Отложено", number(decisions["counts"]["deferred"]))
    status_cols[3].metric("Итоговый заказ, ед.", number(final_order["order_qty"].sum()))
    if not decisions["errors"].empty:
        st.error("В выбранных строках есть незавершённые решения. Исправьте их или снимите «В заказ» — остальные позиции можно подтвердить отдельно.")
        st.dataframe(decisions["errors"].rename(columns=LABELS), hide_index=True, width="stretch")
    with st.expander("Реестр решений: готово, проверить, отложено"):
        register = decided_rows[["supplier", "sku", "name", "workflow_status", "recommended_order_qty", "order_qty", "decision_reason"]].copy()
        register["workflow_status"] = register["workflow_status"].map(STATUS_LABELS)
        st.dataframe(register.rename(columns=LABELS), hide_index=True, width="stretch")

    below_moq = final_order["order_qty"] < final_order["moq"]
    pack = pd.to_numeric(final_order["pack_size"], errors="coerce").fillna(1).clip(lower=1)
    wrong_pack = final_order["order_qty"].mod(pack).abs() > 1e-8
    if (below_moq | wrong_pack).any():
        st.warning("Есть ручные количества ниже MOQ или с нарушением кратности. Согласуйте такие позиции с поставщиком перед отправкой.")

    export_content = csv_bytes(final_order, LABELS)
    decision_signature = approval_signature(calculation_signature, edited_orders)
    previous_approval = st.session_state.approved_signature
    if previous_approval is not None and previous_approval != decision_signature:
        st.session_state.approved_signature = None
        approval_invalidated = True
    if approval_invalidated:
        st.info("Подтверждение сброшено: данные, параметры или количества изменились. Проверьте новый заказ.")
    if final_order.empty:
        st.info("Пока нет готовых выбранных позиций. Включите нужные строки и завершите решения по ним.")
    approval_col, export_col = st.columns(2)
    with approval_col:
        if st.button("Подтвердить итоговый заказ", type="primary", width="stretch",
                     disabled=not decisions["can_approve"]):
            st.session_state.approved_signature = decision_signature
    approved = st.session_state.approved_signature == decision_signature and decisions["can_approve"]
    with export_col:
        st.download_button(
            "Скачать подтверждённый заказ · CSV", data=export_content,
            file_name=f"orders_{as_of:%Y%m%d}.csv", mime="text/csv", width="stretch",
            disabled=not approved,
        )
    if approved:
        st.success("Заказ подтверждён и готов к выгрузке. Отправка поставщикам выполняется вами отдельно.")
    else:
        st.caption("Кнопка итогового CSV доступна после подтверждения. Изменение расчёта или количеств требует нового подтверждения.")

with analysis_tab:
    st.subheader("Почему предлагается этот заказ")
    try:
        st.markdown(explain_plan(plan))
    except Exception:
        st.info("Текстовое обоснование недоступно. Расчёт и пояснения по каждому артикулу доступны ниже.")
    selected_index = st.selectbox(
        "Разобрать артикул", list(orders.index),
        format_func=lambda index: f"{orders.at[index, 'supplier']} · {orders.at[index, 'sku']} · {orders.at[index, 'name']}",
    )
    selected = orders.loc[selected_index]
    if "model_label" in selected:
        st.caption("Подход к оценке спроса: " + str(selected["model_label"]))
    history = plan.get("history", pd.DataFrame())
    if not history.empty:
        sku_history = history.loc[
            (history["supplier"] == selected["supplier"]) & (history["sku"] == selected["sku"])
        ].copy()
        if not sku_history.empty:
            sku_history["month"] = pd.to_datetime(sku_history["month"])
            chart = sku_history.sort_values("month").set_index("month")[["qty", "clean_qty", "adjusted_qty"]]
            chart = chart.rename(columns={
                "qty": "Продажи", "clean_qty": "Без разовых всплесков",
                "adjusted_qty": "С поправкой на дефицит",
            })
            st.line_chart(chart, width="stretch", height=330, x_label="Месяц", y_label="Количество, ед.")
    detail_cols = st.columns(4)
    detail_cols[0].metric("Регулярный спрос / месяц", number(selected["monthly_demand"], 1))
    detail_cols[1].metric("Потребность на период", number(selected["forecast_qty"], 1))
    detail_cols[2].metric("Остаток + в пути", number(selected["stock"] + selected["in_transit"]))
    detail_cols[3].metric("Расчётный заказ", number(selected["order_qty"]))
    st.info(str(selected["reason"]))
    st.caption("Поправка на дефицит — оценка по доступной истории, а не зарегистрированные потерянные продажи.")
    with st.expander("Как читать расчёт"):
        st.write("Сервис оценивает регулярный спрос после обработки разовых всплесков и периодов дефицита, применяет сезонность и рост. Потребность покрывает срок поставки, интервал заказа и страховой запас. Из неё вычитаются остаток и учитываемый товар в пути. Положительный заказ округляется с учётом MOQ и кратности.")
        st.write("Результат — рекомендация для менеджера. В исходных файлах могут отсутствовать даты прихода или размер упаковки; принятые допущения показаны на вкладке качества данных.")

    st.divider()
    st.subheader("С очисткой всплесков и без неё")
    st.caption("Два расчёта на одних данных и с одинаковыми сроками. Различается только обработка разовых продаж; ручные решения менеджера сюда не входят.")
    if st.button("Сравнить два расчёта", key="compare-plans"):
        try:
            with st.spinner("Сравниваем спрос и рекомендации…"):
                cleaned = plan if clean_spikes else calculate_orders(calculation_data, **{**parameters, "clean_spikes": True})
                raw = plan if not clean_spikes else calculate_orders(calculation_data, **{**parameters, "clean_spikes": False})
                columns = ["supplier", "sku", "name", "forecast_qty", "order_qty"]
                comparison = cleaned["orders"][columns].merge(
                    raw["orders"][columns], on=["supplier", "sku", "name"], suffixes=("_clean", "_raw"), how="outer",
                )
                comparison["forecast_delta"] = comparison["forecast_qty_clean"] - comparison["forecast_qty_raw"]
                comparison["order_delta"] = comparison["order_qty_clean"] - comparison["order_qty_raw"]
                st.session_state.plan_comparison = {"signature": calculation_signature, "rows": comparison}
        except Exception as exc:
            st.error(f"Сравнение не выполнено: {exc}")
    cached_comparison = st.session_state.get("plan_comparison", {})
    if cached_comparison.get("signature") == calculation_signature:
        comparison = cached_comparison["rows"]
        compare_cols = st.columns(3)
        compare_cols[0].metric("Потребность без очистки, ед.", number(comparison["forecast_qty_raw"].sum()))
        compare_cols[1].metric("Потребность с очисткой, ед.", number(comparison["forecast_qty_clean"].sum()))
        compare_cols[2].metric("Разница потребности, ед.", number(comparison["forecast_delta"].sum()))
        comparison_labels = {
            **LABELS, "forecast_qty_clean": "Потребность с очисткой", "forecast_qty_raw": "Потребность без очистки",
            "order_qty_clean": "Заказ с очисткой", "order_qty_raw": "Заказ без очистки",
            "forecast_delta": "Разница потребности", "order_delta": "Разница заказа",
        }
        st.dataframe(comparison.rename(columns=comparison_labels), hide_index=True, width="stretch")
        st.caption("Разница = с очисткой − без очистки. Пустые заказы остаются неизвестными. Изменение количества не является доказательством экономии денег или улучшения сервиса.")

with backtest_tab:
    st.subheader("Как модель прогнозирует следующие месяцы")
    st.write("Проверка отделяет прошлое, доступное на дату прогноза, от следующего месяца продаж. Модель сравнивается со средним за 3 месяца и сезонным прогнозом по прошлому году.")
    st.caption("До 120 SKU из текущего расчёта, до 3 контрольных месяцев. Проверяется стандартная модель с очисткой всплесков, без ручных количеств. Запуск — только по кнопке.")
    if st.button("Запустить проверку на истории", key="run-backtest"):
        try:
            from evaluation import run_backtest

            keys = pd.MultiIndex.from_frame(orders[["supplier", "sku"]])
            evaluation_data = {
                name: frame.loc[pd.MultiIndex.from_frame(frame[["supplier", "sku"]]).isin(keys)].copy()
                if isinstance(frame, pd.DataFrame) and {"supplier", "sku"}.issubset(frame.columns) else frame
                for name, frame in calculation_data.items()
            }
            with st.spinner("Проверяем прогнозы на отложенных месяцах…"):
                evaluation = run_backtest(evaluation_data, n_folds=3, max_skus=120, as_of=as_of)
            st.session_state.backtest_result = {"signature": calculation_signature, "result": evaluation}
        except Exception as exc:
            st.error(f"Проверка не завершилась: {exc}")
    cached_backtest = st.session_state.get("backtest_result", {})
    if cached_backtest.get("signature") == calculation_signature:
        evaluation = cached_backtest["result"]
        coverage = evaluation.get("coverage", {})
        sample_cols = st.columns(3)
        sample_cols[0].metric("SKU в проверке", number(coverage.get("sample_skus", 0)))
        sample_cols[1].metric("Подходящих SKU", number(coverage.get("eligible_skus", 0)))
        sample_cols[2].metric("Наблюдений", number(coverage.get("n_observations", 0)))
        methods = {"model": "Модель пополнения", "mean_3m": "Среднее за 3 месяца", "seasonal_naive": "Тот же месяц год назад"}
        scopes = {"all_observed": "Все наблюдаемые продажи", "without_known_zero_stock": "Без известного нулевого остатка", "known_zero_stock": "С известным нулевым остатком"}
        metric_table = evaluation.get("metrics", pd.DataFrame()).copy()
        if not metric_table.empty:
            available_scopes = metric_table["scope"].drop_duplicates().tolist()
            default_scope = available_scopes.index("without_known_zero_stock") if "without_known_zero_stock" in available_scopes else 0
            scope = st.selectbox("Срез проверки", available_scopes, index=default_scope, format_func=lambda value: scopes.get(value, value))
            displayed_metrics = metric_table.loc[metric_table["scope"].eq(scope)].copy()
            displayed_metrics["method"] = displayed_metrics["method"].map(methods).fillna(displayed_metrics["method"])
            metric_labels = {"method": "Метод", "n_observations": "Наблюдений", "mae": "MAE, ед.", "wape_pct": "WAPE, %", "bias": "Смещение, ед.", "bias_pct": "Смещение, %"}
            st.dataframe(displayed_metrics[list(metric_labels)].rename(columns=metric_labels), hide_index=True, width="stretch")
            st.caption("MAE — средняя абсолютная ошибка; WAPE — сумма абсолютных ошибок относительно продаж. Меньше — точнее. Положительное смещение означает завышение прогноза, отрицательное — занижение.")
            if scope == "without_known_zero_stock":
                st.caption("Этот срез включает месяцы без сведений об остатке. Он не подтверждает постоянное наличие товара на складе.")
        else:
            st.info("Недостаточно сопоставимых наблюдений для расчёта метрик.")
        st.info("Проверяются наблюдаемые продажи, а не истинный неудовлетворённый спрос. Нулевой месячный остаток служит только признаком возможного дефицита. В историческом расчёте исключаются отчёты без даты, доступность которых в прошлом нельзя подтвердить. Денежная экономия здесь не оценивается.")
        for warning in evaluation.get("warnings", []):
            st.warning(str(warning))
        predictions = evaluation.get("predictions", pd.DataFrame())
        with st.expander("Месяцы, прогнозы и состав выборки"):
            st.write("Контрольные месяцы: " + ", ".join(str(value) for value in coverage.get("evaluated_months", [])))
            if coverage.get("selection_description"):
                st.write(str(coverage["selection_description"]))
            exclusion_labels = {
                "missing_truth": "Нет фактических продаж контрольного месяца", "missing_previous_3m": "Нет полного предыдущего квартала",
                "missing_previous_year": "Нет того же месяца прошлого года", "missing_model_forecast": "Нет сопоставимого прогноза модели",
                "insufficient_history_skus": "SKU с недостаточной историей", "invalid_sales_rows": "Некорректные строки продаж",
            }
            excluded = [{"Причина исключения": exclusion_labels.get(key, key), "Количество": value}
                        for key, value in coverage.get("exclusions", {}).items() if value]
            if excluded:
                st.dataframe(pd.DataFrame(excluded), hide_index=True, width="stretch")
            if not predictions.empty:
                prediction_labels = {**LABELS, "month": "Месяц", "method": "Метод", "actual": "Фактические продажи", "prediction": "Прогноз", "error": "Ошибка", "abs_error": "Абсолютная ошибка", "target_zero_stock": "Нулевой остаток", "target_stock_status": "Сведения об остатке"}
                readable = predictions.copy()
                readable["method"] = readable["method"].map(methods).fillna(readable["method"])
                if "demand_pattern" in readable:
                    readable["demand_pattern"] = readable["demand_pattern"].map(DEMAND_LABELS).fillna(readable["demand_pattern"])
                if "target_stock_status" in readable:
                    readable["target_stock_status"] = readable["target_stock_status"].map({
                        "known_zero": "Известен нулевой остаток", "known_positive": "Известен положительный остаток",
                        "unknown": "Нет сведений", "conflicting": "Противоречивые сведения",
                    }).fillna("Нет сведений")
                st.dataframe(readable.rename(columns=prediction_labels), hide_index=True, width="stretch")

with agent_tab:
    st.subheader("Помощник вызывает расчёт, затем объясняет результат")
    st.caption("Без API работает локальный разбор сценариев. Подключение OpenAI включается отдельно и не требуется для расчёта заказов.")
    use_api = st.checkbox("Использовать OpenAI API", value=False, key="use-procurement-api")
    api_key = ""
    api_model = ""
    if use_api:
        st.info("При запросе в OpenAI передаются ваш вопрос, названия поставщиков и агрегированные результаты инструментов. Исходные Excel, артикулы и строки продаж не передаются. Не вставляйте в вопрос ключи и клиентские данные.")
        api_key = st.text_input("API-ключ", type="password", key="procurement_api_key",
                                help="Хранится только в памяти текущей сессии приложения; не записывается в файлы и логи.")
        api_model = st.text_input("Доступная вам модель OpenAI", value="", key="procurement_model",
                                  placeholder="Введите идентификатор модели из вашего аккаунта")
        st.button("Забыть ключ", on_click=lambda: st.session_state.update(procurement_api_key=""))
    preset = st.selectbox("Сценарий для помощника", [
        "Свой вопрос", "Поставка ИЭК задержится на 10 дней. Сравни риски.",
        "Спрос ИЭК вырастет на 20%. Сравни заказ.", "Объясни предложенный заказ и позиции, требующие проверки.",
    ])
    with st.form("procurement-agent-question"):
        question = st.text_area("Вопрос или сценарий", value="" if preset == "Свой вопрос" else preset,
                                key=f"agent-question-{preset}", height=100)
        request_agent = st.form_submit_button("Разобрать сценарий", type="primary")
    if request_agent:
        if not question.strip():
            st.warning("Введите вопрос или выберите готовый сценарий.")
        elif use_api and (not api_key.strip() or not api_model.strip()):
            st.warning("Для OpenAI заполните ключ и название доступной модели. Можно выключить API и продолжить локально.")
        else:
            try:
                with st.spinner("Помощник проверяет сценарий расчётными инструментами…"):
                    answer = run_procurement_agent(question, calculation_data, parameters,
                                                   use_api=use_api, api_key=api_key, model=api_model)
                st.session_state.agent_result = {"signature": calculation_signature, "result": answer}
            except Exception:
                st.error("Помощник не смог завершить запрос. Расчёт заказов остаётся доступен; попробуйте локальный режим.")
    cached_answer = st.session_state.get("agent_result", {})
    if cached_answer.get("signature") == calculation_signature:
        answer = cached_answer["result"]
        modes = {"openai": "OpenAI · объяснение после вызова инструментов", "local": "Локальный режим · без внешнего AI",
                 "fallback": "Локальный результат · внешний AI не завершил запрос", "not_configured": "API не настроен"}
        st.caption(modes.get(answer.get("mode"), "Режим не указан"))
        st.markdown(str(answer.get("answer", "Ответ не получен.")))
        comparison = answer.get("comparison", pd.DataFrame())
        if isinstance(comparison, pd.DataFrame) and not comparison.empty:
            st.dataframe(comparison, hide_index=True, width="stretch")
        with st.expander("Ход расчёта и вызовы инструментов"):
            for index, step in enumerate(answer.get("trace", []), start=1):
                st.write(f"Шаг {index}")
                st.json(step)

with data_tab:
    st.subheader("Уточнить исходные данные по артикулу")
    st.caption("Уточнения действуют в текущей сессии и пересчитывают рекомендации. Исходные файлы сохраняются без изменений. После пересчёта ручные решения и подтверждение нужно проверить заново.")
    correction_index = st.selectbox(
        "Артикул для уточнения", list(orders.index), key="correction-sku",
        format_func=lambda index: f"{orders.at[index, 'supplier']} · {orders.at[index, 'sku']} · {orders.at[index, 'name']}",
    )
    correction_row = orders.loc[correction_index]
    unknown_transit = float(correction_row.get("unknown_transit", 0) or 0)
    with st.form(f"input-correction-{correction_row['supplier']}-{correction_row['sku']}-{as_of}"):
        stock_col, eta_col = st.columns(2)
        with stock_col:
            correct_stock = st.checkbox("Подтвердить фактический остаток", value=False)
            confirmed_stock = st.number_input(f"Остаток на {as_of:%d.%m.%Y}, ед.", min_value=0.0, value=None,
                                              placeholder="Введите после сверки", step=1.0)
        with eta_col:
            correct_eta = st.checkbox("Уточнить неизвестные даты прихода", value=False, disabled=unknown_transit <= 0)
            confirmed_eta = st.date_input("Подтверждённая дата прихода", value=as_of, format="DD.MM.YYYY",
                                          disabled=unknown_transit <= 0)
            st.caption(f"Без даты: {number(unknown_transit)} ед. Дата применится только к поступлениям этого артикула, у которых она отсутствует.")
        correction_reason = st.text_input("Основание уточнения", placeholder="Например: остаток сверен в учётной системе, приход подтверждён поставщиком")
        save_correction = st.form_submit_button("Применить уточнение и пересчитать")
    if save_correction:
        if not correct_stock and not correct_eta:
            st.warning("Отметьте, какие данные подтверждаете.")
        elif not correction_reason.strip():
            st.warning("Укажите основание уточнения для журнала решений.")
        elif correct_stock and confirmed_stock is None:
            st.warning("Введите подтверждённый остаток. Неизвестное значение нельзя автоматически заменить нулём.")
        else:
            correction = {"supplier": correction_row["supplier"], "sku": correction_row["sku"], "reason": correction_reason.strip()}
            if correct_stock:
                correction.update(stock=float(confirmed_stock), stock_date=as_of.isoformat())
            if correct_eta:
                correction["unknown_eta"] = confirmed_eta.isoformat()
            st.session_state.input_overrides.append(correction)
            st.rerun()
    if st.session_state.input_overrides:
        with st.expander("Журнал уточнений текущей сессии", expanded=True):
            correction_labels = {**LABELS, "reason": "Основание", "stock_date": "Дата остатка", "unknown_eta": "Уточнённая дата прихода"}
            st.dataframe(pd.DataFrame(st.session_state.input_overrides).rename(columns=correction_labels), hide_index=True, width="stretch")
            if st.button("Сбросить все уточнения исходных данных"):
                st.session_state.input_overrides = []
                st.rerun()
    st.divider()
    st.subheader("Источники и допущения")
    table_names = {
        "sales": "Продажи по месяцам", "stock": "Остатки по месяцам", "transit": "Товар в пути",
        "moq": "MOQ и кратность", "seasonality": "Сезонность", "growth": "Динамика продаж",
        "transactions": "Детальные продажи", "demo": "Синтетический пример",
    }
    warnings = metadata_items(dataset, "warnings") + [str(item) for item in plan.get("warnings", [])]
    warnings = list(dict.fromkeys(warnings))
    if warnings:
        for warning in warnings:
            st.warning(warning)
    else:
        st.success("Расчёт не сообщил о дополнительных предупреждениях.")
    sources = list(dataset.get("sources", []))
    if isinstance(dataset.get("metadata"), dict):
        sources.extend(dataset["metadata"].get("sources", []))
    if sources:
        with st.expander("Использованные источники", expanded=True):
            source_rows = []
            for source in sources:
                if isinstance(source, dict):
                    source_rows.append({
                        "Файл": source.get("file", "—"), "Лист": source.get("sheet", "—"),
                        "Тип": table_names.get(source.get("kind"), "Дополнительные данные"),
                        "Строк": source.get("rows", 0),
                    })
                else:
                    source_rows.append({"Файл": str(source), "Лист": "—", "Тип": "Источник", "Строк": 0})
            st.dataframe(pd.DataFrame(source_rows), hide_index=True, width="stretch")
    inventory = []
    for key, label in table_names.items():
        if key == "demo":
            continue
        frame = dataset.get(key, pd.DataFrame())
        inventory.append({"Набор данных": label, "Строк": len(frame)})
    st.dataframe(pd.DataFrame(inventory), hide_index=True, width="stretch")
    st.caption("Демо использует синтетическую историю. Исходные Excel не включаются в репозиторий; импорт происходит во время работы приложения.")