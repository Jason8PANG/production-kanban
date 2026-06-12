#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
销售目标数据同步脚本
从 Excel 读取数据，同步到 erp_data.hmlv_sales_target_v2 表（siteref=310）
可单独运行，也可被定时任务调用

Excel 列结构:
  日期          -> target_month (取 YYYY-MM)
  目标销售      -> target_amount
  目标工单数量   -> target_order_qty
  目标总工时     -> target_total_hours

注意: 所有记录固定写入 siteref=310（310站点），查询/更新也只针对310站点
"""

import pandas as pd
import pymysql
import os
import sys
from datetime import datetime

EXCEL_PATH = r'I:/Production/01 Cor&Fiber Production/14-手工排产/AI排产文件夹/销售目标.xlsx'
TABLE_NAME = 'hmlv_sales_target_v2'
SITEREF = 310  # 本脚本固定同步310站点数据

MYSQL_HOST = os.environ.get('MYSQL_HOST', '10.0.6.86')
MYSQL_PORT = int(os.environ.get('MYSQL_PORT', 33306))
MYSQL_USER = os.environ.get('MYSQL_USER', 'powerbi')
MYSQL_PASSWORD = os.environ.get('MYSQL_PASSWORD', '!Q1234567')
MYSQL_DATABASE = 'erp_data'


def sync():
    print(f'[{datetime.now():%Y-%m-%d %H:%M:%S}] 开始同步销售目标 (siteref={SITEREF})...')

    df = pd.read_excel(EXCEL_PATH)
    print(f'Excel: {len(df)} 行, 列: {list(df.columns.tolist())}')

    # 列名映射（兼容中英文）
    col_map = {}
    for c in df.columns:
        cl = str(c).strip()
        if '日期' in cl or 'date' in cl.lower() or 'month' in cl.lower():
            col_map['date'] = c
        elif '销售' in cl or 'sales' in cl.lower():
            col_map['amount'] = c
        elif '工单' in cl or 'order' in cl.lower() or 'qty' in cl.lower():
            col_map['order_qty'] = c
        elif '工时' in cl or 'hour' in cl.lower():
            col_map['total_hours'] = c

    if 'date' not in col_map or 'amount' not in col_map:
        raise ValueError('Excel 中未找到"日期"和"目标销售"列')

    conn = pymysql.connect(
        host=MYSQL_HOST, port=MYSQL_PORT, user=MYSQL_USER,
        password=MYSQL_PASSWORD, database=MYSQL_DATABASE,
        charset='utf8mb4', connect_timeout=10
    )
    cursor = conn.cursor()

    updated = 0
    inserted = 0

    for _, row in df.iterrows():
        month = pd.to_datetime(row[col_map['date']]).strftime('%Y-%m')
        amount = float(row[col_map['amount']]) if pd.notna(row[col_map['amount']]) else 0.0
        order_qty = int(float(row[col_map['order_qty']])) if 'order_qty' in col_map and pd.notna(row[col_map['order_qty']]) else None
        total_hours = float(row[col_map['total_hours']]) if 'total_hours' in col_map and pd.notna(row[col_map['total_hours']]) else None

        # 查询时加 siteref=310 过滤，避免和其他站点冲突
        cursor.execute(
            f'SELECT id FROM {TABLE_NAME} WHERE siteref = %s AND target_month = %s',
            (SITEREF, month)
        )
        exists = cursor.fetchone()

        if exists:
            cursor.execute(f'''
                UPDATE {TABLE_NAME}
                SET target_amount=%s, target_order_qty=%s, target_total_hours=%s
                WHERE siteref=%s AND target_month=%s
            ''', (amount, order_qty, total_hours, SITEREF, month))
            updated += 1
        else:
            cursor.execute(f'''
                INSERT INTO {TABLE_NAME} (siteref, target_month, target_amount, target_order_qty, target_total_hours)
                VALUES (%s, %s, %s, %s, %s)
            ''', (SITEREF, month, amount, order_qty, total_hours))
            inserted += 1

        print(f'  {month}: 销售={amount}, 工单={order_qty}, 工时={total_hours}')

    conn.commit()

    # 验证（只显示310的数据）
    cursor.execute(
        f'SELECT target_month, target_amount, target_order_qty, target_total_hours FROM {TABLE_NAME} WHERE siteref=%s ORDER BY target_month',
        (SITEREF,)
    )
    print(f'\n数据库数据 (siteref={SITEREF}):')
    for r in cursor.fetchall():
        print(f'  {r[0]}: 销售={r[1]}, 工单数={r[2]}, 工时={r[3]}')

    cursor.close()
    conn.close()
    print(f'\n同步完成: 新增 {inserted}, 更新 {updated}')
    return {'inserted': inserted, 'updated': updated}


if __name__ == '__main__':
    try:
        sync()
    except Exception as e:
        print(f'错误: {e}')
        import traceback
        traceback.print_exc()
        sys.exit(1)
