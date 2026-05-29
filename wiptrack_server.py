# -*- coding: utf-8 -*-
"""
WIPTrack 实时数据 API 服务器
访问 http://localhost:5678/api/data 获取最新数据
"""

import json
import pymysql
from datetime import datetime, date
from flask import Flask, jsonify, send_from_directory
from flask_cors import CORS
import os

app = Flask(__name__)
CORS(app)  # 允许跨域请求

MYSQL_HOST = os.environ.get('MYSQL_HOST', '10.0.6.86')
MYSQL_PORT = int(os.environ.get('MYSQL_PORT', 33306))
MYSQL_USER = os.environ.get('MYSQL_USER', 'powerbi')
MYSQL_PASSWORD = os.environ.get('MYSQL_PASSWORD', '!Q1234567')
MYSQL_DATABASE = os.environ.get('MYSQL_DATABASE', 'wiptrack')

STATION_ORDER = ['Print', 'Cut', 'Pre', 'Asm', 'Test', 'Pack']
STATION_LABEL = {
    'Print': '工单打印',
    'Cut': '剪线',
    'Pre': '预处理',
    'Asm': '组装',
    'Test': '测试',
    'Pack': '包装',
}
# 中文 Station 名称 → 英文 key 的映射（数据库存中文时使用）
STATION_CN_TO_KEY = {v: k for k, v in STATION_LABEL.items()}
# 追加其他可能的别名（含数据库中实际存储的"中文+英文"混合格式）
STATION_CN_TO_KEY.update({
    'PRINT': 'Print', 'PRINT ': 'Print',
    # ★★★ 数据库纯英文缩写（最优先，精确匹配）★★★
    'Print': 'Print', 'Cut': 'Cut', 'Pre': 'Pre', 'Asm': 'Asm',
    'Test': 'Test', 'Pack': 'Pack',
    # 数据库实际存的值（英文变体）
    'Cutting': 'Cut', 'Pretreat': 'Pre', 'Package': 'Pack',
    'Assembly': 'Asm', 'Job': 'Print',  # Job=工单打印
    # 数据库实际值（中文+英文混合格式）
    '工单打印 Job Print': 'Print',
    '剪线 Cutting': 'Cut',
    '预处理 Pretreat': 'Pre',
    '组装 Assembly': 'Asm',
    '测试 Test': 'Test',
    '包装 Package': 'Pack',
    # 模糊匹配：如果数据库值包含这些关键字也能匹配
    'Job Print': 'Print',
    'Cutting': 'Cut',
    'Pretreat': 'Pre',
    'Assembly': 'Asm',
    'Package': 'Pack',
})


def parse_complete_date(val):
    """
    解析 CompleteDate，支持：
    - datetime 对象（直接返回）
    - date 对象（转为 datetime）
    - 带 AM/PM 的12小时制字符串（如 '2026-05-05 2:24:00 PM'）
      ★ 用 pandas.to_datetime 解析，不受 Windows 中文 locale 影响
    - 普通 24小时制字符串（如 '2026-05-05 14:30:00'）
    返回 datetime 对象，解析失败返回 None。
    """
    if val is None:
        return None
    if isinstance(val, datetime):
        return val
    if isinstance(val, date) and not isinstance(val, datetime):
        return datetime.combine(val, datetime.min.time())
    s = str(val).strip()
    if not s or s == 'NaT' or s == 'nan':
        return None

    # ★ 方法1：pandas.to_datetime 最宽容，不受 locale 影响，能正确处理 AM/PM
    try:
        import pandas as pd
        return pd.to_datetime(s).to_pydatetime()
    except Exception:
        pass

    # ★ 方法2：手动处理 AM/PM（pandas 也失败时的兜底方案）
    import re
    m = re.match(
        r'(?P<date>\d{4}[-/]\d{2}[-/]\d{2})\s+(?P<hour>\d{1,2}):(?P<min>\d{2})(?::(?P<sec>\d{2}))?\s+(?P<ampm>AM|PM)',
        s, re.IGNORECASE
    )
    if m:
        try:
            date_str = m.group('date')
            hour = int(m.group('hour'))
            minute = int(m.group('min'))
            second = int(m.group('sec')) if m.group('sec') else 0
            ampm = m.group('ampm').upper()

            # 12小时制 → 24小时制
            if ampm == 'AM':
                if hour == 12:
                    hour = 0
            else:
                if hour != 12:
                    hour += 12

            if '-' in date_str:
                dt = datetime.strptime(date_str, '%Y-%m-%d')
            else:
                dt = datetime.strptime(date_str, '%Y/%m/%d')
            return dt.replace(hour=hour, minute=minute, second=second)
        except Exception:
            pass

    # ★ 方法3：普通24小时制
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y/%m/%d %H:%M:%S',
                '%Y-%m-%d %H:%M', '%Y/%m/%d %H:%M'):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue

    return None


def compute_station_jobs_with_cascade(records, station_col, date_col, job_col, now_month, station_list=None):
    """
    跨月工序归属：如果一个工单在当月完成了某道工序，
    则该工单前面所有已完成的工序都算当月完成。

    返回: dict[station] = set(jobs)  — 当月各工序应归属的工单集合
    """
    if station_list is None:
        station_list = STATION_ORDER

    # 1) 收集当月实际完成的 (工单, 工序) 对
    month_done = {}  # job -> set of stations completed this month
    for r in records:
        station_raw = str(r.get(station_col, '') or '').strip()
        station_en = STATION_CN_TO_KEY.get(station_raw, '')
        if station_en not in station_list:
            continue
        dv = str(r.get(date_col, '') or '').strip()
        if not dv.startswith(now_month):
            continue
        job = str(r.get(job_col, '') or '').strip()
        if not job:
            continue
        if job not in month_done:
            month_done[job] = set()
        month_done[job].add(station_en)

    # 2) 收集每个工单所有时间完成的工序（不限月份）
    all_done = {}  # job -> set of stations ever completed
    for r in records:
        station_raw = str(r.get(station_col, '') or '').strip()
        station_en = STATION_CN_TO_KEY.get(station_raw, '')
        if station_en not in station_list:
            continue
        job = str(r.get(job_col, '') or '').strip()
        if not job:
            continue
        if job not in all_done:
            all_done[job] = set()
        all_done[job].add(station_en)

    # 3) 对当月有活动的工单，找到最远工序，前面所有已完成的工序都归入当月
    result = {st: set() for st in station_list}
    for job, month_stations in month_done.items():
        # 当月最远工序索引
        max_idx = max(station_list.index(st) for st in month_stations)
        # 该工单所有已完成的工序中，索引 <= max_idx 的都归入当月
        job_all = all_done.get(job, set())
        for st in job_all:
            if station_list.index(st) <= max_idx:
                result[st].add(job)

    return result


def get_pack_completed_jobs(month=None):
    """
    从 production_records 表中获取已完成最后一道工序（包装 Package）的工单集合。
    month: 格式 'YYYY-MM'，不传则返回所有月份的。
    返回: set of job strings
    """
    conn = pymysql.connect(host=MYSQL_HOST, port=MYSQL_PORT, user=MYSQL_USER, password=MYSQL_PASSWORD, database=MYSQL_DATABASE, charset='utf8mb4', connect_timeout=10)
    cursor = conn.cursor()
    if month:
        cursor.execute("""
            SELECT DISTINCT pr.Job
            FROM production_records pr
            WHERE pr.Station = '包装 Package'
              AND pr.SiteRef = 'NAIGROUP_PROD_310'
              AND DATE_FORMAT(pr.CompleteDate, %s) = %s
        """, ('%Y-%m', month))
    else:
        cursor.execute("""
            SELECT DISTINCT pr.Job
            FROM production_records pr
            WHERE pr.Station = '包装 Package'
              AND pr.SiteRef = 'NAIGROUP_PROD_310'
        """)
    jobs = {str(row[0]).strip().upper() for row in cursor.fetchall()}
    conn.close()
    return jobs


def get_erp_schedule():
    """从 erp_data.hmlv_production_schedule 表读取排程数据"""
    import pandas as pd
    conn = pymysql.connect(host=MYSQL_HOST, port=MYSQL_PORT, user=MYSQL_USER, password=MYSQL_PASSWORD, database=MYSQL_DATABASE, charset='utf8mb4', connect_timeout=10)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT job, item, qty, ship_date, line, work_hours_h,
               unit_price, sales_amount, job_status, tested_qty, wo_total,
               cycle_time_h
        FROM erp_data.hmlv_production_schedule
    """)
    columns = [col[0] for col in cursor.description]
    rows_raw = cursor.fetchall()
    conn.close()

    # 将 row tuple 转为 list of dict，避免 pandas DataFrame 形状错误
    data = []
    for row in rows_raw:
        row_dict = {}
        for i, col in enumerate(columns):
            row_dict[col] = row[i]
        data.append(row_dict)
    
    df = pd.DataFrame(data)
    # 统一 job 列为大写，避免大小写不匹配
    df['job'] = df['job'].astype(str).str.strip().str.upper()
    df['qty'] = pd.to_numeric(df['qty'], errors='coerce').fillna(0).astype(int)
    df['tested_qty'] = pd.to_numeric(df['tested_qty'], errors='coerce').fillna(0).astype(int)
    df['wo_total'] = pd.to_numeric(df['wo_total'], errors='coerce').fillna(0).astype(int)
    df['sales_amount'] = pd.to_numeric(df['sales_amount'], errors='coerce').fillna(0)
    df['unit_price'] = pd.to_numeric(df['unit_price'], errors='coerce').fillna(0)
    df['work_hours_h'] = pd.to_numeric(df['work_hours_h'], errors='coerce').fillna(0)
    df['cycle_time_h'] = pd.to_numeric(df['cycle_time_h'], errors='coerce').fillna(0)
    df['_dt'] = pd.to_datetime(df['ship_date'], errors='coerce')
    df['_month'] = df['_dt'].dt.strftime('%Y-%m')
    return df


def get_data():
    conn = pymysql.connect(host=MYSQL_HOST, port=MYSQL_PORT, user=MYSQL_USER, password=MYSQL_PASSWORD, database=MYSQL_DATABASE, charset='utf8mb4', connect_timeout=10)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM production_records WHERE SiteRef = 'NAIGROUP_PROD_310' ORDER BY id")
    columns = [col[0] for col in cursor.description]
    rows_raw = cursor.fetchall()
    conn.close()

    rows = []
    for r in rows_raw:
        row = {}
        for i, col in enumerate(columns):
            val = r[i]
            if isinstance(val, (datetime, date)):
                val = val.strftime('%Y-%m-%d')
            row[col] = val
        rows.append(row)

    # 将 Station 字段中的中文值统一转为英文 key（如"组装"→"Asm"）
    station_col_name = None
    for col in columns:
        if col.lower() in ('station', 'process', 'operation'):
            station_col_name = col
            break
    if station_col_name:
        for row in rows:
            sv = str(row.get(station_col_name, '') or '').strip()
            if sv in STATION_CN_TO_KEY:
                row[station_col_name] = STATION_CN_TO_KEY[sv]

    # 统一 Job 列为大写，避免大小写不匹配
    job_col_name = None
    for col in columns:
        if 'job' in col.lower():
            job_col_name = col
            break
    if job_col_name:
        for row in rows:
            val = row.get(job_col_name)
            if val is not None:
                row[job_col_name] = str(val).strip().upper()

    return columns, rows


@app.route('/api/data')
def api_data():
    try:
        import pandas as pd
        columns, rows = get_data()

        # 加载 ERP 排程数据（用于当月工单数计算）
        df_all = get_erp_schedule()

        # 找关键字段
        col_lower = [c.lower() for c in columns]

        def find_col(*names):
            for n in names:
                for i, c in enumerate(col_lower):
                    if n in c:
                        return columns[i]
            return None

        job_col = find_col('job', 'order', 'wo', 'siteref')
        station_col = find_col('station', 'process', 'operation')
        date_col = find_col('completedate', 'complete', 'date', 'created')

        # ---- KPI ----
        all_jobs = set(r[job_col] for r in rows if r.get(job_col))
        total_jobs = len(all_jobs)

        # 当月工单数
        current_month = datetime.now().strftime('%Y-%m')
        monthly_jobs = set()
        for r in rows:
            d = r.get(date_col, '') or ''
            if d.startswith(current_month) and r.get(job_col):
                monthly_jobs.add(r[job_col])
        month_job_count = len(monthly_jobs) if monthly_jobs else total_jobs

        # ---- 当天各工序完成数 (KPI 卡片用) ----
        today_str = datetime.now().strftime('%Y-%m-%d')
        today_station_done = {}
        for s in STATION_ORDER:
            done_today = set()
            for r in rows:
                sv = str(r.get(station_col, '') or '').strip()
                dv = str(r.get(date_col, '') or '').strip()
                if sv == s and dv.startswith(today_str):
                    j = r.get(job_col)
                    if j:
                        done_today.add(j)
            today_station_done[s] = len(done_today)

        # ---- 工序完成率（以排产表 ship_date 当月 JOB 为准）----
        # 基准数 = 当月排产工单总数（ship_date在当月）
        df_this_month = df_all[df_all['_month'] == current_month]
        month_jobs_erp = set(df_this_month['job'].dropna().astype(str).str.strip().str.upper().unique())
        month_total_jobs = len(month_jobs_erp)
        base = month_total_jobs if month_total_jobs > 0 else 1  # 避免除零

        # 对当月排产 JOB，统计各工序完成情况（不限完成月份，跨月也纳入）
        station_done_map = {s: set() for s in STATION_ORDER}
        for r in rows:
            job = str(r.get(job_col, '') or '').strip().upper()
            if job not in month_jobs_erp:
                continue
            sv = str(r.get(station_col, '') or '').strip()
            station_en = STATION_CN_TO_KEY.get(sv, '')
            if station_en in STATION_ORDER:
                station_done_map[station_en].add(job)

        station_stats = []
        for s in STATION_ORDER:
            done_month = len(station_done_map[s])
            pct = round(done_month / base * 100, 1) if base > 0 else 0
            station_stats.append({
                'station': s,
                'label': STATION_LABEL.get(s, s),
                'done': done_month,
                'base': base,
                'pct': pct
            })

        # ---- 每日趋势 ----
        daily = {}
        for r in rows:
            d = r.get(date_col, '') or ''
            if d:
                d = d[:10]
                if d not in daily:
                    daily[d] = {'done': 0, 'running': 0}
                sv = str(r.get(station_col, '') or '').strip()
                dv = str(r.get(date_col, '') or '').strip()
                if dv:
                    daily[d]['done'] += 1
                else:
                    daily[d]['running'] += 1

        daily_list = sorted([
            {'date': k, 'done': v['done'], 'running': v['running']}
            for k, v in daily.items()
        ], key=lambda x: x['date'])

        # ---- 工序甘特/流程 WIP（基于当月排产 JOB）----
        # 在制品：已打印但尚未到达该工序完成的工单
        wip_by_station = []
        for i, s in enumerate(STATION_ORDER):
            # 在制品 = 前一道工序完成的工单数 - 当前工序完成的工单数
            if i == 0:
                # 第一道工序：WIP = 当月工单总数 - 当前工序完成数
                prev_done = base  # base = 当月排产工单总数
            else:
                prev_s = STATION_ORDER[i-1]
                prev_done = len(station_done_map[prev_s])

            cur_done = len(station_done_map[s])
            wip = max(0, prev_done - cur_done)

            # ---- 计算滞留天数 ----
            # 滞留工单：已完成前一道工序但尚未完成当前工序的工单
            prev_jobs_set = station_done_map[STATION_ORDER[i-1]] if i > 0 else month_jobs_erp
            cur_jobs_set = station_done_map[s]
            滞留_jobs = prev_jobs_set - cur_jobs_set
            
            if len(滞留_jobs) > 0:
                # 计算平均滞留天数
                total_days = 0
                count_with_date = 0
                for job_id in 滞留_jobs:
                    # 查找该工单在前一道工序的完成日期
                    prev_complete_date = None
                    for r in rows:
                        sv = str(r.get(station_col, '') or '').strip()
                        dv = str(r.get(date_col, '') or '').strip()
                        j = r.get(job_col)
                        if sv == (STATION_ORDER[i-1] if i > 0 else 'Print') and dv and str(j).strip() == str(job_id).strip():
                            try:
                                prev_complete_date = datetime.strptime(dv[:10], '%Y-%m-%d')
                                break
                            except:
                                pass
                    
                    if prev_complete_date:
                        # 计算到今天的滞留天数
                        today = datetime.now()
                        days = (today - prev_complete_date).days
                        total_days += days
                        count_with_date += 1
                
                avg_days = round(total_days / count_with_date, 1) if count_with_date > 0 else 0
            else:
                avg_days = 0
            
            wip_by_station.append({
                'station': s,
                'label': STATION_LABEL.get(s, s),
                'wip': wip,
                '滞留_count': len(滞留_jobs),
                '滞留_avg_days': avg_days
            })

        # ---- 最近 50 条工单明细 ----
        recent = rows[-50:] if len(rows) > 50 else rows
        recent_list = [dict(r) for r in recent]

        return jsonify({
            'success': True,
            'updated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'kpi': {
                'month_label': datetime.now().strftime('%Y年%m月'),
                'month_jobs': month_job_count,
                'total_jobs': total_jobs,
                'base': base,
                'total_records': len(rows),
                'today_label': datetime.now().strftime('%m月%d日'),
                'today_station_done': today_station_done,  # 当天各工序完成数
            },
            'station_stats': station_stats,
            'daily_trend': daily_list,
            'wip': wip_by_station,
            'recent': recent_list,
            'columns': columns,
        })

    except Exception as e:
        import traceback
        return jsonify({'success': False, 'error': str(e), 'trace': traceback.format_exc()}), 500


@app.route('/api/excel_jobs')
def api_excel_jobs():
    """从数据库 erp_data.hmlv_production_schedule 表读取生产数据，计算销售统计、组装工时、工序销售"""
    try:
        import pandas as pd
        from datetime import date

        # ========== 从数据库读取生产排程数据 ==========
        df_all = get_erp_schedule()
        now_month = date.today().strftime('%Y-%m')

        # 当月数据
        df_this = df_all[df_all['_month'] == now_month]

        # ========== 获取当月已完成（包装Package完成）的工单集合 ==========
        pack_completed_all = get_pack_completed_jobs()  # 所有月份
        pack_completed_this_month = get_pack_completed_jobs(now_month)  # 当月

        result = {}

        # ========== 排产计划统计 ==========
        result['schedule'] = {
            'label': '排产计划',
            'total': int(df_all['job'].nunique()),
            'by_month': {str(k): int(v) for k, v in df_all.groupby('_month')['job'].nunique().items()}
        }

        # ========== 入库记录统计（production_records中完成Package的工单）==========
        completed_df = df_all[df_all['job'].str.strip().isin(pack_completed_all)]
        result['ruku'] = {
            'label': '入库记录',
            'total': int(completed_df['job'].nunique()),
            'by_month': {str(k): int(v) for k, v in completed_df.groupby('_month')['job'].nunique().items()}
        }

        # ========== 获取各工序完成的工单集合 ==========
        columns_pr, records = get_data()

        def find_col(*names):
            for n in names:
                for c in columns_pr:
                    if n in c.lower():
                        return c
            return None

        job_col = find_col('job', 'order', 'wo', 'siteref')
        station_col = find_col('station', 'process', 'operation')
        date_col = find_col('completedate', 'complete', 'date', 'created')

        station_jobs = {s: set() for s in STATION_ORDER}

        for r in records:
            station_raw = str(r.get(station_col, '') or '').strip()
            station_en = STATION_CN_TO_KEY.get(station_raw, '')
            job = str(r.get(job_col, '') or '').strip()
            if job and station_en in station_jobs:
                station_jobs[station_en].add(job)

        # ========== 工单→销售映射（从数据库erp_data表）==========
        job_sales_map = {}
        for _, row in df_all.iterrows():
            job_key = str(row['job']).strip()
            job_sales_map[job_key] = float(row['sales_amount'])

        # ========== 工单→Item/工单数/工时映射（从数据库erp_data表）==========
        job_item_released = {}
        job_hours_map = {}  # job -> qty * cycle_time_h（工单总工时）
        for _, row in df_all.iterrows():
            j = str(row['job']).strip()
            item = str(row['item']).strip() if pd.notna(row['item']) else ''
            released = int(row['qty']) if pd.notna(row['qty']) else 0
            ct = float(row['cycle_time_h']) if pd.notna(row['cycle_time_h']) else 0.0
            # 总工时 = 工单数量 × 单根时间
            total_h = float(released) * ct
            if j and j not in job_item_released:
                job_item_released[j] = (item, released)
                job_hours_map[j] = total_h

        # ========== 当前月份工单集合（用于各模块引用）==========
        df_this_month = df_all[df_all['_month'] == now_month]
        all_month_jobs = set(df_this_month['job'].dropna().astype(str).str.strip().str.upper().unique())

        # ========== 工序销售金额（以排产表当月 JOB 为准）==========
        STATION_LIST = STATION_ORDER

        # 对当月排产 JOB，统计各工序完成情况（不限完成月份）
        station_done_for_month = {st: set() for st in STATION_LIST}
        for r in records:
            job = str(r.get(job_col, '') or '').strip().upper()
            if job not in all_month_jobs:
                continue
            sv = str(r.get(station_col, '') or '').strip()
            station_en = STATION_CN_TO_KEY.get(sv, '')
            if station_en in STATION_LIST:
                station_done_for_month[station_en].add(job)

        station_sales = {}
        for st in STATION_LIST:
            sales = 0.0
            for job in station_done_for_month[st]:
                sales += job_sales_map.get(job, 0)
            station_sales[st] = round(sales, 2)
        result['station_sales'] = station_sales

        # ========== 当前月份统计 ==========
        # 当月工单总数 = 所有 ship_date 在当月的 JOB（不管完成还是未完成）

        # 已完成：ship_date在当月 且 production_records中完成了Package工序
        completed_jobs = all_month_jobs & pack_completed_this_month
        # 未完成：ship_date在当月 且 未完成Package工序
        pending_jobs = all_month_jobs - completed_jobs

        result['current_month'] = {
            'label': f'{now_month}',
            'total': len(all_month_jobs),
            'completed': len(completed_jobs),
            'pending': len(pending_jobs),
        }

        # ========== 各工序已消耗工时（以排产表当月 JOB 为准，去重）==========
        station_hours = {}
        for st in STATION_LIST:
            hours = 0.0
            for job in station_done_for_month[st]:
                hours += job_hours_map.get(job, 0)
            station_hours[st] = round(hours, 2)
        result['station_hours'] = station_hours

        # ========== 每个工序当天完成的工时 ==========
        # 逻辑：当天工序工时 = 当天完成该工序的所有记录.qty × cycle_time_h 的累加（不去重，每条记录独立计算）
        today_str_db = date.today().strftime('%Y-%m-%d')
        station_hours_today = {}
        for st in STATION_LIST:
            hours = 0.0
            for r in records:
                dv = str(r.get(date_col, '') or '').strip()
                if not dv.startswith(today_str_db):
                    continue
                station_cn = str(r.get(station_col, '') or '').strip()
                station_en = STATION_CN_TO_KEY.get(station_cn, station_cn)
                if station_en == st:
                    job = str(r.get(job_col, '') or '').strip()
                    if job:
                        hours += job_hours_map.get(job, 0)
            station_hours_today[st] = round(hours, 2)
        result['station_hours_today'] = station_hours_today

        # ========== 销售统计（按 job 去重）==========
        df_this_month_dedup = df_this_month.drop_duplicates(subset=['job'], keep='first')
        total_sales = float(df_this_month_dedup['sales_amount'].sum())
        # 用 pack_completed 判断，不再依赖 job_status
        completed_mask = df_this_month_dedup['job'].str.strip().isin(pack_completed_this_month)
        pending_sales = float(df_this_month_dedup[~completed_mask]['sales_amount'].sum())
        completed_sales = float(df_this_month_dedup[completed_mask]['sales_amount'].sum())

        # 入库完成率
        ruku_completed_sales = completed_sales
        sales_completion_rate = round((ruku_completed_sales / total_sales * 100), 1) if total_sales > 0 else 0

        result['sales'] = {
            'pending_sales': pending_sales,
            'completed_sales': completed_sales,
            'total_sales': total_sales,
            'ruku_completed_sales': round(ruku_completed_sales, 2),
            'sales_completion_rate': sales_completion_rate,
        }

        # ========== 组装工时（仅当月ship_date的工单，按 job 去重）==========
        # 逻辑：总工时 = qty × cycle_time_h（工单数量 × 单根时间）
        # ★★★ 直接用 SQL 计算，避免 DataFrame Python 处理的不一致问题 ★★★
        # ★★★ "已完成"判断改为 production_records 中完成 Package 工序的工单 ★★★
        conn_hours = pymysql.connect(host=MYSQL_HOST, port=MYSQL_PORT, user=MYSQL_USER, password=MYSQL_PASSWORD, database=MYSQL_DATABASE, charset='utf8mb4', connect_timeout=10)
        cursor_hours = conn_hours.cursor()

        # 已完成工时：production_records 中完成 Package 且 ship_date 在当月
        cursor_hours.execute("""
            SELECT SUM(ps.qty * ps.cycle_time_h)
            FROM erp_data.hmlv_production_schedule ps
            WHERE DATE_FORMAT(ps.ship_date, %s) = %s
              AND ps.job COLLATE utf8mb4_unicode_ci IN (
                  SELECT DISTINCT pr.Job FROM production_records pr
                  WHERE pr.Station = '包装 Package'
                    AND pr.SiteRef = 'NAIGROUP_PROD_310'
                    AND DATE_FORMAT(pr.CompleteDate, %s) = %s
              )
        """, ('%Y-%m', now_month, '%Y-%m', now_month))
        completed_asm_hours = round(float(cursor_hours.fetchone()[0] or 0), 2)
        cursor_hours.execute("""
            SELECT COUNT(DISTINCT ps.job)
            FROM erp_data.hmlv_production_schedule ps
            WHERE DATE_FORMAT(ps.ship_date, %s) = %s
              AND ps.job COLLATE utf8mb4_unicode_ci IN (
                  SELECT DISTINCT pr.Job FROM production_records pr
                  WHERE pr.Station = '包装 Package'
                    AND pr.SiteRef = 'NAIGROUP_PROD_310'
                    AND DATE_FORMAT(pr.CompleteDate, %s) = %s
              )
        """, ('%Y-%m', now_month, '%Y-%m', now_month))
        completed_asm_count = cursor_hours.fetchone()[0] or 0

        # 未完成工时：ship_date在当月 且 未完成 Package
        cursor_hours.execute("""
            SELECT SUM(ps.qty * ps.cycle_time_h)
            FROM erp_data.hmlv_production_schedule ps
            WHERE DATE_FORMAT(ps.ship_date, %s) = %s
              AND ps.job COLLATE utf8mb4_unicode_ci NOT IN (
                  SELECT DISTINCT pr.Job FROM production_records pr
                  WHERE pr.Station = '包装 Package'
                    AND pr.SiteRef = 'NAIGROUP_PROD_310'
              )
        """, ('%Y-%m', now_month))
        pending_asm_hours = round(float(cursor_hours.fetchone()[0] or 0), 2)
        cursor_hours.execute("""
            SELECT COUNT(DISTINCT ps.job)
            FROM erp_data.hmlv_production_schedule ps
            WHERE DATE_FORMAT(ps.ship_date, %s) = %s
              AND ps.job COLLATE utf8mb4_unicode_ci NOT IN (
                  SELECT DISTINCT pr.Job FROM production_records pr
                  WHERE pr.Station = '包装 Package'
                    AND pr.SiteRef = 'NAIGROUP_PROD_310'
              )
        """, ('%Y-%m', now_month))
        pending_asm_count = cursor_hours.fetchone()[0] or 0

        print(f"[DEBUG] SQL工时: completed={completed_asm_hours}h({completed_asm_count}个), pending={pending_asm_hours}h({pending_asm_count}个), total={round(completed_asm_hours + pending_asm_hours, 2)}h")
        conn_hours.close()
        result['asm_hours'] = {
            'pending_hours': pending_asm_hours,
            'pending_count': pending_asm_count,
            'completed_hours': completed_asm_hours,
            'completed_count': completed_asm_count,
            'total_hours': round(pending_asm_hours + completed_asm_hours, 2),
        }
        # 当月总工时 = asm_hours.total_hours（仅当月ship_date工单）
        result['total_hours_all'] = result['asm_hours']['total_hours']

        # ========== 每日工时目标（仅当月ship_date工单，按工作日计算）==========
        # ★ 总目标工时已改为仅当月ship_date工单（见上方 asm_hours.total_hours）
        from calendar import monthrange

        year, month = date.today().year, date.today().month
        days_in_month = monthrange(year, month)[1]
        today_day = date.today().day

        # 2026年中国法定节假日（放假日期）
        HOLIDAYS_2026 = {
            date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 3),
            date(2026, 2, 15), date(2026, 2, 16), date(2026, 2, 17), date(2026, 2, 18),
            date(2026, 2, 19), date(2026, 2, 20), date(2026, 2, 21), date(2026, 2, 22),
            date(2026, 2, 23),
            date(2026, 4, 4), date(2026, 4, 5), date(2026, 4, 6),
            date(2026, 5, 1), date(2026, 5, 2), date(2026, 5, 3), date(2026, 5, 4),
            date(2026, 5, 5),
            date(2026, 6, 19), date(2026, 6, 20), date(2026, 6, 21),
            date(2026, 9, 25), date(2026, 9, 26), date(2026, 9, 27),
            date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 3), date(2026, 10, 4),
            date(2026, 10, 5), date(2026, 10, 6), date(2026, 10, 7),
        }
        EXTRA_WORKDAYS_2026 = {
            date(2026, 1, 4), date(2026, 2, 14), date(2026, 2, 28),
            date(2026, 5, 9), date(2026, 9, 20), date(2026, 10, 10),
        }

        def is_workday(d):
            if d in HOLIDAYS_2026:
                return False
            if d in EXTRA_WORKDAYS_2026:
                return True
            return d.weekday() < 5

        # 当月工作日总数
        workdays_in_month = sum(
            1 for day in range(1, days_in_month + 1)
            if is_workday(date(year, month, day))
        )
        # 截止到今天已过去的工作日数
        passed_workdays = sum(
            1 for day in range(1, today_day + 1)
            if is_workday(date(year, month, day))
        )

        total_target_hours = result['asm_hours']['total_hours']
        daily_target_hours = round(total_target_hours / workdays_in_month, 2) if workdays_in_month > 0 else 0
        realtime_target_hours = round(daily_target_hours * passed_workdays, 2)
        result['daily_target_hours'] = daily_target_hours
        result['realtime_target_hours'] = realtime_target_hours
        result['workdays_in_month'] = workdays_in_month
        result['passed_workdays'] = passed_workdays

        # ========== 每个工序的每日工时目标（按工序工时占比分摊总每日目标）==========
        total_station_hours = sum(result['station_hours'].values()) or 1
        station_daily_target = {}
        for st in STATION_LIST:
            ratio = result['station_hours'][st] / total_station_hours
            station_daily_target[st] = round(daily_target_hours * ratio, 2)
        result['station_daily_target'] = station_daily_target

        return jsonify({'success': True, 'data': result})

    except Exception as e:
        import traceback
        print(f"[ERROR api_excel_jobs] {e}")
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e), 'trace': traceback.format_exc()}), 500


@app.route('/api/wip')
def api_wip():
    """各工序 WIP 滞留工单列表：每个工序未完成下一道工序的 JOB + Item + 滞留时间"""
    try:
        import pandas as pd

        conn = pymysql.connect(host=MYSQL_HOST, port=MYSQL_PORT, user=MYSQL_USER, password=MYSQL_PASSWORD, database=MYSQL_DATABASE, charset='utf8mb4', connect_timeout=10)
        cursor = conn.cursor()

        # 工序顺序（从 site_station 表）
        # 工序流程：Print → Cut → Pre → Asm → Test → Pack
        STATION_ORDER_CN = ['工单打印', '剪线', '预处理', '组装', '测试', '包装']
        STATION_CN_TO_EN = {
            '工单打印': 'Print', '剪线': 'Cut', '预处理': 'Pre',
            '组装': 'Asm', '测试': 'Test', '包装': 'Pack'
        }

        # 获取310站点所有记录（只统计310站点的数据）
        cursor.execute(
            "SELECT Job, Station, CompleteDate FROM production_records "
            "WHERE SiteRef = 'NAIGROUP_PROD_310' AND CompleteDate IS NOT NULL"
        )
        rows = cursor.fetchall()
        # 不关闭连接，后面还要查异常表

        # 构建 job → {station_en: CompleteDate}
        # 使用全局 parse_complete_date 函数解析 CompleteDate（支持 AM/PM 格式）
        # 同时将数据库中的 Station 值（可能是"中文+英文"混合格式）转为标准英文 key
        job_station_time = {}
        for row in rows:
            job = str(row[0]).strip().upper()
            station_raw = str(row[1]).strip()
            # 转为标准英文 key（先查映射表，找不到时尝试提取英文部分）
            station_en = STATION_CN_TO_KEY.get(station_raw, '')
            if not station_en:
                import re
                m = re.search(r'([A-Za-z]+)', station_raw)
                if m:
                    eng = m.group(1)
                    eng_map = {'Print':'Print','Cut':'Cut','Pre':'Pre','Asm':'Asm',
                               'Test':'Test','Pack':'Pack','Cutting':'Cut','Assembly':'Asm',
                               'Pretreat':'Pre','Package':'Pack','Job':'Print'}
                    station_en = eng_map.get(eng, eng)
            if not station_en:
                station_en = station_raw
            complete_date = parse_complete_date(row[2])
            if job not in job_station_time:
                job_station_time[job] = {}
            if complete_date:
                job_station_time[job][station_en] = complete_date

        # 从数据库 erp_data.hmlv_production_schedule 读取 Job→Item/Line 映射
        job_item_map = {}
        job_line_map = {}
        try:
            import pandas as pd
            df_erp = get_erp_schedule()
            for _, row in df_erp.iterrows():
                j = str(row['job']).strip()
                if not j or j == 'nan':
                    continue
                item = str(row['item']).strip() if pd.notna(row['item']) else ''
                if item and item != 'nan':
                    job_item_map[j] = item
                line = str(row['line']).strip() if pd.notna(row['line']) else ''
                if line and line != 'nan':
                    job_line_map[j] = line
            print(f"[DEBUG] job_item_map 加载 {len(job_item_map)} 条, job_line_map 加载 {len(job_line_map)} 条")
        except Exception as e:
            print(f"[WARN] ERP数据读取失败: {e}")

        # 查询异常工单
        exc_by_job = {}
        try:
            cursor.execute(
                "SELECT Station, Job, description, start_time FROM wip_exceptions "
                "WHERE SiteRef = 'NAIGROUP_PROD_310' AND end_time IS NULL"
            )
            exc_rows = cursor.fetchall()
            for er in exc_rows:
                exc_job = str(er[1]).strip().upper()
                exc_station = str(er[0]).strip()
                exc_desc = str(er[2]).strip() if er[2] else ''
                exc_start = parse_complete_date(er[3])
                if exc_job not in exc_by_job:
                    exc_by_job[exc_job] = []
                exc_by_job[exc_job].append({
                    'station': exc_station,
                    'description': exc_desc,
                    'start_time': exc_start.strftime('%m-%d %H:%M') if exc_start else ''
                })
        except Exception as e:
            print(f"[WARN] 异常工单查询失败: {e}")
            exc_by_job = {}
        
        conn.close()

        now = datetime.now()
        # 当月范围（动态计算）
        year, month = now.year, now.month
        current_month = now.strftime('%Y-%m')
        month_start = datetime(year, month, 1)
        import calendar
        days_in_month = calendar.monthrange(year, month)[1]
        month_end = datetime(year, month, days_in_month, 23, 59, 59)

        # ★ 当月排产 JOB（ship_date 在当月）— 从 ERP 排产表获取
        month_jobs = set()
        try:
            import pandas as pd
            df_erp = get_erp_schedule()
            df_this_month = df_erp[df_erp['_month'] == current_month]
            month_jobs = set(df_this_month['job'].dropna().astype(str).str.strip().str.upper().unique())
            print(f"[DEBUG] WIP month_jobs 从排产表获取: {len(month_jobs)} 个 (ship_date={current_month})")
        except Exception as e:
            print(f"[WARN] WIP 排产数据获取失败: {e}")
            month_jobs = set()

        result = {}

        # ★ 调试：打印关键统计
        print(f"[DEBUG] month_jobs 工单数: {len(month_jobs)}")
        print(f"[DEBUG] 剪线(Cut)WIP计算: 遍历 {len(month_jobs)} 个工单...")

        # 对每个工序（跳过第一道"工单打印"）：
        # - 完成数：该工序在当月完成
        # - 滞留数：有当月记录的工单中，上道工序在当月完成但当前工序未完成
        for i, station_cn in enumerate(STATION_ORDER_CN):
            if i == 0:
                continue  # 工单打印不需要 WIP
            prev_station_cn = STATION_ORDER_CN[i - 1]
            station_en = STATION_CN_TO_EN.get(station_cn, station_cn)
            prev_station_en = STATION_CN_TO_EN.get(prev_station_cn, prev_station_cn)

            done_in_month = 0
            wip_list = []
            for job in month_jobs:  # 只看当月排产 JOB
                stations = job_station_time.get(job, {})
                # 完成数：当月排产 JOB 已完成该工序（不限完成月份）
                if station_en in stations:
                    done_in_month += 1
                # 滞留数：上道工序已完成但当前工序未完成
                elif prev_station_en in stations:
                    prev_t = stations[prev_station_en]
                    # 滞留时间 = 经过的自然日 × 8H
                    delta_days = (now.date() - prev_t.date()).days
                    dwell_hours = round(delta_days * 8, 1)
                    wip_entry = {
                        'job': job,
                        'item': job_item_map.get(job, ''),
                        'line': job_line_map.get(job, ''),
                        'complete_time': prev_t.strftime('%Y-%m-%d %H:%M'),
                        'dwell_hours': dwell_hours,
                    }
                    if job in exc_by_job:
                        wip_entry['exception'] = exc_by_job[job]
                    wip_list.append(wip_entry)

            # 按滞留时间降序，异常工单置顶
            exc_count = sum(1 for e in wip_list if 'exception' in e)
            wip_list.sort(key=lambda x: (-(1 if 'exception' in x else 0), -x['dwell_hours']))

            # ★ 调试：打印各工序统计
            print(f"[DEBUG] {station_en} ({station_cn}): done_in_month={done_in_month}, wip_count={len(wip_list)}, exc_count={exc_count}")

            result[station_en] = {
                'label': station_cn,
                'count': len(wip_list),  # 5月滞留数
                'done_in_month': done_in_month,      # 当月完成数（新增）
                'exception_count': exc_count,         # 异常工单数（新增）
                'jobs': wip_list
            }

        return jsonify({'success': True, 'data': result})

    except Exception as e:
        import traceback
        return jsonify({'success': False, 'error': str(e), 'trace': traceback.format_exc()}), 500


@app.route('/api/search_wo')
def api_search_wo():
    """
    工单状态查询接口
    参数: q=工单号（支持模糊匹配，不区分大小写）
    返回: 该工单在各工序的完成情况及当前所处阶段
    """
    from flask import request as flask_request
    q = (flask_request.args.get('q') or '').strip().upper()
    if not q:
        return jsonify({'success': False, 'error': '请输入工单号'})

    try:
        conn = pymysql.connect(host=MYSQL_HOST, port=MYSQL_PORT, user=MYSQL_USER, password=MYSQL_PASSWORD, database=MYSQL_DATABASE, charset='utf8mb4', connect_timeout=10)
        cursor = conn.cursor()

        # 用 LIKE 模糊匹配工单号（Job 字段），同时过滤 SiteRef
        cursor.execute(
            "SELECT Job, Station, CompleteDate FROM production_records "
            "WHERE SiteRef = 'NAIGROUP_PROD_310' AND LOWER(Job) LIKE %s ORDER BY CompleteDate",
            ('%' + q.lower() + '%',)
        )
        rows = cursor.fetchall()
        conn.close()

        if not rows:
            return jsonify({'success': True, 'found': False, 'q': q, 'results': []})

        # 从数据库 erp_data.hmlv_production_schedule 读取 Job→Line 映射
        job_line_map = {}
        try:
            import pandas as pd
            df_erp = get_erp_schedule()
            for _, row in df_erp.iterrows():
                j = str(row['job']).strip()
                if not j or j == 'nan':
                    continue
                line = str(row['line']).strip() if pd.notna(row['line']) else ''
                if line and line != 'nan':
                    job_line_map[j] = line
        except Exception:
            pass

        # 按工单号归组，记录每道工序的完成时间
        job_map = {}
        for row in rows:
            job = str(row[0]).strip().upper()
            station_raw = str(row[1]).strip()
            complete_dt = parse_complete_date(row[2])  # 使用全局函数解析（支持 AM/PM）
            # 统一 station key
            station_key = STATION_CN_TO_KEY.get(station_raw, station_raw)
            complete_str = ''
            if complete_dt:
                # complete_dt 现在是 datetime 对象，可以安全调用 strftime
                if hasattr(complete_dt, 'hour'):
                    complete_str = complete_dt.strftime('%Y-%m-%d %H:%M')
                else:
                    complete_str = complete_dt.strftime('%Y-%m-%d')
            if job not in job_map:
                job_map[job] = {}
            # 同一工序取最新完成时间（用 datetime 对象比较，而不是字符串）
            if station_key not in job_map[job] or complete_dt > job_map[job][station_key][0]:
                job_map[job][station_key] = (complete_dt, complete_str)

        results = []
        for job, station_times in sorted(job_map.items()):
            # 构造工序流转明细
            steps = []
            last_done_idx = -1
            for idx, s in enumerate(STATION_ORDER):
                ct_tuple = station_times.get(s, (None, ''))
                ct_str = ct_tuple[1] if ct_tuple else ''  # 取 complete_str
                done = bool(ct_str)
                if done:
                    last_done_idx = idx
                steps.append({
                    'station': s,
                    'label': STATION_LABEL.get(s, s),
                    'done': done,
                    'complete_time': ct_str
                })

            # 当前状态判断
            if last_done_idx == len(STATION_ORDER) - 1:
                current_status = '已完工'
                current_station = STATION_LABEL.get(STATION_ORDER[-1], STATION_ORDER[-1])
            elif last_done_idx >= 0:
                next_s = STATION_ORDER[last_done_idx + 1]
                current_status = '进行中'
                current_station = STATION_LABEL.get(next_s, next_s)
            else:
                current_status = '待处理'
                current_station = STATION_LABEL.get(STATION_ORDER[0], STATION_ORDER[0])

            results.append({
                'job': job,
                'line': job_line_map.get(job, ''),
                'current_status': current_status,
                'current_station': current_station,
                'steps': steps
            })

        return jsonify({'success': True, 'found': True, 'q': q, 'results': results})

    except Exception as e:
        import traceback
        return jsonify({'success': False, 'error': str(e), 'trace': traceback.format_exc()}), 500


SALES_TARGET_FILE = os.environ.get('SALES_TARGET_FILE', r'I:/Production/01 Cor&Fiber Production/14-手工排产/AI排产文件夹/销售目标.xlsx')


@app.route('/api/sales-target')
def api_sales_target():
    """从数据库读取销售目标、工单目标、工时目标数据"""
    try:
        now_month = date.today().strftime('%Y-%m')

        conn_erp = pymysql.connect(
            host=MYSQL_HOST, port=MYSQL_PORT, user=MYSQL_USER,
            password=MYSQL_PASSWORD, database='erp_data',
            charset='utf8mb4', connect_timeout=10
        )
        cursor = conn_erp.cursor()

        cursor.execute('SELECT target_month, target_amount, target_order_qty, target_total_hours FROM hmlv_sales_target_v2 ORDER BY target_month')
        rows = cursor.fetchall()

        target_amount = 0.0
        target_jobs = 0
        target_hours = 0.0
        all_targets = []

        for r in rows:
            entry = {
                'month': r[0],
                'target': float(r[1] or 0),
                'target_jobs': int(r[2]) if r[2] is not None else 0,
                'target_hours': float(r[3] or 0)
            }
            all_targets.append(entry)
            if r[0] == now_month:
                target_amount = entry['target']
                target_jobs = entry['target_jobs']
                target_hours = entry['target_hours']

        cursor.close()
        conn_erp.close()

        return jsonify({
            'success': True,
            'month': now_month,
            'target': target_amount,
            'target_jobs': target_jobs,
            'target_hours': target_hours,
            'all_targets': all_targets
        })
    except Exception as e:
        import traceback
        return jsonify({'success': False, 'error': str(e), 'trace': traceback.format_exc()}), 500


@app.route('/api/hours-daily')
def api_hours_daily():
    """
    每日工时完成进度（累计）
    返回当月每个工作日的：当日目标工时、累计目标率、当日完成工时、累计完成率
    """
    try:
        import pandas as pd
        from calendar import monthrange

        year, month = date.today().year, date.today().month
        days_in_month = monthrange(year, month)[1]
        today_day = date.today().day

        # ========== 总目标工时（从数据库 erp_data.hmlv_production_schedule 计算）==========
        # ★ 仅统计ship_date在当前月份的工单，使用 work_hours_h（工单总工时）
        df_erp = get_erp_schedule()
        now_month_daily = date.today().strftime('%Y-%m')
        df_this_month_daily = df_erp[df_erp['_month'] == now_month_daily]
        # ★ 按 job 去重，避免重复记录导致目标工时翻倍
        df_this_month_daily_dedup = df_this_month_daily.drop_duplicates(subset=['job'], keep='first')

        def calc_total_hours_from_db(df_jobs):
            """使用 qty × cycle_time_h 计算总工时（仅当月ship_date工单）"""
            total = 0.0
            for _, row in df_jobs.iterrows():
                qty = float(row['qty']) if pd.notna(row['qty']) else 0.0
                ct = float(row['cycle_time_h']) if pd.notna(row['cycle_time_h']) else 0.0
                total += qty * ct
            return total

        total_target = round(calc_total_hours_from_db(df_this_month_daily_dedup), 2)

        # ========== 工作日判断 ==========
        HOLIDAYS_2026 = {
            date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 3),
            date(2026, 2, 15), date(2026, 2, 16), date(2026, 2, 17), date(2026, 2, 18),
            date(2026, 2, 19), date(2026, 2, 20), date(2026, 2, 21), date(2026, 2, 22),
            date(2026, 2, 23),
            date(2026, 4, 4), date(2026, 4, 5), date(2026, 4, 6),
            date(2026, 5, 1), date(2026, 5, 2), date(2026, 5, 3), date(2026, 5, 4),
            date(2026, 5, 5),
            date(2026, 6, 19), date(2026, 6, 20), date(2026, 6, 21),
            date(2026, 9, 25), date(2026, 9, 26), date(2026, 9, 27),
            date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 3), date(2026, 10, 4),
            date(2026, 10, 5), date(2026, 10, 6), date(2026, 10, 7),
        }
        EXTRA_WORKDAYS_2026 = {
            date(2026, 1, 4), date(2026, 2, 14), date(2026, 2, 28),
            date(2026, 5, 9), date(2026, 9, 20), date(2026, 10, 10),
        }

        def is_workday(d):
            if d in HOLIDAYS_2026: return False
            if d in EXTRA_WORKDAYS_2026: return True
            return d.weekday() < 5

        # 当月工作日列表
        workdays = [date(year, month, d) for d in range(1, days_in_month + 1) if is_workday(date(year, month, d))]
        workdays_in_month = len(workdays)
        daily_target = round(total_target / workdays_in_month, 2) if workdays_in_month > 0 else 0

        # ========== 从数据库获取当月每日完成工时 ==========
        conn = pymysql.connect(host=MYSQL_HOST, port=MYSQL_PORT, user=MYSQL_USER, password=MYSQL_PASSWORD, database=MYSQL_DATABASE, charset='utf8mb4', connect_timeout=10)
        cursor = conn.cursor()

        current_month = date.today().strftime('%Y-%m')
        cursor.execute(
            "SELECT Job, Station, CompleteDate FROM production_records "
            "WHERE SiteRef = 'NAIGROUP_PROD_310' AND CompleteDate IS NOT NULL AND CompleteDate LIKE %s",
            (current_month + '%',)
        )
        rows = cursor.fetchall()
        conn.close()

        # 构建 Job→qty/cycle_time_h 映射（使用 qty × cycle_time_h，仅当月ship_date工单）
        job_hours_map_daily = {}  # job -> (qty, cycle_time_h)
        for _, r in df_this_month_daily.iterrows():
            j = str(r['job']).strip()
            qty = float(r['qty']) if pd.notna(r['qty']) else 0.0
            ct = float(r['cycle_time_h']) if pd.notna(r['cycle_time_h']) else 0.0
            if j and j not in job_hours_map_daily:
                job_hours_map_daily[j] = (qty, ct)

        # 按日期汇总完成工时
        # 策略：每条工序记录都代表该工单完成了某道工序，计入当日完成工时
        # 工时 = qty × cycle_time_h（单根时间 × 工单数），不去重
        daily_completed = {}  # date_str -> hours
        for row in rows:
            job = str(row[0]).strip().upper()
            cd = parse_complete_date(row[2])
            if not cd:
                continue
            day_str = cd.strftime('%Y-%m-%d')

            pair = job_hours_map_daily.get(job, (0, 0))
            hours = pair[0] * pair[1]
            if hours == 0:
                continue

            if day_str not in daily_completed:
                daily_completed[day_str] = 0
            daily_completed[day_str] += hours

        # ========== 构建每日累计进度 ==========
        daily_list = []
        cum_target = 0
        cum_actual = 0

        for wd in workdays:
            if wd.day > today_day:
                break  # 还没到的日期不显示
            wd_str = wd.strftime('%Y-%m-%d')
            cum_target += daily_target
            day_actual = daily_completed.get(wd_str, 0)
            cum_actual += day_actual

            daily_list.append({
                'date': wd_str,
                'date_short': f'{month}/{wd.day}',
                'weekday': ['周一','周二','周三','周四','周五','周六','周日'][wd.weekday()],
                'daily_target': daily_target,
                'cum_target': round(cum_target, 2),
                'target_rate': round(cum_target / total_target * 100, 1) if total_target > 0 else 0,
                'daily_actual': round(day_actual, 2),
                'cum_actual': round(cum_actual, 2),
                'actual_rate': round(cum_actual / total_target * 100, 1) if total_target > 0 else 0,
            })

        return jsonify({
            'success': True,
            'data': {
                'total_target': total_target,
                'workdays_in_month': workdays_in_month,
                'daily_target': daily_target,
                'daily_list': daily_list,
            }
        })

    except Exception as e:
        import traceback
        return jsonify({'success': False, 'error': str(e), 'trace': traceback.format_exc()}), 500


@app.route('/')
def index():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    return send_from_directory(base_dir, 'HMLV生产看板.html')


if __name__ == '__main__':
    port = int(os.environ.get('PORT', '5678'))
    print("=" * 50)
    print("WIPTrack 实时看板服务器启动中...")
    print(f"访问地址: http://localhost:{port}")
    print(f"数据接口: http://localhost:{port}/api/data")
    print("按 Ctrl+C 停止服务器")
    print("=" * 50)
    app.run(host='0.0.0.0', port=port, debug=False)
