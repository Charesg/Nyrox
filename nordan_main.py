# ============================================================
#  OTP BOT — Full Version
#  Two API Panels | Admin Number Management | Wallet System
# ============================================================

import asyncio, logging, re, sqlite3, os, requests, random, string
from datetime import datetime, timedelta
from telegram import (
    Update, InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton, BotCommand
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)

# ============================================================
#  CONFIG
# ============================================================

BOT_TOKEN        = "8857322700:AAEVZ7bLlidd4OR7TWoyAYZKnXyJQ9laA-0"    # ← Nordan bot token
ADMIN_ID         = 7438622949     # ← primary admin user ID
ADMIN_ID2        = 0     # ← secondary admin user ID
ADMIN_IDS        = (ADMIN_ID, ADMIN_ID2)
GROUP_CHAT_ID    = -1004304322155     # ← OTP group chat ID (negative)
GROUP_LINK       = "https://t.me/+LywsBj3lhaU4Y2I0"    # ← group invite link
SUPPORT_LINK     = ""    # ← support link
BOT_USERNAME     = "Nordansms_bot"    # ← bot username WITHOUT @
REQUIRED_CHANNEL = "@t.me/+tc9J6sWP7vljN2M0"    # ← e.g. @nordanchannel (leave empty to disable)
REQUIRED_GROUP   = "@t.me/+LywsBj3lhaU4Y2I0"    # ← e.g. @nordangroup   (leave empty to disable)
CHANNEL_LINK     = "https://t.me/+tc9J6sWP7vljN2M0"    # ← https://t.me/nordanchannel
GROUP_JOIN_LINK  = "https://t.me/+LywsBj3lhaU4Y2I0"    # ← https://t.me/nordangroup

# ── Nordan SMS API
NORDAN_URL    = "https://nordansms.com/restapi/smsreport"
NORDAN_TOKEN  = "nat_db3c0adf8949a917c4d27ba9e9463f234220725f90d026ad4a2b6cb4361c4016"   # ← paste your Nordan token here
FETCH_LIMIT   = 100

NAIRA_RATE     = 1600    # $1 = ₦X  (admin can change)
MIN_WITHDRAWAL = 0.50
NUMBER_EXPIRY  = 3600    # seconds
POLL_INTERVAL  = 10

# ============================================================
#  LOGGING
# ============================================================

class ColorLog(logging.Formatter):
    FORMATS = {
        logging.INFO:    "\033[32m%(asctime)s [INFO]  %(message)s\033[0m",
        logging.WARNING: "\033[33m%(asctime)s [WARN]  %(message)s\033[0m",
        logging.ERROR:   "\033[31m%(asctime)s [ERROR] %(message)s\033[0m",
    }
    def format(self, r):
        return logging.Formatter(self.FORMATS.get(r.levelno,"%(message)s"), datefmt="%Y-%m-%d %H:%M:%S").format(r)

_h = logging.StreamHandler(); _h.setFormatter(ColorLog())
logging.basicConfig(handlers=[_h], level=logging.INFO)
logger = logging.getLogger(__name__)

# ============================================================
#  DATABASE
# ============================================================

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot.db")

def get_conn():
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row; return c

def init_db():
    conn = get_conn(); c = conn.cursor()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        user_id         INTEGER PRIMARY KEY,
        username        TEXT,
        full_name       TEXT,
        is_banned       INTEGER DEFAULT 0,
        balance         REAL DEFAULT 0.0,
        total_earned    REAL DEFAULT 0.0,
        total_withdrawn REAL DEFAULT 0.0,
        trx_wallet      TEXT DEFAULT NULL,
        bank_name       TEXT DEFAULT NULL,
        account_number  TEXT DEFAULT NULL,
        account_name    TEXT DEFAULT NULL,
        referral_code   TEXT UNIQUE,
        referred_by     INTEGER DEFAULT NULL,
        joined_at       TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS numbers (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        number      TEXT UNIQUE NOT NULL,
        country     TEXT NOT NULL,
        service     TEXT NOT NULL,
        price       REAL NOT NULL DEFAULT 0.005,
        status      TEXT DEFAULT 'available',
        assigned_to INTEGER DEFAULT NULL,
        assigned_at TEXT DEFAULT NULL
    );
    CREATE TABLE IF NOT EXISTS otps (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        number      TEXT,
        country     TEXT,
        service     TEXT,
        otp_code    TEXT,
        raw_sms     TEXT,
        user_id     INTEGER,
        received_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS withdrawals (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id        INTEGER,
        amount         REAL,
        method         TEXT,
        wallet         TEXT,
        bank_name      TEXT,
        account_number TEXT,
        status         TEXT DEFAULT 'pending',
        requested_at   TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY, value TEXT
    );
    """)
    # Migrate: add account_name column if missing
    try:
        conn.execute("ALTER TABLE users ADD COLUMN account_name TEXT DEFAULT NULL")
        conn.commit()
    except: pass
    conn.commit(); conn.close()

# ── Settings ──────────────────────────────────────────────────

def get_setting(key, default=None):
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT value FROM settings WHERE key=?", (key,))
    r = c.fetchone(); conn.close()
    return r["value"] if r else default

def set_setting(key, value):
    conn = get_conn()
    conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (key, str(value)))
    conn.commit(); conn.close()

def get_otp_reward():
    v = get_setting("otp_reward"); return float(v) if v else 0.005

def get_referral_bonus():
    v = get_setting("referral_bonus"); return float(v) if v else 0.01

def is_trx_enabled():
    v = get_setting("withdraw_trx"); return v != "0"

def is_ngn_enabled():
    v = get_setting("withdraw_ngn"); return v != "0"

def save_trx_wallet(uid, wallet):
    conn = get_conn()
    conn.execute("UPDATE users SET trx_wallet=? WHERE user_id=?", (wallet, uid))
    conn.commit(); conn.close()

def save_bank_details(uid, bank_name, account_number, account_name):
    conn = get_conn()
    conn.execute("UPDATE users SET bank_name=?, account_number=?, account_name=? WHERE user_id=?",
                 (bank_name, account_number, account_name, uid))
    conn.commit(); conn.close()

# ── Users ─────────────────────────────────────────────────────

def gen_ref_code():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=7))

def upsert_user(uid, username, full_name, referred_by=None):
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT * FROM users WHERE user_id=?", (uid,))
    u = c.fetchone()
    if not u:
        code = gen_ref_code()
        while True:
            c.execute("SELECT 1 FROM users WHERE referral_code=?", (code,))
            if not c.fetchone(): break
            code = gen_ref_code()
        c.execute("INSERT INTO users(user_id,username,full_name,referral_code,referred_by) VALUES(?,?,?,?,?)",
                  (uid, username, full_name, code, referred_by))
        conn.commit()
        c.execute("SELECT * FROM users WHERE user_id=?", (uid,))
        u = c.fetchone()
    conn.close(); return dict(u)

def get_user(uid):
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT * FROM users WHERE user_id=?", (uid,))
    r = c.fetchone(); conn.close()
    return dict(r) if r else None

def get_all_users():
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT * FROM users ORDER BY joined_at DESC")
    rows = c.fetchall(); conn.close(); return [dict(r) for r in rows]

def get_balance(uid):
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT balance FROM users WHERE user_id=?", (uid,))
    r = c.fetchone(); conn.close(); return r["balance"] if r else 0.0

def add_balance(uid, amount):
    conn = get_conn()
    conn.execute("UPDATE users SET balance=balance+?,total_earned=total_earned+? WHERE user_id=?", (amount,amount,uid))
    conn.commit(); conn.close()

def deduct_balance(uid, amount):
    conn = get_conn()
    conn.execute("UPDATE users SET balance=balance-?,total_withdrawn=total_withdrawn+? WHERE user_id=?", (amount,amount,uid))
    conn.commit(); conn.close()

def ban_user(uid):
    conn = get_conn(); conn.execute("UPDATE users SET is_banned=1 WHERE user_id=?", (uid,)); conn.commit(); conn.close()

def unban_user(uid):
    conn = get_conn(); conn.execute("UPDATE users SET is_banned=0 WHERE user_id=?", (uid,)); conn.commit(); conn.close()

def get_referral_count(uid):
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT COUNT(*) as cnt FROM users WHERE referred_by=?", (uid,))
    r = c.fetchone(); conn.close(); return r["cnt"] if r else 0

def get_user_by_ref_code(code):
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT * FROM users WHERE referral_code=?", (code,))
    r = c.fetchone(); conn.close(); return dict(r) if r else None

# ── Numbers ───────────────────────────────────────────────────

def add_number(number, country, service, price):
    conn = get_conn()
    try:
        conn.execute("INSERT OR IGNORE INTO numbers(number,country,service,price) VALUES(?,?,?,?)",
                     (number, country, service, float(price)))
        conn.commit(); return True
    except: return False
    finally: conn.close()

def get_services_with_count():
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT service, COUNT(*) as cnt, MIN(price) as price FROM numbers WHERE status='available' GROUP BY service ORDER BY service")
    rows = c.fetchall(); conn.close()
    return [(r["service"], r["cnt"], r["price"]) for r in rows]

def get_countries_by_service(service):
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT country, COUNT(*) as cnt, MIN(price) as price FROM numbers WHERE status='available' AND service=? GROUP BY country ORDER BY country", (service,))
    rows = c.fetchall(); conn.close()
    return [(r["country"], r["cnt"], r["price"]) for r in rows]

def get_countries_with_count():
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT country, COUNT(*) as cnt FROM numbers WHERE status='available' GROUP BY country ORDER BY country")
    rows = c.fetchall(); conn.close()
    return [(r["country"], r["cnt"]) for r in rows]

def get_all_countries_in_db():
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT country, COUNT(*) as cnt FROM numbers GROUP BY country ORDER BY country")
    rows = c.fetchall(); conn.close()
    return [(r["country"], r["cnt"]) for r in rows]

def get_stock_count():
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT COUNT(*) as cnt FROM numbers WHERE status='available'")
    r = c.fetchone(); conn.close(); return r["cnt"]

def get_number_by_value(num):
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT * FROM numbers WHERE number=?", (num,))
    r = c.fetchone(); conn.close(); return dict(r) if r else None

def get_assigned_number(uid):
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT * FROM numbers WHERE assigned_to=? AND status='assigned'", (uid,))
    r = c.fetchone(); conn.close(); return dict(r) if r else None

def assign_number(uid, country, service):
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT id,number FROM numbers WHERE status='available' AND country=? AND service=?", (country, service))
    rows = c.fetchall()
    if not rows: conn.close(); return None
    row = random.choice(rows)
    conn.execute("UPDATE numbers SET status='assigned',assigned_to=?,assigned_at=datetime('now') WHERE id=?", (uid, row["id"]))
    conn.commit(); conn.close(); return row["number"]

def mark_number_used(num):
    conn = get_conn()
    conn.execute("DELETE FROM numbers WHERE number=?", (num,))
    conn.commit(); conn.close()

def release_expired():
    conn = get_conn()
    conn.execute("""DELETE FROM numbers WHERE status='assigned' AND assigned_at IS NOT NULL
                    AND (strftime('%s','now')-strftime('%s',assigned_at))>?""", (NUMBER_EXPIRY,))
    conn.commit(); conn.close()

def delete_numbers_by_country(country):
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT COUNT(*) as cnt FROM numbers WHERE country=?", (country,))
    cnt = c.fetchone()["cnt"]
    conn.execute("DELETE FROM numbers WHERE country=?", (country,))
    conn.commit(); conn.close(); return cnt

def save_otp(number, country, service, otp_code, uid, raw_sms):
    conn = get_conn()
    conn.execute("INSERT INTO otps(number,country,service,otp_code,user_id,raw_sms) VALUES(?,?,?,?,?,?)",
                 (number, country, service, otp_code, uid, raw_sms))
    conn.commit(); conn.close()

def get_otp_stats():
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT COUNT(*) as t FROM otps"); total = c.fetchone()["t"]
    c.execute("SELECT COUNT(*) as t FROM otps WHERE date(received_at)=date('now')"); today = c.fetchone()["t"]
    conn.close(); return total, today

# ── Withdrawals ───────────────────────────────────────────────

def save_withdrawal(uid, amount, method, wallet=None, bank_name=None, account_number=None):
    conn = get_conn(); c = conn.cursor()
    c.execute("INSERT INTO withdrawals(user_id,amount,method,wallet,bank_name,account_number) VALUES(?,?,?,?,?,?)",
              (uid, amount, method, wallet, bank_name, account_number))
    wid = c.lastrowid
    conn.commit(); conn.close()
    return wid  # return actual inserted ID

def get_pending_withdrawals():
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT * FROM withdrawals WHERE status='pending' ORDER BY requested_at DESC")
    rows = c.fetchall(); conn.close(); return [dict(r) for r in rows]

def get_withdrawal_by_id(wid):
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT * FROM withdrawals WHERE id=?", (wid,))
    r = c.fetchone(); conn.close(); return dict(r) if r else None

def approve_withdrawal(wid):
    conn = get_conn(); conn.execute("UPDATE withdrawals SET status='approved' WHERE id=?", (wid,)); conn.commit(); conn.close()

def mark_payment_sent(wid):
    """Mark as paid AND delete from DB so it no longer shows in pending."""
    conn = get_conn()
    conn.execute("UPDATE withdrawals SET status='paid' WHERE id=?", (wid,))
    conn.commit(); conn.close()

def delete_withdrawal(wid):
    conn = get_conn()
    conn.execute("DELETE FROM withdrawals WHERE id=?", (wid,))
    conn.commit(); conn.close()

def reject_withdrawal(wid):
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT user_id,amount FROM withdrawals WHERE id=?", (wid,))
    r = c.fetchone()
    if r:
        conn.execute("UPDATE users SET balance=balance+?,total_withdrawn=total_withdrawn-? WHERE user_id=?",
                     (r["amount"], r["amount"], r["user_id"]))
    conn.execute("UPDATE withdrawals SET status='rejected' WHERE id=?", (wid,))
    conn.commit(); conn.close()

def get_pending_amount(uid):
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT SUM(amount) as t FROM withdrawals WHERE user_id=? AND status='pending'", (uid,))
    r = c.fetchone(); conn.close(); return r["t"] or 0.0

_withdrawal_msgs: dict = {}  # wid → [(admin_id, msg_id), ...]

# ============================================================
#  HELPERS
# ============================================================

def is_admin(uid): return uid in ADMIN_IDS

COUNTRY_FLAGS = {
    "nigeria":"🇳🇬","ghana":"🇬🇭","kenya":"🇰🇪","south africa":"🇿🇦","ethiopia":"🇪🇹",
    "tanzania":"🇹🇿","uganda":"🇺🇬","senegal":"🇸🇳","cameroon":"🇨🇲","ivory coast":"🇨🇮",
    "mali":"🇲🇱","togo":"🇹🇬","benin":"🇧🇯","niger":"🇳🇪","rwanda":"🇷🇼","zambia":"🇿🇲",
    "zimbabwe":"🇿🇼","angola":"🇦🇴","dr congo":"🇨🇩","mozambique":"🇲🇿","malawi":"🇲🇼",
    "namibia":"🇳🇦","liberia":"🇱🇷","sierra leone":"🇸🇱","gambia":"🇬🇲","gabon":"🇬🇦",
    "chad":"🇹🇩","sudan":"🇸🇩","somalia":"🇸🇴","egypt":"🇪🇬","morocco":"🇲🇦",
    "algeria":"🇩🇿","tunisia":"🇹🇳","libya":"🇱🇾","usa":"🇺🇸","canada":"🇨🇦",
    "uk":"🇬🇧","germany":"🇩🇪","france":"🇫🇷","italy":"🇮🇹","spain":"🇪🇸",
    "portugal":"🇵🇹","netherlands":"🇳🇱","belgium":"🇧🇪","switzerland":"🇨🇭",
    "sweden":"🇸🇪","norway":"🇳🇴","denmark":"🇩🇰","finland":"🇫🇮","poland":"🇵🇱",
    "ukraine":"🇺🇦","russia":"🇷🇺","turkey":"🇹🇷","romania":"🇷🇴","hungary":"🇭🇺",
    "greece":"🇬🇷","czech republic":"🇨🇿","austria":"🇦🇹","serbia":"🇷🇸","croatia":"🇭🇷",
    "georgia":"🇬🇪","armenia":"🇦🇲","azerbaijan":"🇦🇿","india":"🇮🇳","pakistan":"🇵🇰",
    "bangladesh":"🇧🇩","china":"🇨🇳","japan":"🇯🇵","south korea":"🇰🇷","vietnam":"🇻🇳",
    "thailand":"🇹🇭","malaysia":"🇲🇾","indonesia":"🇮🇩","philippines":"🇵🇭",
    "singapore":"🇸🇬","saudi arabia":"🇸🇦","uae":"🇦🇪","qatar":"🇶🇦","kuwait":"🇰🇼",
    "israel":"🇮🇱","iraq":"🇮🇶","iran":"🇮🇷","australia":"🇦🇺","new zealand":"🇳🇿",
    "brazil":"🇧🇷","colombia":"🇨🇴","mexico":"🇲🇽","argentina":"🇦🇷","chile":"🇨🇱",
    "peru":"🇵🇪","venezuela":"🇻🇪",
}

COUNTRY_CODES = {
    "1":("USA","🇺🇸"),"44":("UK","🇬🇧"),"234":("Nigeria","🇳🇬"),"233":("Ghana","🇬🇭"),
    "254":("Kenya","🇰🇪"),"27":("South Africa","🇿🇦"),"7":("Russia","🇷🇺"),
    "33":("France","🇫🇷"),"49":("Germany","🇩🇪"),"39":("Italy","🇮🇹"),
    "34":("Spain","🇪🇸"),"91":("India","🇮🇳"),"92":("Pakistan","🇵🇰"),
    "86":("China","🇨🇳"),"81":("Japan","🇯🇵"),"82":("South Korea","🇰🇷"),
    "55":("Brazil","🇧🇷"),"52":("Mexico","🇲🇽"),"966":("Saudi Arabia","🇸🇦"),
    "971":("UAE","🇦🇪"),"20":("Egypt","🇪🇬"),"212":("Morocco","🇲🇦"),
    "213":("Algeria","🇩🇿"),"216":("Tunisia","🇹🇳"),"256":("Uganda","🇺🇬"),
    "251":("Ethiopia","🇪🇹"),"255":("Tanzania","🇹🇿"),"221":("Senegal","🇸🇳"),
    "237":("Cameroon","🇨🇲"),"225":("Ivory Coast","🇨🇮"),"60":("Malaysia","🇲🇾"),
    "62":("Indonesia","🇮🇩"),"63":("Philippines","🇵🇭"),"66":("Thailand","🇹🇭"),
    "84":("Vietnam","🇻🇳"),"90":("Turkey","🇹🇷"),"380":("Ukraine","🇺🇦"),
    "48":("Poland","🇵🇱"),"31":("Netherlands","🇳🇱"),"32":("Belgium","🇧🇪"),
    "41":("Switzerland","🇨🇭"),"46":("Sweden","🇸🇪"),"47":("Norway","🇳🇴"),
    "45":("Denmark","🇩🇰"),"358":("Finland","🇫🇮"),"61":("Australia","🇦🇺"),
}

SERVICE_ICONS = {
    "WHATSAPP":"📱","FACEBOOK":"📘","INSTAGRAM":"📸","TELEGRAM":"✈️",
    "GOOGLE":"🔍","TWITTER":"🐦","TIKTOK":"🎵","SNAPCHAT":"👻",
    "AMAZON":"📦","PAYPAL":"💳","MICROSOFT":"🪟","APPLE":"🍎",
    "NETFLIX":"🎬","DISCORD":"🎮","UBER":"🚗","LINKEDIN":"💼",
}

def flag(country: str) -> str:
    return COUNTRY_FLAGS.get(country.strip().lower(), "🌍")

def country_from_number(num: str):
    d = re.sub(r"\D","",str(num))
    for l in (3,2,1):
        p = d[:l]
        if p in COUNTRY_CODES: return COUNTRY_CODES[p]
    return "Unknown","🌍"

def detect_service(cli: str, msg: str) -> str:
    t = (cli+" "+msg).lower()
    for svc,kws in {"WHATSAPP":["whatsapp"],"FACEBOOK":["facebook","fb"],
        "INSTAGRAM":["instagram"],"TELEGRAM":["telegram"],"GOOGLE":["google"],
        "TWITTER":["twitter"],"TIKTOK":["tiktok"],"SNAPCHAT":["snapchat"],
        "AMAZON":["amazon"],"PAYPAL":["paypal"],"MICROSOFT":["microsoft"],
        "APPLE":["apple"],"NETFLIX":["netflix"],"DISCORD":["discord"],
        "UBER":["uber"],"LINKEDIN":["linkedin"]}.items():
        if any(k in t for k in kws): return svc
    return cli.upper() if cli else "SMS"

def mask(num: str) -> str:
    d = re.sub(r"\D","",str(num))
    return f"+{'•'*max(len(d)-4,4)}{d[-4:]}" if len(d)>=4 else "+****"

def extract_otp(text: str):
    m = re.search(r'\b(\d{3,4})-(\d{3,4})\b', text)
    if m: return m.group(1)+"-"+m.group(2)
    for p in [r"code[:\s]+(\d{4,9})",r"OTP[:\s]+(\d{4,9})",r"is[:\s]+(\d{4,9})",r"\b(\d{4,9})\b"]:
        m = re.search(p, text, re.IGNORECASE)
        if m: return m.group(1)
    return None

def is_today(dt_str: str) -> bool:
    try: return datetime.strptime(dt_str[:19],"%Y-%m-%d %H:%M:%S").date()==datetime.now().date()
    except: return False

# ============================================================
#  UI BUILDERS
# ============================================================

def main_menu(uid):
    rows = [
        [KeyboardButton("📲 Get Number"), KeyboardButton("📊 My Status")],
        [KeyboardButton("💰 Wallet"),     KeyboardButton("🌍 Countries")],
        [KeyboardButton("💸 Withdraw"),   KeyboardButton("👥 Referral")],
    ]
    if is_admin(uid):
        rows.append([KeyboardButton("⚙️ Admin Panel")])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)

def admin_menu():
    trx_status = "✅ TRX ON" if is_trx_enabled() else "❌ TRX OFF"
    ngn_status = "✅ Naira ON" if is_ngn_enabled() else "❌ Naira OFF"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add Numbers",    callback_data="adm_add"),
         InlineKeyboardButton("❌ Delete Numbers", callback_data="adm_del")],
        [InlineKeyboardButton("👥 Users",          callback_data="adm_users"),
         InlineKeyboardButton("📊 Analytics",      callback_data="adm_stats")],
        [InlineKeyboardButton("🚫 Ban",            callback_data="adm_ban"),
         InlineKeyboardButton("✅ Unban",          callback_data="adm_unban")],
        [InlineKeyboardButton("📢 Broadcast",      callback_data="adm_broadcast")],
        [InlineKeyboardButton("💵 OTP Reward",     callback_data="adm_reward"),
         InlineKeyboardButton("💱 Naira Rate",     callback_data="adm_rate"),
         InlineKeyboardButton("🎁 Ref Bonus",      callback_data="adm_refbonus")],
        [InlineKeyboardButton(trx_status,          callback_data="adm_toggle_trx"),
         InlineKeyboardButton(ngn_status,          callback_data="adm_toggle_ngn")],
        [InlineKeyboardButton("💸 Withdrawals",    callback_data="adm_withdrawals")],
        [InlineKeyboardButton("🔙 Back",           callback_data="back_home")],
    ])

def start_text(name: str) -> str:
    return (
        f"👋 <b>Hey {name}!</b>\n\n"
        f"🔐 <b>Welcome to the OTP Number Bot</b>\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📲  Get virtual numbers\n"
        f"📨  Receive OTPs automatically\n"
        f"💰  Earn rewards per OTP\n"
        f"💸  Withdraw via Naira or USDT TRX\n"
        f"👥  Invite friends & earn bonuses\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👇 <b>Choose an option below to begin:</b>"
    )

def group_card(country: str, ctry_flag: str, service: str, svc_icon: str, masked_num: str, otp_text: str) -> str:
    return (
        f"{svc_icon} <b>{service}</b>  {ctry_flag}  <b>{country}</b>\n"
        f"📞  <code>{masked_num}</code>\n\n"
        f"<blockquote>{otp_text}</blockquote>\n\n"
        f"<b>#</b> Prefix: <code>{otp_text}</code>"
    )

def private_card(country: str, ctry_flag: str, service: str, svc_icon: str, full_num: str, otp_text: str, reward: float, balance: float) -> str:
    now = datetime.now().strftime("%d %b %Y · %H:%M:%S")
    return (
        f"✅ <b>OTP DELIVERED!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{ctry_flag}  <b>{country}</b>   {svc_icon}  <b>{service}</b>\n"
        f"📞  <code>+{full_num}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔑  <b>{otp_text}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💸  Earned:   <b>+${reward:.5f}</b>\n"
        f"💼  Balance:  <b>${balance:.5f}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🕐  <i>{now}</i>"
    )

# ============================================================
#  FORWARD SMS
# ============================================================

_full_messages = {}

async def forward_sms(bot, sms: dict):
    global _full_messages
    num = str(sms.get("num","")).strip()
    msg = str(sms.get("message","")).strip()
    cli = str(sms.get("cli","")).strip()
    if not num or not msg: return

    otp            = extract_otp(msg)
    country, ctry_flag = country_from_number(num)
    service        = detect_service(cli, msg)
    svc_icon       = SERVICE_ICONS.get(service,"📨")
    masked_num     = mask(num)
    otp_text       = otp if otp else msg[:60]
    number_row     = get_number_by_value(num)
    assigned_uid   = None
    reward         = get_otp_reward()

    if number_row and number_row["status"] == "assigned":
        assigned_uid = number_row["assigned_to"]
        country      = number_row["country"] or country
        service      = number_row["service"] or service
        ctry_flag    = flag(country)
        svc_icon     = SERVICE_ICONS.get(service,"📨")
        save_otp(num, country, service, otp, assigned_uid, msg)
        mark_number_used(num)
        if otp: add_balance(assigned_uid, reward)
    elif number_row:
        save_otp(num, number_row["country"] or country, number_row["service"] or service, otp, None, msg)

    # Private message to assigned user
    if assigned_uid:
        bal = get_balance(assigned_uid)
        pvt = private_card(country, ctry_flag, service, svc_icon, num, otp_text, reward, bal)
        try:
            from telegram import CopyTextButton as CTB
            pvt_kb = InlineKeyboardMarkup([[InlineKeyboardButton("📋 Copy OTP", copy_text=CTB(text=otp_text))]])
        except:
            pvt += f"\n\n<code>{otp_text}</code>"; pvt_kb = None
        try: await bot.send_message(assigned_uid, pvt, parse_mode="HTML", reply_markup=pvt_kb)
        except Exception as e: logger.error(f"Private msg: {e}")

    # Group forward — Nexus style
    key = f"otp_{abs(hash(num+msg))%9999999}"
    _full_messages[key] = msg[:500]
    grp = group_card(country, ctry_flag, service, svc_icon, masked_num, otp_text)
    try:
        from telegram import CopyTextButton as CTB
        grp_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(
                f"{svc_icon}  📋  {otp_text}  ─────────────────",
                copy_text=CTB(text=otp_text)
            )],
            [InlineKeyboardButton("✨ Channel",  url=GROUP_LINK),
             InlineKeyboardButton("🎯 Bot Link", url=f"https://t.me/{BOT_USERNAME}")],
        ])
    except:
        grp += f"\n\n<code>{otp_text}</code>"
        grp_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✨ Channel",  url=GROUP_LINK),
             InlineKeyboardButton("🎯 Bot Link", url=f"https://t.me/{BOT_USERNAME}")],
        ])
    try:
        await bot.send_message(GROUP_CHAT_ID, grp, parse_mode="HTML", reply_markup=grp_kb)
        logger.info(f"✅ [N] {masked_num} → {otp_text}")
    except Exception as e: logger.error(f"Group send: {e}")

# ============================================================
#  MEMBERSHIP CHECK
# ============================================================

async def is_member(bot, uid: int) -> bool:
    for chat in (REQUIRED_CHANNEL, REQUIRED_GROUP):
        # Skip if not configured
        if not chat or chat.strip() in ("", "@yourchannel", "@yourgroup"): continue
        try:
            m = await bot.get_chat_member(chat, uid)
            if m.status in ("left","kicked","banned"): return False
        except Exception as e:
            logger.warning(f"Membership check failed for {chat}: {e}")
            # If bot can't check (not admin in channel), let user through
            continue
    return True

async def send_join_prompt(update: Update):
    buttons = []
    if CHANNEL_LINK: buttons.append([InlineKeyboardButton("📢 Join Channel", url=CHANNEL_LINK)])
    if GROUP_JOIN_LINK: buttons.append([InlineKeyboardButton("👥 Join Group", url=GROUP_JOIN_LINK)])
    buttons.append([InlineKeyboardButton("✅ I've Joined", callback_data="check_join")])
    await update.message.reply_text(
        "🔒 <b>Access Required</b>\n\n"
        "You need to join our Channel and Group to use this bot.\n\n"
        + ("1️⃣ Join the Channel\n" if CHANNEL_LINK else "")
        + ("2️⃣ Join the Group\n" if GROUP_JOIN_LINK else "")
        + "\n3️⃣ Tap ✅ <b>I've Joined</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

# ============================================================
#  NORDAN API FETCH  (after_id cursor — never re-forwards old SMS)
# ============================================================

_nordan_after_id = 0  # cursor — advances with every fetch

def fetch_nordan() -> list:
    """Fetch only NEW messages using after_id cursor."""
    global _nordan_after_id
    all_msgs = []
    after_id = _nordan_after_id
    while True:
        try:
            r = requests.get(NORDAN_URL, params={
                "token":    NORDAN_TOKEN,
                "limit":    FETCH_LIMIT,
                "after_id": after_id,
            }, timeout=30)
            if r.status_code != 200:
                logger.error(f"Nordan HTTP {r.status_code}"); break
            d = r.json()
            if not d.get("success"):
                logger.error(f"Nordan error: {d.get('message','')}"); break
            msgs = d.get("messages", [])
            meta = d.get("meta", {})
            all_msgs.extend(msgs)
            next_id = meta.get("next_after_id", after_id)
            if next_id and next_id != after_id: after_id = next_id
            if not meta.get("has_more", False): break
        except Exception as e:
            logger.error(f"Nordan fetch: {e}"); break
    if after_id != _nordan_after_id: _nordan_after_id = after_id
    return all_msgs



# ============================================================
#  POLLING
# ============================================================

async def poll_nordan(bot):
    global _nordan_after_id
    logger.info("🚀 Nordan polling started")
    existing = fetch_nordan()
    logger.info(f"📋 Startup: {len(existing)} existing skipped. cursor={_nordan_after_id}")
    logger.info("✅ Nordan ready — watching for new SMS...")
    while True:
        try:
            new_msgs = fetch_nordan()
            if new_msgs:
                logger.info(f"📨 {len(new_msgs)} new from Nordan")
                for sms in new_msgs:
                    await forward_sms(bot, {
                        "num":     str(sms.get("number","")).strip(),
                        "message": str(sms.get("message","")).strip(),
                        "cli":     str(sms.get("cli","")).strip(),
                        "src":     "N",
                    })
            else:
                logger.info("⏭ No new SMS")
        except Exception as e: logger.error(f"Nordan poll: {e}")
        await asyncio.sleep(POLL_INTERVAL)

async def expiry_loop():
    while True:
        release_expired(); await asyncio.sleep(300)

# ============================================================
#  HELPERS FOR KEYBOARD FLOW
# ============================================================

def _build_service_menu():
    services = get_services_with_count()
    if not services: return None
    rows = [[InlineKeyboardButton(
        f"{SERVICE_ICONS.get(s.upper(),'📨')} {s}  ·  {cnt} avail  ·  ${p:.4f}",
        callback_data=f"svc:{s}")] for s,cnt,p in services]
    return InlineKeyboardMarkup(rows)

# ============================================================
#  HANDLERS
# ============================================================

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ref  = ctx.args[0] if ctx.args else None
    referred_by = None
    if ref:
        ref_user = get_user_by_ref_code(ref)
        if ref_user and ref_user["user_id"] != user.id:
            referred_by = ref_user["user_id"]

    if not is_admin(user.id) and not await is_member(ctx.bot, user.id):
        await send_join_prompt(update); return

    u = upsert_user(user.id, user.username or "", user.full_name or "", referred_by)
    if u["is_banned"]:
        await update.message.reply_text("🚫 You are banned. Contact support."); return

    # Referral bonus on first join
    if referred_by and not u.get("referral_paid"):
        bonus = get_referral_bonus()
        add_balance(referred_by, bonus)
        try: await ctx.bot.send_message(referred_by,
            f"🎉 <b>Referral Bonus!</b>\n\nSomeone joined using your link.\n+<b>${bonus:.5f}</b> added to your balance!",
            parse_mode="HTML")
        except: pass

    await update.message.reply_text(
        start_text(user.first_name), parse_mode="HTML", reply_markup=main_menu(user.id)
    )

async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global NAIRA_RATE
    q    = update.callback_query; await q.answer()
    data = q.data; uid = q.from_user.id; user = q.from_user

    # ── Full SMS popup ─────────────────────────────────────────
    if data.startswith("otp_"):
        txt = _full_messages.get(data,"Message expired.")
        await q.answer(txt[:200], show_alert=True); return

    # ── Membership check ───────────────────────────────────────
    if data == "check_join":
        if await is_member(ctx.bot, uid):
            u2 = upsert_user(uid, user.username or "", user.full_name or "")
            await q.edit_message_text(
                f"✅ <b>Access Granted!</b>\n\n"
                f"👋 Welcome, <b>{user.first_name}</b>!\n\n"
                f"Tap the button below to get started.",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🚀 Start", callback_data="back_home")],
                ])
            )
        else:
            channels = []
            if REQUIRED_CHANNEL and REQUIRED_CHANNEL not in ("@yourchannel",""):
                channels.append(REQUIRED_CHANNEL)
            if REQUIRED_GROUP and REQUIRED_GROUP not in ("@yourgroup",""):
                channels.append(REQUIRED_GROUP)
            if channels:
                await q.answer(f"❌ Please join: {', '.join(channels)} first!", show_alert=True)
            else:
                # No channels configured — just let them in
                upsert_user(uid, user.username or "", user.full_name or "")
                await q.edit_message_text(
                    f"✅ <b>Welcome, {user.first_name}!</b>",
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🚀 Start", callback_data="back_home")],
                    ])
                )
        return

    # ── Home ───────────────────────────────────────────────────
    if data == "back_home":
        u = get_user(uid)
        if u and u["is_banned"]: await q.edit_message_text("🚫 You are banned."); return
        await q.edit_message_text(start_text(user.first_name), parse_mode="HTML", reply_markup=main_menu(uid))
        return

    # ── Get Number ─────────────────────────────────────────────
    if data == "get_number":
        services = get_services_with_count()
        if not services:
            await q.edit_message_text("😔 <b>No numbers in stock right now.</b>\nTry again later.",
                                      parse_mode="HTML", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back",callback_data="back_home")]])
                                      ); return
        rows = [[InlineKeyboardButton(
            f"{SERVICE_ICONS.get(s.upper(),'📨')} {s}  ·  {cnt} avail  ·  ${p:.4f}",
            callback_data=f"svc:{s}")] for s,cnt,p in services]
        rows.append([InlineKeyboardButton("🔙 Back", callback_data="back_home")])
        await q.edit_message_text(
            "📲 <b>Select Service</b>\n<i>Pick the service you want to verify on</i>",
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows)); return

    if data.startswith("svc:"):
        svc = data.split(":",1)[1]
        countries = get_countries_by_service(svc)
        if not countries:
            await q.edit_message_text(f"😔 No numbers for <b>{svc}</b>.", parse_mode="HTML"); return
        icon = SERVICE_ICONS.get(svc.upper(),"📨")
        rows = [[InlineKeyboardButton(
            f"{COUNTRY_FLAGS.get(c.lower(),'🌍')} {c}  ·  {cnt} avail  ·  ${p:.4f}",
            callback_data=f"cntry:{c}:{svc}")] for c,cnt,p in countries]
        rows.append([InlineKeyboardButton("🔙 Back", callback_data="get_number")])
        await q.edit_message_text(
            f"{icon} <b>{svc}</b>\n\n🌍 <b>Select Country:</b>",
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows)); return

    if data.startswith("cntry:"):
        _, country, service = data.split(":",2)
        number = assign_number(uid, country, service)
        if not number:
            await q.edit_message_text(
                f"😔 <b>No numbers left</b> for <b>{service}</b> in <b>{country}</b>.\nTry another option.",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back",callback_data=f"svc:{service}")]])
            ); return
        ctry_flag = COUNTRY_FLAGS.get(country.lower(),"🌍")
        svc_icon  = SERVICE_ICONS.get(service.upper(),"📨")
        reward    = get_otp_reward()
        await q.edit_message_text(
            f"✅ <b>Number Assigned!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{ctry_flag}  <b>{country}</b>   {svc_icon}  <b>{service}</b>\n"
            f"📞  <code>+{number}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💰  Earn <b>${reward:.5f}</b> when OTP arrives\n"
            f"⏳  Waiting for your OTP...",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔁 Change Number", callback_data=f"change:{country}:{service}"),
                 InlineKeyboardButton("🔙 Services",      callback_data="get_number")],
                [InlineKeyboardButton("📢 OTP Group",     url=GROUP_LINK)],
            ])
        ); return

    if data.startswith("change:"):
        _, country, service = data.split(":",2)
        old = get_assigned_number(uid)
        if old:
            conn = get_conn()
            conn.execute("DELETE FROM numbers WHERE id=?", (old["id"],))
            conn.commit(); conn.close()
        number = assign_number(uid, country, service)
        if not number:
            await q.answer("😔 No more numbers available.", show_alert=True); return
        ctry_flag = COUNTRY_FLAGS.get(country.lower(),"🌍")
        svc_icon  = SERVICE_ICONS.get(service.upper(),"📨")
        reward    = get_otp_reward()
        await q.edit_message_text(
            f"🔁 <b>New Number Assigned!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{ctry_flag}  <b>{country}</b>   {svc_icon}  <b>{service}</b>\n"
            f"📞  <code>+{number}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"⏳  Waiting for OTP...",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔁 Change Again", callback_data=f"change:{country}:{service}"),
                 InlineKeyboardButton("🔙 Services",     callback_data="get_number")],
                [InlineKeyboardButton("📢 OTP Group",    url=GROUP_LINK)],
            ])
        ); return

    # ── Status ─────────────────────────────────────────────────
    if data == "my_status":
        num = get_assigned_number(uid)
        if num:
            ctry_flag = COUNTRY_FLAGS.get(num["country"].lower(),"🌍")
            svc_icon  = SERVICE_ICONS.get(num["service"].upper(),"📨")
            await q.edit_message_text(
                f"📊 <b>Active Number</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"{ctry_flag}  <b>{num['country']}</b>   {svc_icon}  <b>{num['service']}</b>\n"
                f"📞  <code>+{num['number']}</code>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"⏳  Waiting for OTP...",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔁 Change Number", callback_data=f"change:{num['country']}:{num['service']}"),
                     InlineKeyboardButton("🔙 Services",      callback_data="get_number")],
                    [InlineKeyboardButton("🔙 Home", callback_data="back_home")],
                ])
            )
        else:
            await q.edit_message_text(
                "📊 <b>No Active Number</b>\n\nYou don't have a number assigned.\nTap below to get one.",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📲 Get Number", callback_data="get_number")],
                    [InlineKeyboardButton("🔙 Home",       callback_data="back_home")],
                ])
            )
        return

    # ── Wallet ─────────────────────────────────────────────────
    if data == "wallet":
        u   = get_user(uid) or upsert_user(uid, user.username or "", user.full_name or "")
        bal = u.get("balance",0.0) or 0.0
        ngn = bal * NAIRA_RATE
        earned     = u.get("total_earned",0.0) or 0.0
        withdrawn  = u.get("total_withdrawn",0.0) or 0.0
        pending    = get_pending_amount(uid)
        trx_wallet = u.get("trx_wallet") or "❌ Not set"
        bank_name  = u.get("bank_name") or "❌ Not set"
        acct_num   = u.get("account_number") or "—"
        acct_name  = u.get("account_name") or "—"
        ref_count  = get_referral_count(uid)
        ref_code   = u.get("referral_code","N/A")
        await q.edit_message_text(
            f"💰 <b>Your Wallet</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👤  ID: <code>{uid}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📈  Total Earned:   <b>${earned:.5f}</b>\n"
            f"✅  Available:      <b>${bal:.5f}</b>  (~₦{ngn:,.0f})\n"
            f"⏳  Pending:        <b>${pending:.5f}</b>\n"
            f"💸  Withdrawn:      <b>${withdrawn:.5f}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👥  Referrals:      <b>{ref_count}</b>\n"
            f"🔗  Ref Code:       <code>{ref_code}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💎  TRX Wallet:\n<code>{trx_wallet}</code>\n\n"
            f"🏦  Bank: <b>{bank_name}</b>\n"
            f"💳  Acct No: <code>{acct_num}</code>\n"
            f"👤  Acct Name: <b>{acct_name}</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💸 Withdraw",           callback_data="withdraw_menu")],
                [InlineKeyboardButton("✏️ Edit TRX/Naira Wallet", callback_data="edit_wallet_menu")],
                [InlineKeyboardButton("🔙 Home",               callback_data="back_home")],
            ])
        ); return

    if data == "edit_wallet_menu":
        await q.edit_message_text(
            "✏️ <b>Edit Wallet Details</b>\n\nChoose which wallet to update:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💎 TRX Wallet",  callback_data="edit_trx")],
                [InlineKeyboardButton("🏦 Naira Wallet", callback_data="edit_ngn")],
                [InlineKeyboardButton("🔙 Back",         callback_data="wallet")],
            ])
        ); return

    if data == "edit_trx":
        if not is_trx_enabled():
            await q.answer("❌ TRX withdrawals are currently unavailable.", show_alert=True); return
        ctx.user_data["state"] = "set_wallet"
        await q.edit_message_text(
            "💎 <b>Enter your TRX wallet address:</b>",
            parse_mode="HTML"); return

    if data == "edit_ngn":
        if not is_ngn_enabled():
            await q.answer("❌ Naira withdrawals are currently unavailable.", show_alert=True); return
        ctx.user_data["state"] = "set_ngn_wallet"
        await q.edit_message_text(
            "🏦 <b>Enter your Naira bank details</b>\n\n"
            "Send in this format:\n"
            "<code>Bank Name | Account Number | Account Name</code>\n\n"
            "Example:\n"
            "<code>GTBank | 0123456789 | John Doe</code>",
            parse_mode="HTML"); return

    # ── Countries ──────────────────────────────────────────────
    if data == "countries":
        items = get_countries_with_count()
        if not items:
            await q.edit_message_text("😔 No numbers in stock.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back",callback_data="back_home")]])
                                      ); return
        lines = ["🌍 <b>Available Countries</b>\n━━━━━━━━━━━━━━━━━━━━━━"]
        for name, cnt in items:
            lines.append(f"{COUNTRY_FLAGS.get(name.lower(),'🌍')}  {name}  —  <b>{cnt}</b> numbers")
        await q.edit_message_text("\n".join(lines), parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Home",callback_data="back_home")]])
        ); return

    # ── Referral ───────────────────────────────────────────────
    if data == "referral":
        u        = get_user(uid) or upsert_user(uid, user.username or "", user.full_name or "")
        ref_code = u.get("referral_code","N/A")
        ref_link = f"https://t.me/{BOT_USERNAME}?start={ref_code}"
        bonus    = get_referral_bonus()
        count    = get_referral_count(uid)
        await q.edit_message_text(
            f"👥 <b>Referral Program</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎁  Bonus per referral: <b>${bonus:.5f}</b>\n"
            f"👤  Your referrals: <b>{count}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🔗  Your Link:\n<code>{ref_link}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"<i>Share your link and earn when friends join!</i>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Home",callback_data="back_home")]])
        ); return

    # ── Withdraw ───────────────────────────────────────────────
    if data == "withdraw_menu":
        u   = get_user(uid) or upsert_user(uid, user.username or "", user.full_name or "")
        bal = u.get("balance",0.0) or 0.0
        ngn = bal * NAIRA_RATE
        trx_on = is_trx_enabled(); ngn_on = is_ngn_enabled()
        method_btns = []
        if trx_on: method_btns.append(InlineKeyboardButton("💎 USDT TRX", callback_data="w_trx"))
        if ngn_on: method_btns.append(InlineKeyboardButton("🇳🇬 Naira",  callback_data="w_ngn"))
        rows = []
        if method_btns: rows.append(method_btns)
        rows.append([InlineKeyboardButton("🔙 Home", callback_data="back_home")])
        no_methods = not trx_on and not ngn_on
        await q.edit_message_text(
            f"💸 <b>Withdraw Funds</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💼  Balance:   <b>${bal:.5f}</b>  (~₦{ngn:,.0f})\n"
            f"📉  Minimum:   <b>${MIN_WITHDRAWAL}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            + ("⚠️ No withdrawal methods available right now." if no_methods else "Choose your withdrawal method:"),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(rows)
        ); return

    if data == "set_wallet":
        ctx.user_data["state"] = "set_wallet"
        await q.edit_message_text(
            "👛 <b>Enter your TRX wallet address:</b>",
            parse_mode="HTML"); return

    if data == "w_trx":
        if not is_trx_enabled():
            await q.answer("❌ TRX withdrawals are currently unavailable.", show_alert=True); return
        u   = get_user(uid)
        bal = u.get("balance",0.0) if u else 0.0
        if bal < MIN_WITHDRAWAL:
            await q.answer(f"❌ Minimum is ${MIN_WITHDRAWAL:.2f}. Your balance: ${bal:.5f}", show_alert=True); return
        wallet = u.get("trx_wallet") if u else None
        if not wallet:
            await q.answer("❌ No TRX wallet saved. Go to Wallet → Edit TRX/Naira Wallet first.", show_alert=True); return
        ctx.user_data["pending_w"] = {"method":"trx","wallet":wallet,"amount":bal}
        await q.edit_message_text(
            f"💎 <b>Confirm USDT TRX Withdrawal</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💰  Amount:  <b>${bal:.5f}</b>\n"
            f"👛  Wallet:  <code>{wallet}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Confirm to proceed:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Confirm", callback_data="w_confirm"),
                 InlineKeyboardButton("❌ Cancel",  callback_data="withdraw_menu")],
            ])
        ); return

    if data == "w_ngn":
        if not is_ngn_enabled():
            await q.answer("❌ Naira withdrawals are currently unavailable.", show_alert=True); return
        u   = get_user(uid)
        bal = u.get("balance",0.0) if u else 0.0
        if bal < MIN_WITHDRAWAL:
            await q.answer(f"❌ Minimum is ${MIN_WITHDRAWAL:.2f}. Your balance: ${bal:.5f}", show_alert=True); return
        bank_name  = u.get("bank_name") if u else None
        acct_num   = u.get("account_number") if u else None
        acct_name  = u.get("account_name") if u else None
        if not bank_name or not acct_num:
            await q.answer("❌ No bank details saved. Go to Wallet → Edit TRX/Naira Wallet first.", show_alert=True); return
        ctx.user_data["pending_w"] = {"method":"ngn","amount":bal,"ngn":bal*NAIRA_RATE,
                                      "bank_name":bank_name,"account_number":acct_num,"account_name":acct_name}
        await q.edit_message_text(
            f"🇳🇬 <b>Confirm Naira Withdrawal</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💰  Amount:   <b>₦{bal*NAIRA_RATE:,.0f}</b>  (${bal:.5f})\n"
            f"🏦  Bank:     <b>{bank_name}</b>\n"
            f"💳  Acct No:  <code>{acct_num}</code>\n"
            f"👤  Acct Name: <b>{acct_name}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Confirm to proceed:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Confirm", callback_data="w_confirm"),
                 InlineKeyboardButton("❌ Cancel",  callback_data="withdraw_menu")],
            ])
        ); return

    if data == "w_confirm":
        pw = ctx.user_data.pop("pending_w", None)
        if not pw: await q.answer("Session expired.", show_alert=True); return
        deduct_balance(uid, pw["amount"])
        wid = save_withdrawal(uid, pw["amount"], pw["method"], wallet=pw.get("wallet"))
        _withdrawal_msgs[wid] = []
        method_label = "TRX" if pw["method"] == "trx" else "Naira"
        if pw["method"] == "trx":
            pay_line = f"👛  <code>{pw.get('wallet','')}</code>"
            save_wallet_arg = {"wallet": pw.get("wallet")}
        else:
            pay_line = (f"🏦  <b>{pw.get('bank_name','')}</b>\n"
                        f"💳  <code>{pw.get('account_number','')}</code>\n"
                        f"👤  {pw.get('account_name','')}")
            save_wallet_arg = {"bank_name": pw.get("bank_name"), "account_number": pw.get("account_number")}
        notif = (
            f"💸 <b>Withdrawal Request</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👤  <code>{uid}</code>  @{user.username or 'N/A'}\n"
            f"💰  ${pw['amount']:.5f} via <b>{method_label}</b>\n"
            f"{pay_line}"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Approve",      callback_data=f"adm_approve:{uid}:{pw['amount']:.5f}:{wid}"),
             InlineKeyboardButton("❌ Reject",       callback_data=f"adm_reject:{uid}:{wid}")],
            [InlineKeyboardButton("💸 Payment Sent", callback_data=f"adm_paid:{wid}")],
        ])
        for admin in ADMIN_IDS:
            try:
                sent = await ctx.bot.send_message(admin, notif, parse_mode="HTML", reply_markup=kb)
                _withdrawal_msgs[wid].append((admin, sent.message_id))
            except: pass
        await q.edit_message_text(
            f"✅ <b>Withdrawal Submitted!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💰  ${pw['amount']:.5f} via <b>{method_label}</b>\n"
            f"{pay_line}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"⏳  Admin will process shortly.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Home",callback_data="back_home")]])
        ); return

    # ── Admin panel ────────────────────────────────────────────
    if data == "admin_panel":
        if not is_admin(uid): return
        await q.edit_message_text("⚙️ <b>Admin Panel</b>", parse_mode="HTML", reply_markup=admin_menu()); return

    if not is_admin(uid): return

    if data == "adm_toggle_trx":
        new_val = "0" if is_trx_enabled() else "1"
        set_setting("withdraw_trx", new_val)
        status = "✅ Enabled" if new_val == "1" else "❌ Disabled"
        await q.answer(f"TRX withdrawals {status}", show_alert=True)
        await q.edit_message_reply_markup(reply_markup=admin_menu()); return

    if data == "adm_toggle_ngn":
        new_val = "0" if is_ngn_enabled() else "1"
        set_setting("withdraw_ngn", new_val)
        status = "✅ Enabled" if new_val == "1" else "❌ Disabled"
        await q.answer(f"Naira withdrawals {status}", show_alert=True)
        await q.edit_message_reply_markup(reply_markup=admin_menu()); return

    if data == "adm_add":
        ctx.user_data["state"] = "adm_add_country"
        await q.edit_message_text(
            "➕ <b>Add Numbers</b>\n\n"
            "Step 1: Enter the <b>Country Name</b>:\n"
            "<i>e.g. Nigeria</i>",
            parse_mode="HTML"); return

    if data == "adm_del":
        countries = get_all_countries_in_db()
        if not countries:
            await q.edit_message_text("No numbers in database.", reply_markup=admin_menu()); return
        rows = [[InlineKeyboardButton(
            f"{COUNTRY_FLAGS.get(c.lower(),'🌍')} {c}  ({cnt})",
            callback_data=f"del_country:{c}")] for c,cnt in countries]
        rows.append([InlineKeyboardButton("🔙 Back", callback_data="admin_panel")])
        await q.edit_message_text(
            "❌ <b>Delete Numbers</b>\n\nTap a country to delete all its numbers:",
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows)); return

    if data.startswith("del_country:"):
        country = data.split(":",1)[1]
        await q.edit_message_text(
            f"⚠️ Delete ALL numbers for <b>{country}</b>?\n\nThis cannot be undone!",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Yes, Delete", callback_data=f"del_confirm:{country}"),
                 InlineKeyboardButton("❌ Cancel",      callback_data="adm_del")],
            ])
        ); return

    if data.startswith("del_confirm:"):
        country = data.split(":",1)[1]
        deleted = delete_numbers_by_country(country)
        await q.edit_message_text(
            f"✅ Deleted <b>{deleted}</b> numbers from <b>{country}</b>.",
            parse_mode="HTML", reply_markup=admin_menu()); return

    if data == "adm_users":
        users = get_all_users()
        lines = [f"👥 <b>Users ({len(users)})</b>\n━━━━━━━━━━━━━━━━━━━━━━"]
        for u in users[:25]:
            status = "🚫" if u.get("is_banned") else "✅"
            lines.append(f"{status} <code>{u['user_id']}</code> @{u.get('username','N/A')} — <b>${u.get('balance',0):.4f}</b>")
        if len(users)>25: lines.append(f"\n…and {len(users)-25} more")
        await q.edit_message_text("\n".join(lines), parse_mode="HTML", reply_markup=admin_menu()); return

    if data == "adm_stats":
        total_otp, today_otp = get_otp_stats()
        stock = get_stock_count()
        users = get_all_users()
        total_bal = sum(u.get("balance",0) or 0 for u in users)
        pending_w = get_pending_withdrawals()
        await q.edit_message_text(
            f"📊 <b>Analytics</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👥  Total Users:       <b>{len(users)}</b>\n"
            f"📦  Numbers in Stock:  <b>{stock}</b>\n"
            f"📨  OTPs Today:        <b>{today_otp}</b>\n"
            f"📨  OTPs Total:        <b>{total_otp}</b>\n"
            f"💰  Total Balances:    <b>${total_bal:.4f}</b>\n"
            f"⏳  Pending Withdraws: <b>{len(pending_w)}</b>",
            parse_mode="HTML", reply_markup=admin_menu()); return

    if data == "adm_ban":
        ctx.user_data["state"] = "adm_ban"
        await q.edit_message_text("🚫 Send the <b>User ID</b> to ban:", parse_mode="HTML"); return

    if data == "adm_unban":
        ctx.user_data["state"] = "adm_unban"
        await q.edit_message_text("✅ Send the <b>User ID</b> to unban:", parse_mode="HTML"); return

    if data == "adm_broadcast":
        ctx.user_data["state"] = "adm_broadcast"
        await q.edit_message_text("📢 <b>Broadcast</b>\n\nSend the message to broadcast to all users:", parse_mode="HTML"); return

    if data == "adm_reward":
        ctx.user_data["state"] = "adm_reward"
        await q.edit_message_text(
            f"💵 Current OTP Reward: <b>${get_otp_reward():.5f}</b>\n\nEnter new reward amount ($):",
            parse_mode="HTML"); return

    if data == "adm_rate":
        ctx.user_data["state"] = "adm_rate"
        await q.edit_message_text(
            f"💱 Current Naira Rate: <b>₦{NAIRA_RATE:,}</b> per $1\n\nEnter new rate:",
            parse_mode="HTML"); return

    if data == "adm_refbonus":
        ctx.user_data["state"] = "adm_refbonus"
        await q.edit_message_text(
            f"🎁 Current Referral Bonus: <b>${get_referral_bonus():.5f}</b>\n\nEnter new bonus amount ($):",
            parse_mode="HTML"); return

    if data == "adm_withdrawals":
        pending = get_pending_withdrawals()
        if not pending:
            await q.edit_message_text("✅ No pending withdrawals.", reply_markup=admin_menu()); return
        await q.edit_message_text(f"💸 <b>{len(pending)} Pending Withdrawal(s)</b>\nSending below...", parse_mode="HTML")
        for w in pending[:20]:
            wid = w["id"]
            if w["method"] == "trx":
                pay_info = f"💎 TRX:\n<code>{w.get('wallet','N/A')}</code>"
            else:
                pay_info = f"🏦 <b>{w.get('bank_name','N/A')}</b>\n<code>{w.get('account_number','N/A')}</code>"
            notif = (
                f"💸 <b>Withdrawal</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"👤  <code>{w['user_id']}</code>\n"
                f"💰  <b>${w['amount']:.5f}</b>  (~₦{w['amount']*NAIRA_RATE:,.0f})\n"
                f"📲  <b>{w['method'].upper()}</b>\n"
                f"{pay_info}\n"
                f"🕐  {w['requested_at']}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━"
            )
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Approve",      callback_data=f"adm_approve:{w['user_id']}:{w['amount']:.5f}:{wid}"),
                 InlineKeyboardButton("❌ Reject",       callback_data=f"adm_reject:{w['user_id']}:{wid}")],
                [InlineKeyboardButton("💸 Payment Sent", callback_data=f"adm_paid:{wid}")],
            ])
            _withdrawal_msgs[wid] = []
            for admin in ADMIN_IDS:
                try:
                    sent = await ctx.bot.send_message(admin, notif, parse_mode="HTML", reply_markup=kb)
                    _withdrawal_msgs[wid].append((admin, sent.message_id))
                except: pass
        return

    # ── Withdrawal actions ─────────────────────────────────────
    if data.startswith("adm_approve:"):
        parts = data.split(":"); target_uid=int(parts[1]); amount=float(parts[2]); wid=int(parts[3])
        w = get_withdrawal_by_id(wid)
        if not w or w["status"]!="pending":
            await q.answer("⚠️ Already processed.", show_alert=True)
            try: await q.delete_message()
            except: pass
            return
        approve_withdrawal(wid)
        for (adm,mid) in _withdrawal_msgs.pop(wid,[]):
            try: await ctx.bot.delete_message(adm, mid)
            except: pass
        try: await ctx.bot.send_message(target_uid,
            f"✅ <b>Withdrawal Approved!</b>\n\n💰 ${amount:.5f}\n💸 Payment will be sent shortly.",
            parse_mode="HTML")
        except: pass
        await ctx.bot.send_message(uid, f"✅ Approved withdrawal #{wid}", parse_mode="HTML"); return

    if data.startswith("adm_reject:"):
        parts = data.split(":"); target_uid=int(parts[1]); wid=int(parts[2])
        w = get_withdrawal_by_id(wid)
        if not w or w["status"]!="pending":
            await q.answer("⚠️ Already processed.", show_alert=True)
            try: await q.delete_message()
            except: pass
            return
        reject_withdrawal(wid)
        for (adm,mid) in _withdrawal_msgs.pop(wid,[]):
            try: await ctx.bot.delete_message(adm, mid)
            except: pass
        try: await ctx.bot.send_message(target_uid,
            "❌ <b>Withdrawal Rejected.</b>\n\nYour balance has been restored.\nContact support if needed.",
            parse_mode="HTML")
        except: pass
        await ctx.bot.send_message(uid, f"❌ Rejected #{wid} — balance restored.", parse_mode="HTML"); return

    if data.startswith("adm_paid:"):
        wid = int(data.split(":")[1])
        w = get_withdrawal_by_id(wid)
        if not w or w["status"] not in ("pending","approved"):
            await q.answer("⚠️ Already marked or not found.", show_alert=True)
            try: await q.delete_message()
            except: pass
            return
        mark_payment_sent(wid)
        delete_withdrawal(wid)   # ← remove from DB so it never shows in pending again
        for (adm,mid) in _withdrawal_msgs.pop(wid,[]):
            try: await ctx.bot.delete_message(adm, mid)
            except: pass
        method = w.get("method","trx").upper()
        wallet_info = f"👛 {w.get('wallet','')}" if w.get("method")=="trx" else f"🏦 {w.get('bank_name','')}"
        try: await ctx.bot.send_message(w["user_id"],
            f"💸 <b>Payment Sent!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💰  ${w['amount']:.5f} via <b>{method}</b>\n"
            f"{wallet_info}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"✅ Check your wallet/account.",
            parse_mode="HTML")
        except: pass
        await ctx.bot.send_message(uid, f"✅ Withdrawal #{wid} — <b>Payment Sent</b> & removed from list.", parse_mode="HTML"); return

# ============================================================
#  MESSAGE HANDLER (state machine)
# ============================================================

async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global NAIRA_RATE
    user  = update.effective_user; text = (update.message.text or "").strip()
    u     = upsert_user(user.id, user.username or "", user.full_name or "")
    state = ctx.user_data.get("state","")

    if u["is_banned"]: await update.message.reply_text("🚫 You are banned."); return
    if not is_admin(user.id) and not await is_member(ctx.bot, user.id):
        await send_join_prompt(update); return

    # ── Admin: Add numbers multi-step ─────────────────────────
    if is_admin(user.id):
        if state == "adm_add_country":
            ctx.user_data["adm_country"] = text
            ctx.user_data["state"]       = "adm_add_service"
            await update.message.reply_text(
                f"✅ Country: <b>{text}</b>\n\nStep 2: Enter the <b>Service Name</b>:\n<i>e.g. WhatsApp</i>",
                parse_mode="HTML"); return

        if state == "adm_add_service":
            ctx.user_data["adm_service"] = text
            ctx.user_data["state"]       = "adm_add_price"
            await update.message.reply_text(
                f"✅ Service: <b>{text}</b>\n\nStep 3: Enter the <b>Price per number</b> in USD:\n<i>e.g. 0.005</i>",
                parse_mode="HTML"); return

        if state == "adm_add_price":
            try: price = float(text)
            except:
                await update.message.reply_text("❌ Invalid price. Enter a number like <code>0.005</code>", parse_mode="HTML"); return
            ctx.user_data["adm_price"] = price
            ctx.user_data["state"]     = "adm_add_numbers"
            await update.message.reply_text(
                f"✅ Price: <b>${price:.4f}</b>\n\n"
                f"Step 4: Send the numbers now.\n"
                f"One number per line, or paste a .txt file:\n"
                f"<code>2348012345678</code>",
                parse_mode="HTML"); return

        if state == "adm_add_numbers":
            country = ctx.user_data.pop("adm_country","")
            service = ctx.user_data.pop("adm_service","")
            price   = ctx.user_data.pop("adm_price", 0.005)
            ctx.user_data.pop("state",None)
            added=0; failed=0
            for line in text.strip().splitlines():
                num = re.sub(r"\D","",line.strip())
                if num:
                    if add_number(num, country, service, price): added+=1
                    else: failed+=1
            await update.message.reply_text(
                f"✅ Added <b>{added}</b> numbers\n"
                f"❌ Failed/Duplicates: <b>{failed}</b>\n\n"
                f"🌍 Country: <b>{country}</b>\n"
                f"📱 Service: <b>{service}</b>\n"
                f"💰 Price: <b>${price:.4f}</b>",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ Admin Panel",callback_data="admin_panel")]])
            ); return

        if state == "adm_ban":
            ctx.user_data.pop("state",None)
            try:
                target=int(text); ban_user(target)
                try: await ctx.bot.send_message(target,"🚫 You have been banned.")
                except: pass
                await update.message.reply_text(f"✅ User <code>{target}</code> banned.", parse_mode="HTML")
            except: await update.message.reply_text("❌ Invalid ID.")
            return

        if state == "adm_unban":
            ctx.user_data.pop("state",None)
            try:
                target=int(text); unban_user(target)
                try: await ctx.bot.send_message(target,"✅ You have been unbanned!")
                except: pass
                await update.message.reply_text(f"✅ User <code>{target}</code> unbanned.", parse_mode="HTML")
            except: await update.message.reply_text("❌ Invalid ID.")
            return

        if state == "adm_broadcast":
            ctx.user_data.pop("state",None)
            users=get_all_users(); ok=0
            for u in users:
                try: await ctx.bot.send_message(u["user_id"],text,parse_mode="HTML"); ok+=1
                except: pass
            await update.message.reply_text(f"📢 Sent to <b>{ok}/{len(users)}</b> users.", parse_mode="HTML"); return

        if state == "adm_reward":
            ctx.user_data.pop("state",None)
            try: set_setting("otp_reward",float(text)); await update.message.reply_text(f"✅ OTP Reward set to <b>${float(text):.5f}</b>", parse_mode="HTML")
            except: await update.message.reply_text("❌ Invalid amount.")
            return

        if state == "adm_rate":
            ctx.user_data.pop("state",None)
            try: NAIRA_RATE=float(text); await update.message.reply_text(f"✅ Naira rate: <b>₦{NAIRA_RATE:,.0f}</b> per $1", parse_mode="HTML")
            except: await update.message.reply_text("❌ Invalid rate.")
            return

        if state == "adm_refbonus":
            ctx.user_data.pop("state",None)
            try: set_setting("referral_bonus",float(text)); await update.message.reply_text(f"✅ Referral bonus set to <b>${float(text):.5f}</b>", parse_mode="HTML")
            except: await update.message.reply_text("❌ Invalid amount.")
            return

    # ── User states ────────────────────────────────────────────
    if state == "set_wallet":
        save_trx_wallet(user.id, text.strip())
        ctx.user_data.pop("state",None)
        await update.message.reply_text(
            f"✅ <b>TRX Wallet Saved!</b>\n<code>{text.strip()}</code>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Wallet",callback_data="wallet")]])
        ); return

    if state == "set_ngn_wallet":
        ctx.user_data.pop("state",None)
        parts = [p.strip() for p in text.split("|")]
        if len(parts) < 3:
            await update.message.reply_text(
                "❌ Wrong format. Please send:\n"
                "<code>Bank Name | Account Number | Account Name</code>",
                parse_mode="HTML"); return
        save_bank_details(user.id, parts[0], parts[1], parts[2])
        await update.message.reply_text(
            f"✅ <b>Bank Details Saved!</b>\n\n"
            f"🏦  <b>{parts[0]}</b>\n"
            f"💳  <code>{parts[1]}</code>\n"
            f"👤  <b>{parts[2]}</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Wallet",callback_data="wallet")]])
        ); return

    # w_ngn_bank state removed — Naira withdrawal now uses saved bank details via w_confirm

    # Route keyboard button presses
    if text == "📲 Get Number":
        # Trigger get_number inline flow
        await update.message.reply_text(
            "📲 <b>Select Service</b>\n<i>Pick the service you want to verify on</i>",
            parse_mode="HTML",
            reply_markup=_build_service_menu()
        )
    elif text == "📊 My Status":
        num = get_assigned_number(user.id)
        if num:
            ctry_flag = COUNTRY_FLAGS.get(num["country"].lower(),"🌍")
            svc_icon  = SERVICE_ICONS.get(num["service"].upper(),"📨")
            await update.message.reply_text(
                f"📊 <b>Active Number</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"{ctry_flag}  <b>{num['country']}</b>   {svc_icon}  <b>{num['service']}</b>\n"
                f"📞  <code>+{num['number']}</code>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"⏳  Waiting for OTP...",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔁 Change Number", callback_data=f"change:{num['country']}:{num['service']}"),
                     InlineKeyboardButton("🔙 Services",      callback_data="get_number")],
                    [InlineKeyboardButton("📢 OTP Group",     url=GROUP_LINK)],
                ])
            )
        else:
            await update.message.reply_text(
                "📊 <b>No Active Number</b>\n\nYou don't have a number assigned.",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📲 Get Number", callback_data="get_number")],
                ])
            )
    elif text == "💰 Wallet":
        u   = get_user(user.id) or upsert_user(user.id, user.username or "", user.full_name or "")
        bal = u.get("balance",0.0) or 0.0
        ngn = bal * NAIRA_RATE
        earned    = u.get("total_earned",0.0) or 0.0
        withdrawn = u.get("total_withdrawn",0.0) or 0.0
        pending   = get_pending_amount(user.id)
        trx_wallet = u.get("trx_wallet") or "Not set"
        ref_count  = get_referral_count(user.id)
        ref_code   = u.get("referral_code","N/A")
        await update.message.reply_text(
            f"💰 <b>Your Wallet</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👤  <code>{user.id}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📈  Total Earned:   <b>${earned:.5f}</b>\n"
            f"✅  Available:      <b>${bal:.5f}</b>  (~₦{ngn:,.0f})\n"
            f"⏳  Pending:        <b>${pending:.5f}</b>\n"
            f"💸  Withdrawn:      <b>${withdrawn:.5f}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👥  Referrals:      <b>{ref_count}</b>\n"
            f"🔗  Ref Code:       <code>{ref_code}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👛  TRX Wallet:\n<code>{trx_wallet}</code>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💸 Withdraw",        callback_data="withdraw_menu"),
                 InlineKeyboardButton("✏️ Set TRX Wallet", callback_data="set_wallet")],
            ])
        )
    elif text == "🌍 Countries":
        items = get_countries_with_count()
        if not items:
            await update.message.reply_text("😔 No numbers in stock."); return
        lines = ["🌍 <b>Available Countries</b>\n━━━━━━━━━━━━━━━━━━━━━━"]
        for name, cnt in items:
            lines.append(f"{COUNTRY_FLAGS.get(name.lower(),'🌍')}  {name}  —  <b>{cnt}</b> numbers")
        await update.message.reply_text("\n".join(lines), parse_mode="HTML")
    elif text == "💸 Withdraw":
        u   = get_user(user.id) or upsert_user(user.id, user.username or "", user.full_name or "")
        bal = u.get("balance",0.0) or 0.0
        ngn = bal * NAIRA_RATE
        await update.message.reply_text(
            f"💸 <b>Withdraw Funds</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💼  Balance:   <b>${bal:.5f}</b>  (~₦{ngn:,.0f})\n"
            f"📉  Minimum:   <b>${MIN_WITHDRAWAL}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Choose your withdrawal method:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💎 USDT TRX",  callback_data="w_trx"),
                 InlineKeyboardButton("🇳🇬 Naira",   callback_data="w_ngn")],
            ])
        )
    elif text == "👥 Referral":
        u        = get_user(user.id) or upsert_user(user.id, user.username or "", user.full_name or "")
        ref_code = u.get("referral_code","N/A")
        ref_link = f"https://t.me/{BOT_USERNAME}?start={ref_code}"
        bonus    = get_referral_bonus()
        count    = get_referral_count(user.id)
        await update.message.reply_text(
            f"👥 <b>Referral Program</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎁  Bonus per referral: <b>${bonus:.5f}</b>\n"
            f"👤  Your referrals: <b>{count}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🔗  Your Link:\n<code>{ref_link}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"<i>Share your link and earn when friends join!</i>",
            parse_mode="HTML"
        )
    elif text == "⚙️ Admin Panel" and is_admin(user.id):
        await update.message.reply_text(
            "⚙️ <b>Admin Panel</b>", parse_mode="HTML", reply_markup=admin_menu()
        )
    else:
        await update.message.reply_text(
            start_text(user.first_name), parse_mode="HTML", reply_markup=main_menu(user.id)
        )

# ── Document handler (admin add numbers via .txt) ─────────────

async def handle_document(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    state = ctx.user_data.get("state","")
    if state != "adm_add_numbers":
        await update.message.reply_text("Use the admin panel ➕ Add Numbers flow first."); return
    doc  = update.message.document
    if not doc.file_name.endswith(".txt"):
        await update.message.reply_text("Only .txt files supported."); return
    file = await ctx.bot.get_file(doc.file_id)
    data = await file.download_as_bytearray()
    text = data.decode("utf-8", errors="ignore")
    country = ctx.user_data.pop("adm_country","")
    service = ctx.user_data.pop("adm_service","")
    price   = ctx.user_data.pop("adm_price", 0.005)
    ctx.user_data.pop("state",None)
    added=0; failed=0
    for line in text.strip().splitlines():
        num = re.sub(r"\D","",line.strip())
        if num:
            if add_number(num, country, service, price): added+=1
            else: failed+=1
    await update.message.reply_text(
        f"✅ Added <b>{added}</b> numbers\n"
        f"❌ Failed/Duplicates: <b>{failed}</b>\n\n"
        f"🌍 Country: <b>{country}</b>\n"
        f"📱 Service: <b>{service}</b>\n"
        f"💰 Price: <b>${price:.4f}</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ Admin Panel",callback_data="admin_panel")]])
    )

# ============================================================
#  MAIN
# ============================================================

async def post_init(app: Application):
    await app.bot.set_my_commands([BotCommand("start","Start the bot")])
    asyncio.create_task(poll_nordan(app.bot))
    asyncio.create_task(expiry_loop())
    logger.info("✅ Nordan bot running")

def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("🚀 Starting bot...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
