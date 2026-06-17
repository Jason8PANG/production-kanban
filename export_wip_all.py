#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""导出工序WIP全部数据库数据到Excel"""

import pymysql
import pandas as pd
from datetime import datetime

OUTPUT = r"J:\PowerBI\DataSet\PRODUCTION\HMLV生产看板\工序WIP数据导出.xlsx"

conn = pymysql.connect(
    host='10.0.6.86', port=33306,
    user='powerbi', password='!Q1234567',
    database='wiptrack', charset='utf8mb4',
    cursorclass=pymysql.cursors.DictCursor
)

# ============================================================
# 1. production_records - 310
# ============================================================
print("[1/5] 导出 production_records (310)...")
cursor = conn.cursor()
cursor.execute("""
    SELECT id, Job, Station, CompleteDate, SiteRef, created_at
    FROM production_records
    WHERE SiteRef = 'NAIGROUP_PROD_310'
    ORDER BY CompleteDate DESC
""")
rows_310 = cursor.fetchall()
df_pr_310 = pd.DataFrame(rows_310)
print(f"  -> {len(df_pr_310)} 条记录")

# ============================================================
# 2. production_records - 410
# ============================================================
print("[2/5] 导出 production_records (410)...")
cursor.execute("""
    SELECT id, Job, Station, CompleteDate, SiteRef, created_at
    FROM production_records
    WHERE SiteRef = 'NAIGROUP_PROD_410'
    ORDER BY CompleteDate DESC
""")
rows_410 = cursor.fetchall()
df_pr_410 = pd.DataFrame(rows_410)
print(f"  -> {len(df_pr_410)} 条记录")

# ============================================================
# 3. erp_data.hmlv_production_schedule
# ============================================================
print("[3/5] 导出 erp_data.hmlv_production_schedule...")
cursor.execute("""
    SELECT job, item, qty, ship_date, line, work_hours_h,
           unit_price, sales_amount, job_status, tested_qty,
           wo_total, cycle_time_h, site_ref
    FROM erp_data.hmlv_production_schedule
    ORDER BY ship_date DESC, job
""")
rows_sch = cursor.fetchall()
df_sch = pd.DataFrame(rows_sch)
print(f"  -> {len(df_sch)} 条记录")

# ============================================================
# 4. production_records 按工序汇总 (310)
# ============================================================
print("[4/5] 生成工序汇总 (310)...")
cursor.execute("""
    SELECT 
        Station,
        COUNT(DISTINCT Job) AS job_count,
        COUNT(*) AS record_count,
        MIN(CompleteDate) AS earliest,
        MAX(CompleteDate) AS latest
    FROM production_records
    WHERE SiteRef = 'NAIGROUP_PROD_310'
    GROUP BY Station
    ORDER BY Station
""")
df_station_310 = pd.DataFrame(cursor.fetchall())
print(f"  -> {len(df_station_310)} 个工序")

# ============================================================
# 5. production_records 按工序汇总 (410)
# ============================================================
print("[5/5] 生成工序汇总 (410)...")
cursor.execute("""
    SELECT 
        Station,
        COUNT(DISTINCT Job) AS job_count,
        COUNT(*) AS record_count,
        MIN(CompleteDate) AS earliest,
        MAX(CompleteDate) AS latest
    FROM production_records
    WHERE SiteRef = 'NAIGROUP_PROD_410'
    GROUP BY Station
    ORDER BY Station
""")
df_station_410 = pd.DataFrame(cursor.fetchall())
print(f"  -> {len(df_station_410)} 个工序")

conn.close()

# ============================================================
# 写入 Excel
# ============================================================
print("\n写入 Excel...")
with pd.ExcelWriter(OUTPUT, engine='openpyxl') as writer:
    df_pr_310.to_excel(writer, sheet_name='生产记录_310', index=False)
    df_pr_410.to_excel(writer, sheet_name='生产记录_410', index=False)
    df_sch.to_excel(writer, sheet_name='排产表_全部', index=False)
    df_station_310.to_excel(writer, sheet_name='工序汇总_310', index=False)
    df_station_410.to_excel(writer, sheet_name='工序汇总_410', index=False)

print(f"\n✅ 导出完成: {OUTPUT}")
print(f"   Sheets: 生产记录_310({len(df_pr_310)}行), 生产记录_410({len(df_pr_410)}行)")
print(f"           排产表_全部({len(df_sch)}行), 工序汇总_310, 工序汇总_410")
