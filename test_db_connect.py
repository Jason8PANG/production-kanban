#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""测试数据库连接 - 排查销售数据"""
import pymysql
from datetime import date

MYSQL_HOST = '10.0.6.86'
MYSQL_PORT = 33306
MYSQL_USER = 'powerbi'
MYSQL_PASSWORD = '!Q1234567'
MYSQL_DATABASE = 'wiptrack'

conn = pymysql.connect(
    host=MYSQL_HOST, port=MYSQL_PORT, user=MYSQL_USER, 
    password=MYSQL_PASSWORD, database=MYSQL_DATABASE, 
    charset='utf8mb4', connect_timeout=10
)
cursor = conn.cursor()

print('=' * 60)
print('销售数据排查诊断')
print('=' * 60)

# 1. 表结构
cursor.execute('DESCRIBE erp_data.hmlv_production_schedule')
cols = cursor.fetchall()
print('\n[1] 表结构:')
for c in cols:
    print(f'    {c[0]:20s} {c[1]}')

# 2. 总统计
cursor.execute('SELECT COUNT(*), SUM(sales_amount), AVG(sales_amount) FROM erp_data.hmlv_production_schedule')
r = cursor.fetchone()
print(f'\n[2] 总统计: 总记录={r[0]}, 销售总额={r[1] or 0}, 平均={r[2] or 0}')

# 3. sales_amount 为 NULL/0 的记录数
cursor.execute("SELECT COUNT(*) FROM erp_data.hmlv_production_schedule WHERE sales_amount IS NULL OR sales_amount = 0")
null_sales = cursor.fetchone()[0]
print(f'    sales_amount 为空或0的记录: {null_sales} 条')

# 4. 当月(2026-05)数据
now = date.today().strftime('%Y-%m')
cursor.execute(
    "SELECT COUNT(*), SUM(sales_amount), "
    "SUM(CASE WHEN job_status = '已完成' THEN sales_amount ELSE 0 END) as completed_sales, "
    "SUM(CASE WHEN job_status != '已完成' OR job_status IS NULL THEN sales_amount ELSE 0 END) as pending_sales "
    "FROM erp_data.hmlv_production_schedule WHERE DATE_FORMAT(ship_date, '%%Y-%%m') = %s",
    (now,)
)
r = cursor.fetchone()
print(f'\n[3] 当月({now})统计:')
print(f'    工单数: {r[0]}')
print(f'    销售总额: {r[1] or 0}')
print(f'    已完成销售: {r[2] or 0}')
print(f'    未完成销售: {r[3] or 0}')

# 5. 当月 job_status 值分布
cursor.execute(
    "SELECT job_status, COUNT(*), SUM(sales_amount) "
    "FROM erp_data.hmlv_production_schedule "
    "WHERE DATE_FORMAT(ship_date, '%%Y-%%m') = %s "
    "GROUP BY job_status",
    (now,)
)
print(f'\n[4] 当月 job_status 分布:')
rows = cursor.fetchall()
if not rows:
    print('    (无当月数据)')
else:
    for r in rows:
        print(f'    "{r[0]}": {r[1]} 条, 销售 {r[2] or 0}')

# 6. 各月销售分布
cursor.execute('''
    SELECT DATE_FORMAT(ship_date, '%Y-%m') as month, COUNT(*), SUM(sales_amount)
    FROM erp_data.hmlv_production_schedule
    GROUP BY month
    ORDER BY month DESC
''')
print('\n[5] 各月销售分布:')
for r in cursor.fetchall():
    print(f'    {r[0]}: {r[1]} 条, 销售 {r[2] or 0}')

# 7. 当月样例
cursor.execute(
    "SELECT job, item, qty, ship_date, sales_amount, job_status "
    "FROM erp_data.hmlv_production_schedule "
    "WHERE DATE_FORMAT(ship_date, '%%Y-%%m') = %s "
    "ORDER BY ship_date DESC LIMIT 10",
    (now,)
)
print(f'\n[6] 当月样例数据 (最多10条):')
rows = cursor.fetchall()
if not rows:
    print('    (无当月数据)')
else:
    for r in rows:
        print(f'    Job={r[0]}, Item={r[1]}, Qty={r[2]}, ShipDate={r[3]}, Sales={r[4] or 0}, Status="{r[5]}"')

conn.close()
print('\n✓ 诊断完成')
