import asyncio
import logging
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from config import BOT_TOKEN, ADMIN_IDS

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Инициализация бота
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# Хранилище для привязанных групп (в продакшене используйте БД)
group_storage = {}

# Проверка прав администратора
def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

# Команда /start
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if message.chat.type != "private":
        return
    
    await message.answer(
        "🤖 <b>Бот для упоминания участников</b>\n\n"
        "📋 <b>Доступные команды:</b>\n"
        "/bind - Привязать бота к группе\n"
        "/all - Упомянуть всех участников\n"
        "/info - Информация о привязанной группе\n"
        "/help - Помощь по командам"
    )

# Команда /help
@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    help_text = """
🔧 <b>Доступные команды:</b>

<b>/bind</b> - Привязать бота к группе
• Добавьте бота в группу как администратора
• Напишите команду в группе: <code>/bind</code>
• Бот сохранит ID группы

<b>/all</b> - Упомянуть всех участников
• Напишите в ЛС боту для пинга всех

<b>/info</b> - Информация о привязанной группе
• Показывает текущую привязанную группу

<b>/help</b> - Показать эту справку

⚙️ <b>Настройка:</b>
1. Добавьте бота в группу
2. Дайте права администратора
3. Напишите /bind в группе
4. Используйте /all в личке с ботом
"""
    await message.answer(help_text)

# Команда /bind - привязка к группе
@dp.message(Command("bind"))
async def cmd_bind(message: types.Message):
    if message.chat.type == "private":
        await message.answer("❌ Эту команду нужно использовать в группе!")
        return
    
    if not await is_chat_admin(message.chat.id, message.from_user.id):
        await message.answer("❌ Только администраторы могут привязывать бота!")
        return
    
    if not await is_bot_admin(message.chat.id):
        await message.answer("❌ Сначала сделайте бота администратором!")
        return
    
    # Сохраняем группу
    group_storage[message.from_user.id] = message.chat.id
    
    await message.answer(
        f"✅ Группа успешно привязана!\n"
        f"📝 Название: <b>{message.chat.title}</b>\n"
        f"🆔 ID: <code>{message.chat.id}</code>\n\n"
        f"Теперь вы можете использовать <code>/all</code> в личных сообщениях с ботом"
    )

# Команда /info - информация о группе
@dp.message(Command("info"))
async def cmd_info(message: types.Message):
    if message.chat.type != "private":
        return
    
    group_id = group_storage.get(message.from_user.id)
    
    if not group_id:
        await message.answer("❌ Группа не привязана! Используйте /bind в группе")
        return
    
    try:
        chat = await bot.get_chat(group_id)
        member_count = await bot.get_chat_member_count(group_id)
        
        await message.answer(
            f"📋 <b>Информация о привязанной группе:</b>\n\n"
            f"🏷️ Название: <b>{chat.title}</b>\n"
            f"🆔 ID: <code>{chat.id}</code>\n"
            f"👥 Участников: <b>{member_count}</b>\n"
            f"📍 Тип: <b>{chat.type}</b>"
        )
    except Exception as e:
        await message.answer(f"❌ Ошибка получения информации: {e}")

# Команда /all - пинг всех участников
@dp.message(Command("all"))
async def cmd_all(message: types.Message):
    if message.chat.type != "private":
        await message.answer("❌ Эта команда работает только в личных сообщениях!")
        return
    
    user_id = message.from_user.id
    group_id = group_storage.get(user_id)
    
    if not group_id:
        await message.answer(
            "❌ Группа не привязана!\n\n"
            "Чтобы привязать:\n"
            "1. Добавьте меня в группу\n"
            "2. Сделайте администратором\n"
            "3. Напишите в группе <code>/bind</code>"
        )
        return
    
    try:
        # Проверяем, что бот все еще администратор
        if not await is_bot_admin(group_id):
            await message.answer("❌ Бот больше не администратор в группе!")
            return
        
        # Получаем список участников
        members = []
        async for member in bot.get_chat_members(group_id):
            user = member.user
            if not user.is_bot and not user.is_deleted:
                members.append(user)
        
        if not members:
            await message.answer("❌ Не удалось получить список участников")
            return
        
        # Создаем упоминания
        mentions = []
        for user in members:
            if user.username:
                mentions.append(f"@{user.username}")
            else:
                name = user.first_name or "Пользователь"
                mentions.append(f'<a href="tg://user?id={user.id}">{name}</a>')
        
        # Разбиваем на части из-за ограничений Telegram
        chunk_size = 15
        total_mentioned = 0
        
        for i in range(0, len(mentions), chunk_size):
            chunk = mentions[i:i + chunk_size]
            mention_text = "🔔 Внимание! \\n" + " ".join(chunk)
            
            try:
                await bot.send_message(
                    group_id, 
                    mention_text,
                    parse_mode=ParseMode.HTML
                )
                total_mentioned += len(chunk)
                await asyncio.sleep(1)  # Задержка между сообщениями
            except Exception as e:
                logger.error(f"Ошибка отправки: {e}")
                continue
        
        await message.answer(
            f"✅ Успешно упомянуто <b>{total_mentioned}</b> участников в группе!"
        )
        
    except Exception as e:
        logger.error(f"Ошибка в /all: {e}")
        await message.answer(f"❌ Произошла ошибка: {e}")

# Вспомогательные функции
async def is_bot_admin(chat_id: int) -> bool:
    try:
        bot_user = await bot.get_me()
        member = await bot.get_chat_member(chat_id, bot_user.id)
        return member.status in ["administrator", "creator"]
    except:
        return False

async def is_chat_admin(chat_id: int, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in ["administrator", "creator"]
    except:
        return False

# Запуск бота
async def main():
    logger.info("Бот запускается...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
