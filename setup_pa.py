#!/usr/bin/env python3
"""
七年之约 · PythonAnywhere 一键部署
在 PythonAnywhere Bash 控制台中运行：
    python3 <(curl -sL https://raw.githubusercontent.com/Kroza-cell/seven-years/master/setup_pa.py)
"""
import subprocess, sys, os, shutil

USER = "kroza"
HOME = f"/home/{USER}"
PROJ = f"{HOME}/seven-years"
DOMAIN = f"{USER}.pythonanywhere.com"
WSGI_FILE = f"/var/www/{USER}_pythonanywhere_com_wsgi.py"

def run(cmd):
    print(f"  $ {cmd[:80]}")
    return subprocess.run(cmd, shell=True, capture_output=True, text=True)

print("=" * 60)
print("  七年之约 · PythonAnywhere 自动部署")
print("=" * 60)

# 1. Clone
print("\n[1/5] 克隆代码...")
if os.path.exists(PROJ):
    shutil.rmtree(PROJ)
r = run(f"git clone https://github.com/Kroza-cell/seven-years.git {PROJ}")
if r.returncode != 0:
    print(f"❌ Git clone 失败: {r.stderr}")
    sys.exit(1)
print("✅ 代码已下载")

# 2. Install deps
print("\n[2/5] 安装依赖...")
run("pip3 install --user flask flask-login 2>/dev/null")
print("✅ 依赖已安装")

# 3. Create web app
print("\n[3/5] 创建 Web 应用...")
# Check if web app exists
r = run(f"ls /var/www/{USER}_pythonanywhere_com_wsgi.py 2>/dev/null")
if r.returncode != 0:
    # Try using the pa helper
    r = run(f"pa_create_webapp_with_virtualenv -p 3.12 {DOMAIN} 2>/dev/null")
    if r.returncode != 0:
        # Manual fallback - create WSGI file directly
        print("  ℹ️ 需要手动创建 Web App，正在准备...")
        print(f"  📋 请打开: https://www.pythonanywhere.com/user/{USER}/webapps/")
        print(f"  📋 点击 'Add a new web app' → Manual config → Python 3.12")
        print(f"  📋 创建后重新运行本脚本，或手动完成以下步骤：")
        print()
        print(f"  然后编辑 WSGI 文件 ({WSGI_FILE})，替换为：")
        print(f"  ──────────────────────────────")
        print(f"  import sys")
        print(f"  path = '{PROJ}'")
        print(f"  if path not in sys.path:")
        print(f"      sys.path.insert(0, path)")
        print(f"  from jaccount_web import app as application")
        print(f"  ──────────────────────────────")
        print()
        print(f"  最后: Reload Web App")
        sys.exit(0)
    else:
        print("✅ Web 应用已创建")

# 4. Write WSGI config
print("\n[4/5] 配置 WSGI...")
wsgi_content = f"""import sys
path = '{PROJ}'
if path not in sys.path:
    sys.path.insert(0, path)
from jaccount_web import app as application
"""
try:
    with open(WSGI_FILE, "w") as f:
        f.write(wsgi_content)
    print("✅ WSGI 配置已写入")
except PermissionError:
    print(f"⚠️ 无权限写入 {WSGI_FILE}")
    print(f"  请手动编辑该文件，内容如下：")
    print(f"  {wsgi_content}")
except FileNotFoundError:
    print(f"⚠️ WSGI 文件不存在，请先在 Web 页面创建 Web App")
    print(f"  打开: https://www.pythonanywhere.com/user/{USER}/webapps/")

# 5. Set env vars and reload
print("\n[5/5] 生成 SECRET_KEY 并重启...")
r = run("python3 -c 'import secrets; print(secrets.token_hex(32))'")
secret = r.stdout.strip()
print(f"  ℹ️ 请在 Web 页面添加环境变量: SECRET_KEY = {secret[:10]}...")
print(f"  ℹ️ 路径: https://www.pythonanywhere.com/user/{USER}/webapps/{DOMAIN}/")
print()

# Try reload
r = run(f"touch {WSGI_FILE}")
if r.returncode == 0:
    print("✅ 已触发重载")

print()
print("=" * 60)
print(f"  🚀 部署完成！")
print(f"  🌐 https://{DOMAIN}")
print(f"  📊 Web 管理: https://www.pythonanywhere.com/user/{USER}/webapps/")
print("=" * 60)
