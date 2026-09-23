from __future__ import annotations

import hashlib
import json
import math
from datetime import date

import pandas as pd
import streamlit as st

from ai_agent import explain_plan
from data_loader import combine_datasets, load_partner_files
from demo_data import load_demo_data
from planner import calculate_orders


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
}
TABLE_COLUMNS = [
    "sku", "name", "category", "monthly_demand", "forecast_qty", "stock",
    "in_transit", "moq", "pack_size", "order_qty", "urgency", "manual_review",
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


def csv_bytes(frame: pd.DataFrame) -> bytes:
    """UTF-8 BOM, Excel-friendly separator, and inert text cells."""
    def safe_text(value):
        if isinstance(value, str) and (
            value.lstrip().startswith(("=", "+", "-", "@"))
            or value.startswith(("\t", "\r", "\n"))
        ):
            return "'" + value
        return value

    result = frame.copy()
    for column in result.columns:
        result[column] = result[column].map(safe_text)
    return result.rename(columns=LABELS).to_csv(index=False, sep=";").encode("utf-8-sig")


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
        st.caption("Файлы обрабатываются сервером приложения в текущей сессии. Внешние AI-сервисы не вызываются.")

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
title_col, badge_col = st.columns([4, 1])
with title_col:
    st.title("Заказы поставщикам")
    st.write("Регулярный спрос, остатки и товар в пути — в одном проверяемом расчёте.")
with badge_col:
    st.markdown("**" + ("Демонстрационные данные" if source_mode == "Демонстрация" else "Ваши отчёты Excel") + "**")
    st.caption(f"На {as_of:%d.%m.%Y}")

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
}
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
            plan = calculate_orders(supplier_subset(dataset, selected_suppliers), **parameters)
        st.session_state.current_plan = plan
        st.session_state.calculation_signature = calculation_signature
    else:
        plan = st.session_state.current_plan
except Exception as exc:
    st.error(f"Не удалось рассчитать заказ: {exc}")
    st.stop()

orders = plan["orders"].copy().reset_index(drop=True)
if orders.empty:
    st.info("Для выбранных параметров нет позиций. Проверьте фильтры и дату расчёта.")
    st.stop()

metrics = st.columns(4)
metrics[0].metric("SKU в расчёте", number(len(orders)))
metrics[1].metric("SKU к пополнению", number((orders["order_qty"] > 0).sum()))
metrics[2].metric("Рекомендуется заказать, ед.", number(orders["order_qty"].sum()))
metrics[3].metric("SKU со всплесками", number((orders["spikes_removed"] > 0).sum()))
st.caption("Показатели отражают расчётную рекомендацию до ручных корректировок.")
missing_qty = orders["order_qty"].isna().sum()
if missing_qty:
    st.warning(f"Для {missing_qty} SKU недостаточно данных для автоматического заказа. Проверьте остаток и даты прихода, затем заполните количество вручную, включая 0 при отказе от заказа.")

order_tab, analysis_tab, data_tab = st.tabs(["Заказ и подтверждение", "Спрос и обоснование", "Качество данных"])
with order_tab:
    st.subheader("Проверьте и скорректируйте заказ")
    st.caption("Редактируется только столбец «К заказу, ед.». Нулевые позиции не попадут в выгрузку.")
    edited_orders = orders.copy()
    edited_orders["recommended_order_qty"] = orders["order_qty"]
    supplier_names = orders["supplier"].drop_duplicates().tolist()
    supplier_tabs = st.tabs([str(value) for value in supplier_names])
    table_columns = [column for column in TABLE_COLUMNS if column in orders]
    for supplier_tab, supplier in zip(supplier_tabs, supplier_names):
        with supplier_tab:
            subset = orders.loc[orders["supplier"] == supplier, table_columns].copy()
            st.caption(f"{len(subset)} SKU · расчётный заказ {number(subset['order_qty'].sum())} ед.")
            edited = st.data_editor(
                subset, width="stretch", hide_index=True, height=380,
                disabled=[column for column in table_columns if column != "order_qty"],
                column_config={
                    **{column: st.column_config.Column(label) for column, label in LABELS.items() if column in table_columns},
                    "name": st.column_config.TextColumn("Наименование", width="large"),
                    "monthly_demand": st.column_config.NumberColumn("Спрос / месяц", format="%.1f"),
                    "forecast_qty": st.column_config.NumberColumn("Потребность", format="%.1f"),
                    "order_qty": st.column_config.NumberColumn("К заказу, ед.", min_value=0, step=1, required=True),
                    "moq": st.column_config.NumberColumn("MOQ", help="Минимальный объём заказа при положительной потребности."),
                    "pack_size": st.column_config.NumberColumn("Кратность", help="Размер упаковки или шаг заказа."),
                },
                key=f"order-editor-{calculation_signature[:16]}-{supplier}",
            )
            edited_orders.loc[edited.index, "order_qty"] = edited["order_qty"]

    quantities = pd.to_numeric(edited_orders["order_qty"], errors="coerce")
    valid = quantities.notna() & quantities.ge(0) & quantities.lt(float("inf")) & quantities.mod(1).eq(0)
    if not valid.all():
        st.error("Для подтверждения заполните количество во всех строках: целое неотрицательное число. Пустые значения требуют решения менеджера.")
    edited_orders["order_qty"] = quantities
    final_order = edited_orders.loc[quantities.gt(0)].copy()
    manual_count = (quantities.notna() & (quantities != edited_orders["recommended_order_qty"])).sum()
    totals = st.columns(3)
    totals[0].metric("Итоговых позиций", number(len(final_order)))
    totals[1].metric("Итоговый заказ, ед.", number(final_order["order_qty"].sum()) if valid.all() else "—")
    totals[2].metric("Ручных корректировок", number(manual_count))

    below_moq = final_order["order_qty"] < final_order["moq"]
    pack = pd.to_numeric(final_order["pack_size"], errors="coerce").fillna(1).clip(lower=1)
    wrong_pack = final_order["order_qty"].mod(pack).abs() > 1e-8
    if (below_moq | wrong_pack).any():
        st.warning("Есть ручные количества ниже MOQ или с нарушением кратности. Согласуйте такие позиции с поставщиком перед отправкой.")

    export_content = csv_bytes(final_order)
    approval_signature = hashlib.sha256(
        calculation_signature.encode("ascii") + csv_bytes(edited_orders)
    ).hexdigest()
    previous_approval = st.session_state.approved_signature
    if previous_approval is not None and previous_approval != approval_signature:
        st.session_state.approved_signature = None
        approval_invalidated = True
    if approval_invalidated:
        st.info("Подтверждение сброшено: данные, параметры или количества изменились. Проверьте новый заказ.")
    if final_order.empty and valid.all():
        st.info("Положительных позиций к заказу нет. По текущим параметрам пополнение не требуется.")
    approval_col, export_col = st.columns(2)
    with approval_col:
        if st.button("Подтвердить итоговый заказ", type="primary", width="stretch",
                     disabled=not valid.all() or final_order.empty):
            st.session_state.approved_signature = approval_signature
    approved = st.session_state.approved_signature == approval_signature and valid.all() and not final_order.empty
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

with data_tab:
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
