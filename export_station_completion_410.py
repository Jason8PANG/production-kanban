#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
410站点（Penang）工序完成工单导出
功能：从数据库导出410站点排产工单的各工序完成情况明细
输出：每个工单一行，显示6道工序（Print/Cut/Pre/Asm/Test/Pack）的完成状态和完成日期
"""

import pymysql
import pandas as pd
from datetime import datetime, date

# ========== 数据库连接 ==========
conn = pymysql.connect(
    host='10.0.6.86', port=33306,
    user='powerbi', password='!Q1234567',
    database='wiptrack', charset='utf8mb4',
    cursorclass=pymysql.cursors.DictCursor
)
cursor = conn.cursor()

SITE_REF = 'NAIGROUP_PROD_410'
SITE_REF_NUM = 410

# 工序顺序和映射
STATION_ORDER = ['Print', 'Cut', 'Pre', 'Asm', 'Test', 'Pack']
STATION_LABEL = {
    'Print': '工单打印',
    'Cut': '剪线',
    'Pre': '预处理',
    'Asm': '组装',
    'Test': '测试',
    'Pack': '包装',
}
STATION_CN_TO_KEY = {v: k for k, v in STATION_LABEL.items()}
STATION_CN_TO_KEY.update({
    'PRINT': 'Print',
    'Cutting': 'Cut', 'Pretreat': 'Pre', 'Package': 'Pack',
    'Assembly': 'Asm', 'Job': 'Print',
    '工单打印 Job Print': 'Print',
    '剪线 Cutting': 'Cut',
    '预处理 Pretreat': 'Pre',
    '组装 Assembly': 'Asm',
    '测试 Test': 'Test',
    '包装 Package': 'Pack',
})

print(f"[1/3] 查询410站点排产表...")
cursor.execute("""
    SELECT job, item, qty, ship_date, line, sales_amount,
           cycle_time_h, job_status, site_ref
    FROM erp_data.hmlv_production_schedule
    WHERE site_ref = %s
    ORDER BY ship_date, job
""", (SITE_REF_NUM,))
schedule_rows = cursor.fetchall()
df_sch = pd.DataFrame(schedule_rows)
# job统一大写去重
df_sch['job'] = df_sch['job'].astype(str).str.strip().str.upper()
df_sch = df_sch.drop_duplicates(subset=['job'], keep='first')
print(f"  -> 排产表 {len(df_sch)} 个工单")

print(f"[2/3] 查询410站点报工记录...")
cursor.execute("""
    SELECT Job, Station, CompleteDate
    FROM production_records
    WHERE SiteRef = %s
    ORDER BY CompleteDate DESC
""", (SITE_REF,))
record_rows = cursor.fetchall()
df_rec = pd.DataFrame(record_rows)
if len(df_rec) > 0:
    df_rec['Job'] = df_rec['Job'].astype(str).str.strip().str.upper()
print(f"  -> 报工记录 {len(df_rec)} 条")

conn.close()

# ========== 构建工序完成矩阵 ==========
print(f"[3/3] 构建工序完成矩阵...")

# job -> {station_en: CompleteDate}
job_station_dates = {}
for _, r in df_rec.iterrows():
    job = str(r.get('Job', '') or '').strip()
    if not job:
        continue
    station_raw = str(r.get('Station', '') or '').strip()
    station_en = STATION_CN_TO_KEY.get(station_raw, '')
    if station_en not in STATION_ORDER:
        continue
    cd = r.get('CompleteDate')
    cd_str = ''
    if cd is not None:
        if hasattr(cd, 'strftime'):
            cd_str = cd.strftime('%Y-%m-%d')
        else:
            cd_str = str(cd)[:10]
    if job not in job_station_dates:
        job_station_dates[job] = {}
    # 取最新完成日期
    if station_en not in job_station_dates[job] or cd_str > job_station_dates[job][station_en]:
        job_station_dates[job][station_en] = cd_str

# ========== 找出未排产但已有报工记录的工单 ==========
scheduled_jobs = set(df_sch['job'].tolist())
unscheduled_jobs = sorted(j for j in job_station_dates if j not in scheduled_jobs)
print(f"  -> 排产表外有报工的工单: {len(unscheduled_jobs)} 个")

# ========== 组装导出数据 ==========
now_month = date.today().strftime('%Y-%m')
export_rows = []

def make_row(job, schedule_info=None):
    """schedule_info: (item, qty, ship_date, line, sales, cycle_time, job_status) or None"""
    jsd = job_station_dates.get(job, {})
    if schedule_info:
        item, qty, sd, line, sales, ct, js = schedule_info
        ship_str = sd.strftime('%Y-%m-%d') if sd and hasattr(sd, 'strftime') else str(sd)[:10] if sd else ''
        row = {
            '工单号': job,
            'Item': str(item or ''),
            '工单数量': int(qty) if pd.notna(qty) else 0,
            '出货日期': ship_str,
            '出货月份': ship_str[:7] if ship_str else '',
            'Line': str(line or ''),
            '销售金额': float(sales) if pd.notna(sales) else 0,
            '单根时间(h)': float(ct) if pd.notna(ct) else 0,
            '总工时(h)': round(int(qty or 0) * float(ct or 0), 2),
            '工单状态': str(js or ''),
            '是否在排产表': '是',
        }
    else:
        row = {
            '工单号': job,
            'Item': '',
            '工单数量': '',
            '出货日期': '',
            '出货月份': '未排产',
            'Line': '',
            '销售金额': '',
            '单根时间(h)': '',
            '总工时(h)': '',
            '工单状态': '',
            '是否在排产表': '否',
        }
    completed_count = 0
    for st in STATION_ORDER:
        col_date = f'{STATION_LABEL[st]}完成日期'
        col_status = f'{STATION_LABEL[st]}状态'
        d = jsd.get(st, '')
        row[col_date] = d
        row[col_status] = '✓已完成' if d else '未完成'
        if d:
            completed_count += 1
    row['已完成工序数'] = completed_count
    row['工序完成进度'] = f'{completed_count}/{len(STATION_ORDER)}'
    row['是否全工序完成'] = '是' if completed_count == len(STATION_ORDER) else '否'
    return row

# 排产表工单
for _, r in df_sch.iterrows():
    job = str(r['job']).strip()
    row = make_row(job, (
        r.get('item'), r.get('qty'), r.get('ship_date'),
        r.get('line'), r.get('sales_amount'), r.get('cycle_time_h'),
        r.get('job_status')
    ))
    export_rows.append(row)

# 未排产但有报工的工单（追加在末尾）
for job in unscheduled_jobs:
    row = make_row(job, None)
    export_rows.append(row)

df_export = pd.DataFrame(export_rows)

# ========== 统计汇总 ==========
summary_rows = []
# 按月统计（含"未排产"）
for month, grp in df_export.groupby('出货月份'):
    total = len(grp)
    fully_completed = len(grp[grp['是否全工序完成'] == '是'])
    summary_rows.append({
        '出货月份': month,
        '工单总数': total,
        '全工序完成数': fully_completed,
        '完成率': f'{round(fully_completed/total*100, 1)}%' if total > 0 else '0%',
    })
# 排序：未排产放最后
sort_order = {'未排产': '9999-99'}
df_summary = pd.DataFrame(summary_rows)
df_summary['_sort'] = df_summary['出货月份'].map(sort_order).fillna(df_summary['出货月份'])
df_summary = df_summary.sort_values('_sort').drop(columns=['_sort'])

# 按工序统计
station_summary_rows = []
for st in STATION_ORDER:
    col = f'{STATION_LABEL[st]}状态'
    done = len(df_export[df_export[col] == '✓已完成'])
    station_summary_rows.append({
        '工序': f'{STATION_LABEL[st]} ({st})',
        '已完成工单数': done,
        '总工单数': len(df_export),
        '完成率': f'{round(done/len(df_export)*100, 1)}%' if len(df_export) > 0 else '0%',
    })
df_station_summary = pd.DataFrame(station_summary_rows)

# ========== 写入 Excel ==========
import os
out_dir = os.path.join(os.environ.get('USERPROFILE', os.environ.get('HOME', '.')), 'Desktop')
out_path = os.path.join(out_dir, '410_工序完成工单导出.xlsx')
now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

with pd.ExcelWriter(out_path, engine='openpyxl') as writer:
    df_export.to_excel(writer, sheet_name='工序完成明细', index=False)
    df_summary.to_excel(writer, sheet_name='按月汇总', index=False)
    df_station_summary.to_excel(writer, sheet_name='按工序汇总', index=False)

print(f"\n{'='*60}")
print(f"导出完成！")
print(f"文件: {out_path}")
print(f"时间: {now_str}")
print(f"{'='*60}")
print(f"\n明细: {len(df_export)} 个工单（排产表 {len(df_sch)} + 未排产 {len(unscheduled_jobs)}）")
print(f"\n--- 按月汇总 ---")
print(df_summary.to_string(index=False))
print(f"\n--- 按工序汇总 ---")
print(df_station_summary.to_string(index=False))

# 打印当月工单完成情况
current_month_jobs = df_export[df_export['出货月份'] == now_month]
if len(current_month_jobs) > 0:
    print(f"\n--- 当月({now_month})工单 ---")
    print(f"当月工单总数: {len(current_month_jobs)}")
    fully = len(current_month_jobs[current_month_jobs['是否全工序完成'] == '是'])
    print(f"全工序完成: {fully} ({round(fully/len(current_month_jobs)*100,1)}%)")
    print(f"未全部完成: {len(current_month_jobs) - fully}")

# 打印未排产工单情况
if unscheduled_jobs:
    print(f"\n--- 未排产工单（{len(unscheduled_jobs)}个）工序分布 ---")
    us_count = {}
    for job in unscheduled_jobs:
        for st in job_station_dates.get(job, {}):
            us_count[st] = us_count.get(st, 0) + 1
    for st in STATION_ORDER:
        c = us_count.get(st, 0)
        if c > 0:
            print(f"  {STATION_LABEL[st]} ({st}): {c}")
