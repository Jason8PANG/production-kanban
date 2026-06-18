"""导出排产表 erp_data.hmlv_production_schedule 全量数据"""
import pymysql
import pandas as pd
from datetime import datetime

DB_CONFIG = {
    'host': '10.0.6.86',
    'port': 33306,
    'user': 'powerbi',
    'password': '!Q1234567',
    'database': 'wiptrack',
    'charset': 'utf8mb4',
}

OUTPUT_PATH = r'j:\PowerBI\DataSet\PRODUCTION\HMLV生产看板\排产表_全量数据.xlsx'

conn = pymysql.connect(**DB_CONFIG)
try:
    with conn.cursor() as cur:
        # 总行数
        cur.execute("SELECT COUNT(*) FROM erp_data.hmlv_production_schedule")
        total = cur.fetchone()[0]
        print(f"排产表总行数: {total}")

        # 按 site_ref 统计
        cur.execute("SELECT COALESCE(site_ref, 'NULL') as sr, COUNT(*) as cnt FROM erp_data.hmlv_production_schedule GROUP BY site_ref")
        print("site_ref 分布:")
        for row in cur.fetchall():
            print(f"  site_ref={row[0]}: {row[1]} 行")

        # 查询全量数据
        cur.execute("SELECT * FROM erp_data.hmlv_production_schedule ORDER BY ship_date, job")
        columns = [desc[0] for desc in cur.description]
        rows = cur.fetchall()

    df = pd.DataFrame(rows, columns=columns)
    print(f"\n列名: {columns}")
    print(f"数据行数: {len(df)}")

    # 写入Excel，多个sheet
    with pd.ExcelWriter(OUTPUT_PATH, engine='openpyxl') as writer:
        # Sheet1: 全量数据
        df.to_excel(writer, sheet_name='排产表_全量', index=False)

        # 按 site_ref 分sheet（如果有多种）
        if 'site_ref' in df.columns:
            for sr, grp in df.groupby('site_ref', dropna=False):
                label = f"site_ref={sr}" if pd.notna(sr) else "site_ref=NULL"
                # sheet名最长31字符
                sheet_name = label[:31]
                grp.to_excel(writer, sheet_name=sheet_name, index=False)
                print(f"  {label}: {len(grp)} 行")

    print(f"\n✅ 导出成功: {OUTPUT_PATH}")
finally:
    conn.close()
