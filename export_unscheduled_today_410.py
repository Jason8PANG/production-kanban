#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""410站点：未排产工单今天完成工序导出"""
import pymysql
import pandas as pd
import os
from datetime import datetime

conn = pymysql.connect(
    host='10.0.6.86', port=33306, user='powerbi', password='!Q1234567',
    database='wiptrack', charset='utf8mb4', cursorclass=pymysql.cursors.DictCursor
)
cursor = conn.cursor()

TODAY = '2026-07-27'
SITE_REF = 'NAIGROUP_PROD_410'

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

# 1. 今天410报工的job
cursor.execute(
    "SELECT DISTINCT Job FROM production_records WHERE SiteRef=%s AND DATE(CompleteDate)=%s",
    (SITE_REF, TODAY)
)
today_jobs = [str(r['Job']).strip().upper() for r in cursor.fetchall()]
print(f"今天410报工独立工单: {len(today_jobs)} 个")

# 2. 排产表中的工单
ph = ','.join(['%s'] * len(today_jobs))
cursor.execute(
    'SELECT DISTINCT job FROM erp_data.hmlv_production_schedule WHERE job IN (' + ph + ')',
    today_jobs
)
in_sch = set(str(r['job']).strip().upper() for r in cursor.fetchall())
unscheduled = sorted(j for j in today_jobs if j not in in_sch)
print(f"未排产工单: {len(unscheduled)} 个")

# 3. 查今天完成的工序明细
cursor.execute(
    "SELECT Job, Station, CompleteDate FROM production_records "
    "WHERE SiteRef=%s AND DATE(CompleteDate)=%s ORDER BY Job, CompleteDate",
    (SITE_REF, TODAY)
)
all_recs = cursor.fetchall()
conn.close()

rows = []
seen = set()
for r in all_recs:
    job = str(r['Job']).strip().upper()
    if job not in unscheduled:
        continue
    sr = str(r['Station'] or '').strip()
    se = STN.get(sr, sr)
    cd = r['CompleteDate']
    cd_s = cd.strftime('%Y-%m-%d %H:%M') if cd and hasattr(cd, 'strftime') else str(cd)[:16] if cd else ''
    key = (job, se)
    if key not in seen:
        seen.add(key)
        rows.append({
            '工单号': job,
            '工序': se,
            '工序原始名称': sr,
            '完成时间': cd_s,
            '备注': '未排产，后续会排'
        })

df_dtl = pd.DataFrame(rows)

# 4. 按工单汇总
job_stations = {}
for _, r in df_dtl.iterrows():
    job_stations.setdefault(r['工单号'], []).append(r['工序'])

summary_rows = []
for job in sorted(job_stations.keys()):
    sts = job_stations[job]
    summary_rows.append({
        '工单号': job,
        '已完成工序': ', '.join(sts),
        '完成工序数': len(sts),
        '工序进度': f'{len(sts)}/6'
    })
df_sum = pd.DataFrame(summary_rows)

# 导出
out = os.path.join(os.environ.get('USERPROFILE', ''), 'Desktop', '410_未排产工单_今天完成工序.xlsx')
with pd.ExcelWriter(out, engine='openpyxl') as w:
    df_dtl.to_excel(w, sheet_name='工序明细', index=False)
    df_sum.to_excel(w, sheet_name='工单汇总', index=False)

print(f"\n导出: {out}")
print(f"明细: {len(df_dtl)} 条记录")
print(f"汇总: {len(df_sum)} 个工单")
print(f"\n--- 工序分布 ---")
for s, c in df_dtl['工序'].value_counts().items():
    print(f"  {s}: {c} 条")
