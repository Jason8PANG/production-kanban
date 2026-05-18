# HMLV生产看板 (Production Kanban)

HMLV生产实时看板系统，用于 310 站点 WIP 后台数据管理和生产排程监控。

## 功能特性

- 📊 **KPI 实时指标**: 当月工单数、完成率、总工时等
- 🔄 **工序流程监控**: Print→Cut→Pre→Asm→Test→Pack 全流程追踪
- 📈 **每日工时进度**: 工作日目标 vs 实际完成对比
- ⚠️ **滞留工单预警**: 各工序 WIP 滞留工单列表及滞留时间
- 🔍 **工单查询**: 支持模糊搜索工单号，查看工序流转明细
- 💰 **销售统计**: 按工序、按月统计销售金额

## 快速部署

### 前置条件

- Docker & Docker Compose
- ODBC 数据源配置 (DSN: wiptrack)
- Docker 网络: `public-net`

### 创建 Docker 网络

```bash
docker network create public-net
```

### 启动服务

```bash
docker compose up -d --build
```

### 访问服务

- 看板页面: http://localhost:8081
- 数据接口: http://localhost:8081/api/data
- 销售工时接口: http://localhost:8081/api/excel_jobs
- WIP 滞留接口: http://localhost:8081/api/wip
- 工单查询接口: http://localhost:8081/api/search_wo?q=xxx

## 环境变量

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| ODBC_DSN | wiptrack | ODBC 数据源名称 |
| ODBC_UID | powerbi | 数据库用户名 |
| ODBC_PWD | !Q1234567 | 数据库密码 |
| PORT | 5678 | 服务端口 |

## 技术栈

- **后端**: Python 3.11 + Flask + pyodbc
- **数据处理**: pandas + openpyxl
- **数据库**: SQL Server (ODBC)
- **前端**: 纯静态 HTML/CSS/JS

## 目录结构

```
.
├── wiptrack_server.py      # Flask 后端服务
├── HMLV生产看板.html        # 前端看板页面
├── docker-compose.yml       # Docker Compose 配置
├── Dockerfile               # Docker 构建文件
├── requirements.txt         # Python 依赖
└── README.md                # 说明文档
```

## License

Internal use only.
