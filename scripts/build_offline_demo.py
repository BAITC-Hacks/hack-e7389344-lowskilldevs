"""Build a deterministic, self-contained jury demo from the actual planner.

Run: python scripts/build_offline_demo.py
Only synthetic demo_data is loaded. No partner workbooks or network are used.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from demo_data import load_demo_data
from planner import calculate_orders


def build_payload() -> dict:
    data = load_demo_data()
    cases, histories = {}, {}
    for lead in (15, 30, 45, 60):
        for clean in (True, False):
            for growth in (0, 20, 40):
                result = calculate_orders(
                    data, lead_time_days=lead, review_days=30, safety_days=14,
                    growth_pct=growth, as_of="2026-09-22", clean_spikes=clean,
                )
                key = f"{lead}|{int(clean)}|{growth}"
                cases[key] = {
                    "orders": json.loads(result["orders"].to_json(orient="records", date_format="iso")),
                    "summary": result["summary"], "warnings": result["warnings"],
                }
                history = json.loads(result["history"].to_json(orient="records", date_format="iso"))
                if str(int(clean)) in histories:
                    assert histories[str(int(clean))] == history, "History unexpectedly depends on forecast settings"
                else:
                    histories[str(int(clean))] = history
    source = (ROOT / "planner.py").read_bytes() + (ROOT / "demo_data.py").read_bytes()
    return {
        "as_of": "2026-09-22", "version": hashlib.sha256(source).hexdigest()[:12],
        "source": "planner.calculate_orders + demo_data.load_demo_data",
        "cases": cases, "histories": histories,
    }


HTML = r'''<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="description" content="Автономная синтетическая демонстрация расчёта заказов LogiPilot AI">
<title>LogiPilot · автономная демонстрация</title>
<style>
:root{--ink:#11233d;--muted:#5b6d83;--blue:#2563eb;--navy:#0c1a30;--paper:#f3f6fb;--line:#dce4ef;--amber:#f59e0b;--green:#087b5a;--radius:16px}*{box-sizing:border-box}body{margin:0;font:15px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif;color:var(--ink);background:var(--paper)}button,input,select{font:inherit}button,select,input[type=checkbox]{cursor:pointer}button:focus-visible,select:focus-visible,input:focus-visible,summary:focus-visible{outline:3px solid #93b5ff;outline-offset:3px}button:disabled{cursor:not-allowed;opacity:.45}header{background:var(--navy);color:white;padding:21px max(24px,calc((100vw - 1460px)/2));display:flex;gap:16px;align-items:center;justify-content:space-between}.brand{font-size:20px;letter-spacing:.06em;font-weight:800;display:flex;gap:12px;align-items:center}.mark{width:34px;height:34px;border-radius:10px;background:#3d7bff;display:grid;place-items:center;font-size:21px}.tag{font-size:12px;color:#b9c9df;border:1px solid #334a67;padding:6px 12px;border-radius:30px}.hero{max-width:1460px;margin:auto;padding:30px 24px 24px;display:flex;align-items:end;justify-content:space-between;gap:24px}.eyebrow{font-size:11px;font-weight:800;letter-spacing:.15em;text-transform:uppercase;color:var(--blue)}h1{font-size:clamp(27px,3vw,42px);line-height:1.15;letter-spacing:-.045em;margin:9px 0 12px}h2{font-size:19px;letter-spacing:-.02em;margin:0}h3{font-size:15px;margin:0 0 12px}.hero p{margin:0;max-width:770px;color:var(--muted)}.date{font-size:13px;text-align:right;white-space:nowrap;color:var(--muted)}.date strong{display:block;color:var(--ink);font-size:18px}main{max-width:1460px;margin:0 auto;padding:0 24px 32px;display:grid;grid-template-columns:274px minmax(0,1fr);gap:22px}.panel{background:white;border:1px solid var(--line);border-radius:var(--radius);box-shadow:0 4px 18px #1b365b05}.controls{padding:23px;align-self:start;position:sticky;top:20px}.step{display:inline-grid;place-items:center;background:#eaf1ff;color:var(--blue);width:25px;height:25px;border-radius:8px;margin-right:7px;font-size:12px;font-weight:800}.field{display:block;margin:21px 0 0;font-size:12px;font-weight:700}.field select{display:block;width:100%;border:1px solid #cdd8e7;border-radius:9px;background:#fff;color:var(--ink);padding:10px 11px;margin-top:7px;font-size:14px}.hint{font-size:12px;line-height:1.5;color:var(--muted);margin:10px 0 0}.toggle{margin:22px 0 0;padding:13px 11px;background:#edf4ff;border-radius:10px;display:flex;gap:9px;font-size:13px;font-weight:700;align-items:start}.toggle input{accent-color:var(--blue);width:17px;height:17px;flex-shrink:0;margin-top:2px}.divider{height:1px;background:var(--line);margin:22px 0}.scenario-note{font-size:12px;line-height:1.65;color:var(--muted)}.scenario-note strong{color:var(--ink)}.content{display:grid;gap:19px;min-width:0}.stats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}.stat{padding:18px;background:white;border:1px solid var(--line);border-radius:14px}.stat span{display:block;font-size:11px;font-weight:700;color:var(--muted)}.stat b{display:block;font-size:30px;line-height:1.2;letter-spacing:-.05em;margin:8px 0 4px}.stat small{color:var(--muted);font-size:11px}.stat.primary{background:var(--blue);border-color:var(--blue);color:white}.stat.primary span,.stat.primary small{color:#dce9ff}.section-head{padding:21px 22px 15px;display:flex;align-items:center;justify-content:space-between;gap:14px}.section-head p{margin:4px 0 0;font-size:12px;color:var(--muted)}.section-head select{max-width:360px;padding:8px 10px;border:1px solid var(--line);border-radius:8px;background:white;font-size:12px}.table-wrap{overflow-x:auto}table{width:100%;border-collapse:collapse;font-size:12px;white-space:nowrap}thead{background:#f7f9fc;color:var(--muted)}th{text-align:right;font-size:10px;font-weight:750;padding:12px 11px;border-top:1px solid var(--line);border-bottom:1px solid var(--line)}th:nth-child(2),th:nth-child(3),th:nth-child(4){text-align:left}td{text-align:right;padding:13px 11px;border-bottom:1px solid #edf1f6}td:nth-child(2),td:nth-child(3),td:nth-child(4){text-align:left}tbody tr{cursor:pointer}tbody tr:hover{background:#f5f8ff}tbody tr.active{background:#edf4ff}td strong{font-size:12px}td small{display:block;color:var(--muted);font-size:10px;font-weight:400}.sku-name{max-width:200px;overflow:hidden;text-overflow:ellipsis}.badge{display:inline-block;padding:3px 7px;border-radius:5px;font-size:10px;font-weight:650;background:#eaf0fa;color:#536985}.badge.rare{background:#e7f7f2;color:var(--green)}.badge.review{background:#fff3d8;color:#94600a}.qty{font-weight:800;color:var(--blue);font-size:14px}.row-check{accent-color:var(--blue);width:15px;height:15px}.table-foot{padding:12px 22px;color:var(--muted);font-size:11px}.chart-area{padding:0 22px 16px}.chart-top{display:flex;justify-content:space-between;gap:14px;align-items:center;margin:0 0 10px;font-size:11px;color:var(--muted)}.legend{display:flex;gap:18px}.legend span:before{content:"";display:inline-block;width:18px;height:3px;margin-right:6px;vertical-align:middle;background:var(--amber)}.legend span:last-child:before{background:var(--blue)}.chart{width:100%;display:block;height:230px;overflow:visible}.chart text{font-family:system-ui,sans-serif;font-size:10px;fill:#728298}.insight{padding:16px 18px;background:#f7f9fd;border:1px solid #e1e8f2;border-radius:11px;font-size:12px;line-height:1.8;color:#42546c;margin-top:9px}.insight strong{color:var(--ink)}.chart-metrics{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-top:13px}.mini{font-size:11px;color:var(--muted)}.mini b{display:block;font-size:19px;color:var(--ink);font-weight:750}.export{padding:22px;display:flex;align-items:center;justify-content:space-between;gap:20px}.export p{font-size:12px;color:var(--muted);margin:6px 0 0}.actions{display:flex;gap:9px;flex-wrap:wrap;justify-content:end}.button{border:0;border-radius:9px;padding:11px 16px;background:var(--blue);color:white;font-size:12px;font-weight:700}.button.secondary{background:#edf2fa;color:var(--ink)}.approval{font-size:11px;color:var(--muted);margin-top:8px;text-align:right}.approval.done{color:var(--green)}details{padding:16px 20px}summary{font-size:12px;font-weight:700;cursor:pointer}details p,details li{font-size:12px;color:var(--muted)}details ul{padding-left:20px}.footer{font-size:11px;color:var(--muted);padding:0 4px;line-height:1.7}.footer strong{color:var(--ink)}.notice{display:none;background:#fff6e5;border:1px solid #f7d799;border-radius:10px;padding:11px 15px;font-size:12px;color:#815408}.notice.show{display:block}.empty{padding:22px;color:var(--muted)}@media(max-width:1100px){main{grid-template-columns:235px minmax(0,1fr);gap:14px}.controls{padding:18px}.stats{gap:8px}.stat{padding:14px 11px}.stat b{font-size:25px}.section-head{align-items:start;flex-direction:column}.section-head select{max-width:100%;width:100%}.export{flex-direction:column;align-items:stretch}.actions{justify-content:start}.approval{text-align:left}}@media(max-width:760px){header{padding:17px 18px}.brand{font-size:16px}.tag{font-size:10px;padding:5px 8px}.hero{padding:24px 18px 20px;display:block}.date{display:none}main{padding:0 14px 24px;display:block}.controls{position:static;margin-bottom:15px;display:grid;grid-template-columns:1fr 1fr;column-gap:14px}.controls>h3,.controls>.hint,.controls>.toggle,.controls>.divider,.controls>.scenario-note{grid-column:1/-1}.field{margin-top:12px}.toggle{margin-top:16px}.divider{margin:16px 0}.stats{grid-template-columns:repeat(2,1fr)}.stat{padding:15px}.stat b{font-size:29px}.chart-metrics{gap:6px}.mini{font-size:10px}.mini b{font-size:17px}.section-head,.chart-area{padding-left:16px;padding-right:16px}.hero p{font-size:13px}.content{gap:15px}}
</style>
</head>
<body>
<header><div class="brand"><span class="mark" aria-hidden="true">↗</span>LOGIPILOT<span style="font-weight:400;color:#7992b4">AI</span></div><span class="tag">LowSkillDevs · автономное демо</span></header>
<div class="hero"><div><div class="eyebrow">Электрокомплект · пополнение склада</div><h1>Заказ, который можно объяснить.</h1><p>Проверьте, как разовая продажа, срок поставки и рост спроса меняют предложение закупщику. Все числа — результаты расчётного модуля проекта.</p></div><div class="date">Дата сценария<strong>22 сентября 2026</strong></div></div>
<main>
<aside class="controls panel">
<h3><span class="step">1</span>Настройте сценарий</h3>
<label class="field" for="lead">Срок поставки<select id="lead"><option value="15">15 дней</option><option value="30" selected>30 дней</option><option value="45">45 дней</option><option value="60">60 дней</option></select></label>
<label class="field" for="growth">Поправка годового роста<select id="growth"><option value="0">Без ручной поправки</option><option value="20">+20 процентных пунктов</option><option value="40">+40 процентных пунктов</option></select></label>
<label class="field" for="supplier">Поставщик<select id="supplier"><option value="">Все поставщики</option></select></label>
<label class="toggle"><input type="checkbox" id="clean" checked><span>Сглаживать разовые всплески</span></label>
<p class="hint">Переключатель меняет очистку истории. Остальные правила расчёта сохраняются.</p>
<div class="divider"></div>
<div class="scenario-note"><strong id="horizon"></strong><br>Срок поставки + 30 дней между заказами + 14 дней страхового запаса.<br><br>Сезонность, остаток, подтверждённые поступления, MOQ и кратность уже учтены.</div>
</aside>
<div class="content">
<section class="stats" aria-label="Показатели сценария">
<div class="stat primary"><span>К ПОПОЛНЕНИЮ</span><b id="count">—</b><small>позиций с рекомендацией</small></div>
<div class="stat"><span>К ЗАКАЗУ, ЕД.</span><b id="units">—</b><small>сумма по выбранному поставщику</small></div>
<div class="stat"><span>СГЛАЖЕНО ВСПЛЕСКОВ</span><b id="spikes">—</b><small>месяцев по всем позициям</small></div>
<div class="stat"><span>НА ПРОВЕРКУ</span><b id="review">—</b><small>нужны решения менеджера</small></div>
</section>
<div id="review-notice" class="notice" role="status"></div>
<section class="panel"><div class="section-head"><div><h2><span class="step">2</span>Предложение по заказу</h2><p>Нажмите на строку, чтобы разобрать расчёт. Флажки выбирают позиции для CSV.</p></div><span class="badge" id="sku-count"></span></div>
<div class="table-wrap"><table><thead><tr><th aria-label="Выбрать"></th><th>Артикул / товар</th><th>Поставщик</th><th>Модель спроса</th><th>В месяц</th><th>На горизонт</th><th>Остаток¹</th><th>В пути²</th><th>Заказать</th></tr></thead><tbody id="rows"></tbody></table></div>
<div class="table-foot">¹ Последний доступный снимок. ² Поступления с датой внутри горизонта. «Проверить» не означает нулевую потребность.</div></section>
<section class="panel"><div class="section-head"><div><h2><span class="step">3</span>История → понятное решение</h2><p id="chart-subtitle">Исходные и очищенные месячные продажи</p></div><label><span style="position:absolute;left:-10000px">Артикул для анализа</span><select id="focus"></select></label></div>
<div class="chart-area"><div class="chart-top"><div class="legend"><span>Исходные продажи</span><span>После очистки</span></div><span>ед. / месяц</span></div><svg id="chart" class="chart" role="img" aria-label="График исходных и очищенных продаж" viewBox="0 0 900 230"></svg>
<div class="chart-metrics"><div class="mini">Убрано из всплесков, ед.<b id="removed">—</b></div><div class="mini">Поправка на дефицит, ед.³<b id="lost">—</b></div><div class="mini">Рекомендация по SKU, ед.<b id="focus-order">—</b></div></div>
<div id="reason" class="insight"></div><p class="hint">³ Приближённая поправка по месячным снимкам, а не подтверждённые потерянные продажи.</p></div></section>
<section class="panel export"><div><h2>Решение остаётся за менеджером</h2><p id="selection">Выберите позиции в таблице.</p><p>Подтверждение действует только для текущего выбора. Отправки поставщикам нет.</p></div><div><div class="actions"><button type="button" id="confirm" class="button">Подтвердить выбор</button><button type="button" id="download" class="button secondary" disabled>Скачать CSV</button></div><div id="approval" class="approval" aria-live="polite">Сначала проверьте и подтвердите позиции</div></div></section>
<details class="panel"><summary>Допущения и воспроизводимость</summary><p><strong>Синтетическая демонстрация; результаты рассчитаны Python-модулем проекта.</strong> Страница переключает 24 заранее рассчитанных сценария. Произвольные параметры, импорт Excel и интерактивные вызовы агента относятся к основному приложению Streamlit; в этом HTML нет LLM-вызовов.</p><p>Точность прогноза и экономический эффект здесь не измеряются. Изменение суммы заказа само по себе не означает экономию или повышение качества.</p><ul id="warnings"></ul><p id="version"></p></details>
<div class="footer"><strong>Полностью автономно.</strong> Откройте этот файл в браузере без интернета. Внутри только синтетические данные и готовые результаты; исходные Excel компании не используются. Это резервная демонстрация, не средство размещения реальных заказов.</div>
</div>
</main>
<noscript><p style="padding:24px">Для переключения сценариев и просмотра готовых результатов включите JavaScript. Интернет не требуется.</p></noscript>
<script id="demo-data" type="application/json">__PAYLOAD__</script>
<script>
"use strict";
const data=JSON.parse(document.getElementById("demo-data").textContent);
const $=id=>document.getElementById(id);
const fmt=(v,d=0)=>v===null||v===undefined||!Number.isFinite(Number(v))?"—":new Intl.NumberFormat("ru-RU",{maximumFractionDigits:d}).format(v);
const esc=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const key=r=>r.supplier+"|"+r.sku;
const patterns={regular:"Регулярный",intermittent:"Редкий повторяющийся",insufficient:"Мало наблюдений",no_demand:"Продаж не было"};
let currentRows=[],selected=new Set(),focusKey="",approved=false;
function scenario(){return data.cases[$("lead").value+"|"+Number($("clean").checked)+"|"+$("growth").value];}
function revoke(){approved=false;$("download").disabled=true;$("approval").textContent="Сначала проверьте и подтвердите позиции";$("approval").classList.remove("done");}
function selectedRows(){return currentRows.filter(r=>selected.has(key(r))&&r.order_qty!==null&&r.order_qty>0&&!r.manual_review);}
function selectionInfo(){const rows=selectedRows();$("selection").textContent="Выбрано: "+rows.length+" позиций · "+fmt(rows.reduce((s,r)=>s+r.order_qty,0))+" ед. в сумме.";$("confirm").disabled=!rows.length;}
function render(){
 const value=scenario(),supplier=$("supplier").value;
 currentRows=value.orders.filter(r=>!supplier||r.supplier===supplier);
 selected=new Set(currentRows.filter(r=>r.order_qty!==null&&r.order_qty>0&&!r.manual_review).map(key));
 if(!currentRows.some(r=>key(r)===focusKey))focusKey=key(currentRows.find(r=>r.sku==="DEMO-002")||currentRows[0]);
 $("horizon").textContent="Горизонт: "+value.summary.horizon_days+" дней";
 $("count").textContent=fmt(currentRows.filter(r=>r.order_qty>0).length);
 $("units").textContent=fmt(currentRows.reduce((s,r)=>s+(r.order_qty||0),0));
 $("spikes").textContent=fmt(currentRows.reduce((s,r)=>s+r.spikes_removed,0));
 const reviews=currentRows.filter(r=>r.manual_review).length;
 $("review").textContent=fmt(reviews);$("sku-count").textContent=currentRows.length+" SKU";
 $("review-notice").textContent=reviews+" позиций требуют уточнения в основном приложении. Их нельзя подтвердить в автономном демо; готовые позиции доступны отдельно.";
 $("review-notice").classList.toggle("show",reviews>0);
 $("focus").innerHTML=currentRows.map(r=>'<option value="'+esc(key(r))+'">'+esc(r.sku+" · "+r.name)+'</option>').join("");
 $("focus").value=focusKey;
 $("warnings").innerHTML=value.warnings.map(w=>"<li>"+esc(w)+"</li>").join("");
 revoke();renderTable();renderFocus();selectionInfo();
}
function renderTable(){
 $("rows").innerHTML=currentRows.map(r=>{
  const allowed=r.order_qty!==null&&r.order_qty>0&&!r.manual_review;
  const cls=r.manual_review?"review":r.demand_pattern==="intermittent"?"rare":"";
  return '<tr data-key="'+esc(key(r))+'" class="'+(key(r)===focusKey?"active":"")+'"><td><input class="row-check" type="checkbox" aria-label="Выбрать '+esc(r.sku)+'" '+(selected.has(key(r))&&allowed?"checked ":"")+(allowed?"":"disabled")+'></td><td><strong>'+esc(r.sku)+'</strong><small class="sku-name" title="'+esc(r.name)+'">'+esc(r.name)+'</small></td><td>'+esc(r.supplier)+'</td><td><span class="badge '+cls+'" title="'+esc(r.model_label)+'">'+esc(patterns[r.demand_pattern]||r.demand_pattern)+'</span></td><td>'+fmt(r.monthly_demand,1)+'</td><td>'+fmt(r.forecast_qty,1)+'</td><td>'+fmt(r.stock,1)+'</td><td>'+fmt(r.in_transit,1)+'</td><td class="qty">'+(r.order_qty===null?'<span class="badge review">Проверить</span>':fmt(r.order_qty))+'</td></tr>';
 }).join("");
 $("rows").querySelectorAll("tr").forEach(tr=>{
  tr.addEventListener("click",event=>{if(event.target.type==="checkbox")return;focusKey=tr.dataset.key;$("focus").value=focusKey;renderTable();renderFocus();});
  tr.querySelector("input").addEventListener("change",event=>{event.target.checked?selected.add(tr.dataset.key):selected.delete(tr.dataset.key);revoke();selectionInfo();});
 });
}
function renderFocus(){
 const row=currentRows.find(r=>key(r)===focusKey);if(!row)return;
 const history=data.histories[String(Number($("clean").checked))].filter(r=>key(r)===focusKey);
 const svg=$("chart"),W=900,H=230,L=46,R=12,T=12,B=29,plotW=W-L-R,plotH=H-T-B;
 const maximum=Math.max(1,...history.map(r=>Math.max(r.qty,r.clean_qty))),top=Math.ceil(maximum/5)*5;
 const x=i=>L+(history.length<2?plotW/2:i*plotW/(history.length-1)),y=v=>T+plotH*(1-v/top);
 let markup='<title>'+esc(row.sku+": исходные и очищенные месячные продажи")+'</title>';
 for(let i=0;i<=4;i++){const value=top*i/4,Y=y(value);markup+='<line x1="'+L+'" x2="'+(W-R)+'" y1="'+Y+'" y2="'+Y+'" stroke="#e4ebf5"/><text x="'+(L-8)+'" y="'+(Y+4)+'" text-anchor="end">'+fmt(value)+'</text>';}
 const pointString=field=>history.map((r,i)=>x(i).toFixed(2)+","+y(r[field]).toFixed(2)).join(" ");
 markup+='<polyline points="'+pointString("qty")+'" fill="none" stroke="#f59e0b" stroke-width="2.4" stroke-linejoin="round"/>';
 markup+='<polyline points="'+pointString("clean_qty")+'" fill="none" stroke="#2563eb" stroke-width="3" stroke-linejoin="round"/>';
 history.forEach((r,i)=>{const date=new Date(Date.UTC(Number(r.month.slice(0,4)),Number(r.month.slice(5,7))-1,1)),label=new Intl.DateTimeFormat("ru-RU",{month:"short",year:"2-digit",timeZone:"UTC"}).format(date);if(i===0||i===history.length-1||i%4===0)markup+='<text x="'+x(i)+'" y="'+(H-6)+'" text-anchor="middle">'+esc(label)+'</text>';markup+='<circle cx="'+x(i)+'" cy="'+y(r.qty)+'" r="3" fill="#f59e0b"><title>'+esc(label+": продажи "+fmt(r.qty,2)+", после очистки "+fmt(r.clean_qty,2))+'</title></circle>';});
 svg.innerHTML=markup;
 $("chart-subtitle").textContent=row.sku+" · "+row.model_label;
 $("removed").textContent=fmt(row.spike_units_removed,1);$("lost").textContent=fmt(row.lost_demand,1);$("focus-order").textContent=row.order_qty===null?"На проверку":fmt(row.order_qty);
 $("reason").innerHTML="<strong>Почему такое количество.</strong> "+esc(row.reason);
}
function csvCell(value){let s=String(value??"");if(/^[=+@-]/.test(s))s="'"+s;return '"'+s.replace(/"/g,'""')+'"';}
function download(){if(!approved)return;const headers=["Поставщик","Артикул","Наименование","К заказу, ед.","MOQ","Кратность","Дата сценария","Тип данных"];
 const lines=[headers,...selectedRows().map(r=>[r.supplier,r.sku,r.name,r.order_qty,r.moq,r.pack_size,data.as_of,"СИНТЕТИЧЕСКОЕ ДЕМО"])].map(row=>row.map(csvCell).join(";"));
 const blob=new Blob(["\ufeff"+lines.join("\r\n")],{type:"text/csv;charset=utf-8;"}),url=URL.createObjectURL(blob),link=document.createElement("a");link.href=url;link.download="logipilot_SYNTHETIC_demo_2026-09-22.csv";document.body.appendChild(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
const first=data.cases["30|1|0"];
$("supplier").innerHTML+=[...new Set(first.orders.map(r=>r.supplier))].map(s=>'<option value="'+esc(s)+'">'+esc(s)+'</option>').join("");
$("version").textContent="Источник: "+data.source+" · версия исходных расчётов "+data.version+" · дата данных "+data.as_of+". Пересборка: python scripts/build_offline_demo.py.";
["lead","growth","supplier","clean"].forEach(id=>$(id).addEventListener("change",render));
$("focus").addEventListener("change",()=>{focusKey=$("focus").value;renderTable();renderFocus();});
$("confirm").addEventListener("click",()=>{if(!selectedRows().length)return;approved=true;$("download").disabled=false;$("approval").textContent="Выбранные позиции подтверждены локально";$("approval").classList.add("done");});
$("download").addEventListener("click",download);
render();
</script>
</body></html>
'''


def main() -> None:
    payload = build_payload()
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    encoded = encoded.replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    document = HTML.replace("__PAYLOAD__", encoded)
    assert "__PAYLOAD__" not in document
    assert "http://" not in document and "https://" not in document
    assert len(payload["cases"]) == 24
    output = ROOT / "docs" / "demo.html"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(document, encoding="utf-8", newline="\n")
    print(f"Built {output.name}: 24 scenarios, {len(payload['cases']['30|1|0']['orders'])} synthetic SKUs; source {payload['version']}")


if __name__ == "__main__":
    main()
