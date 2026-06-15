# -*- coding: utf-8 -*-
"""
wiptrack.production_records - SiteRef=NAIGROUP_PROD_410 报工序完成数据导出
输出文件: 报工序_410_导出.xlsx
"""
import pymysql
import pandas as pd
from datetime import datetime

conn = pymysql.connect(
    host='10.0.6.86', port=33306, user='powerbi',
    password='!Q1234567', database='wiptrack', charset='utf8mb4'
)

print("连接成功，查询 SiteRef=NAIGROUP_PROD_410 报工数据...")

sql = """
    SELECT *
    FROM wiptrack.production_records
    WHERE SiteRef = 'NAIGROUP_PROD_410'
    ORDER BY CompleteDate DESC, Job
"""

df = pd.read_sql(sql, conn)
conn.close()

print(f"查询完成: {len(df)} 条记录, {len(df.columns)} 字段")

out_path = r"J:/PowerBI/DataSet/PRODUCTION/HMLV生产看板/报工序_410_导出.xlsx"
with pd.ExcelWriter(out_path, engine='openpyxl') as writer:
    df.to_excel(writer, sheet_name='production_records_410', index=False)

print(f"\n导出完成！")
print(f"文件: {out_path}")
print(f"记录: {len(df)}")
print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

if len(df) > 0 and 'CompleteDate' in df.columns:
    print(f"日期范围: {df['CompleteDate'].min()} ~ {df['CompleteDate'].max()}")
    print(f"Station 分布:")
    for s, c in df['Station'].value_counts().items():
        print(f"  {s}: {c}")
