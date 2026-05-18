import subprocess
import os

os.environ['GIT_TERMINAL_PROMPT'] = '0'
os.chdir(r'j:\PowerBI\DataSet\PRODUCTION\HMLV生产看板')

result = subprocess.run(
    ['git', 'add', '.workbuddy/memory/MEMORY.md', 'README.md'],
    capture_output=True,
    text=True,
    env=os.environ
)

result2 = subprocess.run(
    ['git', 'commit', '-m', 'docs: update for MySQL architecture and fix memory path'],
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
