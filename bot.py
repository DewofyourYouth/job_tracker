"""career-ops — a phone-first Telegram front-end for the job tracker.

Deliberately separate from personal_ops ("you're not your job"): its own bot
token, its own process, but it can share a VPS. It wraps the existing
`data/applications.csv` via commands.track, so the CLI and the bot stay in sync.

Core loop (the part that's tedious on a computer):
  • paste a job URL            → records an application (auto-fills from listings.csv)
  • "add: Company | Title"     → manual entry (LinkedIn etc., no URL)
  • /list  /stats              → see where things stand
  • /update                    → tap a job, tap a new status

Run:  CAREER_OPS_BOT_TOKEN=... [CAREER_OPS_CHAT_ID=...] python bot.py
"""

from __future__ import annotations

import logging
import os
import re
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# Resolve data paths relative to this file regardless of where we're launched.
ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)

from commands import track  # noqa: E402  (after chdir so its relative paths resolve)

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO
)
logger = logging.getLogger("career_ops")


def _load_dotenv(path: Path = ROOT / ".env") -> None:
    """Minimal .env loader (no dependency). Doesn't override existing env vars."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


_load_dotenv()

TOKEN = os.environ.get("CAREER_OPS_BOT_TOKEN", "")
ALLOWED_CHAT_ID = os.environ.get("CAREER_OPS_CHAT_ID", "").strip()

URL_RE = re.compile(r"https?://\S+")
STATUS_EMOJI = {
    "applied": "🟦",
    "phone_screen": "🔵",
    "interview": "🟡",
    "offer": "🟢",
    "rejected": "🔴",
    "withdrew": "⚪️",
}


def _authorized(update: Update) -> bool:
    if not ALLOWED_CHAT_ID:
        return True  # open (set CAREER_OPS_CHAT_ID to lock to your chat)
    chat = update.effective_chat
    return bool(chat and str(chat.id) == ALLOWED_CHAT_ID)


def _label(row: dict) -> str:
    company, title = row.get("Company", "").strip(), row.get("Job Title", "").strip()
    if company and title:
        return f"{company} — {title}"
    return company or title or row.get("URL", "") or "(untitled)"


def _add_application(company: str, title: str, url: str, source: str, notes: str = "") -> tuple[bool, str]:
    """Append an application. Dedup by URL when present, else by company+title."""
    rows = track._load()
    if url:
        listing = track._find_listing(url)
        company = company or listing.get("Company", "")
        title = title or listing.get("Job Title", "")
        if any(r.get("URL") == url for r in rows):
            return False, "already tracked"
    elif any(
        r.get("Company", "").lower() == company.lower()
        and r.get("Job Title", "").lower() == title.lower()
        and company
        for r in rows
    ):
        return False, "already tracked"

    rows.append({
        "Company": company,
        "Job Title": title,
        "URL": url,
        "Applied Date": date.today().isoformat(),
        "Status": "applied",
        "Notes": notes,
        "Source": source,
    })
    track._save(rows)
    return True, _label(rows[-1])


# ----------------------------- handlers -----------------------------

HELP = (
    "*career-ops* — log applications from your phone.\n\n"
    "• *Paste a job URL* → I record it (auto-fills company/title if it's in your listings).\n"
    "• `add: Company | Title` → manual entry (e.g. a LinkedIn apply).\n"
    "• /list — recent applications  ·  /list interview — filter by status\n"
    "• /stats — counts + momentum\n"
    "• /update — tap a job, set a new status"
)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    await update.message.reply_text(HELP, parse_mode="Markdown", disable_web_page_preview=True)


async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    rows = track._load()
    status = (context.args[0].lower() if context.args else "")
    if status:
        rows = [r for r in rows if r.get("Status", "").lower() == status]
    if not rows:
        await update.message.reply_text("No applications" + (f" with status '{status}'." if status else " yet. Paste a job URL to start."))
        return
    rows = rows[-15:]
    lines = [
        f"{STATUS_EMOJI.get(r.get('Status',''), '·')} {_label(r)}  _{r.get('Applied Date','')}_"
        for r in rows
    ]
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown", disable_web_page_preview=True)


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    rows = track._load()
    if not rows:
        await update.message.reply_text("No applications yet.")
        return
    counts: dict[str, int] = {}
    for r in rows:
        counts[r.get("Status", "applied")] = counts.get(r.get("Status", "applied"), 0) + 1
    today = sum(1 for r in rows if r.get("Applied Date") == date.today().isoformat())
    order = ["applied", "phone_screen", "interview", "offer", "rejected", "withdrew"]
    parts = [f"{STATUS_EMOJI.get(s,'·')} {s.replace('_',' ')}: *{counts[s]}*" for s in order if counts.get(s)]
    msg = f"*{len(rows)}* applications tracked  ·  *{today}* today\n" + "\n".join(parts)
    await update.message.reply_text(msg, parse_mode="Markdown")


async def cmd_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    rows = track._load()
    if not rows:
        await update.message.reply_text("No applications to update yet.")
        return
    recent = list(enumerate(rows))[-8:]
    buttons = [[InlineKeyboardButton(f"{STATUS_EMOJI.get(r.get('Status',''),'·')} {_label(r)}"[:60], callback_data=f"pick:{i}")] for i, r in recent]
    await update.message.reply_text("Which application?", reply_markup=InlineKeyboardMarkup(buttons))


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    q = update.callback_query
    await q.answer()
    data = q.data or ""
    rows = track._load()

    if data.startswith("pick:"):
        idx = int(data.split(":", 1)[1])
        if not (0 <= idx < len(rows)):
            await q.edit_message_text("That application is no longer there — try /update again.")
            return
        statuses = [s for s in ["applied", "phone_screen", "interview", "offer", "rejected", "withdrew"]]
        buttons = [[InlineKeyboardButton(f"{STATUS_EMOJI[s]} {s.replace('_',' ')}", callback_data=f"set:{idx}:{s}")] for s in statuses]
        await q.edit_message_text(f"*{_label(rows[idx])}*\nNew status?", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))
        return

    if data.startswith("set:"):
        _, idx_s, status = data.split(":", 2)
        idx = int(idx_s)
        if not (0 <= idx < len(rows)) or status not in track.VALID_STATUSES:
            await q.edit_message_text("Couldn't apply that update — try /update again.")
            return
        rows[idx]["Status"] = status
        track._save(rows)
        await q.edit_message_text(f"{STATUS_EMOJI.get(status,'·')} *{_label(rows[idx])}* → {status.replace('_',' ')}", parse_mode="Markdown")


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    text = (update.message.text or "").strip()

    # 1) Pasted a URL → record the application.
    m = URL_RE.search(text)
    if m:
        url = m.group(0).rstrip(").,")
        source = (urlparse(url).netloc or "").replace("www.", "")
        added, label = _add_application("", "", url, source)
        if added:
            await update.message.reply_text(f"✅ Logged: *{label}*\nStatus: applied · {date.today().isoformat()}", parse_mode="Markdown", disable_web_page_preview=True)
        else:
            await update.message.reply_text(f"Already tracked: {label}" if label != "already tracked" else "Already tracked.")
        return

    # 2) "add: Company | Title [| notes]" → manual entry.
    if text.lower().startswith("add:"):
        parts = [p.strip() for p in text[4:].split("|")]
        company = parts[0] if parts else ""
        title = parts[1] if len(parts) > 1 else ""
        notes = parts[2] if len(parts) > 2 else ""
        if not company:
            await update.message.reply_text("Format: `add: Company | Title`", parse_mode="Markdown")
            return
        added, label = _add_application(company, title, "", "manual", notes)
        await update.message.reply_text(
            (f"✅ Logged: *{label}*" if added else f"Already tracked: {label}"),
            parse_mode="Markdown",
        )
        return

    await update.message.reply_text("Paste a job URL to log it, or `add: Company | Title`. /help for more.", parse_mode="Markdown")


def main():
    if not TOKEN:
        raise SystemExit("Set CAREER_OPS_BOT_TOKEN (create a bot via @BotFather).")
    app = (
        Application.builder()
        .token(TOKEN)
        .connect_timeout(30)
        .read_timeout(30)
        .write_timeout(30)
        .pool_timeout(30)
        .build()
    )
    app.add_handler(CommandHandler(["start", "help"], cmd_start))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("update", cmd_update))
    app.add_handler(CallbackQueryHandler(on_callback, pattern="^(pick|set):"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    logger.info("career-ops bot starting (data: %s)", track.APPLICATIONS_CSV)
    app.run_polling()


if __name__ == "__main__":
    main()
