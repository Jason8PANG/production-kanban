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

| 数据源 | 数据库类型 | 连接方式 | 说明 |
|--------|-----------|----------|------|
| **wiptrack** | MySQL | pymysql 原生直连 | 生产记录（production_records）和排产计划（hmlv_production_schedule） |
| **Andon** | MS SQL Server | pyodbc (ODBC) | Andon 系统数据（生产异常、设备状态等） |

## CentOS 快速部署

### 前置条件

- Docker & Docker Compose
- **MySQL** 数据库网络可达
- **MS SQL Server** 数据库网络可达（如需 Andon 数据）

### 1. 安装 Docker

```bash
# 安装 Docker
sudo yum install -y yum-utils
sudo yum-config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
sudo yum install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

# 启动 Docker
sudo systemctl start docker
sudo systemctl enable docker
```

### 2. 克隆代码

```bash
git clone https://github.com/Jason8PANG/production-kanban.git
cd production-kanban
```

### 3. 配置 MySQL 连接 (CentOS)

#### 3.1 安装 MySQL ODBC 驱动（仅用于 Andon SQL Server 数据源）

```bash
# 添加 Microsoft 仓库
curl -o /etc/yum.repos.d/mssql-release.repo https://packages.microsoft.com/config/rhel/8/prod.repo

# 接受许可并安装
sudo ACCEPT_EULA=Y yum install -y msodbcsql18
# 可选：安装命令行工具
sudo ACCEPT_EULA=Y yum install -y mssql-tools18
```

#### 3.2 配置环境变量

创建 `.env` 文件（基于 `.env.example`）：

```bash
cp .env.example .env
nano .env
```

`.env` 内容示例：

```env
# MySQL 直连配置（wiptrack 数据源）
MYSQL_HOST=你的MySQL服务器IP
MYSQL_PORT=3306
MYSQL_USER=powerbi
MYSQL_PASSWORD=你的密码
MYSQL_DATABASE=wiptrack
```

> **提示**：`.env` 文件已在 `.gitignore` 中，不会被提交到 GitHub。

#### 3.3 验证 MySQL 连接

```bash
# 安装 MySQL 客户端
sudo yum install -y mysql

# 测试连接
mysql -h 你的MySQL服务器IP -P 3306 -u powerbi -p
```

### 4. 创建 Docker 网络

```bash
docker network create public-net
```

### 5. 启动服务

```bash
docker compose up -d --build
```

### 6. 查看日志

```bash
docker compose logs -f hmlv-kanban
```

### 7. 防火墙设置

```bash
# 开放 8081 端口
sudo firewall-cmd --permanent --add-port=8081/tcp
sudo firewall-cmd --reload
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
| MYSQL_HOST | localhost | MySQL 服务器地址 |
| MYSQL_PORT | 3306 | MySQL 端口 |
| MYSQL_USER | powerbi | MySQL 用户名 |
| MYSQL_PASSWORD |  | MySQL 密码（留空） |
| SQLSERVER_HOST |  | SQL Server 地址（Andon） |
| SQLSERVER_PORT | 1433 | SQL Server 端口 |
| SQLSERVER_USER |  | SQL Server 用户名 |
| SQLSERVER_PASSWORD |  | SQL Server 密码 |
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
