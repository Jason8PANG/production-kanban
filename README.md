# HMLV生产看板 (Production Kanban)

HMLV生产实时看板系统，用于 310 站点 WIP 后台数据管理和生产排程监控。

## 功能特性

- 📊 **KPI 实时指标**: 当月工单数、完成率、总工时等
- 🔄 **工序流程监控**: Print→Cut→Pre→Asm→Test→Pack 全流程追踪
- 📈 **每日工时进度**: 工作日目标 vs 实际完成对比
- ⚠️ **滞留工单预警**: 各工序 WIP 滞留工单列表及滞留时间
- 🔍 **工单查询**: 支持模糊搜索工单号，查看工序流转明细
- 💰 **销售统计**: 按工序、按月统计销售金额

## 数据源说明

| 数据源 | 数据库类型 | ODBC DSN | 说明 |
|--------|-----------|----------|------|
| **wiptrack** | MySQL | wiptrack | 生产记录（production_records）和排产计划（hmlv_production_schedule） |
| **Andon** | MS SQL Server | andon_mssql | Andon 系统数据（生产异常、设备状态等） |

## 快速部署

### 前置条件

- Docker & Docker Compose
- **MySQL** 数据库网络可达
- **MS SQL Server** 数据库网络可达
- Linux 服务器需配置 MySQL ODBC 和 SQL Server ODBC 驱动

### 1. 克隆代码

```bash
git clone https://github.com/Jason8PANG/production-kanban.git
cd production-kanban
```

### 2. 配置 ODBC 数据源 (Linux)

在 Linux 服务器上安装 ODBC 驱动：

```bash
sudo apt update
sudo apt install -y unixodbc unixodbc-dev odbcinst1debian2
# MySQL/MariaDB ODBC 驱动
sudo apt install -y odbc-mariadb
# MS SQL Server ODBC 驱动
curl https://packages.microsoft.com/keys/microsoft.asc | sudo apt-key add -
curl https://packages.microsoft.com/config/ubuntu/20.04/prod.list | sudo tee /etc/apt/sources.list.d/mssql-release.list
sudo apt update
sudo ACCEPT_EULA=Y apt install -y msodbcsql18
```

配置 ODBC 数据源，编辑 `/etc/odbc.ini`：

```ini
[wiptrack]
Driver      = MariaDB Connector/ODBC 3.1
Server      = 你的MySQL服务器IP
Port        = 3306
Database    = 你的数据库名
Uid         = powerbi
PWD         = !Q1234567

[andon_mssql]
Driver      = ODBC Driver 18 for SQL Server
Server      = 你的SQLServer服务器IP,1433
Database    = 你的Andon数据库名
Uid         = 你的用户名
PWD         = 你的密码
Encrypt     = no
TrustServerCertificate = yes
```

> **提示**:
> - `wiptrack` 数据源指向 **MySQL**（存放生产记录和排产计划）
> - `andon_mssql` 数据源指向 **MS SQL Server**（存放 Andon 数据）
> - 数据库服务器 IP、端口和数据库名需要根据实际情况修改

### 3. 创建 Docker 网络

```bash
docker network create public-net
```

### 4. 启动服务

```bash
docker compose up -d --build
```

### 5. 查看日志

```bash
docker compose logs -f hmlv-kanban
```

### 访问服务

- 看板页面: http://服务器IP:8081
- 数据接口: http://服务器IP:8081/api/data
- 销售工时接口: http://服务器IP:8081/api/excel_jobs
- WIP 滞留接口: http://服务器IP:8081/api/wip
- 工单查询接口: http://服务器IP:8081/api/search_wo?q=xxx

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
- **数据库**: MySQL (wiptrack) + MS SQL Server (Andon)
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
