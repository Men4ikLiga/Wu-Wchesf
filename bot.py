import os
import asyncio
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command

# Настройка
BOT_TOKEN = os.getenv("BOT_TOKEN")  # Берём токен из переменных окружения
if not BOT_TOKEN:
    print("❌ Ошибка: BOT_TOKEN не установлен!")
    exit(1)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Храним привязанные группы {user_id: chat_id}
user_groups = {}

# Команда /start
@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    await message.answer(
        "🤖 Бот для упоминания участников\n\n"
        "📋 Команды:\n"
        "/bind - Привязать к группе (используй в группе)\n"
        "/all - Пинг всех участников (используй в ЛС)\n"
        "/info - Информация о группе"
    )

# Команда /bind - привязка к группе
@dp.message(Command("bind"))
async def bind_cmd(message: types.Message):
    if message.chat.type == "private":
        await message.answer("❌ Используй эту команду в группе!")
        return
    
    # Проверяем что бот админ
    try:
        bot_info = await bot.get_me()
        bot_member = await bot.get_chat_member(message.chat.id, bot_info.id)
        if bot_member.status not in ["administrator", "creator"]:
            await message.answer("❌ Сначала сделай бота администратором!")
            return
    except:
        await message.answer("❌ Ошибка проверки прав бота")
        return
    
    # Сохраняем группу
    user_groups[message.from_user.id] = message.chat.id
    
    await message.answer(
        f"✅ Группа привязана!\n"
        f"📝 {message.chat.title}\n\n"
        f"Теперь пиши /all в личке боту"
    )

# Команда /info
@dp.message(Command("info"))
async def info_cmd(message: types.Message):
    if message.chat.type != "private":
        return
    
    chat_id = user_groups.get(message.from_user.id)
    if not chat_id:
        await message.answer("❌ Группа не привязана! Используй /bind в группе")
        return
    
    try:
        chat = await bot.get_chat(chat_id)
        members_count = await bot.get_chat_member_count(chat_id)
        await message.answer(
            f"📋 Группа: {chat.title}\n"
            f"👥 Участников: {members_count}\n"
            f"🆔 ID: {chat_id}"
        )
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")

# Команда /all - пинг всех
@dp.message(Command("all"))
async def all_cmd(message: types.Message):
    if message.chat.type != "private":
        await message.answer("❌ Эта команда только в ЛС!")
        return
    
    user_id = message.from_user.id
    chat_id = user_groups.get(user_id)
    
    if not chat_id:
        await message.answer(
            "❌ Группа не привязана!\n\n"
            "Как привязать:\n"
            "1. Добавь бота в группу\n"
            "2. Дай права админа\n"
            "3. Напиши /bind в группе"
        )
        return
    
    try:
        # Получаем участников
        members = []
        async for member in bot.get_chat_members(chat_id):
            user = member.user
            if not user.is_bot and not user.is_deleted:
                members.append(user)
        
        if not members:
            await message.answer("❌ Нет участников для упоминания")
            return
        
        # Создаём упоминания
        mentions = []
        for user in members:
            if user.username:
                mentions.append(f"@{user.username}")
            else:
                name = user.first_name or "Участник"
                mentions.append(f'<a href="tg://user?id={user.id}">{name}</a>')
        
        # Отправляем частями
        chunk_size = 20
        total_sent = 0
        
        for i in range(0, len(mentions), chunk_size):
            chunk = mentions[i:i + chunk_size]
            text = "📢 Внимание всем!\\n" + " ".join(chunk)
            
            try:
                await bot.send_message(chat_id, text, parse_mode="HTML")
                total_sent += len(chunk)
                await asyncio.sleep(0.5)  # Небольшая задержка
            except Exception as e:
                print(f"Ошибка отправки: {e}")
                continue
        
        await message.answer(f"✅ Упомянуто {total_sent} участников!")
        
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")

# Запуск бота
async def main():
    print("🚀 Бот запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
