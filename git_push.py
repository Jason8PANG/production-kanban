import subprocess
import os

os.environ['GIT_TERMINAL_PROMPT'] = '0'
# 09-21 迁移后的新目录（旧目录 HMLV生产看板 已删除）
os.chdir(r'j:\PowerBI\DataSet\PRODUCTION\HMLV&Penang')

result = subprocess.run(
    ['git', 'add', 'HMLV生产看板.html', 'wiptrack_server.py', 'README.md', '.workbuddy/memory/MEMORY.md'],
    capture_output=True,
    text=True,
    env=os.environ
)

result2 = subprocess.run(
    ['git', 'commit', '-m', 'fix: 手动刷新按钮无反馈修复——点击即显示"刷新中…"+禁用+圆点橙色闪动, 2秒兜底恢复; 新增"数据更新"时间显示(区分本地时钟与数据时间); 修正倒计时初值30s→300s与间隔选择器一致'],
    capture_output=True,
    text=True,
    env=os.environ
)

result3 = subprocess.run(
    ['git', 'push', '-u', 'origin', 'main'],
    capture_output=True,
    text=True,
    env=os.environ
)
print(f"Add: {result.stdout} {result.stderr}")
print(f"Commit: {result2.stdout} {result2.stderr}")
print(f"Push STDOUT: {result3.stdout}")
print(f"Push STDERR: {result3.stderr}")
print(f"Return code: {result3.returncode}")
