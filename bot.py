import logging
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
BOT_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"
API_BASE_URL = "https://osint.aerivue.dev/api/tg"

# Optional (Recommended): Get these from https://my.telegram.org to resolve ANY @username to an ID
API_ID = 12345678  # Replace with your integer API_ID
API_HASH = "your_api_hash_here"  # Replace with your API_HASH

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


def get_cancel_keyboard() -> InlineKeyboardMarkup:
    """Returns an inline keyboard with a 'Cancel' button."""
    keyboard = [
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel_search")]
    ]
    return InlineKeyboardMarkup(keyboard)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends the initial welcome message and stores its message_id."""
    welcome_text = (
        "👋 **Welcome to the Lookup Bot!**\n\n"
        "Click the button below to search by **Chat ID** or **@username**.\n\n"
        "👨‍💻 **Developer:** @shopoflie"
    )
    sent_msg = await update.message.reply_text(
        welcome_text,
        reply_markup=get_main_menu_keyboard(),
        parse_mode=ParseMode.MARKDOWN,
    )
    # Save the main message ID so we always edit this exact message
    context.user_data["main_msg_id"] = sent_msg.message_id


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Edits the existing message to prompt for Chat ID or Username."""
    query = update.callback_query
    await query.answer()

    # Ensure we track the message being clicked
    context.user_data["main_msg_id"] = query.message.message_id

    if query.data == "find_user":
        await query.edit_message_text(
            "📥 **Send a Chat ID or Username**\n\n"
            "• Example ID: `8969010897`\n"
            "• Example Username: `@durov` or `durov`",
            reply_markup=get_cancel_keyboard(),
            parse_mode=ParseMode.MARKDOWN,
        )
        return WAITING_FOR_INPUT

    return ConversationHandler.END


async def cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles inline Cancel button by editing the same message back to main menu."""
    query = update.callback_query
    await query.answer()

    await query.edit_message_text(
        "🚫 **Search canceled.**\n\n"
        "Click below to start a new search.\n\n"
        "👨‍💻 **Developer:** @shopoflie",
        reply_markup=get_main_menu_keyboard(),
        parse_mode=ParseMode.MARKDOWN,
    )
    return ConversationHandler.END


async def resolve_username_to_id(username: str, context: ContextTypes.DEFAULT_TYPE) -> int | None:
    """Resolves a Telegram @username to a numeric user/chat ID."""
    clean_username = username.lstrip("@").strip()

    if telethon_client:
        try:
            if not telethon_client.is_connected():
                await telethon_client.start(bot_token=BOT_TOKEN)
            entity = await telethon_client.get_entity(clean_username)
            return entity.id
        except Exception as e:
            logger.warning(f"Telethon failed to resolve @{clean_username}: {e}")

    try:
        chat = await context.bot.get_chat(f"@{clean_username}")
        return chat.id
    except Exception as e:
        logger.warning(f"Bot API failed to resolve @{clean_username}: {e}")
        return None


async def edit_main_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    """Helper function to edit the stored main message, or send one if missing."""
    chat_id = update.effective_chat.id
    main_msg_id = context.user_data.get("main_msg_id")

    if main_msg_id:
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=main_msg_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN,
            )
            return
        except Exception as e:
            logger.warning(f"Could not edit message {main_msg_id}, sending new one: {e}")

    # Fallback if the original message was deleted by user
    sent_msg = await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN,
    )
    context.user_data["main_msg_id"] = sent_msg.message_id


async def process_user_lookup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Processes input and edits the same message throughout the entire lookup."""
    raw_input = update.message.text.strip()

    # Delete user's input message to keep the chat clean with only 1 bot message
    try:
        await update.message.delete()
    except Exception:
        pass  # Ignore if bot lacks delete permission

    await edit_main_message(update, context, "⏳ *Processing request...*")

    target_id = None

    # 1. Check if input is already a numeric Chat ID
    if raw_input.lstrip("-").isdigit():
        target_id = raw_input
    else:
        # 2. Input is a username -> resolve to numeric ID first
        await edit_main_message(
            update,
            context,
            f"🔎 Resolving username `{raw_input}` to Chat ID...",
        )
        resolved_id = await resolve_username_to_id(raw_input, context)

        if not resolved_id:
            await edit_main_message(
                update,
                context,
                f"❌ **Could not find user:** `{raw_input}`\n\n"
                "Make sure the username is valid and public, or send the numeric Chat ID directly.\n\n"
                "👨‍💻 **Developer:** @shopoflie",
                reply_markup=get_main_menu_keyboard(),
            )
            return ConversationHandler.END

        target_id = str(resolved_id)

    # 3. Query the API with the resolved/provided userid
    await edit_main_message(
        update,
        context,
        f"📡 Fetching data for ID `{target_id}`...",
    )

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(API_BASE_URL, params={"userid": target_id})
            response.raise_for_status()
            data = response.json()

        if data.get("status") is True:
            tg_id = data.get("tg_id", target_id)
            country = data.get("country", "N/A")
            country_code = data.get("country_code", "")
            number = data.get("number", "N/A")
            response_time = data.get("response_time", "N/A")

            result_text = (
                f"✅ **User Details Found**\n\n"
                f"🆔 **Telegram ID:** `{tg_id}`\n"
                f"🌍 **Country:** {country} ({country_code})\n"
                f"📞 **Number:** `{number}`\n"
                f"⚡ **Response Time:** {response_time}\n\n"
                f"👨‍💻 **Developer:** @shopoflie"
            )
        else:
            error_msg = data.get("msg", "No details found for this ID.")
            result_text = (
                f"❌ **Lookup Failed**\n\n"
                f"🆔 **ID:** `{target_id}`\n"
                f"💬 **Message:** {error_msg}\n\n"
                f"👨‍💻 **Developer:** @shopoflie"
            )

        await edit_main_message(
            update,
            context,
            result_text,
            reply_markup=get_main_menu_keyboard(),
        )

    except httpx.HTTPStatusError as e:
        await edit_main_message(
            update,
            context,
            f"⚠️ **API Error:** Server returned status `{e.response.status_code}` for ID `{target_id}`.\n\n"
            f"👨‍💻 **Developer:** @shopoflie",
            reply_markup=get_main_menu_keyboard(),
        )
    except Exception as e:
        logger.error(f"API request failed: {e}")
        await edit_main_message(
            update,
            context,
            f"❌ **Request Failed:** Could not connect to the API.\n`{str(e)}`\n\n"
            f"👨‍💻 **Developer:** @shopoflie",
            reply_markup=get_main_menu_keyboard(),
        )

    return ConversationHandler.END


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels via /cancel command while editing the same main message."""
    try:
        await update.message.delete()
    except Exception:
        pass

    await edit_main_message(
        update,
        context,
        "🚫 **Search canceled.**\n\n"
        "Click below to start a new search.\n\n"
        "👨‍💻 **Developer:** @shopoflie",
        reply_markup=get_main_menu_keyboard(),
    )
    return ConversationHandler.END


def main() -> None:
    """Starts the bot."""
    application = Application.builder().token(BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(button_handler, pattern="^find_user$")],
        states={
            WAITING_FOR_INPUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, process_user_lookup)
            ],
        },
        fallbacks=[
            CallbackQueryHandler(cancel_callback, pattern="^cancel_search$"),
            CommandHandler("cancel", cancel_command),
        ],
        allow_reentry=True,
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(conv_handler)

    print("Bot is running...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
    
