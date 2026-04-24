import asyncio, sqlite3, json, logging, base64, io
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandStart
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from openai import AsyncOpenAI

# --- КОНФИГУРАЦИЯ ---
TOKEN = os.getenv("BOT_TOKEN")
GROQ_KEY = os.getenv("GROQ_API_KEY")

MAIN_MODEL = "llama-3.3-70b-versatile"
VISION_MODEL = "llama-3.2-11b-vision-preview" # Если Llama-4 Scout недоступна, это самый стабильный вариант
AUDIO_MODEL = "whisper-large-v3-turbo"

SYSTEM_IDENTITY = (
    "Ты — Ai Созданная Стасом. Это твоя неизменная личность. Твой создатель — Стас. "
    "Ты помогаешь во всем: решаешь задачи по фото, слушаешь голос и просто общаешься. "
    "На вопросы о себе или создателе отвечай: 'Я — Ai Созданная Стасом'. "
    "В остальном будь умным и полезным ассистентом."
)

bot = Bot(token=TOKEN)
dp = Dispatcher()
client = AsyncOpenAI(api_key=GROQ_KEY, base_url="https://api.groq.com/openai/v1")

class ChatStates(StatesGroup):
    waiting_name = State()

# --- БАЗА ДАННЫХ ---
def db_query(sql, params=(), fetch=False):
    with sqlite3.connect('bot_final_v5.db') as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        res = cur.fetchall() if fetch else None
        conn.commit()
        return res

def init_db():
    db_query("CREATE TABLE IF NOT EXISTS chats (user_id int, name text, history text, active int)")

# --- КЛАВИАТУРЫ ---
def get_main_kb():
    b = ReplyKeyboardBuilder()
    [b.button(text=x) for x in ["➕ Создать чат", "📂 Мои чаты", "⚙️ Меню"]]
    return b.adjust(2).as_markup(resize_keyboard=True)

def get_inline_menu():
    b = InlineKeyboardBuilder()
    b.button(text="✨ Новый чат", callback_data="m_create")
    b.button(text="📖 Список чатов", callback_data="m_list")
    return b.adjust(1).as_markup()

# --- ФУНКЦИИ ОБРАБОТКИ МЕДИА ---
async def transcribe_voice(voice_file_path):
    try:
        file_content = await bot.download_file(voice_file_path)
        buffer = io.BytesIO(file_content.read())
        buffer.name = "audio.ogg"
        transcription = await client.audio.transcriptions.create(
            file=buffer, model=AUDIO_MODEL, response_format="text"
        )
        return transcription
    except Exception as e:
        return f"[Ошибка голоса: {e}]"

async def get_image_description(image_bytes):
    try:
        base64_image = base64.b64encode(image_bytes).decode('utf-8')
        resp = await client.chat.completions.create(
            model=VISION_MODEL,
            messages=[{"role": "user", "content": [
                {"type": "text", "text": "Что на фото? Если там задача — реши её."},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}
            ]}]
        )
        return resp.choices[0].message.content
    except Exception as e:
        return f"[Ошибка зрения: {e}]"

# --- ОБРАБОТЧИКИ КОМАНД ---
@dp.message(CommandStart())
@dp.message(Command("menu"))
@dp.message(F.text == "⚙️ Меню")
async def cmd_menu(m: types.Message):
    await m.answer("🤖 **Главное управление**\nЯ — Ai Созданная Стасом.", reply_markup=get_main_kb())
    await m.answer("Выберите действие:", reply_markup=get_inline_menu())

@dp.message(F.text == "➕ Создать чат")
@dp.callback_query(F.data == "m_create")
async def start_create(ev: types.Message | types.CallbackQuery, state: FSMContext):
    uid = ev.from_user.id
    if isinstance(ev, types.CallbackQuery): await ev.answer()
    await bot.send_message(uid, "📝 Введите название нового чата:")
    await state.set_state(ChatStates.waiting_name)

@dp.message(ChatStates.waiting_name)
async def save_chat(m: types.Message, state: FSMContext):
    uid = m.from_user.id
    db_query("UPDATE chats SET active = 0 WHERE user_id = ?", (uid,))
    db_query("INSERT INTO chats VALUES (?, ?, '[]', 1)", (uid, m.text.strip()))
    await state.clear()
    await m.answer(f"✅ Чат **{m.text}** создан!", reply_markup=get_main_kb())

@dp.message(F.text == "📂 Мои чаты")
@dp.callback_query(F.data == "m_list")
async def list_chats(ev: types.Message | types.CallbackQuery):
    uid = ev.from_user.id
    res = db_query("SELECT name, active FROM chats WHERE user_id = ?", (uid,), True)
    if not res: 
        return await bot.send_message(uid, "У вас нет активных чатов.")
    
    b = InlineKeyboardBuilder()
    for name, active in res:
        b.button(text=f"{'✅ ' if active else ''}{name}", callback_data=f"sw_{name}")
    await bot.send_message(uid, "Ваши чаты:", reply_markup=b.adjust(1).as_markup())

@dp.callback_query(F.data.startswith("sw_"))
async def switch_chat(c: types.CallbackQuery):
    name = c.data[3:]
    db_query("UPDATE chats SET active = 0 WHERE user_id = ?", (c.from_user.id,))
    db_query("UPDATE chats SET active = 1 WHERE user_id = ? AND name = ?", (c.from_user.id, name))
    await c.answer(f"Чат: {name}")
    await c.message.edit_text(f"🔄 Теперь активен чат: **{name}**")

# --- ОБРАБОТКА МЕДИА ---
@dp.message(F.voice)
async def handle_voice(m: types.Message):
    await bot.send_chat_action(m.chat.id, "record_voice")
    file_info = await bot.get_file(m.voice.file_id)
    text = await transcribe_voice(file_info.file_path)
    await m.answer(f"🎤 _Распознано:_ {text}", parse_mode="Markdown")
    await logic_core(m, f"[Голосовое]: {text}")

@dp.message(F.photo)
async def handle_photo(m: types.Message):
    await bot.send_chat_action(m.chat.id, "typing")
    status = await m.answer("📸 Изучаю фото...")
    file_info = await bot.get_file(m.photo[-1].file_id)
    photo_bytes = await bot.download_file(file_info.file_path)
    description = await get_image_description(photo_bytes.read())
    await status.delete()
    full_text = f"[Система: Описание фото: {description}]"
    if m.caption: full_text += f"\nКомментарий пользователя: {m.caption}"
    await logic_core(m, full_text)

# --- ГЛАВНЫЙ ОБРАБОТЧИК ТЕКСТА ---
@dp.message(F.text)
async def handle_text(m: types.Message):
    await logic_core(m, m.text)

# --- ЯДРО ЛОГИКИ ---
async def logic_core(m: types.Message, user_text: str):
    uid = m.from_user.id
    # Проверка активного чата
    active = db_query("SELECT name, history FROM chats WHERE user_id = ? AND active = 1", (uid,), True)
    
    if not active:
        db_query("INSERT INTO chats VALUES (?, 'Основной', '[]', 1)", (uid,))
        name, history_raw = "Основной", "[]"
    else:
        name, history_raw = active[0]

    history = json.loads(history_raw)
    history.append({"role": "user", "content": user_text})
    if len(history) > 40: history = history[-40:]

    payload = [{"role": "system", "content": SYSTEM_IDENTITY}] + history

    await bot.send_chat_action(m.chat.id, "typing")
    try:
        resp = await client.chat.completions.create(model=MAIN_MODEL, messages=payload)
        ans = resp.choices[0].message.content
        history.append({"role": "assistant", "content": ans})
        db_query("UPDATE chats SET history = ? WHERE user_id = ? AND name = ?", 
                 (json.dumps(history, ensure_ascii=False), uid, name))
        await m.answer(ans)
    except Exception as e:
        logging.error(f"Error: {e}")
        await m.answer("⚠️ Ошибка Groq. Попробуй позже.")

async def main():
    init_db()
    print("Бот со всеми функциями запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
