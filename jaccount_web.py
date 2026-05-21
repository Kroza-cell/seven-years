"""
七年之约 · Web 版（多用户）
Flask + Flask-Login + SQLite
"""
import sys, sqlite3, re, os
from datetime import date, datetime, timedelta
from pathlib import Path
from flask import (
    Flask, render_template, request, jsonify,
    redirect, url_for, flash, session,
)
from flask_login import (
    LoginManager, login_user, logout_user,
    login_required, current_user, UserMixin,
)
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-in-production")
DB_PATH = Path(__file__).parent / "jaccount.db"

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"
login_manager.login_message = "请先登录"

EXPENSE_CATS = ["🍜 餐饮", "🚗 交通", "🏠 住房", "🛒 购物", "🎮 娱乐",
                "💊 医疗", "📚 学习", "🎁 人情", "📱 通讯", "💡 水电",
                "💼 工作", "🔧 其他"]
INCOME_CATS = ["💵 工资", "🧧 奖金", "📈 投资", "🎁 礼金", "🔧 兼职", "💰 其他"]
MOODS = ["😄", "😊", "😐", "😔", "😤"]


# ═══════════════ 用户模型 ═══════════════

class User(UserMixin):
    def __init__(self, user_id, username):
        self.id = user_id
        self.username = username


@login_manager.user_loader
def load_user(user_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE id=?", (int(user_id),)).fetchone()
    conn.close()
    if row:
        return User(row["id"], row["username"])
    return None


# ═══════════════ 数据库 ═══════════════

def get_db():
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        );
        CREATE TABLE IF NOT EXISTS goal (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL UNIQUE,
            name TEXT NOT NULL DEFAULT '七年之约',
            target REAL NOT NULL DEFAULT 0,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS goal_phases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            goal_id INTEGER NOT NULL,
            phase_name TEXT NOT NULL,
            phase_target REAL NOT NULL,
            sort_order INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (goal_id) REFERENCES goal(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            balance REAL DEFAULT 0,
            pct INTEGER DEFAULT 0,
            note TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            date TEXT NOT NULL, type TEXT NOT NULL,
            category TEXT NOT NULL, amount REAL NOT NULL,
            account_id INTEGER DEFAULT 1, note TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS daily_review (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            income_sum REAL DEFAULT 0,
            expense_sum REAL DEFAULT 0,
            net_today REAL DEFAULT 0,
            total_saved REAL DEFAULT 0,
            progress_pct REAL DEFAULT 0,
            summary TEXT, tomorrow TEXT, mood TEXT DEFAULT '😐',
            created_at TEXT DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            UNIQUE(user_id, date)
        );
    """)
    conn.commit()
    conn.close()


def ensure_user_accounts(conn, uid):
    """为新用户创建默认三账户"""
    existing = conn.execute("SELECT COUNT(*) FROM accounts WHERE user_id=?", (uid,)).fetchone()[0]
    if existing == 0:
        conn.executemany(
            "INSERT INTO accounts (user_id, name, balance, pct, note) VALUES (?,?,?,?,?)",
            [
                (uid, "💰 消费账户", 0, 50, "日常开支"),
                (uid, "🛡️ 应急账户", 0, 20, "3-6个月生活费"),
                (uid, "📈 储蓄/投资账户", 0, 30, "长期不动，等待风口"),
            ],
        )
        conn.commit()


# ═══════════════ 辅助函数 ═══════════════

def _calc_progress(conn, uid, goal, saved):
    if not goal:
        return {"pct": 0, "pct_capped": 0, "elapsed": 0, "remaining": 1,
                "total_days": 1, "monthly_needed": 0, "daily_avg": 0, "daily_needed": 0,
                "time_pct": 0, "lag": False, "phases": [], "active_phase": None,
                "total_phases": 0, "completed_phases": 0}

    total_days = (date.fromisoformat(goal["end_date"]) - date.fromisoformat(goal["start_date"])).days
    elapsed = max(0, (date.today() - date.fromisoformat(goal["start_date"])).days)
    remaining = max(1, total_days - elapsed)
    pct = saved / goal["target"] * 100 if goal["target"] > 0 else 0
    time_pct = elapsed / total_days * 100 if total_days > 0 else 0
    monthly_needed = (goal["target"] - saved) / max(1, remaining / 30.44)

    phases = conn.execute(
        "SELECT * FROM goal_phases WHERE user_id=? AND goal_id=? ORDER BY sort_order",
        (uid, goal["id"]),
    ).fetchall()

    phase_data = []
    cumulative_target = 0
    for ph in phases:
        prev_cumulative = cumulative_target
        cumulative_target += ph["phase_target"]
        if saved <= prev_cumulative:
            phase_saved, phase_pct, status = 0, 0, "locked"
        elif saved >= cumulative_target:
            phase_saved, phase_pct, status = ph["phase_target"], 100, "completed"
        else:
            phase_saved = saved - prev_cumulative
            phase_pct = phase_saved / ph["phase_target"] * 100 if ph["phase_target"] > 0 else 0
            status = "active"
        phase_data.append({
            "name": ph["phase_name"], "target": ph["phase_target"],
            "cumulative": cumulative_target, "saved": round(phase_saved, -2),
            "pct": round(min(phase_pct, 100), 1),
            "remaining": round(ph["phase_target"] - phase_saved, -2), "status": status,
        })

    daily_avg = round(saved / elapsed, -1) if elapsed > 0 and saved >= 10 else (round(saved / elapsed, 1) if elapsed > 0 else 0)
    daily_needed = round((goal["target"] - saved) / remaining, -1) if (goal["target"] - saved) / remaining >= 10 else round((goal["target"] - saved) / remaining, 1)
    active_phase = next((p for p in phase_data if p["status"] == "active"), None)

    return {
        "pct": round(pct, 1), "pct_capped": round(min(pct, 100), 1),
        "elapsed": elapsed, "remaining": remaining, "total_days": total_days,
        "monthly_needed": round(monthly_needed, 0),
        "daily_avg": daily_avg, "daily_needed": daily_needed,
        "time_pct": round(time_pct, 1), "lag": pct < time_pct,
        "phases": phase_data, "active_phase": active_phase,
        "total_phases": len(phase_data),
        "completed_phases": sum(1 for p in phase_data if p["status"] == "completed"),
    }


def _daily_summary(conn, uid, d):
    inc = conn.execute(
        "SELECT COALESCE(SUM(amount),0) FROM transactions WHERE user_id=? AND date=? AND type='income'",
        (uid, d)).fetchone()[0]
    exp = conn.execute(
        "SELECT COALESCE(SUM(amount),0) FROM transactions WHERE user_id=? AND date=? AND type='expense'",
        (uid, d)).fetchone()[0]
    return inc, exp, inc - exp


def _munger_analyze(conn, uid, period_start, period_end, period_label):
    inc = conn.execute(
        "SELECT COALESCE(SUM(amount),0) FROM transactions WHERE user_id=? AND date>=? AND date<=? AND type='income'",
        (uid, period_start, period_end)).fetchone()[0]
    exp = conn.execute(
        "SELECT COALESCE(SUM(amount),0) FROM transactions WHERE user_id=? AND date>=? AND date<=? AND type='expense'",
        (uid, period_start, period_end)).fetchone()[0]
    net, saving_rate = inc - exp, inc - exp / inc * 100 if inc > 0 else 0
    if inc > 0: saving_rate = net / inc * 100
    else: saving_rate = 0

    exp_cats = conn.execute(
        "SELECT category, SUM(amount) as amt FROM transactions WHERE user_id=? AND date>=? AND date<=? AND type='expense' GROUP BY category ORDER BY amt DESC",
        (uid, period_start, period_end)).fetchall()
    inc_cats = conn.execute(
        "SELECT category, SUM(amount) as amt FROM transactions WHERE user_id=? AND date>=? AND date<=? AND type='income' GROUP BY category ORDER BY amt DESC",
        (uid, period_start, period_end)).fetchall()
    accounts = conn.execute("SELECT name, balance FROM accounts WHERE user_id=? ORDER BY id", (uid,)).fetchall()

    emergency_bal = accounts[1]["balance"] if len(accounts) > 1 else 0
    discretionary = sum(r["amt"] for r in exp_cats if any(k in r["category"] for k in ["餐饮", "购物", "娱乐"]))
    discretionary_pct = discretionary / exp * 100 if exp > 0 else 0
    study_exp = sum(r["amt"] for r in exp_cats if "学习" in r["category"])
    study_pct = study_exp / exp * 100 if exp > 0 else 0
    salary_inc = sum(r["amt"] for r in inc_cats if "工资" in r["category"])
    salary_pct = salary_inc / inc * 100 if inc > 0 else 0

    monthly_exp_avg = exp / max(1, (date.fromisoformat(period_end) - date.fromisoformat(period_start)).days / 30.44)
    emergency_months = emergency_bal / monthly_exp_avg if monthly_exp_avg > 0 else 0

    advice = []
    if saving_rate >= 40:
        advice.append({"level": "good", "quote": "The big money is not in the buying and selling, but in the waiting.", "advice": f"{period_label}储蓄率达 {saving_rate:.0f}%。大多数人破产，是因为他们无法静坐等待。你已经比 90% 的人做得好。"})
    elif saving_rate >= 25:
        advice.append({"level": "ok", "quote": "It's not supposed to be easy. Anyone who finds it easy is stupid.", "advice": f"{period_label}储蓄率 {saving_rate:.0f}%，刚过及格线。继续。"})
    else:
        advice.append({"level": "danger", "quote": "The first rule of compounding: Never interrupt it unnecessarily.", "advice": f"{period_label}储蓄率仅 {saving_rate:.0f}%。你每花掉 100 元，就主动打断了 100 元替你工作的复利链条。"})

    if exp_cats:
        top_cat, top_pct = exp_cats[0]["category"], exp_cats[0]["amt"] / exp * 100 if exp > 0 else 0
        if "餐饮" in top_cat and top_pct > 25:
            advice.append({"level": "warning", "quote": "A man who eats his retirement one meal at a time will find the menu surprisingly filling.", "advice": f"餐饮占总支出的 {top_pct:.0f}%。芒格一生住在同一栋房子里。"})
        elif "购物" in top_cat and top_pct > 20:
            advice.append({"level": "danger", "quote": "The world is full of people who would rather feel rich than be rich.", "advice": f"购物消费占 {top_pct:.0f}%。买入不需要的东西，就是在向未来借钱。"})
        elif "娱乐" in top_cat and top_pct > 15:
            advice.append({"level": "warning", "quote": "To get what you want, you have to deserve what you want.", "advice": f"娱乐支出占比 {top_pct:.0f}%。"})

    if discretionary_pct > 30 and study_pct < 5:
        advice.append({"level": "danger", "quote": "Spend each day trying to be a little wiser.", "advice": f"可支配支出达 {discretionary_pct:.0f}%，但学习投入仅 {study_pct:.0f}%。你最好的投资品是你自己的大脑。"})

    if emergency_months < 1:
        advice.append({"level": "danger", "quote": "It's not the things you don't know that get you in trouble.", "advice": f"应急储备仅够支撑 {emergency_months:.1f} 个月。先存够 3 个月生活费再谈投资。"})
    elif emergency_months < 3:
        advice.append({"level": "warning", "quote": "The wise man anticipates trouble.", "advice": f"应急储备可支撑 {emergency_months:.0f} 个月，仍不足 3 个月底线。"})
    elif emergency_months >= 6:
        advice.append({"level": "good", "quote": "Knowing what you don't know is more useful than being brilliant.", "advice": f"应急储备充足，可支撑 {emergency_months:.0f} 个月。"})

    if salary_pct > 90 and inc > 0:
        advice.append({"level": "ok", "quote": "Invert, always invert: what would guarantee failure?", "advice": f"工资占收入的 {salary_pct:.0f}%。如果你唯一的收入来源明天消失，你还能撑多久？"})

    if not advice:
        advice.append({"level": "ok", "quote": "It is remarkable how much long-term advantage people have gotten by trying to be consistently not stupid.", "advice": f"{period_label}数据有限，但芒格有句话值得记住。"})

    return {
        "period": period_label, "start": period_start, "end": period_end,
        "income": inc, "expense": exp, "net": net,
        "saving_rate": round(saving_rate, 1),
        "expense_cats": [{"category": r["category"], "amount": r["amt"]} for r in exp_cats],
        "income_cats": [{"category": r["category"], "amount": r["amt"]} for r in inc_cats],
        "discretionary_pct": round(discretionary_pct, 1),
        "study_pct": round(study_pct, 1),
        "emergency_months": round(emergency_months, 1),
        "salary_pct": round(salary_pct, 1),
        "advice": advice,
    }


def _buffett_analyze(basic):
    saving_rate = basic["saving_rate"]
    exp_cats = basic["expense_cats"]
    discretionary_pct = basic["discretionary_pct"]
    study_pct = basic["study_pct"]
    emergency_months = basic["emergency_months"]
    salary_pct = basic["salary_pct"]
    inc = basic["income"]
    exp = basic["expense"]
    period_label = basic["period"]
    net = basic["net"]

    monthly_save = net / max(1, (date.fromisoformat(basic["end"]) - date.fromisoformat(basic["start"])).days / 30.44)
    future_10y = 0
    if monthly_save > 0:
        for i in range(120):
            future_10y += monthly_save
            future_10y *= (1 + 0.08 / 12)
        future_10y = round(future_10y, -2)

    advice = []
    if saving_rate >= 40:
        advice.append({"level": "good", "quote": "Someone's sitting in the shade today because someone planted a tree a long time ago.", "advice": f"{period_label}储蓄率 {saving_rate:.0f}%。如果保持每月存 {monthly_save:,.0f} 元、年化 8%，10 年后大约是 {future_10y:,.0f} 元。"})
    elif saving_rate >= 25:
        advice.append({"level": "ok", "quote": "No matter how great the talent or efforts, some things just take time.", "advice": f"{period_label}储蓄率 {saving_rate:.0f}%。巴菲特 99% 的财富是 50 岁之后赚的。保持耐心。"})
    else:
        advice.append({"level": "danger", "quote": "Do not save what is left after spending, but spend what is left after saving.", "advice": f"{period_label}储蓄率仅 {saving_rate:.0f}%。先存后花——不是花了再存。"})

    if exp_cats:
        top_cat = exp_cats[0]["category"]
        top_pct = exp_cats[0]["amount"] / exp * 100 if exp > 0 else 0
        if "餐饮" in top_cat and top_pct > 25:
            advice.append({"level": "warning", "quote": "Price is what you pay. Value is what you get.", "advice": f"餐饮占 {top_pct:.0f}%。巴菲特每天吃麦当劳早餐，预算不超过 3.17 美元。"})
        elif "购物" in top_cat and top_pct > 20:
            advice.append({"level": "danger", "quote": "If you buy things you do not need, soon you will have to sell things you do need.", "advice": f"购物占 {top_pct:.0f}%。巴菲特 1958 年买的房子，至今还住在里面。"})
        elif "娱乐" in top_cat and top_pct > 15:
            advice.append({"level": "warning", "quote": "The difference between successful people and really successful people is that really successful people say no to almost everything.", "advice": f"娱乐占 {top_pct:.0f}%。"})

    if discretionary_pct > 30 and study_pct < 5:
        advice.append({"level": "danger", "quote": "The most important investment you can make is in yourself.", "advice": f"可支配支出 {discretionary_pct:.0f}%，学习仅 {study_pct:.0f}%。知识就像复利。"})
    elif study_pct >= 10:
        advice.append({"level": "good", "quote": "Read 500 pages every day. That's how knowledge works.", "advice": f"学习投入占 {study_pct:.0f}%。最高的年化回报率永远在你的两耳之间。"})

    if emergency_months < 1:
        advice.append({"level": "danger", "quote": "Rule No.1: Never lose money. Rule No.2: Never forget Rule No.1.", "advice": f"应急储备仅 {emergency_months:.1f} 个月。没有安全边际的投资者迟早会出局。"})
    elif emergency_months < 3:
        advice.append({"level": "warning", "quote": "Cash is to a business as oxygen is to an individual.", "advice": f"应急储备 {emergency_months:.0f} 个月，还没到 3 个月的底线。"})
    elif emergency_months >= 6:
        advice.append({"level": "good", "quote": "Opportunities come infrequently. When it rains gold, put out the bucket.", "advice": f"应急储备 {emergency_months:.0f} 个月，安全边际充足。"})

    if salary_pct > 90 and inc > 0:
        advice.append({"level": "ok", "quote": "Never depend on a single income.", "advice": f"工资占收入 {salary_pct:.0f}%。让你的钱为你工作。"})

    if saving_rate >= 35 and emergency_months >= 3 and study_pct >= 5:
        advice.append({"level": "good", "quote": "The stock market is a device for transferring money from the impatient to the patient.", "advice": "三项指标全部健康。你已经跑赢了 95% 的人。"})

    if not advice:
        advice.append({"level": "ok", "quote": "The best investment you can make is in yourself.", "advice": f"{period_label}数据有限。从认真记账开始。"})

    return advice


def _last_day_of_month(y, m):
    if m == 12: return 31
    return (date(y, m + 1, 1) - timedelta(days=1)).day


# ═══════════════ 认证路由 ═══════════════

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if not username or not password:
            return render_template("login.html", error="请填写用户名和密码")
        if not re.match(r'^[a-zA-Z0-9_\-\u4e00-\u9fff]{2,20}$', username):
            return render_template("login.html", error="用户名需 2-20 个字符（中英文、数字、_-）")
        conn = get_db()
        row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        conn.close()
        if row and check_password_hash(row["password_hash"], password):
            login_user(User(row["id"], row["username"]))
            return redirect(url_for("index"))
        return render_template("login.html", error="用户名或密码错误")
    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if not username or not password:
            return render_template("login.html", error="请填写用户名和密码", tab="register")
        if not re.match(r'^[a-zA-Z0-9_\-\u4e00-\u9fff]{2,20}$', username):
            return render_template("login.html", error="用户名需 2-20 个字符（中英文、数字、_-）", tab="register")
        if len(password) < 6:
            return render_template("login.html", error="密码至少 6 位", tab="register")
        conn = get_db()
        existing = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
        if existing:
            conn.close()
            return render_template("login.html", error="用户名已存在", tab="register")
        h = generate_password_hash(password)
        cur = conn.cursor()
        cur.execute("INSERT INTO users (username, password_hash) VALUES (?,?)", (username, h))
        uid = cur.lastrowid
        ensure_user_accounts(conn, uid)
        conn.commit()
        conn.close()
        login_user(User(uid, username))
        return redirect(url_for("index"))
    return render_template("login.html", tab="register")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


# ═══════════════ 主页面 ═══════════════

@app.route("/")
@login_required
def index():
    conn = get_db()
    uid = current_user.id
    ensure_user_accounts(conn, uid)
    today = str(date.today())

    goal = conn.execute("SELECT * FROM goal WHERE user_id=? ORDER BY id DESC LIMIT 1", (uid,)).fetchone()
    if not goal:
        conn.close()
        return render_template("onboard.html", today=today)

    ti = conn.execute("SELECT COALESCE(SUM(amount),0) FROM transactions WHERE user_id=? AND type='income'", (uid,)).fetchone()[0]
    te = conn.execute("SELECT COALESCE(SUM(amount),0) FROM transactions WHERE user_id=? AND type='expense'", (uid,)).fetchone()[0]
    saved = ti - te
    inc_today, exp_today, net_today = _daily_summary(conn, uid, today)

    accounts = conn.execute("SELECT * FROM accounts WHERE user_id=? ORDER BY id", (uid,)).fetchall()
    total_balance = sum(a["balance"] for a in accounts)
    accounts_pct = []
    for a in accounts:
        d = dict(a)
        d["balance_pct"] = round(d["balance"] / total_balance * 100, 1) if total_balance > 0 else 0
        accounts_pct.append(d)

    review = conn.execute("SELECT * FROM daily_review WHERE user_id=? AND date=?", (uid, today)).fetchone()
    txns = conn.execute("SELECT * FROM transactions WHERE user_id=? AND date=? ORDER BY created_at DESC", (uid, today)).fetchall()
    progress = _calc_progress(conn, uid, goal, saved)

    month = today[:7]
    m_inc = conn.execute("SELECT COALESCE(SUM(amount),0) FROM transactions WHERE user_id=? AND date LIKE ? AND type='income'", (uid, month + "%")).fetchone()[0]
    m_exp = conn.execute("SELECT COALESCE(SUM(amount),0) FROM transactions WHERE user_id=? AND date LIKE ? AND type='expense'", (uid, month + "%")).fetchone()[0]

    conn.close()
    return render_template(
        "index.html",
        today=today, goal=goal, saved=saved, ti=ti, te=te,
        inc_today=inc_today, exp_today=exp_today,
        accounts=accounts_pct, review=review, txns=txns,
        progress=progress, m_inc=m_inc, m_exp=m_exp,
        expense_cats=EXPENSE_CATS, income_cats=INCOME_CATS, moods=MOODS,
    )


# ═══════════════ API ═══════════════

@app.route("/api/goal", methods=["POST"])
@login_required
def api_set_goal():
    data = request.json
    uid = current_user.id
    conn = get_db()
    conn.execute("DELETE FROM goal_phases WHERE user_id=?", (uid,))
    conn.execute("DELETE FROM goal WHERE user_id=?", (uid,))
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO goal (user_id, name, target, start_date, end_date) VALUES (?,?,?,?,?)",
        (uid, data["name"], data.get("total_target", data.get("target", 0)),
         data["start_date"], data["end_date"]),
    )
    gid = cur.lastrowid
    for i, ph in enumerate(data.get("phases", [])):
        cur.execute(
            "INSERT INTO goal_phases (user_id, goal_id, phase_name, phase_target, sort_order) VALUES (?,?,?,?,?)",
            (uid, gid, ph["name"], ph["target"], i),
        )
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/transaction", methods=["POST"])
@login_required
def api_add_transaction():
    data = request.json
    uid = current_user.id
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO transactions (user_id, date, type, category, amount, account_id, note) VALUES (?,?,?,?,?,?,?)",
        (uid, data["date"], data["type"], data["category"], data["amount"],
         data.get("account_id", 1), data.get("note", "")),
    )
    if data["type"] == "income":
        cur.execute("UPDATE accounts SET balance=balance+? WHERE user_id=? AND id=?",
                    (data["amount"], uid, data.get("account_id", 1)))
    else:
        cur.execute("UPDATE accounts SET balance=balance-? WHERE user_id=? AND id=?",
                    (data["amount"], uid, data.get("account_id", 1)))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/review", methods=["POST"])
@login_required
def api_review():
    data = request.json
    uid = current_user.id
    d = data["date"]
    conn = get_db()
    inc_today, exp_today, net = _daily_summary(conn, uid, d)
    ti = conn.execute("SELECT COALESCE(SUM(amount),0) FROM transactions WHERE user_id=? AND type='income'", (uid,)).fetchone()[0]
    te = conn.execute("SELECT COALESCE(SUM(amount),0) FROM transactions WHERE user_id=? AND type='expense'", (uid,)).fetchone()[0]
    saved = ti - te
    goal = conn.execute("SELECT * FROM goal WHERE user_id=? ORDER BY id DESC LIMIT 1", (uid,)).fetchone()
    pct = saved / goal["target"] * 100 if goal and goal["target"] > 0 else 0

    conn.execute("""
        INSERT INTO daily_review (user_id, date, income_sum, expense_sum, net_today, total_saved, progress_pct, summary, tomorrow, mood)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(user_id, date) DO UPDATE SET
            income_sum=excluded.income_sum, expense_sum=excluded.expense_sum,
            net_today=excluded.net_today, total_saved=excluded.total_saved,
            progress_pct=excluded.progress_pct, summary=excluded.summary,
            tomorrow=excluded.tomorrow, mood=excluded.mood
    """, (uid, d, inc_today, exp_today, net, saved, pct,
          data["summary"], data["tomorrow"], data["mood"]))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/stats")
@login_required
def api_stats():
    uid = current_user.id
    conn = get_db()
    month = str(date.today())[:7]
    daily = conn.execute(
        "SELECT date, type, SUM(amount) as amt FROM transactions WHERE user_id=? AND date LIKE ? GROUP BY date, type ORDER BY date",
        (uid, month + "%")).fetchall()
    dates, incomes, expenses = [], [], []
    day_map = {}
    for row in daily:
        day_map.setdefault(row["date"], {"income": 0, "expense": 0})
        day_map[row["date"]][row["type"]] = row["amt"]
    for d in sorted(day_map):
        dates.append(d[-2:])
        incomes.append(day_map[d]["income"])
        expenses.append(day_map[d]["expense"])

    exp_cats = conn.execute(
        "SELECT category, SUM(amount) as amt FROM transactions WHERE user_id=? AND date LIKE ? AND type='expense' GROUP BY category ORDER BY amt DESC",
        (uid, month + "%")).fetchall()
    accounts = conn.execute("SELECT name, balance FROM accounts WHERE user_id=? ORDER BY id", (uid,)).fetchall()
    moods_data = conn.execute(
        "SELECT date, mood, net_today FROM daily_review WHERE user_id=? ORDER BY date DESC LIMIT 7", (uid,)).fetchall()
    conn.close()
    return jsonify({
        "daily": {"dates": dates, "incomes": incomes, "expenses": expenses},
        "expense_cats": [{"category": r["category"], "amount": r["amt"]} for r in exp_cats],
        "accounts": [{"name": r["name"], "balance": r["balance"]} for r in accounts],
        "moods": [{"date": r["date"], "mood": r["mood"], "net": r["net_today"]} for r in moods_data],
    })


@app.route("/api/history")
@login_required
def api_history():
    uid = current_user.id
    conn = get_db()
    reviews = conn.execute("SELECT * FROM daily_review WHERE user_id=? ORDER BY date DESC LIMIT 30", (uid,)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in reviews])


@app.route("/api/transactions")
@login_required
def api_transactions():
    uid = current_user.id
    conn = get_db()
    txns = conn.execute("SELECT * FROM transactions WHERE user_id=? ORDER BY date DESC, id DESC LIMIT 50", (uid,)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in txns])


@app.route("/api/analysis")
@login_required
def api_analysis():
    uid = current_user.id
    period = request.args.get("period", "half")
    offset = int(request.args.get("offset", "0"))
    conn = get_db()
    today = date.today()

    if period == "quarter":
        q = (today.month - 1) // 3 + 1 + offset
        y = today.year
        while q < 1: q += 4; y -= 1
        while q > 4: q -= 4; y += 1
        sm = (q - 1) * 3 + 1
        ps = f"{y}-{sm:02d}-01"
        pe = f"{y}-12-31" if q == 4 else f"{y}-{q*3:02d}-{_last_day_of_month(y, q*3)}"
        label = f"{y}年 Q{q}"
    elif period == "year":
        y = today.year + offset
        ps, pe, label = f"{y}-01-01", f"{y}-12-31", f"{y}年"
    elif period == "half":
        y = today.year
        h = (1 if today.month <= 6 else 2) + offset
        while h < 1: h += 2; y -= 1
        while h > 2: h -= 2; y += 1
        ps = f"{y}-{'01' if h==1 else '07'}-01"
        pe = f"{y}-{'06-30' if h==1 else '12-31'}"
        label = f"{y}年 {'上半年' if h==1 else '下半年'}"
    else:
        ps, pe = str(today)[:7]+"-01", str(today)
        label = f"{today.year}年 {today.month}月"

    result = _munger_analyze(conn, uid, ps, pe, label)
    result["advice_munger"] = result.pop("advice")
    result["advice_buffett"] = _buffett_analyze(result)
    conn.close()
    return jsonify(result)


# ═══════════════ 账户管理 API ═══════════════

@app.route("/api/accounts", methods=["GET", "POST"])
@login_required
def api_accounts():
    uid = current_user.id
    conn = get_db()
    if request.method == "POST":
        data = request.json
        conn.execute("DELETE FROM accounts WHERE user_id=?", (uid,))
        for a in data:
            conn.execute(
                "INSERT INTO accounts (user_id, name, balance, pct, note) VALUES (?,?,?,?,?)",
                (uid, a["name"], a["balance"], a["pct"], a.get("note", "")),
            )
        conn.commit()
        conn.close()
        return jsonify({"ok": True})
    else:
        accs = conn.execute("SELECT * FROM accounts WHERE user_id=? ORDER BY id", (uid,)).fetchall()
        conn.close()
        return jsonify([dict(a) for a in accs])


# ═══════════════ 启动 ═══════════════

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding='utf-8')
    init_db()
    print("\n  🏦 七年之约 · 多用户版")
    print("  🌐 http://127.0.0.1:5000\n")
    app.run(debug=False, host="0.0.0.0", port=5000)
