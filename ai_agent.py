"""Local explanation of verified planner output; no external API calls."""
from __future__ import annotations
import pandas as pd


def explain_plan(plan: dict) -> str:
    orders = plan.get('orders', pd.DataFrame())
    if orders.empty:
        return 'Нет позиций для расчёта по выбранным данным и фильтрам.'
    quantities = pd.to_numeric(orders['order_qty'], errors='coerce')
    proposed = orders.loc[quantities.fillna(0) > 0]
    manual = int(orders.get('manual_review', pd.Series(False,index=orders.index)).fillna(False).sum())
    spikes = int((pd.to_numeric(orders['spikes_removed'],errors='coerce').fillna(0) > 0).sum())
    lost = float(pd.to_numeric(orders['lost_demand'],errors='coerce').fillna(0).sum())
    parts = [
        f'Предложено пополнение **{len(proposed)} позиций** у **{proposed["supplier"].nunique()} поставщиков**.',
        'Потребность покрывает срок поставки, период между заказами и страховой запас. '
        'Из неё вычитаются доступный остаток и ожидаемые поступления в пределах горизонта. '
        'Положительный заказ округляется с учётом минимальной партии и кратности.',
    ]
    if spikes:
        parts.append(f'У **{spikes} позиций** обнаружены всплески, исключённые из регулярного спроса.')
    if lost > 0:
        parts.append(f'Оценка недопродаж за исторические месяцы: **{lost:,.0f} единиц**. '
                     'Это модельная оценка по месячным остаткам, а не подтверждённые потерянные продажи.')
    if manual:
        parts.append(f'**{manual} позиций требуют ручной проверки** исходных остатков или сроков поступления.')
    parts.append('Проверьте объяснение каждой позиции перед утверждением. Расчёт не отправляет заказы поставщикам.')
    return '\n\n'.join(parts)
