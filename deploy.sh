#!/bin/bash
# 七年之约 · PythonAnywhere 一键部署
set -e
echo "🏦 七年之约 · 部署中..."

# 克隆代码
rm -rf ~/seven-years 2>/dev/null
git clone https://github.com/Kroza-cell/seven-years.git ~/seven-years
cd ~/seven-years

# 安装依赖
pip3 install --user flask flask-login gunicorn

echo ""
echo "✅ 代码已部署到 ~/seven-years/"
echo ""
echo "🔧 接下来需要手动配置 Web 应用，请按以下步骤操作："
echo ""
echo "1. 打开 https://www.pythonanywhere.com/user/kroza/webapps/"
echo "2. 点 'Add a new web app'"
echo "3. 选择 'Manual configuration' → Python 3.12"
echo "4. 在 'Code' 部分设置："
echo "   Source code: /home/kroza/seven-years"
echo "   Working directory: /home/kroza/seven-years"
echo "5. 编辑 WSGI configuration file，替换为："
echo ""
echo "import sys"
echo "path = '/home/kroza/seven-years'"
echo "if path not in sys.path:"
echo "    sys.path.insert(0, path)"
echo "from jaccount_web import app as application"
echo ""
echo "6. 在 'Environment variables' 添加："
echo "   SECRET_KEY = $(python3 -c 'import secrets; print(secrets.token_hex(32))')"
echo ""
echo "7. 点顶部绿色 Reload 按钮"
echo ""
echo "完成后访问: https://kroza.pythonanywhere.com"
