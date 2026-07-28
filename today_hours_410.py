#!/usr/bin/env python
"""今天410站点完成工序工时统计"""
import pymysql

conn = pymysql.connect(
    host='10.0.6.86', port=33306, user='powerbi', password='!Q1234567',
    database='wiptrack', charset='utf8mb4', cursorclass=pymysql.cursors.DictCursor
)
cur = conn.cursor()

TODAY = '2026-07-27'
SITE_P = 'NAIGROUP_PROD_410'

STN = {
    '工单打印': 'Print', '剪线': 'Cut', '预处理': 'Pre',
    '组装': 'Asm', '测试': 'Test', '包装': 'Pack',
    'PRINT': 'Print', 'Print': 'Print', 'Cut': 'Cut', 'Pre': 'Pre',
    'Asm': 'Asm', 'Test': 'Test', 'Pack': 'Pack',
    'Cutting': 'Cut', 'Pretreat': 'Pre', 'Package': 'Pack',
    'Assembly': 'Asm', 'Job': 'Print',
    '工单打印 Job Print': 'Print', '剪线 Cutting': 'Cut',
    '预处理 Pretreat': 'Pre', '组装 Assembly': 'Asm',
    '测试 Test': 'Test', '包装 Package': 'Pack',
}
STATION_ORDER = ['Print', 'Cut', 'Pre', 'Asm', 'Test', 'Pack']

# 今天410的报工记录
cur.execute(
    "SELECT Job, Station FROM production_records WHERE SiteRef=%s AND DATE(CompleteDate)=%s",
    (SITE_P, TODAY)
)
recs = cur.fetchall()
today_jobs = set(str(r['Job']).strip().upper() for r in recs)

# 排产表工时
if today_jobs:
    ph = ','.join(['%s'] * len(today_jobs))
    cur.execute(
        'SELECT job, qty, cycle_time_h FROM erp_data.hmlv_production_schedule WHERE site_ref=410 AND job IN (' + ph + ')',
        list(today_jobs)
    )
    sch_map = {}
    for r in cur.fetchall():
        j = str(r['job']).strip().upper()
        if j not in sch_map:
            q = int(r['qty'] or 0)
            ct = float(r['cycle_time_h'] or 0)
            sch_map[j] = q * ct

conn.close()

in_sch = set(sch_map.keys())
unscheduled = today_jobs - in_sch

# 按工序统计
station_hours = {}
station_unknown = {}
for r in recs:
    job = str(r['Job']).strip().upper()
    sr = str(r.get('Station', '') or '').strip()
    se = STN.get(sr, sr)
    hours = sch_map.get(job)
    if hours is not None:
        station_hours[se] = station_hours.get(se, 0) + hours
    else:
        station_unknown[se] = station_unknown.get(se, 0) + 1

total_known = sum(station_hours.values())
total_unknown = sum(station_unknown.values())

print(f"今天({TODAY}) 410站点")
print(f"报工记录: {len(recs)} 条 | 独立工单: {len(today_jobs)} 个")
print(f"排产表内有工时: {len(in_sch)} 个 | 未排产: {len(unscheduled)} 个")
print()
print(f"{'工序':<10} {'工时(H)':>12}  {'未知工时记录':>12}")
print("-" * 42)
for st in STATION_ORDER:
    h = station_hours.get(st, 0)
    u = station_unknown.get(st, 0)
    print(f"{st:<10} {h:>12.2f}  {u:>12}")
print("-" * 42)
print(f"{'合计':<10} {total_known:>12.2f}  {total_unknown:>12}")
print()
print(f"已知工时合计: {total_known:.2f} H（仅排产表内工单）")
print(f"未知记录: {total_unknown} 条（工单不在排产表，无qty/cycle_time_h）")
