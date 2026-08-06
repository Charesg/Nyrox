# ============================================================
#  CORE SMS PANEL — OTP FORWARDER BOT
#  Panel: http://139.99.68.231/ints/
#  Login: http://139.99.68.231/ints/login
#  CDR:   http://139.99.68.231/ints/agent/SMSCDRStats
# ============================================================

import asyncio
import logging
import re
import bs4
import requests
from bs4 import BeautifulSoup
from datetime import datetime

from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CallbackQueryHandler, ContextTypes

# ============================================================
#  CONFIG  ← fill in before running
# ============================================================

BOT_TOKEN     = "8959068683:AAHUY1uouPi8O79x6ICA-CHs3Ev9rhopHvA"       # Telegram bot token
GROUP_CHAT_ID =-1004205950683        # Group/channel to forward into (negative number)
BOT_USERNAME  = "@Noxvoidbot"       # Bot username without @
GROUP_LINK    = "https://t.me/+dS7Lf639NhcwMzhk"       # Group invite link
CHAT_LINK     = ""       # Support link

PANEL_URL     = "http://139.99.68.231/ints"
LOGIN_URL     = f"{PANEL_URL}/login"
CDR_URL       = f"{PANEL_URL}/agent/SMSCDRStats"

USERNAME      = "Coolvicky"   # ← your username
PASSWORD      = "Coolvicky"            # ← paste your password here

POLL_INTERVAL = 15            # seconds between scrapes

# ============================================================
#  LOGGING
# ============================================================

class ColorLog(logging.Formatter):
    G = "\033[32m"; Y = "\033[33m"; R = "\033[31m"; RESET = "\033[0m"
    FORMATS = {
        logging.INFO:    G + "%(asctime)s [INFO]  %(message)s" + RESET,
        logging.WARNING: Y + "%(asctime)s [WARN]  %(message)s" + RESET,
        logging.ERROR:   R + "%(asctime)s [ERROR] %(message)s" + RESET,
    }
    def format(self, record):
        return logging.Formatter(
            self.FORMATS.get(record.levelno, "%(message)s"),
            datefmt="%Y-%m-%d %H:%M:%S"
        ).format(record)

_h = logging.StreamHandler(); _h.setFormatter(ColorLog())
logging.basicConfig(handlers=[_h], level=logging.INFO)
logger = logging.getLogger(__name__)

# ============================================================
#  HELPERS
# ============================================================

COUNTRY_CODES = {
    "1":("USA","🇺🇸"),"52":("Mexico","🇲🇽"),"55":("Brazil","🇧🇷"),
    "57":("Colombia","🇨🇴"),"51":("Peru","🇵🇪"),"54":("Argentina","🇦🇷"),
    "56":("Chile","🇨🇱"),"58":("Venezuela","🇻🇪"),"591":("Bolivia","🇧🇴"),
    "593":("Ecuador","🇪🇨"),"595":("Paraguay","🇵🇾"),"598":("Uruguay","🇺🇾"),
    "7":("Russia","🇷🇺"),"33":("France","🇫🇷"),"34":("Spain","🇪🇸"),
    "39":("Italy","🇮🇹"),"44":("UK","🇬🇧"),"49":("Germany","🇩🇪"),
    "31":("Netherlands","🇳🇱"),"32":("Belgium","🇧🇪"),"41":("Switzerland","🇨🇭"),
    "46":("Sweden","🇸🇪"),"47":("Norway","🇳🇴"),"45":("Denmark","🇩🇰"),
    "358":("Finland","🇫🇮"),"48":("Poland","🇵🇱"),"380":("Ukraine","🇺🇦"),
    "90":("Turkey","🇹🇷"),"40":("Romania","🇷🇴"),"36":("Hungary","🇭🇺"),
    "420":("Czech Republic","🇨🇿"),"30":("Greece","🇬🇷"),"351":("Portugal","🇵🇹"),
    "375":("Belarus","🇧🇾"),"374":("Armenia","🇦🇲"),"994":("Azerbaijan","🇦🇿"),
    "995":("Georgia","🇬🇪"),"91":("India","🇮🇳"),"92":("Pakistan","🇵🇰"),
    "880":("Bangladesh","🇧🇩"),"86":("China","🇨🇳"),"81":("Japan","🇯🇵"),
    "82":("South Korea","🇰🇷"),"84":("Vietnam","🇻🇳"),"66":("Thailand","🇹🇭"),
    "60":("Malaysia","🇲🇾"),"62":("Indonesia","🇮🇩"),"63":("Philippines","🇵🇭"),
    "65":("Singapore","🇸🇬"),"98":("Iran","🇮🇷"),"61":("Australia","🇦🇺"),
    "966":("Saudi Arabia","🇸🇦"),"971":("UAE","🇦🇪"),"974":("Qatar","🇶🇦"),
    "965":("Kuwait","🇰🇼"),"962":("Jordan","🇯🇴"),"961":("Lebanon","🇱🇧"),
    "972":("Israel","🇮🇱"),"964":("Iraq","🇮🇶"),"20":("Egypt","🇪🇬"),
    "27":("South Africa","🇿🇦"),"212":("Morocco","🇲🇦"),"213":("Algeria","🇩🇿"),
    "216":("Tunisia","🇹🇳"),"234":("Nigeria","🇳🇬"),"233":("Ghana","🇬🇭"),
    "254":("Kenya","🇰🇪"),"255":("Tanzania","🇹🇿"),"256":("Uganda","🇺🇬"),
    "251":("Ethiopia","🇪🇹"),"221":("Senegal","🇸🇳"),"237":("Cameroon","🇨🇲"),
    "225":("Ivory Coast","🇨🇮"),"250":("Rwanda","🇷🇼"),"243":("DR Congo","🇨🇩"),
}

SERVICE_ICONS = {
    "WHATSAPP":"📱","FACEBOOK":"📘","INSTAGRAM":"📸","TELEGRAM":"✈️",
    "GOOGLE":"🔍","TWITTER":"🐦","TIKTOK":"🎵","SNAPCHAT":"👻",
    "AMAZON":"📦","PAYPAL":"💳","MICROSOFT":"🪟","APPLE":"🍎",
    "NETFLIX":"🎬","DISCORD":"🎮","UBER":"🚗","LINKEDIN":"💼",
}

def get_country_from_number(num: str):
    clean = re.sub(r"\D", "", str(num))
    for l in (3, 2, 1):
        p = clean[:l]
        if p in COUNTRY_CODES:
            return COUNTRY_CODES[p]
    return "Unknown", "🌍"

def detect_service(cli: str, msg: str) -> str:
    t = (cli + " " + msg).lower()
    for svc, kws in {
        "WHATSAPP":["whatsapp"],"FACEBOOK":["facebook","fb"],
        "INSTAGRAM":["instagram"],"TELEGRAM":["telegram"],
        "GOOGLE":["google"],"TWITTER":["twitter"],"TIKTOK":["tiktok"],
        "SNAPCHAT":["snapchat"],"AMAZON":["amazon"],"PAYPAL":["paypal"],
        "MICROSOFT":["microsoft"],"APPLE":["apple"],"NETFLIX":["netflix"],
        "DISCORD":["discord"],"UBER":["uber"],"LINKEDIN":["linkedin"],
    }.items():
        if any(k in t for k in kws): return svc
    return cli.upper() if cli else "SMS"

def mask_number(num: str) -> str:
    d = re.sub(r"\D", "", str(num))
    return f"+***•••{d[-4:]}" if len(d) >= 4 else "+***"

def extract_otp(text: str):
    m = re.search(r'\b(\d{3,4})-(\d{3,4})\b', text)
    if m: return m.group(1) + "-" + m.group(2)
    for p in [
        r"code[:\s]+(\d{4,9})", r"OTP[:\s]+(\d{4,9})",
        r"is[:\s]+(\d{4,9})", r"\b(\d{4,9})\b"
    ]:
        m = re.search(p, text, re.IGNORECASE)
        if m: return m.group(1)
    return None

def is_today(dt_str: str) -> bool:
    try:
        return datetime.strptime(dt_str[:19], "%Y-%m-%d %H:%M:%S").date() == datetime.now().date()
    except Exception:
        return False

# ============================================================
#  SESSION — Login & stay authenticated
# ============================================================

_session = requests.Session()
_session.headers.update({"User-Agent": "Mozilla/5.0"})
_logged_in = False

def solve_captcha(question: str) -> str:
    """Solve simple arithmetic captcha like 'What is 1 + 9 = ?'"""
    try:
        nums = re.findall(r"\d+", question)
        if "+" in question and len(nums) >= 2:
            return str(int(nums[0]) + int(nums[1]))
        if "-" in question and len(nums) >= 2:
            return str(int(nums[0]) - int(nums[1]))
        if "*" in question and len(nums) >= 2:
            return str(int(nums[0]) * int(nums[1]))
    except Exception:
        pass
    return "0"

def login() -> bool:
    global _logged_in
    try:
        logger.info("🔐 Logging in to Core SMS panel...")
        # GET login page to grab CSRF token + captcha
        resp = _session.get(LOGIN_URL, timeout=30)
        soup = BeautifulSoup(resp.text, "html.parser")

        # Extract CSRF token if present
        csrf = ""
        csrf_input = soup.find("input", {"name": re.compile(r"csrf|token", re.I)})
        if csrf_input:
            csrf = csrf_input.get("value", "")

        # Solve captcha
        captcha_answer = "0"
        captcha_label  = soup.find(string=re.compile(r"What is", re.I))
        if captcha_label:
            captcha_answer = solve_captcha(str(captcha_label))
            logger.info(f"🧮 Captcha: {str(captcha_label).strip()} → {captcha_answer}")

        # Find captcha input field name
        captcha_field = "captcha"
        for inp in soup.find_all("input"):
            name = inp.get("name","").lower()
            if "captcha" in name or "answer" in name or "math" in name:
                captcha_field = inp.get("name")
                break

        # Find username/password field names
        user_field = "username"; pass_field = "password"
        for inp in soup.find_all("input"):
            t = inp.get("type","").lower(); n = inp.get("name","").lower()
            if t in ("text","email") and "user" in n: user_field = inp.get("name")
            if t == "password": pass_field = inp.get("name")

        payload = {
            user_field:    USERNAME,
            pass_field:    PASSWORD,
            captcha_field: captcha_answer,
        }
        if csrf:
            payload["_token"] = csrf

        post = _session.post(LOGIN_URL, data=payload, timeout=30, allow_redirects=True)
        # Check if login succeeded (redirected away from login page)
        if "login" not in post.url.lower() and post.status_code == 200:
            _logged_in = True
            logger.info("✅ Login successful")
            return True
        # Also check for dashboard content as a fallback
        if "logout" in post.text.lower() or "dashboard" in post.text.lower() or "CDR" in post.text:
            _logged_in = True
            logger.info("✅ Login successful (content check)")
            return True

        logger.error(f"❌ Login failed — URL: {post.url}")
        _logged_in = False
        return False
    except Exception as e:
        logger.error(f"Login error: {e}")
        _logged_in = False
        return False

# ============================================================
#  SCRAPE CDR TABLE
# ============================================================

def fetch_cdr() -> list:
    """Scrape the CDR Reports & Stats table — returns list of SMS dicts."""
    global _logged_in
    try:
        resp = _session.get(CDR_URL, timeout=30, params={"length": 100})
        # Session expired — re-login
        if "login" in resp.url.lower() or resp.status_code in (401, 403):
            logger.warning("⚠️ Session expired — re-logging in")
            _logged_in = False
            if not login(): return []
            resp = _session.get(CDR_URL, timeout=30, params={"length": 100})

        soup  = BeautifulSoup(resp.text, "html.parser")
        table = soup.find("table")
        if not table:
            logger.warning("No table found on CDR page")
            return []

        # Parse headers
        headers = []
        for th in table.find_all("th"):
            headers.append(th.get_text(strip=True).lower())

        # Map column indices
        def col(names):
            for n in names:
                for i, h in enumerate(headers):
                    if n in h: return i
            return None

        idx_date   = col(["date"])
        idx_range  = col(["range"])
        idx_num    = col(["number"])
        idx_cli    = col(["cli"])
        idx_sms    = col(["sms","message","body"])
        idx_payout = col(["payout","my payout"])

        records = []
        tbody = table.find("tbody") or table
        for row in tbody.find_all("tr"):
            cells = [td.get_text(separator=" ", strip=True) for td in row.find_all("td")]
            if len(cells) < 3: continue

            def cell(idx):
                return cells[idx].strip() if idx is not None and idx < len(cells) else ""

            dt     = cell(idx_date)
            num    = re.sub(r"\D", "", cell(idx_num))
            cli    = cell(idx_cli)
            sms    = cell(idx_sms)
            payout = cell(idx_payout)
            rng    = cell(idx_range)

            if not num or not sms: continue
            if not is_today(dt):   continue

            records.append({
                "dt":      dt,
                "num":     num,
                "cli":     cli,
                "message": sms,
                "payout":  payout,
                "range":   rng,
            })

        logger.info(f"Core SMS → {len(records)} today's records")
        return records

    except Exception as e:
        logger.error(f"CDR scrape error: {e}")
        return []

# ============================================================
#  FORWARD TO GROUP
# ============================================================

_full_messages = {}

async def forward_sms(bot, sms: dict):
    num    = sms.get("num", "")
    msg    = sms.get("message", "")
    cli    = sms.get("cli", "")
    payout = sms.get("payout", "")
    rng    = sms.get("range", "")

    if not num or not msg: return

    otp           = extract_otp(msg)
    country, flag = get_country_from_number(num)
    service       = detect_service(cli, msg)
    svc_icon      = SERVICE_ICONS.get(service, "📨")
    masked        = mask_number(num)
    otp_text      = otp if otp else msg[:60]
    now           = datetime.now().strftime("%H:%M:%S")

    # Use range for country hint if country unknown
    if country == "Unknown" and rng:
        country = rng.split("_")[0] if "_" in rng else rng

    payout_str = f"${payout}" if payout else ""

    msg_key = f"core_{abs(hash(num + msg)) % 9999999}"
    _full_messages[msg_key] = msg[:500]

    group_text = (
        f"┏━━━━━━━━━━━━━━━━━━━━━━┓\n"
        f"┃  ⚡ <b>CORE SMS LIVE</b>    ┃\n"
        f"┗━━━━━━━━━━━━━━━━━━━━━━┛\n\n"
        f"  {flag}  <b>{country}</b>\n"
        f"  {svc_icon}  <b>{service}</b>\n\n"
        f"  📞  <code>{masked}</code>\n\n"
        f"╔══════════════════════╗\n"
        f"║  🔑  <b>{otp_text}</b>\n"
        f"╚══════════════════════╝\n\n"
        f"  🕐  <i>{now}</i>"
        + (f"  ·  💰 <i>{payout_str}</i>" if payout_str else "")
    )

    try:
        from telegram import CopyTextButton as CTB
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(text=f"📋 {otp_text}", copy_text=CTB(text=otp_text))],
            [InlineKeyboardButton("🤖 Bot",    url=f"https://t.me/{BOT_USERNAME}"),
             InlineKeyboardButton("💬 Chat",   url=CHAT_LINK)],
            [InlineKeyboardButton("📢 Group",  url=GROUP_LINK)],
            [InlineKeyboardButton("📩 Full Message", callback_data=msg_key)],
        ])
    except Exception:
        group_text += f"\n\n<code>{otp_text}</code>"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🤖 Bot",   url=f"https://t.me/{BOT_USERNAME}"),
             InlineKeyboardButton("💬 Chat",  url=CHAT_LINK)],
            [InlineKeyboardButton("📢 Group", url=GROUP_LINK)],
            [InlineKeyboardButton("📩 Full Message", callback_data=msg_key)],
        ])

    try:
        await bot.send_message(
            chat_id=GROUP_CHAT_ID,
            text=group_text,
            parse_mode="HTML",
            reply_markup=kb
        )
        logger.info(f"✅ Forwarded: {masked} → {otp_text}")
    except Exception as e:
        logger.error(f"Send error: {e}")

# ============================================================
#  CALLBACK — Full Message popup
# ============================================================

async def handle_callback(update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text  = _full_messages.get(query.data)
    if text:
        await query.answer(text=text[:200], show_alert=True)
    else:
        await query.answer("Message expired.", show_alert=True)

# ============================================================
#  POLLING LOOP
# ============================================================

_seen = set()

async def poll(bot):
    global _seen, _logged_in

    logger.info("🚀 Core SMS polling started")

    # Login first
    if not login():
        logger.error("❌ Could not login — check credentials")
        return

    # Mark existing records as seen — don't forward on startup
    for sms in fetch_cdr():
        _seen.add(f"{sms['num']}_{hash(sms['message'])}")
    logger.info(f"📋 Startup: {len(_seen)} existing records marked seen")

    while True:
        try:
            records   = fetch_cdr()
            new_count = 0
            for sms in records:
                key = f"{sms['num']}_{hash(sms['message'])}"
                if key in _seen: continue
                _seen.add(key)
                await forward_sms(bot, sms)
                new_count += 1

            # Trim memory
            if len(_seen) > 3000:
                _seen = set(list(_seen)[-1500:])

            if new_count:
                logger.info(f"📨 {new_count} new SMS forwarded")
            else:
                logger.info("⏭ No new SMS")

        except Exception as e:
            logger.error(f"Polling error: {e}")

        await asyncio.sleep(POLL_INTERVAL)

# ============================================================
#  MAIN
# ============================================================

async def post_init(app):
    asyncio.create_task(poll(app.bot))
    logger.info("✅ Core SMS bot running")

def main():
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )
    app.add_handler(CallbackQueryHandler(
        handle_callback, pattern=r"^core_\d+$"
    ))
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
