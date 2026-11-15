import json
import logging
import os
import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)
from telethon import TelegramClient

# --- CONFIGURATION ---
BOT_TOKEN = "9177120976:AAE5jNGmaesakmcd4o4gOtyF09hxTXEKVIk"
API_BASE_URL = "https://osint.aerivue.dev/api/tg"

# Optional (Recommended): Get these from https://my.telegram.org to resolve ANY @username to an ID
API_ID = 25121973  # Replace with your integer API_ID (or set to None to use Bot API only)
API_HASH = "d9a7c8ce0b699cf8d93115cf6bf51cad"  # Replace with your API_HASH

# Conversation States
WAITING_FOR_INPUT = 1

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Initialize Telethon client if credentials are provided
telethon_client = None
if API_ID and API_HASH and API_HASH != "your_api_hash_here":
    telethon_client = TelegramClient("bot_resolver_session", API_ID, API_HASH)


def get_main_menu_keyboard() -> InlineKeyboardMarkup:
    """Returns the inline keyboard with the 'Find User' button."""
    keyboard = [
        [InlineKeyboardButton("🔍 Find User", callback_data="find_user")]
    ]
    return InlineKeyboardMarkup(keyboard)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends a welcome message with the Find User button."""
    welcome_text = (
        "👋 **Welcome to the OSINT Lookup Bot!**\n\n"
        "Click the button below to search by **Chat ID** or **@username**."
    )
    await update.message.reply_text(
        welcome_text,
        reply_markup=get_main_menu_keyboard(),
        parse_mode=ParseMode.MARKDOWN,
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles the 'Find User' button click and prompts for input."""
    query = update.callback_query
    await query.answer()

    if query.data == "find_user":
        await query.message.reply_text(
            "📥 **Send a Chat ID or Username**\n\n"
            "• Example ID: `8969010897`\n"
            "• Example Username: `@durov` or `durov`\n\n"
            "_(Send /cancel to abort)_",
            parse_mode=ParseMode.MARKDOWN,
        )
        return WAITING_FOR_INPUT
    return ConversationHandler.END


async def resolve_username_to_id(username: str, context: ContextTypes.DEFAULT_TYPE) -> int | None:
    """Resolves a Telegram @username to a numeric user/chat ID."""
    clean_username = username.lstrip("@").strip()

    # Method 1: Use Telethon (MTProto) - resolves ANY public username
    if telethon_client:
        try:
            if not telethon_client.is_connected():
                await telethon_client.start(bot_token=BOT_TOKEN)
            entity = await telethon_client.get_entity(clean_username)
            return entity.id
        except Exception as e:
            logger.warning(f"Telethon failed to resolve @{clean_username}: {e}")

    # Method 2: Fallback to standard Bot API get_chat
    try:
        chat = await context.bot.get_chat(f"@{clean_username}")
        return chat.id
    except Exception as e:
        logger.warning(f"Bot API failed to resolve @{clean_username}: {e}")
        return None


async def process_user_lookup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Processes the received Chat ID or Username, queries the API, and sends results."""
    raw_input = update.message.text.strip()
    status_msg = await update.message.reply_text("⏳ *Processing request...*", parse_mode=ParseMode.MARKDOWN)

    target_id = None

    # 1. Check if input is already a numeric Chat ID (supports negative IDs for groups/channels)
    if raw_input.lstrip("-").isdigit():
        target_id = raw_input
    else:
        # 2. Input is a username -> resolve to numeric ID first
        await status_msg.edit_text(
            f"🔎 Resolving username `{raw_input}` to Chat ID...",
            parse_mode=ParseMode.MARKDOWN,
        )
        resolved_id = await resolve_username_to_id(raw_input, context)

        if not resolved_id:
            await status_msg.edit_text(
                f"❌ **Could not find user:** `{raw_input}`\n\n"
                "Make sure the username is valid and public, or send the numeric Chat ID directly.",
                reply_markup=get_main_menu_keyboard(),
                parse_mode=ParseMode.MARKDOWN,
            )
            return ConversationHandler.END

        target_id = str(resolved_id)

    # 3. Query the OSINT API with the resolved/provided userid
    await status_msg.edit_text(
        f"📡 Fetching data for ID `{target_id}`...",
        parse_mode=ParseMode.MARKDOWN,
    )

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(API_BASE_URL, params={"userid": target_id})
            response.raise_for_status()

            # Format output nicely if JSON, otherwise return raw text
            try:
                data = response.json()
                formatted_result = json.dumps(data, indent=2, ensure_ascii=False)
            except json.JSONDecodeError:
                formatted_result = response.text

        # Telegram messages have a 4096-character limit
        if len(formatted_result) > 3800:
            formatted_result = formatted_result[:3800] + "\n... [Truncated]"

        result_text = (
            f"✅ **Results for ID:** `{target_id}`\n"
            f"```json\n{formatted_result}\n```"
        )

        await status_msg.edit_text(
            result_text,
            reply_markup=get_main_menu_keyboard(),
            parse_mode=ParseMode.MARKDOWN,
        )

    except httpx.HTTPStatusError as e:
        await status_msg.edit_text(
            f"⚠️ **API Error:** Server returned status `{e.response.status_code}` for ID `{target_id}`.",
            reply_markup=get_main_menu_keyboard(),
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as e:
        logger.error(f"API request failed: {e}")
        await status_msg.edit_text(
            f"❌ **Request Failed:** Could not connect to the API.\n`{str(e)}`",
            reply_markup=get_main_menu_keyboard(),
            parse_mode=ParseMode.MARKDOWN,
        )

    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels the current search conversation."""
    await update.message.reply_text(
        "🚫 Search canceled.",
        reply_markup=get_main_menu_keyboard(),
    )
    return ConversationHandler.END


def main() -> None:
    """Starts the bot."""
    application = Application.builder().token(BOT_TOKEN).build()

    # Conversation handler for the 'Find User' button flow
    conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(button_handler, pattern="^find_user$")],
        states={
            WAITING_FOR_INPUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, process_user_lookup)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(conv_handler)

    print("Bot is running...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
      
