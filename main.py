import os
import json
import logging
import re
import gspread
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv

# ===== КОНФИГУРАЦИЯ И УТИЛИТЫ =====
load_dotenv()

# Вспомогательные функции для сообщений
async def reply_with_keyboard(update: Update, text: str, keyboard_func=None):
    """Отправить сообщение с клавиатурой"""
    reply_markup = keyboard_func() if keyboard_func else ReplyKeyboardRemove()
    await update.message.reply_text(text, reply_markup=reply_markup)

async def reply_without_keyboard(update: Update, text: str):
    """Отправить сообщение без клавиатуры"""
    await update.message.reply_text(text, reply_markup=ReplyKeyboardRemove())

# Конфигурация
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")  # Изменено для Railway
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID")
SHEET_NAME = os.environ.get("SHEET_NAME", "Актуальное_расписание")

# Получим данные сервисного аккаунта из JSON строки для Railway
SERVICE_ACCOUNT_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT")
if not SERVICE_ACCOUNT_JSON:
    raise ValueError("GOOGLE_SERVICE_ACCOUNT не установлена в переменных окружения")

# Распарсим JSON
service_account_info = json.loads(SERVICE_ACCOUNT_JSON)

# ===== СОСТОЯНИЯ =====
SELECTING_DATE, SELECTING_ACTION, ADDING_TITLE, ADDING_DESCRIPTION, CONFIRM_OVERWRITE, CONFIRM_DELETE = range(6)

# ===== КЛАВИАТУРЫ =====
def get_actions_menu():
    return ReplyKeyboardMarkup([
        ['👀 Посмотреть', '✏️ Добавить/Изменить'],
        ['🗑️ Удалить', '« Назад к дате']
    ], resize_keyboard=True)

def get_confirmation_keyboard():
    return ReplyKeyboardMarkup([['✅ Подтвердить', '❌ Отменить'], ['« Назад']], resize_keyboard=True)

def get_back_keyboard():
    return ReplyKeyboardMarkup([['« Назад']], resize_keyboard=True)

# ===== GOOGLE SHEETS МЕНЕДЖЕР =====
class GoogleSheetsManager:
    def __init__(self):
        self.scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        self.creds = Credentials.from_service_account_info(service_account_info, scopes=self.scope)
        self.client = gspread.authorize(self.creds)
        self.sheet = self.client.open_by_key(SPREADSHEET_ID).worksheet(SHEET_NAME)
    
    def get_event_by_date(self, date):
        try:
            if cell := self.sheet.find(date):
                row = cell.row
                title = self.sheet.cell(row, 2).value
                description = self.sheet.cell(row, 3).value
                return {'date': date, 'title': title, 'description': description, 'exists': bool(title or description)}
            return {'date': date, 'title': None, 'description': None, 'exists': False}
        except Exception as e:
            logging.error(f"Ошибка поиска даты: {e}")
            return None
    
    def update_event(self, date, title, description):
        try:
            if cell := self.sheet.find(date):
                row = cell.row
            else:
                row = len(self.sheet.col_values(1)) + 1
                self.sheet.update_cell(row, 1, date)
            
            self.sheet.update_cell(row, 2, title)
            self.sheet.update_cell(row, 3, description)
            return True
        except Exception as e:
            logging.error(f"Ошибка обновления: {e}")
            return False
    
    def delete_event(self, date):
        try:
            if cell := self.sheet.find(date):
                row = cell.row
                self.sheet.update_cell(row, 2, '')
                self.sheet.update_cell(row, 3, '')
                return True
            return False
        except Exception as e:
            logging.error(f"Ошибка удаления: {e}")
            return False

# ===== ВАЛИДАЦИЯ =====
def is_valid_date(date_str):
    if not re.match(r'^\d{2}\.\d{2}\.\d{4}$', date_str):
        return False
    try:
        day, month, year = map(int, date_str.split('.'))
        return 1 <= month <= 12 and 1 <= day <= 31 and year >= 2024
    except:
        return False

# ===== ОБРАБОТЧИКИ =====
MENU_TEXTS = {'👀 Посмотреть', '✏️ Добавить/Изменить', '🗑️ Удалить', 
              '« Назад к дате', '✅ Подтвердить', '❌ Отменить'}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await reply_without_keyboard(update, 
        "Привет! Я бот-ежедневник. За какое число вы хотите посмотреть или изменить расписание? Введите дату в формате DD.MM.YYYY:")
    return SELECTING_DATE

async def handle_date_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    
    if text in MENU_TEXTS:
        await reply_without_keyboard(update, 
            "За какое число вы хотите посмотреть или изменить расписание? Введите дату в формате DD.MM.YYYY:")
        return SELECTING_DATE
    
    date = text.strip()
    
    if not is_valid_date(date):
        await reply_without_keyboard(update, 
            "❌ Неверный формат даты. Пожалуйста, введите дату в формате DD.MM.YYYY:")
        return SELECTING_DATE
    
    context.user_data['user_date'] = date
    await reply_with_keyboard(update, f"📅 Выберите действие для даты {date}:", get_actions_menu)
    return SELECTING_ACTION

async def view_event(update: Update, context: ContextTypes.DEFAULT_TYPE):
    date = context.user_data.get('user_date')
    event_data = GoogleSheetsManager().get_event_by_date(date)
    
    if event_data and event_data['exists']:
        message = f"📅 __{date}__\n\n"
        message += f"-Мероприятие: {event_data['title'] or 'Нет данных'}\n"
        message += f"-Описание: {event_data['description'] or 'Нет данных'}"
    else:
        message = f"📭 На {date} мероприятий не найдено."
    
    await reply_with_keyboard(update, message, get_actions_menu)
    return SELECTING_ACTION

async def start_add_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    date = context.user_data.get('user_date')
    event_data = GoogleSheetsManager().get_event_by_date(date)
    
    if event_data and event_data['exists']:
        context.user_data['existing_event'] = event_data
        message = f"⚠️ На эту дату уже запланировано мероприятие:\n\n"
        message += f"📅 __{date}__\n\n"
        message += f"-Мероприятие: {event_data['title']}\n"
        message += f"-Описание: {event_data['description']}\n\n"
        message += "Вы хотите подтвердить изменения или отменить действие?"
        await reply_with_keyboard(update, message, get_confirmation_keyboard)
        return CONFIRM_OVERWRITE
    else:
        await reply_with_keyboard(update, "Введите название и ссылку для нового мероприятия:", get_back_keyboard)
        return ADDING_TITLE

async def handle_overwrite_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "❌ Отменить":
        await reply_with_keyboard(update, "Действие отменено.", get_actions_menu)
        return SELECTING_ACTION
    
    await reply_with_keyboard(update, "Введите новое название и ссылку для мероприятия:", get_back_keyboard)
    return ADDING_TITLE

async def handle_title_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['user_title'] = update.message.text
    await reply_with_keyboard(update, "Теперь введите описание мероприятия:", get_back_keyboard)
    return ADDING_DESCRIPTION

async def handle_description_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    date = context.user_data.get('user_date')
    title = context.user_data.get('user_title')
    description = update.message.text
    
    success = GoogleSheetsManager().update_event(date, title, description)
    message = (f"✅ Мероприятие на {date} успешно обновлено!" 
               if success else "❌ Произошла ошибка при сохранении.")
    
    await reply_with_keyboard(update, message, get_actions_menu)
    context.user_data.pop('user_title', None)
    return SELECTING_ACTION

async def start_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    date = context.user_data.get('user_date')
    event_data = GoogleSheetsManager().get_event_by_date(date)
    
    if not event_data or not event_data['exists']:
        await reply_with_keyboard(update, f"На {date} нет мероприятий для удаления.", get_actions_menu)
        return SELECTING_ACTION
    
    await reply_with_keyboard(update, f"❓ Вы уверены, что хотите удалить мероприятие за {date}?", get_confirmation_keyboard)
    return CONFIRM_DELETE

async def handle_delete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "❌ Отменить":
        await reply_with_keyboard(update, "Удаление отменено.", get_actions_menu)
        return SELECTING_ACTION
    
    date = context.user_data.get('user_date')
    success = GoogleSheetsManager().delete_event(date)
    message = (f"✅ Мероприятие на {date} удалено!" 
               if success else "❌ Произошла ошибка при удалении.")
    
    await reply_with_keyboard(update, message, get_actions_menu)
    return SELECTING_ACTION

async def back_to_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await reply_without_keyboard(update, 
        "За какое число вы хотите посмотреть или изменить расписание? Введите дату в формате DD.MM.YYYY:")
    return SELECTING_DATE

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await reply_without_keyboard(update, "Действие отменено.")
    return ConversationHandler.END

# ===== ГЛАВНАЯ ФУНКЦИЯ (адаптирована для Railway) =====
def main():
    # Настройка логирования для Railway
    logging.basicConfig(
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        level=logging.INFO,
        handlers=[logging.StreamHandler()]
    )
    
    logger = logging.getLogger(__name__)
    
    # Проверка переменных окружения
    required_vars = ['TELEGRAM_BOT_TOKEN', 'GOOGLE_SERVICE_ACCOUNT', 'SPREADSHEET_ID']
    for var in required_vars:
        if not os.environ.get(var):
            logger.error(f"❌ Отсутствует переменная окружения: {var}")
            logger.error("Пожалуйста, установите ее в настройках Railway")
            return
    
    logger.info("🚀 Запуск бота на Railway...")
    
    app = Application.builder().token(BOT_TOKEN).build()
    
    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler('start', start),
        ],
        states={
            SELECTING_DATE: [
                MessageHandler(filters.Text(['« Назад']), back_to_date),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_date_input)
            ],
            SELECTING_ACTION: [
                MessageHandler(filters.Text(['👀 Посмотреть']), view_event),
                MessageHandler(filters.Text(['✏️ Добавить/Изменить']), start_add_edit),
                MessageHandler(filters.Text(['🗑️ Удалить']), start_delete),
                MessageHandler(filters.Text(['« Назад к дате']), back_to_date)
            ],
            CONFIRM_OVERWRITE: [
                MessageHandler(filters.Text(['✅ Подтвердить', '❌ Отменить']), handle_overwrite_confirm),
                MessageHandler(filters.Text(['« Назад']), back_to_date)
            ],
            ADDING_TITLE: [
                MessageHandler(filters.Text(['« Назад']), back_to_date),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_title_input)
            ],
            ADDING_DESCRIPTION: [
                MessageHandler(filters.Text(['« Назад']), back_to_date),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_description_input)
            ],
            CONFIRM_DELETE: [
                MessageHandler(filters.Text(['✅ Подтвердить', '❌ Отменить']), handle_delete_confirm),
                MessageHandler(filters.Text(['« Назад']), back_to_date)
            ]
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )
    
    app.add_handler(conv_handler)
    
    # Запуск бота
    logger.info("=" * 50)
    logger.info("🚀 Telegram Бот-ежедневник")
    logger.info("📅 С интеграцией Google Sheets")
    logger.info("⏰ Работает 24/7 на Railway")
    logger.info("=" * 50)
    
    app.run_polling()

if __name__ == '__main__':
    main()