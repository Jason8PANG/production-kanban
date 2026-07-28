#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
410站点：今天完成工序但不是7月出货的工单筛选
"""

import pymysql
import pandas as pd
from datetime import datetime

conn = pymysql.connect(
    host='10.0.6.86', port=33306,
    user='powerbi', password='!Q1234567',
    database='wiptrack', charset='utf8mb4',
    cursorclass=pymysql.cursors.DictCursor
)
cursor = conn.cursor()

SITE_REF = 'NAIGROUP_PROD_410'
TODAY = '2026-07-27'

STATION_LABEL = {
    'Print': '工单打印', 'Cut': '剪线', 'Pre': '预处理',
    'Asm': '组装', 'Test': '测试', 'Pack': '包装',
}
STATION_CN_TO_KEY = {v: k for k, v in STATION_LABEL.items()}
STATION_CN_TO_KEY.update({
    'PRINT': 'Print', 'Cutting': 'Cut', 'Pretreat': 'Pre',
    'Package': 'Pack', 'Assembly': 'Asm', 'Job': 'Print',
    '工单打印 Job Print': 'Print', '剪线 Cutting': 'Cut',
    '预处理 Pretreat': 'Pre', '组装 Assembly': 'Asm',
    '测试 Test': 'Test', '包装 Package': 'Pack',
})

# 1. 查今天410完成的报工记录
print(f"[1/3] 查询410站点今天({TODAY})的报工记录...")
cursor.execute("""
    SELECT Job, Station, CompleteDate
    FROM production_records
    WHERE SiteRef = %s
      AND DATE(CompleteDate) = %s
    ORDER BY CompleteDate DESC
""", (SITE_REF, TODAY))
today_records = cursor.fetchall()
print(f"  -> 今天报工记录 {len(today_records)} 条")

# 2. 查排产表获取ship_date
print(f"[2/3] 查询410排产表...")
cursor.execute("""
    SELECT DISTINCT job, item, qty, ship_date, line, sales_amount, cycle_time_h
    FROM erp_data.hmlv_production_schedule
    WHERE site_ref = 410
""")
schedule_rows = cursor.fetchall()
job_info = {}
for r in schedule_rows:
    j = str(r['job']).strip().upper()
    if j not in job_info:
        sd = r.get('ship_date')
        sd_str = sd.strftime('%Y-%m-%d') if sd and hasattr(sd, 'strftime') else str(sd)[:10] if sd else ''
        job_info[j] = {
            'item': str(r.get('item', '') or ''),
            'qty': int(r['qty']) if r.get('qty') else 0,
            'ship_date': sd_str,
            'ship_month': sd_str[:7],
            'line': str(r.get('line', '') or ''),
            'sales_amount': float(r['sales_amount']) if r.get('sales_amount') else 0,
            'cycle_time_h': float(r['cycle_time_h']) if r.get('cycle_time_h') else 0,
        }
print(f"  -> 排产表 {len(job_info)} 个工单")

conn.close()

# 3. 筛选：今天完成但不是7月出货的
print(f"[3/3] 筛选非7月出货的工单...")

export_rows = []
for r in today_records:
    job = str(r.get('Job', '') or '').strip().upper()
    station_raw = str(r.get('Station', '') or '').strip()
    station_en = STATION_CN_TO_KEY.get(station_raw, station_raw)
    cd = r.get('CompleteDate')
    cd_str = cd.strftime('%Y-%m-%d %H:%M') if cd and hasattr(cd, 'strftime') else str(cd)[:16] if cd else ''

    info = job_info.get(job)
    if info is None:
        # 排产表没有这个工单
        export_rows.append({
            '工单号': job,
            '工序': f'{station_en} ({station_raw})',
            '完成时间': cd_str,
            'Item': '(排产表无此工单)',
            '工单数量': '',
            '出货日期': '',
            '出货月份': '',
            'Line': '',
            '销售金额': '',
            '备注': '排产表中未找到',
        })
        continue

    ship_month = info['ship_month']
    if ship_month != '2026-07':
        export_rows.append({
            '工单号': job,
            '工序': f'{station_en} ({STATION_LABEL.get(station_en, station_raw)})',
            '完成时间': cd_str,
            'Item': info['item'],
            '工单数量': info['qty'],
            '出货日期': info['ship_date'],
            '出货月份': ship_month,
            'Line': info['line'],
            '销售金额': info['sales_amount'],
            '总工时(h)': round(info['qty'] * info['cycle_time_h'], 2),
            '备注': f'非7月出货（{ship_month}）' if ship_month else '无出货日期',
        })

df_export = pd.DataFrame(export_rows)

# 统计
total_today = len(today_records)
non_july = len(df_export)
july_count = total_today - non_july - sum(1 for r in today_records if str(r.get('Job','')).strip().upper() not in job_info)

print(f"\n{'='*60}")
print(f"统计结果")
print(f"{'='*60}")
print(f"今天({TODAY}) 410站点报工记录总数: {total_today}")
print(f"其中 7月出货工单: {total_today - non_july}")
print(f"其中 非7月出货工单: {non_july}")
if non_july > 0:
    print(f"\n--- 非七月出货工单按月份分布 ---")
    month_dist = df_export[df_export['出货月份'] != ''].groupby('出货月份').size()
    for m, c in month_dist.items():
        print(f"  {m}: {c} 条")
    no_date = len(df_export[df_export['出货月份'] == ''])
    if no_date > 0:
        print(f"  无出货日期: {no_date} 条")

# 导出到桌面
import os
out_dir = os.path.join(os.environ.get('USERPROFILE', os.environ.get('HOME', '.')), 'Desktop')
out_path = os.path.join(out_dir, f'410_今天非7月出货_{TODAY}.xlsx')

with pd.ExcelWriter(out_path, engine='openpyxl') as writer:
    df_export.to_excel(writer, sheet_name='非7月出货明细', index=False)

print(f"\n导出完成: {out_path}")
print(f"记录数: {len(df_export)}")
