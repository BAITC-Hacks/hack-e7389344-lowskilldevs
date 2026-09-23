"""Deterministic synthetic scenarios; contains no partner records."""
from __future__ import annotations
import math
import pandas as pd


def load_demo_data() -> dict:
    months = pd.date_range('2024-01-01', '2026-08-01', freq='MS')
    products = [
        ('ИЭК', 'DEMO-001', 'Автоматический выключатель 16А', 'Модульное оборудование', 120, 55, 12, 6, 'steady'),
        ('ИЭК', 'DEMO-002', 'Кабельный канал 40×25', 'Кабельные системы', 210, 80, 50, 10, 'spike'),
        ('ИЭК', 'DEMO-003', 'Светильник складской LED', 'Освещение', 65, 20, 10, 5, 'stockout'),
        ('ИЭК', 'DEMO-004', 'Щит распределительный', 'Модульное оборудование', 45, 350, 4, 2, 'excess'),
        ('Systeme Electric', 'DEMO-101', 'Контактор 25А', 'Модульное оборудование', 90, 40, 10, 5, 'growth'),
        ('Systeme Electric', 'DEMO-102', 'Розетка с заземлением', 'Электроустановка', 340, 170, 100, 20, 'steady'),
        ('Systeme Electric', 'DEMO-103', 'Выключатель одноклавишный', 'Электроустановка', 230, 100, 50, 10, 'spike'),
        ('Systeme Electric', 'DEMO-104', 'Датчик движения', 'Освещение', 55, 15, 10, 5, 'seasonal'),
    ]
    factors = [0.74, 0.77, 0.89, 0.98, 1.04, 1.08, 0.96, 1.02, 1.21, 1.25, 1.13, 0.93]
    mean = sum(factors)/12
    factors = [f/mean for f in factors]
    sales, stock, moq, transit, seasons, growth = [], [], [], [], [], []
    for index, (supplier, sku, name, category, base, balance, minimum, pack, mode) in enumerate(products):
        for i, month in enumerate(months):
            trend = 1 + (0.012*i if mode == 'growth' else 0.002*i)
            qty = round(base * factors[month.month-1] * trend * (1 + 0.05*math.sin(i*1.7+index)))
            available = round(base*2)
            if mode == 'spike' and month == pd.Timestamp('2026-06-01'):
                qty += base*16
            if mode == 'stockout' and month in pd.to_datetime(['2026-04-01','2026-05-01','2026-07-01']):
                qty = 2
                available = 0
            sales.append([supplier,sku,name,category,month,qty])
            stock.append([supplier,sku,month,available])
        stock.append([supplier,sku,pd.Timestamp('2026-09-22'),balance])
        moq.append([supplier,sku,minimum,pack])
        if index in (0, 4, 5):
            transit.append([supplier,sku,round(base*0.6),pd.Timestamp('2026-10-03')])
    for supplier in ('ИЭК','Systeme Electric'):
        for month, factor in enumerate(factors,1):
            seasons.append([supplier,'Все',month,factor])
        growth.append([supplier,'Все',0.06])
    return {
        'sales': pd.DataFrame(sales,columns=['supplier','sku','name','category','month','qty']),
        'stock': pd.DataFrame(stock,columns=['supplier','sku','month','stock']),
        'moq': pd.DataFrame(moq,columns=['supplier','sku','moq','pack_size']),
        'transit': pd.DataFrame(transit,columns=['supplier','sku','qty','eta']),
        'seasonality': pd.DataFrame(seasons,columns=['supplier','category','month','factor']),
        'growth': pd.DataFrame(growth,columns=['supplier','category','growth_rate']),
        'transactions': pd.DataFrame(columns=['supplier','sku','date','qty','customer_id']),
        'sources': [{'file':'Синтетический пример','kind':'demo','rows':len(sales)}],
        'warnings': ['Демонстрационные данные вымышлены. Дата примера: 22.09.2026.'],
    }
