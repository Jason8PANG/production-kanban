# -*- coding: utf-8 -*-
"""导出310站点 Pack(包装)工序累计完成销售的工单明细"""
import pymysql
import pandas as pd
from datetime import datetime

DB = dict(host='10.0.6.86', port=33306, user='powerbi', password='!Q1234567', database='wiptrack', charset='utf8mb4')

STATION_MAP = {
    'PRINT': 'Print', 'Cut': 'Cut', 'Pre': 'Pre', 'Asm': 'Asm', 'Test': 'Test', 'Pack': 'Pack',
    'Cutting': 'Cut', 'Pretreat': 'Pre', 'Package': 'Pack', 'Assembly': 'Asm', 'Job': 'Print',
    '工单打印 Job Print': 'Print', '剪线 Cutting': 'Cut', '预处理 Pretreat': 'Pre',
    '组装 Assembly': 'Asm', '测试 Test': 'Test', '包装 Package': 'Pack',
    'Job Print': 'Print', '工单打印': 'Print', '剪线': 'Cut', '预处理': 'Pre',
    '组装': 'Asm', '测试': 'Test', '包装': 'Pack',
}

SITE_REF = 'NAIGROUP_PROD_310'

conn = pymysql.connect(**DB)

now = datetime.now()
now_month = f"{now.year}-{now.month:02d}"
print(f"当前月份: {now_month}")

# 1. 获取当月排产工单（site_ref=310, ship_date在当月）
sql_erp = """
    SELECT job, item, line, qty, cycle_time_h, sales_amount, ship_date, job_status
    FROM erp_data.hmlv_production_schedule
    WHERE site_ref = 310
      AND DATE_FORMAT(ship_date, '%%Y-%%m') = %s
"""
df_erp = pd.read_sql(sql_erp, conn, params=(now_month,))
df_erp['job'] = df_erp['job'].astype(str).str.strip().str.upper()
print(f"当月排产工单数(310): {len(df_erp)}")

job_info = {}
for _, row in df_erp.iterrows():
    j = row['job']
    job_info[j] = {
        'item': row.get('item', ''),
        'line': row.get('line', ''),
        'sales_amount': float(row['sales_amount']) if pd.notna(row['sales_amount']) else 0.0,
        'ship_date': str(row['ship_date'])[:10] if pd.notna(row['ship_date']) else '',
        'qty': float(row['qty']) if pd.notna(row['qty']) else 0,
        'cycle_time_h': float(row['cycle_time_h']) if pd.notna(row['cycle_time_h']) else 0,
    }

all_month_jobs = set(job_info.keys())

# 2. 获取 production_records 中 Pack 工序完成记录
placeholders = ','.join(['%s'] * len(all_month_jobs))
sql_pr = f"""
    SELECT Job, Station, CompleteDate
    FROM production_records
    WHERE SiteRef = %s
      AND UPPER(TRIM(Job)) IN ({placeholders})
"""
params_pr = [SITE_REF] + list(all_month_jobs)
df_pr = pd.read_sql(sql_pr, conn, params=params_pr)
df_pr['Job'] = df_pr['Job'].astype(str).str.strip().str.upper()
conn.close()

# 3. 筛选 Pack 工序完成的工单
pack_jobs = {}  # job -> complete_date
for _, r in df_pr.iterrows():
    job = r['Job']
    station_raw = str(r['Station'] or '').strip()
    station_en = STATION_MAP.get(station_raw, '')
    if station_en != 'Pack':
        continue
    if job not in all_month_jobs:
        continue
    dv = str(r['CompleteDate'] or '').strip()[:10]
    if job not in pack_jobs or (dv and dv < pack_jobs[job]):
        pack_jobs[job] = dv

# 4. 导出 Excel
rows = []
for job, complete_date in sorted(pack_jobs.items(), key=lambda x: -job_info.get(x[0], {}).get('sales_amount', 0)):
    info = job_info.get(job, {})
    rows.append({
        '工单号': job,
        'Item': info.get('item', ''),
        'Line': info.get('line', ''),
        '销售金额': round(info.get('sales_amount', 0), 2),
        '工单数量': info.get('qty', 0),
        '单根工时': info.get('cycle_time_h', 0),
        '出货日期(ship_date)': info.get('ship_date', ''),
        '完成日期(CompleteDate)': complete_date,
    })

df_out = pd.DataFrame(rows)
total_sales = sum(r['销售金额'] for r in rows)
print(f"\nPack(包装)工序: {len(rows)} 工单, 累计销售 ${total_sales:,.2f}")

output_file = 'C:/Users/guangli.wang/Desktop/310_包装累计完成销售工单明细.xlsx'
with pd.ExcelWriter(output_file, engine='openpyxl') as writer:
    df_out.to_excel(writer, sheet_name='Pack_包装', index=False)

print(f"已导出: {output_file}")
