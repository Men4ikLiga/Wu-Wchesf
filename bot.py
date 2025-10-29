import os
import asyncio
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command

# Настройка
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    print("❌ Ошибка: BOT_TOKEN не установлен!")
    exit(1)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Храним привязанные группы
user_groups = {}

@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    await message.answer(
        "🤖 Бот для упоминания участников\n\n"
        "📋 Команды:\n"
        "/bind - Привязать к группе\n" 
        "/all - Пинг всех участников\n"
        "/info - Информация о группе"
    )

@dp.message(Command("bind"))
async def bind_cmd(message: types.Message):
    if message.chat.type == "private":
        await message.answer("❌ Используй в группе!")
        return
    
    user_groups[message.from_user.id] = message.chat.id
    await message.answer(f"✅ Группа '{message.chat.title}' привязана!")

@dp.message(Command("info")) 
async def info_cmd(message: types.Message):
    if message.chat.type != "private":
        return
    
    chat_id = user_groups.get(message.from_user.id)
    if not chat_id:
        await message.answer("❌ Группа не привязана!")
        return
    
    try:
        chat = await bot.get_chat(chat_id)
        members_count = await bot.get_chat_member_count(chat_id)
        await message.answer(f"📋 Группа: {chat.title}\n👥 Участников: {members_count}")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")

@dp.message(Command("all"))
async def all_cmd(message: types.Message):
    if message.chat.type != "private":
        await message.answer("❌ Используй в ЛС!")
        return
    
    user_id = message.from_user.id
    chat_id = user_groups.get(user_id)
    
    if not chat_id:
        await message.answer("❌ Группа не привязана! Используй /bind в группе")
        return
    
    try:
        # Получаем количество участников
        members_count = await bot.get_chat_member_count(chat_id)
        await message.answer(f"🔍 Начинаю сбор участников... Всего: {members_count}")
        
        # Получаем администраторов чтобы их исключить (если нужно)
        admins = []
        try:
            async for admin in bot.get_chat_administrators(chat_id):
                admins.append(admin.user.id)
        except:
            pass
        
        # Получаем участников через offset
        members = []
        offset = 0
        limit = 200
        
        while len(members) < members_count:
            try:
                # В aiogram 3.x нужно использовать get_chat_members с параметрами
                chat_members = []
                async for member in bot.get_chat_members(chat_id, offset=offset, limit=limit):
                    chat_members.append(member)
                    offset += 1
                
                if not chat_members:
                    break
                    
                for member in chat_members:
                    user = member.user
                    if not user.is_bot and user.id not in admins:  # Исключаем ботов и админов если нужно
                        members.append(user)
                        
                if len(chat_members) < limit:
                    break
                    
            except Exception as e:
                await message.answer(f"❌ Ошибка при получении участников: {e}")
                break
        
        if not members:
            await message.answer("❌ Не удалось получить участников")
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
        chunk_size = 15
        total_sent = 0
        
        for i in range(0, len(mentions), chunk_size):
            chunk = mentions[i:i + chunk_size]
            text = "📢 Внимание всем!\\n" + " ".join(chunk)
            
            try:
                await bot.send_message(chat_id, text, parse_mode="HTML")
                total_sent += len(chunk)
                await asyncio.sleep(1)  # Задержка против ограничений
            except Exception as e:
                await message.answer(f"⚠️ Не удалось отправить часть упоминаний: {e}")
                continue
        
        await message.answer(f"✅ Успешно упомянуто {total_sent} участников!")
        
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")

async def main():
    print("🚀 Бот запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
