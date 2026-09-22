# -*- coding: utf-8 -*-
"""
WIPTrack 实时数据 API 服务器
访问 http://localhost:5678/api/data 获取最新数据
"""

import json
import re
import pymysql
from datetime import datetime, date, timedelta
from flask import Flask, jsonify, send_from_directory, request
from flask_cors import CORS
import os

app = Flask(__name__)
CORS(app)  # 允许跨域请求

MYSQL_HOST = os.environ.get('MYSQL_HOST', '10.0.6.86')
MYSQL_PORT = int(os.environ.get('MYSQL_PORT', 33306))
MYSQL_USER = os.environ.get('MYSQL_USER', 'powerbi')
MYSQL_PASSWORD = os.environ.get('MYSQL_PASSWORD', '!Q1234567')
MYSQL_DATABASE = os.environ.get('MYSQL_DATABASE', 'wiptrack')

# --- csi_datawarehouse (MS SQL Server) 库存数据源 ---
# 用于 WIP 缺料异常校验：异常标注"缺料/Shortage"时，到 dbo.SLItemLoc 核对物料是否实际有库存
CSI_SQL_SERVER = os.environ.get('CSI_SQL_SERVER', r'SUZVPRINT01\CUSTOMSSYS')
CSI_SQL_DATABASE = os.environ.get('CSI_SQL_DATABASE', 'csi_datawarehouse')
CSI_SQL_USER = os.environ.get('CSI_SQL_USER', 'naipowerbiuser')
CSI_SQL_PASSWORD = os.environ.get('CSI_SQL_PASSWORD', 'LT8QGjMn8XwAjp')
CSI_SQL_DRIVER = os.environ.get('CSI_SQL_DRIVER', 'ODBC Driver 17 for SQL Server')
# 判定"有库存"的最小数量（> 该值才标黄）；默认 0，即只要库存 > 0 就标黄
CSI_STOCK_MIN_QTY = float(os.environ.get('CSI_STOCK_MIN_QTY', '0') or 0)

# --- 站点配置 ---
SITE_CONFIG = {
    '310': {'SiteRef': 'NAIGROUP_PROD_310', 'site_ref': 310, 'name': 'HMLV生产看板', 'name_en': 'HMLV Production Kanban'},
    '410': {'SiteRef': 'NAIGROUP_PROD_410', 'site_ref': 410, 'name': 'Penang生产看板', 'name_en': 'Penang Production Kanban'},
}
DEFAULT_SITE = '310'

def get_site_config(site_code):
    """获取站点配置，无效时fallback到默认310"""
    return SITE_CONFIG.get(site_code, SITE_CONFIG[DEFAULT_SITE])

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


def normalize_station_key(raw, default=''):
    """工序名 → 英文key（大小写不敏感）。
    数据库 Station 列可能混存 '预处理 Pretreat' / '预处理 pretreat' 等大小写变体，
    先精确查表，找不到再按 lower 遍历，最后回退 default。"""
    if raw is None:
        return default
    s = str(raw).strip()
    if s in STATION_CN_TO_KEY:
        return STATION_CN_TO_KEY[s]
    s_lower = s.lower()
    for k, v in STATION_CN_TO_KEY.items():
        if str(k).strip().lower() == s_lower:
            return v
    return default


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


def count_workdays(start_date, end_date):
    """
    计算两个日期之间的工作日天数（排除周六和周日）。
    start_date: datetime，前一道工序完成日期
    end_date: datetime，截止日期（今天）
    返回: int，工作日天数
    """
    if start_date >= end_date:
        return 0
    count = 0
    current = start_date.date() if isinstance(start_date, datetime) else start_date
    end = end_date.date() if isinstance(end_date, datetime) else end_date
    while current < end:
        if current.weekday() < 5:  # 0=周一 ... 4=周五，5=周六，6=周日
            count += 1
        current += timedelta(days=1)
    return count


# ===================== WIP 缺料异常 × SLItemLoc 库存校验 =====================
# 物料编码规则：1 个大写字母 + 4~7 位数字（如 A080875 / C030273 / M0065）
_MATERIAL_CODE_RE = re.compile(r'(?<![A-Za-z0-9])([A-Z]\d{4,7})(?![A-Za-z0-9])')
# 缺料关键词（中文 + 英文，含常见拼写变体；避免用裸 "short" 以免误判测试站电性能不良）
_SHORTAGE_RE = re.compile(
    r'缺料|缺线|缺线材|shortage|shoratage|short\s+of|out\s+of\s+stock|no\s+stock',
    re.IGNORECASE
)


def _is_material_shortage(exception_type, description):
    """判断异常是否为缺料类。exception_type=material_shortage 或描述含缺料关键词。"""
    if str(exception_type or '').strip().lower() == 'material_shortage':
        return True
    return bool(_SHORTAGE_RE.search(description or ''))


def _extract_material_codes(materials_json, description):
    """提取异常涉及的物料编码列表。
    优先用 wip_exceptions.materials（JSON 数组），为空时回退到描述文本正则提取。
    materials 支持两种元素：
      - 字符串: ["C030273", ...]
      - 对象:   [{"item":"D000166","qty":79,"unit":"PCS"}, ...] → 取 item 字段"""
    codes = []
    if materials_json:
        raw = materials_json
        if isinstance(raw, (bytes, bytearray)):
            try:
                raw = raw.decode('utf-8', 'ignore')
            except Exception:
                raw = str(raw)
        try:
            val = json.loads(raw)
            if isinstance(val, list):
                for x in val:
                    if isinstance(x, dict):
                        # 对象元素：item/ITEM 字段是物料号
                        code = x.get('item') or x.get('ITEM') or x.get('Item')
                        if code:
                            codes.append(str(code).strip().upper())
                    elif str(x).strip():
                        codes.append(str(x).strip().upper())
            elif isinstance(val, str) and val.strip():
                codes = [val.strip().upper()]
        except Exception:
            # materials 不是合法 JSON 时，也尝试直接正则提取
            codes = [m.group(1).upper() for m in _MATERIAL_CODE_RE.finditer(str(raw))]
    if not codes:
        codes = [m.group(1).upper() for m in _MATERIAL_CODE_RE.finditer(description or '')]
    # 去重且保持顺序
    return list(dict.fromkeys(codes))


def _parse_material_qty_map(materials_json):
    """解析 materials JSON 中对象元素自带的数量，返回 {物料: 数量}。
    如 [{"item":"D000166","qty":79,"unit":"PCS"}] → {'D000166': 79.0}
    仅对象元素带 qty 时才有值；普通字符串数组返回 {}。"""
    if not materials_json:
        return {}
    raw = materials_json
    if isinstance(raw, (bytes, bytearray)):
        try:
            raw = raw.decode('utf-8', 'ignore')
        except Exception:
            raw = str(raw)
    result = {}
    try:
        val = json.loads(raw)
        if isinstance(val, list):
            for x in val:
                if isinstance(x, dict):
                    code = x.get('item') or x.get('ITEM') or x.get('Item')
                    qty = x.get('qty', x.get('QTY'))
                    if code is not None and qty is not None:
                        try:
                            result[str(code).strip().upper()] = float(qty)
                        except Exception:
                            pass
    except Exception:
        pass
    return result


def get_material_stock(site_code, items, exclude_mrb=True, timeout=8):
    """查询 csi_datawarehouse.dbo.SLItemLoc，返回 {物料编码: 可用库存数量}（仅含 >0）。
    - site_code: '310' / '410'，对应 SLItemLoc.SiteRef（310 只查 310 库存，410 只查 410）
    - 库存口径（2026-09-19 确立，2026-09-22 复核后维持不变）：MrbFlag=0（排除报废/待判）
      + PermFlag=0（排除永久库位）+ Loc NOT LIKE '%floor%'（排除 FLOOR-Core/SPO2/CLR 等线边仓）
    - 口径依据（2026-09-22 业务确认）：**线边仓视为线上已占用，不算可用库存**；
      永久库位（PermFlag=1）同样不计入。因此"缺料但有库存"的黄色告警只在
      常规可领用库位（如 STOCK，且 PermFlag=0）库存充足时才出现。
    - 已知现象（非 bug）：物料在 STOCK 与 FLOOR-Core 之间移动会导致该工单在黄/红之间切换。
      案例 J000035104-0000 / D070323：库存 3000 在 FLOOR-Core（被排除）→ 显示红色，属预期。
    查询失败（网络/驱动/权限等）时返回空 dict，不影响主流程。"""
    items = [str(i).strip().upper() for i in items if str(i).strip()]
    if not items:
        return {}
    try:
        import pyodbc
        conn_str = (
            f"DRIVER={{{CSI_SQL_DRIVER}}};SERVER={CSI_SQL_SERVER};DATABASE={CSI_SQL_DATABASE};"
            f"UID={CSI_SQL_USER};PWD={CSI_SQL_PASSWORD};TrustServerCertificate=yes;Encrypt=no;"
            f"Connection Timeout={timeout}"
        )
        conn = pyodbc.connect(conn_str, timeout=timeout)
        cur = conn.cursor()
        result = {}
        for i in range(0, len(items), 500):
            chunk = items[i:i + 500]
            placeholders = ','.join(['?'] * len(chunk))
            sql = (
                f"SELECT Item, SUM(QtyOnHand) AS qty FROM dbo.SLItemLoc "
                f"WHERE SiteRef = ? AND Item IN ({placeholders})"
                + (" AND MrbFlag = 0" if exclude_mrb else "")
                + " AND PermFlag = 0 AND Loc NOT LIKE '%floor%'"
                + " GROUP BY Item"
            )
            cur.execute(sql, [str(site_code)] + chunk)
            for it, qty in cur.fetchall():
                try:
                    q = float(qty) if qty is not None else 0.0
                except Exception:
                    q = 0.0
                if q > CSI_STOCK_MIN_QTY:
                    result[str(it).strip().upper()] = q
        conn.close()
        return result
    except Exception as e:
        print(f"[WARN] SLItemLoc stock query failed (site={site_code}): {e}")
        return {}


# 缺料数量：数字 + 单位（PCS/FT/根/条/个 等；必须带单位，纯数字不算，避免误抓"只能产出3"之类）
_QTY_UNIT_RE = re.compile(r'(\d+(?:\.\d+)?)\s*(?:pcs?|ft|feet|meters?|mtrs?|米|根|条|个|set|sets?|kg)(?![A-Za-z0-9])', re.IGNORECASE)
# 中文数字 + 单位（如 "还有三根缺料D080053"）
_CN_NUM_MAP = {'一': 1, '二': 2, '两': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9}
_CN_QTY_RE = re.compile(r'([一二两三四五六七八九十]+)\s*(?:根|条|个|pcs?|条|米)')


def _parse_qty_number(text):
    """从文本中解析第一个 '数量+单位'（如 2PCS / 13.4FT / 三根），返回 float；无则 None。"""
    m = _QTY_UNIT_RE.search(text or '')
    if m:
        try:
            return float(m.group(1))
        except Exception:
            return None
    m = _CN_QTY_RE.search(text or '')
    if m:
        cn = m.group(1)
        if '十' in cn:
            left, _, right = cn.partition('十')
            left_v = _CN_NUM_MAP.get(left, 1) if left else 1
            right_v = _CN_NUM_MAP.get(right, 0) if right else 0
            return float(left_v * 10 + right_v)
        val = 0
        for ch in cn:
            val = val * 10 + _CN_NUM_MAP.get(ch, 0)
        return float(val) if val else None
    return None


def _find_qty_in(text, take_last=False):
    """在文本中按位置找数量（数字+单位 或 中文数字+单位），返回 float；无则 None。
    take_last=True 时取位置最靠后的一个（即最靠近物料号/最靠近末尾的）。"""
    text = text or ''
    cands = []
    for m in _QTY_UNIT_RE.finditer(text):
        cands.append((m.start(), m.group(1), False))
    for m in _CN_QTY_RE.finditer(text):
        cands.append((m.start(), m.group(0), True))
    if not cands:
        return None
    cands.sort(key=lambda x: x[0])
    pick = cands[-1] if take_last else cands[0]
    if pick[2]:
        return _parse_qty_number(pick[1])
    try:
        return float(pick[1])
    except Exception:
        return None


def _extract_material_quantities(material_codes, description):
    """解析每个物料的缺料数量，返回 {物料: 数量}。
    解析顺序（对每个物料）：
      1) 物料号后 25 字符内的 数量+单位，取最靠前的（如 'D041134 (75PCS)'、'C030273 - 2PCS'）
      2) 物料号前 18 字符内的 数量+单位，取最靠后的（最靠近物料号，如 '还有2PCS缺料H000198'）
      3) 兜底：整个描述里位置最靠后的 数量+单位
    完全解析不到数量时返回 {}——按规则该异常不参与库存匹配。"""
    desc = str(description or '')
    if not desc:
        return {}
    upper = desc.upper()
    result = {}
    for code in material_codes:
        pos = upper.find(code)
        if pos >= 0:
            after = desc[pos + len(code): pos + len(code) + 25]
            q = _find_qty_in(after, take_last=False)
            if q is not None:
                result[code] = q
                continue
            before = desc[max(0, pos - 18): pos]
            q = _find_qty_in(before, take_last=True)
            if q is not None:
                result[code] = q
    if not result:
        # 兜底：整个描述里位置最靠后的数量，平摊给所有物料
        q = _find_qty_in(desc, take_last=True)
        if q is not None:
            result = {c: q for c in material_codes}
    return result


def annotate_exceptions_with_stock(exc_by_job, site_code):
    """给缺料异常补充库存信息（2026-09-19 按数量匹配版）：
    - 仅当异常能解析出缺料数量（materials JSON 自带 qty 或描述文本解析）时才参与匹配；无数量的缺料异常直接忽略
    - 黄色条件：该站点(SiteRef)可用库存 QtyOnHand >= 缺料数量（每个有数量的物料都要满足）
    - 每个异常条目增加 need_qty({物料: 缺料数量}) / stock_items({物料: 库存}) / stock_ok(bool)
    - 返回 stock_alert_jobs: set(job)，即"库存足够却标缺料"的工单集合"""
    # 1) 解析每条缺料异常的数量（materials JSON 的 qty 优先，描述文本解析兜底）
    for lst in exc_by_job.values():
        for e in lst:
            e['need_qty'] = {}
            if e.get('shortage') and e.get('materials'):
                json_qty = e.get('materials_qty') or {}
                if json_qty:
                    e['need_qty'] = dict(json_qty)
                else:
                    e['need_qty'] = _extract_material_quantities(e['materials'], e.get('description', ''))
    all_codes = set()
    for lst in exc_by_job.values():
        for e in lst:
            if e['need_qty']:
                all_codes.update(e['need_qty'].keys())

    # 2) 只查有数量的物料库存（310 只查 310 的库存，410 只查 410）
    stock_map = get_material_stock(site_code, all_codes) if all_codes else {}

    # 3) 逐条比对：库存 >= 缺料数量 → 黄色
    stock_alert_jobs = set()
    for job, lst in exc_by_job.items():
        for e in lst:
            need = e.get('need_qty') or {}
            if not need:
                e['stock_items'] = {}
                e['stock_ok'] = False
                continue
            hits = {}
            ok = True
            for code, qty in need.items():
                stock = stock_map.get(code, 0.0)
                hits[code] = stock
                if stock < qty:
                    ok = False
            e['stock_items'] = hits
            e['stock_ok'] = ok
            if ok:
                stock_alert_jobs.add(job)
    return stock_alert_jobs


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
        station_en = normalize_station_key(station_raw, '')
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
        station_en = normalize_station_key(station_raw, '')
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


def get_pack_completed_jobs(month=None, site_config=None):
    """
    从 production_records 表中获取已完成最后一道工序（包装 Package）的工单集合。
    month: 格式 'YYYY-MM'，不传则返回所有月份的。
    site_config: 站点配置 {'SiteRef': '...', 'site_ref': ...}
    返回: set of job strings
    """
    if site_config is None:
        site_config = SITE_CONFIG[DEFAULT_SITE]
    conn = pymysql.connect(host=MYSQL_HOST, port=MYSQL_PORT, user=MYSQL_USER, password=MYSQL_PASSWORD, database=MYSQL_DATABASE, charset='utf8mb4', connect_timeout=10)
    cursor = conn.cursor()
    if month:
        cursor.execute("""
            SELECT DISTINCT pr.Job
            FROM production_records pr
            WHERE pr.Station = '包装 Package'
              AND pr.SiteRef = %s
              AND DATE_FORMAT(pr.CompleteDate, %s) = %s
        """, (site_config['SiteRef'], '%Y-%m', month))
    else:
        cursor.execute("""
            SELECT DISTINCT pr.Job
            FROM production_records pr
            WHERE pr.Station = '包装 Package'
              AND pr.SiteRef = %s
        """, (site_config['SiteRef'],))
    jobs = {str(row[0]).strip().upper() for row in cursor.fetchall()}
    conn.close()
    return jobs


def get_erp_schedule(site_config=None):
    """从 erp_data.hmlv_production_schedule 表读取排程数据"""
    if site_config is None:
        site_config = SITE_CONFIG[DEFAULT_SITE]
    import pandas as pd
    conn = pymysql.connect(host=MYSQL_HOST, port=MYSQL_PORT, user=MYSQL_USER, password=MYSQL_PASSWORD, database=MYSQL_DATABASE, charset='utf8mb4', connect_timeout=10)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT job, item, qty, ship_date, line, work_hours_h,
               unit_price, sales_amount, job_status, tested_qty, wo_total,
               cycle_time_h
        FROM erp_data.hmlv_production_schedule
        WHERE site_ref = %s OR (site_ref IS NULL AND NOT EXISTS (
            SELECT 1 FROM erp_data.hmlv_production_schedule WHERE site_ref = %s
        ))
    """, (site_config['site_ref'], site_config['site_ref']))
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
    if df.empty:
        # 空结果：创建带必要列的 DataFrame，避免后续 KeyError
        df = pd.DataFrame(columns=['job', 'item', 'qty', 'ship_date', 'line',
                                   'work_hours_h', 'unit_price', 'sales_amount',
                                   'job_status', 'tested_qty', 'wo_total', 'cycle_time_h'])
        df['qty'] = pd.Series(dtype='int')
        df['tested_qty'] = pd.Series(dtype='int')
        df['wo_total'] = pd.Series(dtype='int')
        df['sales_amount'] = pd.Series(dtype='float')
        df['unit_price'] = pd.Series(dtype='float')
        df['work_hours_h'] = pd.Series(dtype='float')
        df['cycle_time_h'] = pd.Series(dtype='float')
        df['_dt'] = pd.Series(dtype='datetime64[ns]')
        df['_month'] = pd.Series(dtype='str')
    else:
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


def get_data(site_config=None):
    if site_config is None:
        site_config = SITE_CONFIG[DEFAULT_SITE]
    conn = pymysql.connect(host=MYSQL_HOST, port=MYSQL_PORT, user=MYSQL_USER, password=MYSQL_PASSWORD, database=MYSQL_DATABASE, charset='utf8mb4', connect_timeout=10)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM production_records WHERE SiteRef = %s ORDER BY id", (site_config['SiteRef'],))
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
        site = request.args.get('site', DEFAULT_SITE)
        cfg = get_site_config(site)

        import pandas as pd
        columns, rows = get_data(cfg)

        # 加载 ERP 排程数据（用于当月工单数计算）
        df_all = get_erp_schedule(cfg)

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
            station_en = normalize_station_key(sv, '')
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

        # ---- 工序甘特/流程 WIP（基于 production_records 全部工单）----
        # 全部有生产记录的工单，Pack 已完成的自然被 WIP 公式排除
        all_prod_jobs = set()
        station_done_all = {s: set() for s in STATION_ORDER}
        # 记录每道工序每个工单的最早完成时间（用于滞留计算）
        station_job_dates = {s: {} for s in STATION_ORDER}  # station → {job: earliest_date}
        for r in rows:
            job = str(r.get(job_col, '') or '').strip().upper()
            all_prod_jobs.add(job)
            sv = str(r.get(station_col, '') or '').strip()
            station_en = normalize_station_key(sv, '')
            if station_en in STATION_ORDER:
                station_done_all[station_en].add(job)
                dv = str(r.get(date_col, '') or '').strip()
                if dv:
                    try:
                        dt = datetime.strptime(dv[:10], '%Y-%m-%d')
                        if job not in station_job_dates[station_en] or dt < station_job_dates[station_en][job]:
                            station_job_dates[station_en][job] = dt
                    except:
                        pass

        # 加载排产表 Item/Line 映射
        job_item_map_data = {}
        job_line_map_data = {}
        try:
            for _, row in df_all.iterrows():
                j = str(row['job']).strip().upper()
                if not j or j == 'nan':
                    continue
                item = str(row['item']).strip() if pd.notna(row['item']) else ''
                if item and item != 'nan':
                    job_item_map_data[j] = item
                line = str(row['line']).strip() if pd.notna(row['line']) else ''
                if line and line != 'nan':
                    job_line_map_data[j] = line
        except Exception:
            pass

        base_all = len(all_prod_jobs) if len(all_prod_jobs) > 0 else 1

        wip_by_station = []
        today = datetime.now()
        for i, s in enumerate(STATION_ORDER):
            # 在制品 = 前一道工序完成的工单数 - 当前工序完成的工单数
            if i == 0:
                prev_done = base_all
                prev_s_key = None
            else:
                prev_s = STATION_ORDER[i-1]
                prev_done = len(station_done_all[prev_s])
                prev_s_key = prev_s

            cur_done = len(station_done_all[s])
            wip = max(0, prev_done - cur_done)

            # ---- 计算滞留天数 ----
            # 滞留工单：已完成前一道工序但尚未完成当前工序的工单
            prev_jobs_set = station_done_all[prev_s_key] if prev_s_key else all_prod_jobs
            cur_jobs_set = station_done_all[s]
            滞留_jobs = prev_jobs_set - cur_jobs_set

            滞留_details = []
            total_days = 0
            count_with_date = 0
            
            for job_id in 滞留_jobs:
                # 获取前一道工序完成日期
                prev_complete_date = None
                if prev_s_key:
                    prev_complete_date = station_job_dates.get(prev_s_key, {}).get(job_id)
                
                if prev_complete_date:
                    days = count_workdays(prev_complete_date, today)
                    total_days += days
                    count_with_date += 1
                    dwell_hours = round(days * 8, 1)
                    complete_str = prev_complete_date.strftime('%Y-%m-%d')
                else:
                    dwell_hours = 0
                    complete_str = ''
                
                滞留_details.append({
                    'job': job_id,
                    'item': job_item_map_data.get(job_id, ''),
                    'line': job_line_map_data.get(job_id, ''),
                    'prev_complete': complete_str,
                    'dwell_hours': dwell_hours,
                    'dwell_days': days if prev_complete_date else 0,
                })
            
            # 按滞留时间降序
            滞留_details.sort(key=lambda x: -x.get('dwell_hours', 0))
            
            avg_days = round(total_days / count_with_date, 1) if count_with_date > 0 else 0
            
            wip_by_station.append({
                'station': s,
                'label': STATION_LABEL.get(s, s),
                'wip': wip,
                '滞留_count': len(滞留_jobs),
                '滞留_avg_days': avg_days,
                '滞留_details': 滞留_details,
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

        site = request.args.get('site', DEFAULT_SITE)
        cfg = get_site_config(site)

        # ========== 日期筛选：支持传入指定日期查看当日工序完成情况 ==========
        date_param = request.args.get('date', '').strip()
        if date_param:
            try:
                from datetime import datetime as dt
                filter_date = dt.strptime(date_param, '%Y-%m-%d').date()
            except ValueError:
                filter_date = date.today()
        else:
            filter_date = date.today()
        filter_date_str = filter_date.strftime('%Y-%m-%d')

        # ========== 从数据库读取生产排程数据 ==========
        df_all = get_erp_schedule(cfg)
        now_month = date.today().strftime('%Y-%m')

        # 当月数据
        df_this = df_all[df_all['_month'] == now_month]

        # ========== 获取当月已完成（包装Package完成）的工单集合 ==========
        pack_completed_all = get_pack_completed_jobs(site_config=cfg)  # 所有月份
        pack_completed_this_month = get_pack_completed_jobs(now_month, cfg)  # 当月

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
        columns_pr, records = get_data(cfg)

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
            station_en = normalize_station_key(station_raw, '')
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

        # 对当月排产 JOB，统计各工序完成情况（截至筛选日期）
        # 当选择了日期时，只计算 CompleteDate ≤ filter_date 的完成记录
        station_done_for_month = {st: set() for st in STATION_LIST}
        for r in records:
            job = str(r.get(job_col, '') or '').strip().upper()
            if job not in all_month_jobs:
                continue
            sv = str(r.get(station_col, '') or '').strip()
            station_en = normalize_station_key(sv, '')
            if station_en in STATION_LIST:
                # 日期筛选：只计入 CompleteDate ≤ filter_date 的记录
                dv = str(r.get(date_col, '') or '').strip()
                if dv:
                    # 截取日期部分（前10字符 YYYY-MM-DD）
                    rec_date = dv[:10]
                    if rec_date > filter_date_str:
                        continue
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
        completed_jobs = all_month_jobs & pack_completed_all
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

        # ========== 各工序累计完成工单数（截至筛选日期）==========
        station_jobs_cum = {}
        for st in STATION_LIST:
            station_jobs_cum[st] = len(station_done_for_month[st])
        result['station_jobs_cum'] = station_jobs_cum

        # ========== 每个工序指定日期完成的工时 ==========
        # 逻辑：指定日期工序工时 = 该日期完成该工序的所有记录.qty × cycle_time_h 的累加（不去重，每条记录独立计算）
        station_hours_today = {}
        for st in STATION_LIST:
            hours = 0.0
            for r in records:
                dv = str(r.get(date_col, '') or '').strip()
                if not dv.startswith(filter_date_str):
                    continue
                station_cn = str(r.get(station_col, '') or '').strip()
                station_en = normalize_station_key(station_cn, station_cn)
                if station_en == st:
                    job = str(r.get(job_col, '') or '').strip()
                    if job:
                        hours += job_hours_map.get(job, 0)
            station_hours_today[st] = round(hours, 2)
        result['station_hours_today'] = station_hours_today

        # ========== 每个工序指定日期完成的销售（按工单去重）==========
        station_sales_today = {}
        for st in STATION_LIST:
            today_jobs_set = set()
            for r in records:
                dv = str(r.get(date_col, '') or '').strip()
                if not dv.startswith(filter_date_str):
                    continue
                station_cn = str(r.get(station_col, '') or '').strip()
                station_en = normalize_station_key(station_cn, station_cn)
                if station_en == st:
                    job = str(r.get(job_col, '') or '').strip().upper()
                    if job:
                        today_jobs_set.add(job)
            sales = 0.0
            for job in today_jobs_set:
                sales += job_sales_map.get(job, 0)
            station_sales_today[st] = round(sales, 2)
        result['station_sales_today'] = station_sales_today

        # ========== 每个工序指定日期完成的工单数量（按工单去重）==========
        station_jobs_today = {}
        for st in STATION_LIST:
            today_jobs_set = set()
            for r in records:
                dv = str(r.get(date_col, '') or '').strip()
                if not dv.startswith(filter_date_str):
                    continue
                station_cn = str(r.get(station_col, '') or '').strip()
                station_en = normalize_station_key(station_cn, station_cn)
                if station_en == st:
                    job = str(r.get(job_col, '') or '').strip().upper()
                    if job:
                        today_jobs_set.add(job)
            station_jobs_today[st] = len(today_jobs_set)
        result['station_jobs_today'] = station_jobs_today

        # ========== 销售统计（按 job 去重）==========
        df_this_month_dedup = df_this_month.drop_duplicates(subset=['job'], keep='first')
        total_sales = float(df_this_month_dedup['sales_amount'].sum())
        # 用 pack_completed 判断，不再依赖 job_status
        completed_mask = df_this_month_dedup['job'].str.strip().isin(pack_completed_all)
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

        # 已完成工时：ship_date 在当月 且 已完成 Package（不限完成月份）
        cursor_hours.execute("""
            SELECT SUM(ps.qty * ps.cycle_time_h)
            FROM erp_data.hmlv_production_schedule ps
            WHERE ps.site_ref = %s
              AND DATE_FORMAT(ps.ship_date, %s) = %s
              AND ps.job COLLATE utf8mb4_unicode_ci IN (
                  SELECT DISTINCT pr.Job FROM production_records pr
                  WHERE pr.Station = '包装 Package'
                    AND pr.SiteRef = %s
              )
        """, (cfg['site_ref'], '%Y-%m', now_month, cfg['SiteRef']))
        completed_asm_hours = round(float(cursor_hours.fetchone()[0] or 0), 2)
        cursor_hours.execute("""
            SELECT COUNT(DISTINCT ps.job)
            FROM erp_data.hmlv_production_schedule ps
            WHERE ps.site_ref = %s
              AND DATE_FORMAT(ps.ship_date, %s) = %s
              AND ps.job COLLATE utf8mb4_unicode_ci IN (
                  SELECT DISTINCT pr.Job FROM production_records pr
                  WHERE pr.Station = '包装 Package'
                    AND pr.SiteRef = %s
              )
        """, (cfg['site_ref'], '%Y-%m', now_month, cfg['SiteRef']))
        completed_asm_count = cursor_hours.fetchone()[0] or 0

        # 未完成工时：ship_date在当月 且 未完成 Package
        cursor_hours.execute("""
            SELECT SUM(ps.qty * ps.cycle_time_h)
            FROM erp_data.hmlv_production_schedule ps
            WHERE ps.site_ref = %s
              AND DATE_FORMAT(ps.ship_date, %s) = %s
              AND ps.job COLLATE utf8mb4_unicode_ci NOT IN (
                  SELECT DISTINCT pr.Job FROM production_records pr
                  WHERE pr.Station = '包装 Package'
                    AND pr.SiteRef = %s
              )
        """, (cfg['site_ref'], '%Y-%m', now_month, cfg['SiteRef']))
        pending_asm_hours = round(float(cursor_hours.fetchone()[0] or 0), 2)
        cursor_hours.execute("""
            SELECT COUNT(DISTINCT ps.job)
            FROM erp_data.hmlv_production_schedule ps
            WHERE ps.site_ref = %s
              AND DATE_FORMAT(ps.ship_date, %s) = %s
              AND ps.job COLLATE utf8mb4_unicode_ci NOT IN (
                  SELECT DISTINCT pr.Job FROM production_records pr
                  WHERE pr.Station = '包装 Package'
                    AND pr.SiteRef = %s
              )
        """, (cfg['site_ref'], '%Y-%m', now_month, cfg['SiteRef']))
        pending_asm_count = cursor_hours.fetchone()[0] or 0

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

        result['filter_date'] = filter_date.strftime('%Y-%m-%d')
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

        site = request.args.get('site', DEFAULT_SITE)
        cfg = get_site_config(site)

        conn = pymysql.connect(host=MYSQL_HOST, port=MYSQL_PORT, user=MYSQL_USER, password=MYSQL_PASSWORD, database=MYSQL_DATABASE, charset='utf8mb4', connect_timeout=10)
        cursor = conn.cursor()

        # 工序顺序（从 site_station 表）
        # 工序流程：Print → Cut → Pre → Asm → Test → Pack
        STATION_ORDER_CN = ['工单打印', '剪线', '预处理', '组装', '测试', '包装']
        STATION_CN_TO_EN = {
            '工单打印': 'Print', '剪线': 'Cut', '预处理': 'Pre',
            '组装': 'Asm', '测试': 'Test', '包装': 'Pack'
        }

        # 获取站点所有记录
        cursor.execute(
            "SELECT Job, Station, CompleteDate FROM production_records "
            "WHERE SiteRef = %s AND CompleteDate IS NOT NULL",
            (cfg['SiteRef'],)
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
            station_en = normalize_station_key(station_raw, '')
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

        # 从数据库 erp_data.hmlv_production_schedule 读取 Job→Item/Line/ShipMonth 映射
        job_item_map = {}
        job_line_map = {}
        job_ship_month_map = {}
        try:
            import pandas as pd
            df_erp = get_erp_schedule(cfg)
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
                ship_month = str(row['_month']).strip() if pd.notna(row['_month']) else ''
                if ship_month and ship_month != 'nan':
                    job_ship_month_map[j] = ship_month
            print(f"[DEBUG] job_item_map loaded {len(job_item_map)} rows, job_line_map loaded {len(job_line_map)} rows, job_ship_month_map loaded {len(job_ship_month_map)} rows")
        except Exception as e:
            print(f"[WARN] ERP data load failed: {e}")

        # 查询异常工单
        exc_by_job = {}
        try:
            cursor.execute(
                "SELECT Station, Job, description, start_time, exception_type, materials FROM wip_exceptions "
                "WHERE SiteRef = %s AND end_time IS NULL",
                (cfg['SiteRef'],)
            )
            exc_rows = cursor.fetchall()
            for er in exc_rows:
                exc_job = str(er[1]).strip().upper()
                exc_station = str(er[0]).strip()
                # 将异常的 station 转为标准英文 key
                exc_station_en = normalize_station_key(exc_station, '')
                if not exc_station_en:
                    m = re.search(r'([A-Za-z]+)', exc_station)
                    if m:
                        eng = m.group(1)
                        eng_map = {'Print':'Print','Cut':'Cut','Pre':'Pre','Asm':'Asm',
                                   'Test':'Test','Pack':'Pack','Cutting':'Cut','Assembly':'Asm',
                                   'Pretreat':'Pre','Package':'Pack','Job':'Print'}
                        exc_station_en = eng_map.get(eng, eng)
                # ★ 工序完成即视为异常已关闭：该异常所属工序在 production_records 中已有完成记录时跳过
                job_stations = job_station_time.get(exc_job, {})
                if exc_station_en and exc_station_en in job_stations:
                    continue
                exc_desc = str(er[2]).strip() if er[2] else ''
                exc_type = str(er[4]).strip().lower() if er[4] else ''
                exc_start = parse_complete_date(er[3])
                is_short = _is_material_shortage(exc_type, exc_desc)
                if exc_job not in exc_by_job:
                    exc_by_job[exc_job] = []
                exc_by_job[exc_job].append({
                    'station': exc_station,
                    'description': exc_desc,
                    'start_time': exc_start.strftime('%m-%d %H:%M') if exc_start else '',
                    'type': exc_type,
                    'shortage': is_short,
                    'materials': _extract_material_codes(er[5], exc_desc) if is_short else [],
                    'materials_qty': _parse_material_qty_map(er[5]) if is_short else {},
                })
        except Exception as e:
            print(f"[WARN] exception query failed: {e}")
            exc_by_job = {}

        conn.close()

        # ★ 缺料异常 × SLItemLoc 库存校验：标注"缺料"但 ERP 实际有库存的 → 黄色告警
        stock_alert_jobs = set()
        try:
            stock_alert_jobs = annotate_exceptions_with_stock(exc_by_job, str(cfg['site_ref']))
            if stock_alert_jobs:
                print(f"[INFO] site={cfg['site_ref']} 缺料但有库存的工单 {len(stock_alert_jobs)} 个")
        except Exception as e:
            print(f"[WARN] stock annotation failed: {e}")

        now = datetime.now()

        # ★ 全部有生产记录的工单（Pack 已完成的自然被排除）
        all_prod_jobs_wip = set(job_station_time.keys())

        result = {}

        # 对每个工序（跳过第一道"工单打印"）：
        # - 完成数：该工序已完成
        # - 滞留数：上道工序已完成但当前工序未完成（以 production_records 为基准）
        for i, station_cn in enumerate(STATION_ORDER_CN):
            if i == 0:
                continue  # 工单打印不需要 WIP
            prev_station_cn = STATION_ORDER_CN[i - 1]
            station_en = STATION_CN_TO_EN.get(station_cn, station_cn)
            prev_station_en = STATION_CN_TO_EN.get(prev_station_cn, prev_station_cn)

            done_in_month = 0
            wip_list = []
            for job in all_prod_jobs_wip:
                stations = job_station_time.get(job, {})
                # 完成数：有生产记录的工单已完成该工序
                if station_en in stations:
                    done_in_month += 1
                # 滞留数：上道工序已完成但当前工序未完成
                elif prev_station_en in stations:
                    prev_t = stations[prev_station_en]
                    # 滞留时间 = 经过的工作日（排除周六日）× 8H
                    delta_days = count_workdays(prev_t, now)
                    dwell_hours = round(delta_days * 8, 1)
                    wip_entry = {
                        'job': job,
                        'item': job_item_map.get(job, ''),
                        'line': job_line_map.get(job, ''),
                        'ship_month': job_ship_month_map.get(job, ''),
                        'complete_time': prev_t.strftime('%Y-%m-%d %H:%M'),
                        'dwell_hours': dwell_hours,
                    }
                    if job in exc_by_job:
                        wip_entry['exception'] = exc_by_job[job]
                        # ★ 缺料但有库存 → 前端黄色告警
                        if job in stock_alert_jobs:
                            wip_entry['stock_alert'] = True
                    wip_list.append(wip_entry)

            # 按滞留时间降序，异常工单置顶
            exc_count = sum(1 for e in wip_list if 'exception' in e)
            stock_alert_count = sum(1 for e in wip_list if e.get('stock_alert'))
            wip_list.sort(key=lambda x: (-(1 if 'exception' in x else 0), -x['dwell_hours']))

            result[station_en] = {
                'label': station_cn,
                'count': len(wip_list),  # 滞留数
                'done_in_month': done_in_month,      # 全部完成数
                'exception_count': exc_count,         # 异常工单数
                'stock_alert_count': stock_alert_count,  # 缺料但有库存的工单数（黄色）
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
    q = (request.args.get('q') or '').strip().upper()
    if not q:
        return jsonify({'success': False, 'error': '请输入工单号'})

    site = request.args.get('site', DEFAULT_SITE)
    cfg = get_site_config(site)

    try:
        conn = pymysql.connect(host=MYSQL_HOST, port=MYSQL_PORT, user=MYSQL_USER, password=MYSQL_PASSWORD, database=MYSQL_DATABASE, charset='utf8mb4', connect_timeout=10)
        cursor = conn.cursor()

        # 用 LIKE 模糊匹配工单号（Job 字段），同时过滤 SiteRef
        cursor.execute(
            "SELECT Job, Station, CompleteDate FROM production_records "
            "WHERE SiteRef = %s AND LOWER(Job) LIKE %s ORDER BY CompleteDate",
            (cfg['SiteRef'], '%' + q.lower() + '%')
        )
        rows = cursor.fetchall()

        if not rows:
            conn.close()
            return jsonify({'success': True, 'found': False, 'q': q, 'results': []})

        # 收集匹配到的工单号（去重）
        matched_jobs = list(set(str(r[0]).strip().upper() for r in rows))

        # 预构建 job → 已完成工序集合（用于判断异常所属工序是否已完成）
        job_completed_stations = {}
        for row in rows:
            j = str(row[0]).strip().upper()
            station_raw = str(row[1]).strip()
            station_en = normalize_station_key(station_raw, '')
            if not station_en:
                import re
                m = re.search(r'([A-Za-z]+)', station_raw)
                if m:
                    eng = m.group(1)
                    eng_map = {'Print':'Print','Cut':'Cut','Pre':'Pre','Asm':'Asm',
                               'Test':'Test','Pack':'Pack','Cutting':'Cut','Assembly':'Asm',
                               'Pretreat':'Pre','Package':'Pack','Job':'Print'}
                    station_en = eng_map.get(eng, eng)
            if station_en:
                if j not in job_completed_stations:
                    job_completed_stations[j] = set()
                job_completed_stations[j].add(station_en)

        # 查询异常信息（活跃 + 已关闭都查，前端区分显示）
        exc_by_job = {}
        if matched_jobs:
            placeholders = ','.join(['%s'] * len(matched_jobs))
            cursor.execute(
                f"SELECT Job, Station, description, start_time, end_time, exception_type, materials FROM wip_exceptions "
                f"WHERE SiteRef = %s AND UPPER(Job) IN ({placeholders}) ORDER BY start_time DESC",
                (cfg['SiteRef'],) + tuple(matched_jobs)
            )
            exc_rows = cursor.fetchall()
            for er in exc_rows:
                exc_job = str(er[0]).strip().upper()
                exc_station = str(er[1]).strip()
                # 将异常的 station 转为标准英文 key
                exc_station_en = normalize_station_key(exc_station, '')
                if not exc_station_en:
                    m = re.search(r'([A-Za-z]+)', exc_station)
                    if m:
                        eng = m.group(1)
                        eng_map = {'Print':'Print','Cut':'Cut','Pre':'Pre','Asm':'Asm',
                                   'Test':'Test','Pack':'Pack','Cutting':'Cut','Assembly':'Asm',
                                   'Pretreat':'Pre','Package':'Pack','Job':'Print'}
                        exc_station_en = eng_map.get(eng, eng)
                exc_desc = str(er[2]).strip() if er[2] else ''
                exc_start = parse_complete_date(er[3])
                exc_end = parse_complete_date(er[4]) if er[4] else None
                exc_type = str(er[5]).strip().lower() if er[5] else ''
                is_short = _is_material_shortage(exc_type, exc_desc)
                # ★ 工序完成即视为异常已关闭：该工序在 production_records 中有完成记录时 active=False
                station_completed = exc_station_en and exc_station_en in job_completed_stations.get(exc_job, set())
                if exc_job not in exc_by_job:
                    exc_by_job[exc_job] = []
                exc_by_job[exc_job].append({
                    'station': exc_station,
                    'description': exc_desc,
                    'start_time': exc_start.strftime('%m-%d %H:%M') if exc_start else '',
                    'active': exc_end is None and not station_completed,  # True=活跃, False=已关闭(含工序完成自动关闭)
                    'type': exc_type,
                    'shortage': is_short,
                    'materials': _extract_material_codes(er[6], exc_desc) if is_short else [],
                    'materials_qty': _parse_material_qty_map(er[6]) if is_short else {},
                })

        conn.close()

        # ★ 缺料异常 × SLItemLoc 库存校验（与 /api/wip 一致）
        try:
            annotate_exceptions_with_stock(exc_by_job, str(cfg['site_ref']))
        except Exception as e:
            print(f"[WARN] search_wo stock annotation failed: {e}")

        # 从数据库 erp_data.hmlv_production_schedule 读取 Job→Line/Item 映射
        job_line_map = {}
        job_item_map = {}
        try:
            import pandas as pd
            df_erp = get_erp_schedule(cfg)
            for _, row in df_erp.iterrows():
                j = str(row['job']).strip()
                if not j or j == 'nan':
                    continue
                line = str(row['line']).strip() if pd.notna(row['line']) else ''
                item = str(row['item']).strip() if pd.notna(row['item']) else ''
                if line and line != 'nan':
                    job_line_map[j] = line
                if item and item != 'nan':
                    job_item_map[j] = item
        except Exception:
            pass

        # 按工单号归组，记录每道工序的完成时间
        job_map = {}
        for row in rows:
            job = str(row[0]).strip().upper()
            station_raw = str(row[1]).strip()
            complete_dt = parse_complete_date(row[2])  # 使用全局函数解析（支持 AM/PM）
            # 统一 station key（大小写不敏感匹配：数据库中可能存 'Pretreat' 也可能存 'pretreat'）
            station_key = normalize_station_key(station_raw, station_raw)
            if not station_key:
                # 大小写不敏感回退
                station_lower = station_raw.lower()
                for k, v in STATION_CN_TO_KEY.items():
                    if k.lower() == station_lower:
                        station_key = v
                        break
            if not station_key:
                station_key = station_raw  # 实在找不到就用原始值
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
                'item': job_item_map.get(job, ''),
                'line': job_line_map.get(job, ''),
                'current_status': current_status,
                'current_station': current_station,
                'steps': steps,
                'exceptions': exc_by_job.get(job, None)  # None=无异常, list=异常列表
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
        site = request.args.get('site', DEFAULT_SITE)
        cfg = get_site_config(site)

        conn_erp = pymysql.connect(
            host=MYSQL_HOST, port=MYSQL_PORT, user=MYSQL_USER,
            password=MYSQL_PASSWORD, database='erp_data',
            charset='utf8mb4', connect_timeout=10
        )
        cursor = conn_erp.cursor()

        cursor.execute(
            'SELECT target_month, target_amount, target_order_qty, target_total_hours FROM hmlv_sales_target_v2 WHERE siteref=%s ORDER BY target_month',
            (cfg['site_ref'],)
        )
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

        site = request.args.get('site', DEFAULT_SITE)
        cfg = get_site_config(site)

        year, month = date.today().year, date.today().month
        days_in_month = monthrange(year, month)[1]
        today_day = date.today().day

        # ========== 总目标工时（从数据库 erp_data.hmlv_production_schedule 计算）==========
        # ★ 仅统计ship_date在当前月份的工单，使用 work_hours_h（工单总工时）
        df_erp = get_erp_schedule(cfg)
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
            "WHERE SiteRef = %s AND CompleteDate IS NOT NULL AND CompleteDate LIKE %s",
            (cfg['SiteRef'], current_month + '%')
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
    html_path = os.path.join(base_dir, 'HMLV生产看板.html')
    resp = send_from_directory(base_dir, 'HMLV生产看板.html')
    # 强制不缓存，避免浏览器沿用旧页面
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate, max-age=0'
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['Expires'] = '0'
    # 页面版本标识：HTML 文件修改时间戳，便于确认是否加载到最新版
    try:
        ver = str(int(os.path.getmtime(html_path)))
    except Exception:
        ver = '0'
    resp.headers['X-Kanban-Version'] = ver
    return resp


if __name__ == '__main__':
    port = int(os.environ.get('PORT', '5678'))
    print("=" * 50)
    print("WIPTrack Kanban Server starting...")
    print(f"URL: http://localhost:{port}")
    print(f"API: http://localhost:{port}/api/data")
    print("Press Ctrl+C to stop")
    print("=" * 50)
    app.run(host='0.0.0.0', port=port, debug=False)
