# -*- coding: utf-8 -*-
"""
erp_data.hmlv_production_schedule - site_ref=310 数据导出
输出文件: 排产表_310_导出.xlsx
"""
import pymysql
import pandas as pd
from datetime import datetime

# 数据库连接
conn = pymysql.connect(
    host='10.0.6.86', port=33306, user='powerbi',
    password='!Q1234567', database='erp_data', charset='utf8mb4'
)

print("数据库连接成功，正在查询 site_ref=310 数据...")

# 查询 site_ref=310 的所有数据
sql = """
    SELECT *
    FROM erp_data.hmlv_production_schedule
    WHERE site_ref = 310
    ORDER BY ship_date, job
"""

df = pd.read_sql(sql, conn)
conn.close()

print(f"查询完成，共 {len(df)} 条记录，{len(df.columns)} 个字段")
print(f"字段列表: {list(df.columns)}")

# 导出到 Excel
out_path = r"J:/PowerBI/DataSet/PRODUCTION/HMLV生产看板/排产表_310_导出.xlsx"
with pd.ExcelWriter(out_path, engine='openpyxl') as writer:
    df.to_excel(writer, sheet_name='site310', index=False)

print(f"\n导出完成！")
print(f"文件路径: {out_path}")
print(f"记录数量: {len(df)}")
print(f"导出时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

# 打印数据概览
if len(df) > 0:
    print(f"\n=== 数据概览 ===")
    if 'ship_date' in df.columns:
        print(f"ship_date 范围: {df['ship_date'].min()} ~ {df['ship_date'].max()}")
    if 'job' in df.columns:
        print(f"job 数量: {df['job'].nunique()}")
    print(f"\n前5行预览:")
    print(df.head(5).to_string())
