import asyncio
import logging  
import time
import re
import random
import html
import aiohttp
import os

from aiohttp import ClientSession
from collections import Counter
from nudenet import NudeDetector
from io import BytesIO
from collections import deque
from pathlib import Path
from typing import Optional, Callable, Dict, Any, Awaitable, Tuple
from aiogram import Bot, Dispatcher, Router, types, F, BaseMiddleware
from aiogram.utils.text_decorations import HtmlDecoration
from aiogram.filters import CommandStart, StateFilter
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, InputFile, LabeledPrice, PreCheckoutQuery, BufferedInputFile, ChatMemberAdministrator, ChatInviteLink, LabeledPrice, PreCheckoutQuery
from aiogram.types import File as TgFile
from aiogram.types.input_file import FSInputFile
from aiogram.exceptions import (
    TelegramAPIError, TelegramBadRequest, TelegramNotFound, TelegramForbiddenError,
    TelegramConflictError, TelegramUnauthorizedError, TelegramRetryAfter, TelegramMigrateToChat
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from PIL import Image, ImageDraw, ImageFont



try:
    from database import *
    from settings import *
except ImportError as e:
    print(f"Ошибка импорта: {e}. Пожалуйста, убедитесь, что файлы database.py и settings.py существуют и находятся в правильном месте.")
    exit()

router = Router()
detector = NudeDetector()
html_detect = HtmlDecoration()

PHOTOS_DIR = Path("photos_users")
PHOTOS_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

def apply_watermark(
    im: Image.Image,
    text: str = "©YourBrand",
    font_path: str = "arial.ttf",
    spacing: int = 20,
    opacity: int = 50,
) -> Image.Image:
    w, h = im.size
    layer = Image.new("RGBA", (w, h), (255, 255, 255, 0))
    draw = ImageDraw.Draw(layer)
    size = max(20, w // 20)
    font = ImageFont.truetype(font_path, size)
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]

    for y in range(0, h, th + spacing):
        for x in range(0, w, tw + spacing):
            draw.text((x, y), text, font=font, fill=(255, 255, 255, opacity))

    return Image.alpha_composite(im.convert("RGBA"), layer).convert("RGB")

class AntiFloodMiddleware(BaseMiddleware):
    def __init__(self, limit: int = 1):
        self.limit = limit
        self.last_time: Dict[int, float] = {}

    async def __call__(
        self,
        handler: Callable[[types.Message | types.CallbackQuery, Dict[str, Any]], Awaitable[Any]],
        event: types.Message | types.CallbackQuery,
        data: Dict[str, Any]
    ) -> Any:
        if isinstance(event, types.Message):
            if event.text and event.text.startswith('/start'):
                return await handler(event, data)

            user_id = event.from_user.id
            current_time = time.time()

            if user_id in self.last_time:
                last_time = self.last_time[user_id]
                if (current_time - last_time) < self.limit:
                    await event.answer("⚠️ Пожалуйста, не флудите! Ожидайте {:.0f} сек.".format(self.limit))
                    return

            self.last_time[user_id] = current_time
            return await handler(event, data)

        elif isinstance(event, types.CallbackQuery):
            user_id = event.from_user.id
            current_time = time.time()

            if user_id in self.last_time:
                last_time = self.last_time[user_id]
                if (current_time - last_time) < self.limit:
                    await event.answer("⚠️ Пожалуйста, не флудите! Ожидайте {:.0f} сек.".format(self.limit), show_alert=True)
                    return

            self.last_time[user_id] = current_time
            return await handler(event, data)

class SellState(StatesGroup):
    PRICE_PHOTO = State()
    PHOTO = State()

class AdminState(StatesGroup):
    PROMOCODE_INPUT = State()
    ADD_PROMO_CODE = State()
    REMOVE_PROMO_CODE = State()
    MAILING = State()
    AWARDS = State()
    WITHDRAW = State()
    USERS_CHECK = State()
    ADD_OP = State()
    REMOVE_OP = State()
    EDIT_SPIN_COST = State()
    EDIT_SLOT_REWARD = State()
    REMOVE_AUTO = State()
    ADD_AUTO = State()
    DEPOSIT = State()


async def request_op(user_id, chat_id, first_name, language_code, bot: Bot, ref_id=None, gender=None, is_premium=None):
    headers = {
        'Content-Type': 'application/json',
        'Auth': f'{SUBGRAM_TOKEN}',
        'Accept': 'application/json',
    }
    data = {'UserId': user_id, 'ChatId': chat_id, 'first_name': first_name, 'language_code': language_code}
    if gender:
        data['Gender'] = gender
    if is_premium:
        data['Premium'] = is_premium

    async with aiohttp.ClientSession() as session:
        async with session.post('https://api.subgram.ru/request-op-tokenless/', headers=headers, json=data) as response:
            if not response.ok or response.status != 200:
                logging.error("Ошибка при запросе SubGram. Если такая видишь такую ошибку - ставь другие настройки Subgram или проверь свой API KEY. Вот ошибка: %s" % str(await response.text()))
                return 'ok'
            response_json = await response.json()

            if response_json.get('status') == 'warning':
                if ref_id:
                    await show_op(chat_id,response_json.get("links",[]), bot, ref_id=ref_id)
                else:
                    await show_op(chat_id,response_json.get("links",[]), bot)
            elif response_json.get('status') == 'gender':
                if ref_id:
                    await show_gender(chat_id, bot, ref_id=ref_id)
                else:
                    await show_gender(chat_id, bot)
            return response_json.get("status")

@router.callback_query(F.data.startswith("subgram-op"))
async def subgram_op_callback(call: CallbackQuery, bot: Bot):
    try:
        user = call.from_user
        user_id = user.id
        ref_id = None
        
        args = call.data.split(':')
        if len(args) > 1 and args[1].isdigit():
            ref_id = int(args[1])
        elif len(args) > 1:
            ref_id = args[1]

        response = await request_op(
            user_id=user_id,
            chat_id=call.message.chat.id,
            first_name=user.first_name,
            language_code=user.language_code,
            bot=bot,
            ref_id=ref_id,
            is_premium=getattr(user, 'is_premium', None)
        )

        if response != 'ok':
            await bot.answer_callback_query(call.id, "❌ Вы всё ещё не подписаны на все каналы!", show_alert=True)
            return

        await bot.answer_callback_query(call.id, 'Спасибо за подписку 👍', show_alert=True)

        if not user_exists(user_id):
            if ref_id is not None:
                await handle_referral_bonus(ref_id, user_id, bot)
                add_user(user_id, user.first_name, ref_id)
            else:
                add_user(user_id, user.first_name)
            
        try:
            await bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception as e:
            logging.error(f"Ошибка при удалении сообщения: {e}")

        await send_main_menu(user_id, bot)
    except Exception as e:
        logging.error(f"Subgram op error: {e}", exc_info=True)
        await bot.answer_callback_query(call.id, "⚠️ Произошла ошибка при проверке подписки", show_alert=True)

async def show_op(chat_id,links, bot: Bot, ref_id=None):
    markup = InlineKeyboardBuilder()
    temp_row = []
    sponsor_count = 0
    for url in links:
        sponsor_count += 1
        name = f'Cпонсор №{sponsor_count}'
        button = types.InlineKeyboardButton(text=name, url=url)
        temp_row.append(button) 

        if sponsor_count % 2 == 0:
            markup.row(*temp_row)
            temp_row = []

    if temp_row:
        markup.row(*temp_row)
    if ref_id != "None":
        item1 = types.InlineKeyboardButton(text='✅ Я подписан',callback_data=f'subgram-op:{ref_id}')
    else:
        item1 = types.InlineKeyboardButton(text='✅ Я подписан',callback_data='subgram-op')
    markup.row(item1)
    photo = FSInputFile("photo/check_sub.png")
    await bot.send_photo(chat_id, photo, caption="<b>Для продолжения использования бота подпишись на следующие каналы наших спонсоров</b>\n\n<blockquote><b>💜Спасибо за то что вы выбрали НАС</b></blockquote>", parse_mode='HTML', reply_markup=markup.as_markup())


async def show_gender(chat_id, bot: Bot, ref_id=None):
    btn_male = types.InlineKeyboardButton(text='👱‍♂️ Парень', callback_data=f'gendergram_male:{ref_id or "None"}')
    btn_female = types.InlineKeyboardButton(text='👩‍🦰 Девушка', callback_data=f'gendergram_female:{ref_id or "None"}')

    markup = types.InlineKeyboardMarkup(inline_keyboard=[
        [btn_male, btn_female]
    ])

    await bot.send_message(
        chat_id, 
        "<b>😮 Системе не удалось автоматически определить твой пол!</b>\n\nПожалуйста, укажите, <u>кто вы?</u>", 
        reply_markup=markup, 
        parse_mode='HTML'
    )

@router.callback_query(F.data.startswith('gendergram_'))
async def gendergram(call: types.CallbackQuery, state: FSMContext, bot: Bot):
    data = call.data.split(':')
    gender = data[0].split('gendergram_')[1]
    ref_id = None
    args = call.data.split(':')
    if len(args) > 1 and args[1].isdigit():
        ref_id = int(args[1])
    elif len(args) > 1:
        ref_id = args[1]
    
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    first_name = call.from_user.first_name
    language_code = call.from_user.language_code
    is_premium = getattr(call.from_user, 'is_premium', None)

    try:
        await bot.delete_message(chat_id, call.message.message_id)
    except Exception as e:
        logging.error(f"Ошибка при удалении сообщения: {e}")
    await state.update_data(gender=gender)
    response = await request_op(user_id, chat_id, first_name, language_code, bot, ref_id=ref_id, gender=gender, is_premium=is_premium)

    if response == 'ok':
        if not user_exists(user_id):
            if ref_id is not None:
                await handle_referral_bonus(ref_id, user_id, bot)
                add_user(user_id, first_name, ref_id)
            else:
                add_user(user_id, first_name)
        await bot.answer_callback_query(call.id, 'Спасибо за подписку 👍')
        await state.clear()
        await send_main_menu(user_id, bot)
    else:
        await bot.answer_callback_query(call.id, '❌ Вы всё ещё не подписаны на все каналы!', show_alert=True)

async def pvo_arabov(lang: str) -> bool:
    if lang not in ALLOWED_LANGUAGE_CODES:
        return False

    return True

@router.message(CommandStart())
async def start_command(message: Message, bot: Bot):
    try:
        user = message.from_user
        user_id = user.id
        
        args = message.text.split()

        if get_banned_user(user_id) == 1:
            await bot.send_message(user_id, "<b>🚫 Вы заблокированы в боте!</b>", parse_mode='HTML')
            return

        referral_id = None
        if len(args) > 1:
            referral_id = int(args[1]) if args[1].isdigit() else args[1]

        if not await pvo_arabov(user.language_code):
            username_info = f"\n📕 Юзернейм: @{user.username}" if user.username else ""

            await bot.send_message(
                referral_id,
                f"📛 <b>Ошибка: реферал использует недопустимый язык.</b>\n\n"
                "<blockquote><b>Разрешённые:</b> 🇺🇦 Украинский, 🇧🇾 Белорусский, 🇺🇿 Узбекский, 🇷🇺 Русский.</blockquote>"
                f"{username_info}",
                parse_mode='HTML'
            )
            return


        response = await request_op(
                user_id=user_id,
                chat_id=message.chat.id,
                first_name=user.first_name,
                language_code=user.language_code,
                bot=bot,
                ref_id=referral_id,
                is_premium=getattr(user, 'is_premium', None)
            )

        if response != 'ok':
            return

        channels = get_channels_ids()
        if not await check_subscription(user_id, channels, bot, referral_id):
            return

        if flyer_status[0] and not await flyer_check(key=FLYER_TOKEN, user_id=user_id, lang_code=user.language_code):
            return
        
        builder_start = InlineKeyboardBuilder()
        buttons = [
            ('🎰 Мини-Игры', 'games'),
            ('🌟 Купить / Продать фото', 'photo_selling'),
            ('👤 Профиль', 'profile'),
            ('🔄 Вывести звезды', 'stars_withdraw'),
            ('🔗 Получить ссылку', 'get_ref'),
            ('🏆 Топ', 'top')
        ]

        for text, callback_data in buttons:
            builder_start.button(text=text, callback_data=callback_data)

        builder_start.button(text="📕 Помощь", url=admin_url)

        builder_start.adjust(1, 1, 2, 2, 1)
        markup = builder_start.as_markup()

        if not user_exists(user_id):
            if referral_id and user_exists(referral_id):
                add_user(user_id, user.first_name, referral_id)
                await handle_referral_bonus(referral_id, user_id, bot)
            else:
                add_user(user_id, user.first_name, None)

        sell_count = get_total_photo_selling_count()
        withdrawn_count = get_total_withdrawn()
        await bot.send_message(user_id, "⭐")
        await bot.send_photo(
            chat_id=user_id,
            photo=FSInputFile("photo/start.png"),
            caption=(
                "<b>✨ Добро пожаловать в Главное Меню! ✨</b>\n\n"
                f"📸 <b>Всего продано фото:</b> <code>{sell_count}</code>\n"
                f"💰 <b>Всего выведено:</b> <code>{withdrawn_count}</code>\n\n"
                "<b>❔ Как заработать звёзды?</b>\n"
                "<blockquote>"
                "🔹 <i>Продавайте свои фото</i> — за продажу вы получаете звёзды.\n"
                "🔹 <i>Играйте в мини-играх</i> — вы можете выиграть или проиграть звёзды\n"
                "🔹 <i>Приглашайте друзей</i> — делитесь реферальной ссылкой и получайте бонусы.\n"
                "</blockquote>\n"
                "📲 <i>Чтобы посмотреть свой профиль и получить ссылку для приглашений, нажмите кнопку ниже.</i>"
            ),
            parse_mode='HTML',
            reply_markup=markup
        )

    except Exception as e:
        print(f"Error: {e}")
        await message.reply(
            "<b>Произошла ошибка при запуске бота.</b>\n\n<i>⚠️ Пожалуйста, попробуйте позже.</i>",
            parse_mode='HTML'
        )

@router.callback_query(F.data == "photo_selling")
async def photo_sellings(call: CallbackQuery, bot: Bot):
    user = call.from_user
    user_id = call.from_user.id
    banned = get_banned_user(user_id)
    if banned == 1:
        await bot.answer_callback_query(call.id, "🚫 Вы заблокированы в боте!", show_alert=True)
        return


    if subgram_status[0]:
        response = await request_op(
                user_id=user_id,
                chat_id=call.message.chat.id,
                first_name=user.first_name,
                language_code=user.language_code,
                bot=bot,
                ref_id=None,
                is_premium=getattr(user, 'is_premium', None)
            )

        if response != 'ok':
            return

    if flyer_status[0] and not await flyer_check(key=FLYER_TOKEN, user_id=user_id, lang_code=user.language_code):
        return

    try:
        await bot.delete_message(chat_id=call.from_user.id, message_id=call.message.message_id)
    except Exception as e:
        print(f"Ошибка при удалении сообщения: {e}")
    
    builder_rinok = InlineKeyboardBuilder()
    builder_rinok.button(text="🌟 Купить", callback_data="buy_photo")
    builder_rinok.button(text="👤 Продать", callback_data="sell_photo")
    builder_rinok.button(text="⬅️ В главное меню", callback_data="back_main")
    builder_rinok.adjust(2, 1)
    markup_rinok = builder_rinok.as_markup()
    
    await bot.send_photo(
        chat_id=user_id,
        photo=FSInputFile('photo/foto_rinok.png'),
        caption=(
            "<b>Вы вошли в меню купли/продажи фотографий.</b>\n\n<blockquote>"
            "📸 <i>Чтобы купить фото, нажмите кнопку ниже.</i>\n"
            "👤 <i>Чтобы продать свои фото, нажмите кнопку ниже.</i></blockquote>"
        ),
        reply_markup=markup_rinok,
        parse_mode='HTML'
    )

@router.callback_query(F.data == "buy_photo")
async def buy_photo(call: CallbackQuery, bot: Bot):
    user_id = call.from_user.id
    user = call.from_user
    banned = get_banned_user(user_id)
    if banned == 1:
        await bot.answer_callback_query(call.id, "🚫 Вы заблокированы в боте!", show_alert=True)
        return

    if subgram_status[0]:
        response = await request_op(
                user_id=user_id,
                chat_id=call.message.chat.id,
                first_name=user.first_name,
                language_code=user.language_code,
                bot=bot,
                ref_id=None,
                is_premium=getattr(user, 'is_premium', None)
            )

        if response != 'ok':
            return

    if flyer_status[0] and not await flyer_check(key=FLYER_TOKEN, user_id=user_id, lang_code=user.language_code):
        return
    try:
        await bot.delete_message(chat_id=call.from_user.id, message_id=call.message.message_id)
    except Exception as e:
        print(f"Ошибка при удалении сообщения: {e}")
    
    photos = [p for p in list_photos(only_unsold=True) if p['user_id'] != user_id]
    if not photos:
        markup_back = InlineKeyboardBuilder().button(text="⬅️ В главное меню", callback_data="back_main").as_markup()
        await bot.send_message(user_id, "📸 <b>Нет доступных фотографий для покупки.</b>", parse_mode='HTML', reply_markup=markup_back)
        return
    
    random_photo = random.choice(photos)
    photo_id = random_photo['id']
    photo_user_id = random_photo['user_id']
    photo_path = random_photo['path_to_photo']
    photo_price = random_photo['price']

    original = Image.open(photo_path)
    watermarked = apply_watermark(original, text=f"@{(await bot.get_me()).username}", font_path="arial.ttf")
    buf = BytesIO()
    watermarked.save(buf, format="JPEG")
    buf.seek(0)
    data = buf.getvalue()
    input_file = BufferedInputFile(
        file=data,
        filename=f"photo_{photo_id}.jpg"
    )

    markup_photo = InlineKeyboardBuilder()
    markup_photo.button(text="📸 Купить", callback_data=f"process_buy:{photo_id}")
    markup_photo.button(text="🔄 Следующее", callback_data="buy_photo")
    markup_photo.button(text="⬅️ В главное меню", callback_data="back_main")
    markup_photo.adjust(2, 1)
    markup_photo = markup_photo.as_markup()
    
    await bot.send_photo(
        chat_id=user_id,
        photo=input_file,
        caption=f"📸 <b>Фото #{photo_id}</b>\n<i>Цена: {photo_price} ⭐️</i>",
        parse_mode='HTML',
        reply_markup=markup_photo
    )

def delete_file(filepath):
    if os.path.exists(filepath):
        try:
            os.remove(filepath)
            print(f"✅ Файл удалён: {filepath}")
        except Exception as e:
            print(f"❌ Ошибка при удалении файла: {e}")
    else:
        print(f"⚠️ Файл не найден: {filepath}")

@router.callback_query(F.data.startswith("process_buy:"))
async def process_buy(call: CallbackQuery, bot: Bot, state: FSMContext):
    user_id = call.from_user.id
    banned = get_banned_user(user_id)
    if banned == 1:
        await bot.answer_callback_query(call.id, "🚫 Вы заблокированы в боте!", show_alert=True)
        return
    
    photo_id = call.data.split(":")[1]
    photo = get_photo(photo_id)
    if not photo:
        await bot.answer_callback_query(call.id, "📸 Фото не найдено.", show_alert=True)
        return
    
    photo_price = photo['price']
    photo_user_id = photo['user_id']
    photo_path = photo['path_to_photo']

    if user_id == photo_user_id:
        await bot.answer_callback_query(call.id, "📸 Вы не можете купить свое фото.", show_alert=True)
        return
    
    balance = get_balance_user(user_id)
    if balance < photo_price:
        await bot.answer_callback_query(call.id, "❌ У вас недостаточно звёзд!", show_alert=True)
        return
    
    remove_stars(user_id, photo_price)
    add_stars(photo_user_id, photo_price)
    increment_count_photo_selling(photo_user_id)
    mark_photo_purchased(photo_id)


    try:
        await bot.delete_message(chat_id=call.from_user.id, message_id=call.message.message_id)
    except Exception as e:
        print(f"Ошибка при удалении сообщения: {e}")

    markup = InlineKeyboardBuilder()
    markup.button(text="❤️‍🔥 Далее к покупкам!", callback_data="buy_photo")
    markup.button(text="⬅️ В главное меню", callback_data="back_main")
    markup.adjust(1, 1)
    markup = markup.as_markup()

    await bot.send_photo(
        chat_id=photo_user_id,
        photo=FSInputFile(photo_path),
        caption=f"<b>✅ Ваше фото #{photo_id} куплено!</b>\n<i>Цена: {photo_price} ⭐️</i>",
        parse_mode='HTML'
    )
    
    await bot.send_photo(
        chat_id=user_id,
        photo=FSInputFile(photo_path),
        caption=f"<b>✅ Фото #{photo_id} куплено!</b>\n<i>Цена: {photo_price} ⭐️</i>",
        parse_mode='HTML',
        reply_markup=markup
    )

    try:
        delete_file(photo_path)
    except Exception as e:
        print(f"Ошибка при удалении фото: {e}")

def is_nude(image_path, threshold=0.25):
    banned = {
        'ANUS_EXPOSED',
        'BUTTOCKS_EXPOSED',
        'FEMALE_BREAST_EXPOSED',
        'FEMALE_GENITALIA_EXPOSED',
        'MALE_GENITALIA_EXPOSED'
    }
    result = detector.detect(image_path)
    return any(obj['class'] in banned and obj['score'] > threshold for obj in result)

@router.callback_query(F.data == "sell_photo")
async def sell_photo(call: CallbackQuery, bot: Bot, state: FSMContext):
    user_id = call.from_user.id
    user = call.from_user
    banned = get_banned_user(user_id)
    if banned == 1:
        await bot.answer_callback_query(call.id, "🚫 Вы заблокированы в боте!", show_alert=True)
        return

    if subgram_status[0]:
        response = await request_op(
                user_id=user_id,
                chat_id=call.message.chat.id,
                first_name=user.first_name,
                language_code=user.language_code,
                bot=bot,
                ref_id=None,
                is_premium=getattr(user, 'is_premium', None)
            )

        if response != 'ok':
            return
    if flyer_status[0] and not await flyer_check(key=FLYER_TOKEN, user_id=user_id, lang_code=user.language_code):
        return

    try:
        await bot.delete_message(chat_id=call.from_user.id, message_id=call.message.message_id)
    except Exception as e:
        print(f"Ошибка при удалении сообщения: {e}")
    
    await state.set_state(SellState.PRICE_PHOTO)
    await bot.send_message(user_id, "📸 <b>Введите цену фото:</b>\n<i>Пример: 2.0</i>", parse_mode='HTML')

@router.message(SellState.PRICE_PHOTO)
async def sell_photo_price(message: Message, bot: Bot, state: FSMContext):
    user_id = message.from_user.id
    banned = get_banned_user(user_id)
    if banned == 1:
        await message.reply("🚫 Вы заблокированы в боте!")
        await state.clear()
        return

    text = message.text.strip().replace(",", ".")

    try:
        price = float(text)
    except ValueError:
        await message.reply("❌ Неверный формат. Введите число, например: 2 или 2.0")
        return

    if not (1.0 <= price <= 5.0):
        await message.reply("❌ Цена должна быть от 1.0 до 5.0. Попробуйте ещё раз.")
        return

    await state.update_data(price=price)
    await state.set_state(SellState.PHOTO)
    await message.answer("📸 <b>Отправьте фото:</b>", parse_mode='HTML')


@router.message(StateFilter(SellState.PHOTO), F.content_type == "photo")
async def sell_photo_handle(message: Message, state: FSMContext, bot: Bot):
    user_id = message.from_user.id
    if get_banned_user(user_id):
        await message.reply("🚫 Вы заблокированы в боте!")
        await state.clear()
        return

    photo = message.photo[-1]
    unique_id = photo.file_unique_id
    file_name = f"{user_id}_{unique_id}.jpg"
    file_path = PHOTOS_DIR / file_name

    if file_path.exists():
        await message.reply(
            "❌ Эта фотография уже была загружена. Пожалуйста, отправьте другую фотографию."
        )
        return

    file_info: TgFile = await bot.get_file(photo.file_id)
    await bot.download_file(file_info.file_path, str(file_path))

    if is_nude(str(file_path)):
        await message.reply(f"<b>❌ Эта фотография не подходит для продажи.</b>\n<i>Пожалуйста, отправьте другую фотографию.</i>\n\n‼️ Это не так? Напишите об этом администратору: {admin_url}", disable_web_page_preview=True, parse_mode='HTML')
        os.remove(file_path)
        return

    data = await state.get_data()
    price = data.get("price")
    add_photo(user_id, price, str(file_path))

    for offset in range(4):
        try:
            await bot.delete_message(chat_id=user_id,
                                     message_id=message.message_id - offset)
        except TelegramBadRequest:
            continue

    markup_back = InlineKeyboardBuilder()
    markup_back.button(text="⬅️ В главное меню", callback_data="back_main")
    markup_back.adjust(1)

    await bot.send_message(
        user_id,
        "<b>✅ Фото успешно выставлено на продажу!</b>\n\n"
        "<i>ℹ️ Вас уведомят о покупке вашей фотографии.</i>",
        parse_mode='HTML',
        reply_markup=markup_back.as_markup()
    )
    await state.clear()

@router.callback_query(F.data == "stars_withdraw")
async def withdraw_start(call: CallbackQuery, bot: Bot):
    user_id = call.from_user.id
    user = call.from_user
    banned = get_banned_user(user_id)
    if banned == 1:
        await bot.answer_callback_query(call.id, "🚫 Вы заблокированы в боте!", show_alert=True)
        return

    if subgram_status[0]:
        response = await request_op(
                user_id=user_id,
                chat_id=call.message.chat.id,
                first_name=user.first_name,
                language_code=user.language_code,
                bot=bot,
                ref_id=None,
                is_premium=getattr(user, 'is_premium', None)
            )

        if response != 'ok':
            return
    if flyer_status[0] and not await flyer_check(key=FLYER_TOKEN, user_id=user_id, lang_code=user.language_code):
        return

    try:
        await bot.delete_message(chat_id=call.from_user.id, message_id=call.message.message_id)
    except Exception as e:
        print(f"Ошибка при удалении сообщения: {e}")

    builder = InlineKeyboardBuilder()
    buttons = [
        ('15 ⭐️(🧸)', 'withdraw:15:🧸'),
        ('15 ⭐️(💝)', 'withdraw:15:💝'),
        ('25 ⭐️(🌹)', 'withdraw:25:🌹'),
        ('25 ⭐️(🎁)', 'withdraw:25:🎁'),
        ('50 ⭐️(🍾)', 'withdraw:50:🍾'),
        ('50 ⭐️(🚀)', 'withdraw:50:🚀'),
        ('50 ⭐️(💐)', 'withdraw:50:💐'),
        ('50 ⭐️(🎂)', 'withdraw:50:🎂'),
        ('100 ⭐️(🏆)', 'withdraw:100:🏆'),
        ('100 ⭐️(💍)', 'withdraw:100:💍'),
        ('100 ⭐️(💎)', 'withdraw:100:💎'),
    ]

    for text, callback_data in buttons:
        builder.button(text=text, callback_data=callback_data)

    builder.button(text="⬅️ В главное меню", callback_data="back_main")
    builder.adjust(2, 2, 2, 2, 2, 1, 1)
    markup = builder.as_markup()

    balance = get_balance_user(user_id)

    await bot.send_photo(
        chat_id=user_id,
        photo=FSInputFile('photo/withdraw.png'),
        caption=(
            f"<b>🔸 У тебя на счету: {balance:.2f} ⭐️</b>\n\n"
            f"📌 <b>Внимание! Условия вывода:</b>\n"
            "<blockquote>"
            f"<i>🔹 Иметь достаточное количество звёзд — <b>{stars_to_withdraw[0]}</b>\n"
            f"🔸 Иметь достаточное количество проданых фото — <b>{photos_to_withdraw[0]}</b>\n"
            f"🔹 Быть подписаным на все указанные ресурсы ниже ⬇️\n"
            f"🔸 <a href='{channel_osn}'>Основной канал</a> | <a href='{channel_withdraw}'>Канал вывода</a> | <a href='{chater}'>Чат</a></i>"
            "</blockquote>\n\n"
            f"{"🔄 <b>Вам доступен автовывод!</b>\n\n" if check_auto(user_id) else ""}"
            "<b>Выбери количество звёзд, которое хочешь обменять, из доступных вариантов ниже:</b>"
        ),
        parse_mode='HTML',
        reply_markup=markup
    )

def get_gift_id_by_emoji(emoji: str) -> str | None:
    gifts = {
        "💝": "5170145012310081615",
        "🧸": "5170233102089322756",
        "🎁": "5170250947678437525",
        "🌹": "5168103777563050263",
        "🎂": "5170144170496491616",
        "🎐": "5170314324215857265",
        "🚀": "5170564780938756245",
        "🏆": "5168043875654172773",
        "💍": "5170690322832818290",
        "💎": "5170521118301225164",
        "🍾": "6028601630662853006"
    }
    return gifts.get(emoji)

async def get_bot_star_balance():
    url = f"https://api.telegram.org/bot{TOKEN}/getMyStarBalance"

    async with ClientSession() as session:
        async with session.get(url) as resp:
            result = await resp.json()
            return result['result']['amount']


@router.callback_query(F.data.startswith("withdraw:"))
async def withdraw_callback(call: CallbackQuery, bot: Bot, state: FSMContext):
    user_id = call.from_user.id
    banned = get_banned_user(user_id)
    if banned == 1:
        await bot.answer_callback_query(call.id, "🚫 Вы заблокированы в боте!", show_alert=True)
        return
    
    username = call.from_user.username
    if username is None:
        await bot.answer_callback_query(call.id, "⚠️ Для вывода необходимо установить username.", show_alert=True)
        return

    data = call.data.split(":")
    amount = int(data[1])
    emoji = data[2]
    
    bot_balance = await get_bot_star_balance()

    balance = get_balance_user(user_id)
    if balance < stars_to_withdraw[0]:
        await bot.answer_callback_query(call.id, f"🚫 Минимум для вывода — {stars_to_withdraw[0]}⭐", show_alert=True)
        return

    if balance < amount:
        await bot.answer_callback_query(call.id, "🚫 У вас недостаточно звёзд!", show_alert=True)
        return

    photos = get_photo_sell_count(user_id)
    if photos < photos_to_withdraw[0]:
        await bot.answer_callback_query(call.id, "🚫 У вас недостаточно проданных фото!", show_alert=True)
        return

    try:
        for admin in admins_id:
            await bot.send_message(admin, f"<b>👤 Пользователь {user_id} | @{username}\n\nℹ️ Запросил вывод: {amount}⭐️\n💰 Баланс: {balance}\n💸 Выведено: {get_withdrawed(user_id)}</b>", parse_mode='HTML')

        remove_stars(user_id, amount)
        add_withdrawal(user_id, amount)

        if check_auto(user_id) and bot_balance >= amount:
            id_gift = get_gift_id_by_emoji(emoji)
            if id_gift is not None:
                await bot.send_gift(user_id, id_gift, text="🎁 Успешная выплата")
            
            await bot.send_message(user_id, f"✅ Ваша заявка на вывод <b>звёзд</b> обработана!",parse_mode='HTML')
            await bot.send_message(chahnel_withdraw_id, f"[🟢 <b>АВТОВЫВОД</b>]\n\n👤 Пользователь: @{username} | ID {user_id}\n💫 Количество: <code>{amount}</code>⭐️ [{emoji}]\n\n🔄 Статус: <b>Подарок отправлен 🎁</b>", parse_mode='HTML')
            return
        elif check_auto(user_id) and bot_balance < amount:
            await bot.send_message(user_id, f"❌ Баланс бота недостаточен для авто-выплаты!\n<i>Переводим вас на ручную обработку...</i>", parse_mode='HTML')
        success, id_v = add_withdrawale(username, user_id, amount)
        status = get_status_withdrawal(user_id)
        pizda = await bot.send_message(
                    chahnel_withdraw_id,
                    f"<b>✅ Запрос на вывод №{id_v}</b>\n\n👤 Пользователь: @{username} | ID {user_id}\n"
                    f"💫 Количество: <code>{amount}</code>⭐️ [{emoji}]\n\n🔄 Статус: <b>{status}</b>",
                    disable_web_page_preview=True,
                    parse_mode='HTML'
                )
        builder_channel = InlineKeyboardBuilder()
        builder_channel.button(text="✅ Отправить", callback_data=f"paid:{id_v}:{pizda.message_id}:{user_id}:{username}:{amount}:{emoji}")
        builder_channel.button(text="❌ Отклонить", callback_data=f"denied:{id_v}:{pizda.message_id}:{user_id}:{username}:{amount}:{emoji}")
        builder_channel.button(text="👤 Профиль", url=f"tg://user?id={user_id}")
        markup_channel = builder_channel.adjust(2, 1).as_markup()
        await bot.edit_message_text(
                    chat_id=pizda.chat.id,
                    message_id=pizda.message_id,
                    text=f"<b>✅ Запрос на вывод №{id_v}</b>\n\n👤 Пользователь: @{username} | ID {user_id}\n"
                         f"💫 Количество: <code>{amount}</code>⭐️ [{emoji}]\n\n🔄 Статус: <b>{status}</b>",
                    parse_mode='HTML',
                    reply_markup=markup_channel,
                    disable_web_page_preview=True
                )
        await bot.answer_callback_query(call.id, f"✅ Заявка на вывод {amount}⭐️ отправлена!", show_alert=True)

    except Exception as e:
        print("error withdraw: ", e)
        await bot.answer_callback_query(call.id, "❌ Произошла ошибка при обработке вашего запроса на вывод.", show_alert=True)

@router.callback_query(F.data.startswith("denied"))
async def denied_callback(call: CallbackQuery, bot: Bot):
    if call.from_user.id in admins_id:
        data = call.data.split(":")
        # print(data)
        id_v, mesag_id, us_id, us_name, strs, emoji = map(str, data[1:7])

        reason_markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🎰 Накрутка", callback_data=f"balk:{id_v}:{mesag_id}:{us_id}:{us_name}:{strs}:{emoji}:narkutka")],
            [InlineKeyboardButton(text="🎫 Не выполнены условия вывода", callback_data=f"balk:{id_v}:{mesag_id}:{us_id}:{us_name}:{strs}:{emoji}:usloviya")],
            [InlineKeyboardButton(text="❌ Черный список", callback_data=f"balk:{id_v}:{mesag_id}:{us_id}:{us_name}:{strs}:{emoji}:black_list")],
            [InlineKeyboardButton(text="⚠️ Багаюз", callback_data=f"balk:{id_v}:{mesag_id}:{us_id}:{us_name}:{strs}:{emoji}:bagous")]
        ])

        text = (
            f"<b>✅ Запрос на вывод №{id_v}</b>\n\n"
            f"👤 Пользователь: @{us_name} | ID: {us_id}\n"
            f"💫 Количество: <code>{strs}</code>⭐️ [{emoji}]\n\n"
            f"🔄 Статус: <b>Отказано 🚫</b>\n\n"
            f"<b><a href='{channel_osn}'>Основной канал</a></b> | "
            f"<b><a href='{chater}'>Чат</a></b> | "
            f"<b><a href='{'https://t.me/' + (await bot.me()).username}'>Бот</a></b>"
        )

        await safe_edit_message(bot, chahnel_withdraw_id, int(mesag_id), text, reason_markup)
    else:
        await bot.answer_callback_query(call.id, "⚠️ Вы не администратор.")

@router.callback_query(F.data.startswith("balk"))
async def denied_reason_callback(call: CallbackQuery, bot: Bot):
    if call.from_user.id in admins_id:
        data = call.data.split(":")
        id_v, mesag_id, us_id, us_name, strs, emoji, reason = map(str, data[1:8])

        reasons = {
            "narkutka": "🎰 Накрутка",
            "usloviya": "🎫 Отсутствует подписка на канал/чат",
            "black_list": "❌ Черный список",
            "bagous": "⚠️ Багаюз"
        }

        reason_text = reasons.get(reason, "Неизвестная причина")

        text = (
            f"<b>✅ Запрос на вывод №{id_v}</b>\n\n"
            f"👤 Пользователь: @{us_name} | ID: {us_id}\n"
            f"💫 Количество: <code>{strs}</code>⭐️ [{emoji}]\n\n"
            f"🔄 Статус: <b>Отказано 🚫</b>\n"
            f"⚠️ Причина: <b>{reason_text}</b> \u200B\n\n"
            f"<b><a href='{channel_osn}'>Основной канал</a></b> | "
            f"<b><a href='{chater}'>Чат</a></b> | "
            f"<b><a href='{'https://t.me/' + (await bot.me()).username}'>Бот</a></b>"
        )

        await safe_edit_message(bot, chahnel_withdraw_id, int(mesag_id), text, None)
    else:
        await bot.answer_callback_query(call.id, "⚠️ Вы не администратор.")
@router.callback_query(F.data.startswith("paid"))
async def paid_callback(call: CallbackQuery, bot: Bot):
    if call.from_user.id in admins_id:
        data = call.data.split(":")
        id_v = int(data[1])
        message_id = int(data[2])
        user_id = int(data[3])
        username = data[4]
        stars = int(data[5])
        emoji = data[6]
        try:
            update_status_withdrawal(id_v, "Подарок отправлен 🎁")
            await bot.send_message(user_id, text="✅ Ваша заявка на вывод <b>звёзд</b> подтвердждена!",parse_mode='HTML')
            await call.message.edit_text(
                text=(
                    f"<b>✅ Запрос на вывод №{id_v}</b>\n\n"
                    f"👤 Пользователь: @{username} | ID: {user_id}\n"
                    f"💫 Количество: <code>{stars}</code>⭐️ [{emoji}]\n\n"
                    "🔄 Статус: <b>Подарок отправлен 🎁</b>\n\n"
                    f"<b><a href='{channel_osn}'>Основной канал</a></b> | "
                    f"<b><a href='{chater}'>Чат</a></b> | "
                    f"<b><a href='https://t.me/{(await bot.me()).username}'>Бот</a></b>"
                ),
                parse_mode='HTML',
                disable_web_page_preview=True
            )

        except Exception as e:
            print(f"error paid: {e}")
    else:
        await bot.answer_callback_query(call.id, "🚫 Вы не администратор!", show_alert=True)
        return

@router.message(F.text == '/adminpanel')
async def adminpanel_command(message: Message, bot: Bot):
    if message.from_user.id in admins_id:
        count_users = get_count_users()
        admin_builder = InlineKeyboardBuilder()
        admin_builder.button(text="⚙️ Изменить конфиг", callback_data='change_config')
        admin_builder.button(text="🎁 Пополнить баланс для автовывода", callback_data='more_balance')
        admin_builder.button(text="📄 Добавить в автовывод", callback_data='add_auto')
        admin_builder.button(text='📋 Список в автовыводе', callback_data='info_auto')
        admin_builder.button(text='❌ Удалить из автовывода', callback_data='remove_auto')
        admin_builder.button(text='🎁 Добавить промокод', callback_data='add_promo_code')
        admin_builder.button(text='📋 Список промокодов', callback_data='info_promo_codes')
        admin_builder.button(text='❌ Удалить промокод', callback_data='remove_promo_code')
        admin_builder.button(text='👤 Информация о пользователе', callback_data='users_check')
        admin_builder.button(text='➕ Добавить ОП', callback_data='add_op')
        admin_builder.button(text='📄 Список ОП', callback_data='list_op')
        admin_builder.button(text='➖ Удалить ОП', callback_data='delete_op')
        admin_builder.button(text='📤 Рассылка', callback_data='mailing')
        panel_admin = admin_builder.adjust(1, 1, 3, 3, 1, 3, 1).as_markup()
        await bot.send_message(message.from_user.id, f"<b>🎉 Вы вошли в панель администратора:</b>\n\n👥 Пользователей: {count_users}", parse_mode='HTML', reply_markup=panel_admin)
    else:
        await bot.send_message(message.from_user.id, "⚠️ Вы не администратор!", parse_mode='HTML')

@router.callback_query(F.data == "more_balance")
async def deposit(call: CallbackQuery, bot: Bot, state: FSMContext):
    await bot.send_message(call.from_user.id, "⭐️ Введите количество звёзд для пополнения баланса бота: ")
    await state.set_state(AdminState.DEPOSIT)

@router.message(AdminState.DEPOSIT)
async def depka(message: Message, state: FSMContext, bot: Bot):
    try:
        count_stars = int(message.text)
        if count_stars <= 0:
            raise ValueError

        await state.clear()

        prices = [LabeledPrice(label="XTR", amount=count_stars)]

        builder = InlineKeyboardBuilder()
        builder.button(text=f"⭐️ Пополнить {count_stars}", pay=True)

        await bot.send_invoice(
            chat_id=message.chat.id,
            title="Пополнение звёзд ⭐️",
            description=f"Вы пополняете баланс на {count_stars}⭐️",
            payload=f"deposit:{count_stars}",
            provider_token="",
            currency="XTR",
            prices=prices,
            reply_markup=builder.as_markup()
        )

    except ValueError:
        await bot.send_message(message.from_user.id, "⚠️ Введите корректное число больше нуля!")

@router.pre_checkout_query()
async def pre_checkout(pre_checkout_query: PreCheckoutQuery, bot: Bot):
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

@router.message(F.successful_payment)
async def on_success(message: Message, bot: Bot):
    payload = message.successful_payment.invoice_payload
    if payload.startswith("deposit:"):
        stars = int(payload.split(":")[1])
        await bot.send_message(message.chat.id, f"✅ Пополнение на {stars}⭐️ прошло успешно!")

@router.callback_query(F.data.endswith("_auto"))
async def auto(call: CallbackQuery, bot: Bot, state: FSMContext):
    call_data = call.data.split("_")[0]
    user_id = call.from_user.id
    if call_data == "add":
        await bot.send_message(user_id, "ℹ️ Введите ID доверенного пользователя: ")
        await state.set_state(AdminState.ADD_AUTO)
    elif call_data == "info":
        list_users = get_auto_withdrawals()

        text = "📋 Список доверенных пользователей на автовывод:\n\n"
        for index, user in enumerate(list_users):
            text += f"{index + 1}. {user}\n"

        await bot.send_message(user_id, text=text)
    elif call_data == "remove":
        await bot.send_message(user_id, "ℹ️ Введите ID доверенного пользователя для удаления: ")
        await state.set_state(AdminState.REMOVE_AUTO)

@router.message(AdminState.ADD_AUTO)
async def add_auto(message: Message, state: FSMContext, bot: Bot):
    user_id = message.text
    add_to_auto_withdrawals(user_id)
    await bot.send_message(message.from_user.id, "✅ Пользователь успешно добавлен в автовывод!")
    await bot.send_message(int(user_id), "<b>✅ Вы были внесены в список доверенных пользователей!</b>", parse_mode='HTML')
    await bot.send_gift()
    await state.clear()

@router.message(AdminState.REMOVE_AUTO)
async def remove_auto(message: Message, state: FSMContext, bot: Bot):
    user_id = message.text
    remove_from_auto_withdrawals(user_id)
    await bot.send_message(message.from_user.id, "✅ Пользователь успешно удален из автовывода!")
    await bot.send_message(int(user_id), "❌ <b>Вы были удалены из списка доверенных пользователей!</b>", parse_mode='HTML')
    await state.clear()            

@router.callback_query(F.data == "delete_op")
async def delete_op(call: CallbackQuery, bot: Bot, state: FSMContext):
    await bot.send_message(call.from_user.id, "Введите ID канала:")
    await state.set_state(AdminState.REMOVE_OP)

@router.message(AdminState.REMOVE_OP)
async def delete_op_message(message: Message, state: FSMContext, bot: Bot):
    channel = message.text
    try:
        delete_channel(channel)
    except:
        await bot.send_message(message.from_user.id, "❌ Ошибка!")
    await bot.send_message(message.from_user.id, "✅ Канал успешно удален!")
    await state.clear()

@router.callback_query(F.data == 'list_op')
async def list_op(call: CallbackQuery, bot: Bot):
    channels = get_channels_ids()

    if not channels:
        text = "<b>🎉 Список ОП:</b>\n\n<b>Пусто</b>"
    else:
        text = "<b>🎉 Список ОП:</b>\n\n"
        for index, channel_id in enumerate(channels):
            text += f"{index + 1}. <code>{int(channel_id)}</code>\n"

    await bot.send_message(call.from_user.id, text, parse_mode='HTML')

@router.callback_query(F.data == "add_op")
async def add_op_callback(call: CallbackQuery, bot: Bot, state: FSMContext):
    
    await bot.send_message(call.from_user.id, "Введите ID канала:")
    await state.set_state(AdminState.ADD_OP)

async def is_bot_admin_in_channel(bot: Bot, channel_id: str) -> tuple[bool, str | None]:
    try:
        member = await bot.get_chat_member(chat_id=channel_id, user_id=bot.id)
        if isinstance(member, ChatMemberAdministrator):
            return True, None
        else:
            return False, "❌ Бот не является администратором в этом канале."

    except TelegramForbiddenError:
        return False, "🚫 Бот не может получить доступ к этому каналу. Он должен быть в нём."
    except TelegramBadRequest as e:
        return False, f"⚠️ Ошибка: {e.message}"

async def create_invite_link(bot: Bot, channel_id: str, link_name: str) -> str | None:
    try:
        invite_link: ChatInviteLink = await bot.create_chat_invite_link(
            chat_id=channel_id,
            name=link_name,
            creates_join_request=False,
            expire_date=None,
            member_limit=None
        )
        return invite_link.invite_link

    except TelegramForbiddenError:
        print("🚫 У бота нет прав создавать ссылку в этом канале.")
        return None
    except TelegramBadRequest as e:
        print(f"⚠️ Ошибка при создании ссылки: {e.message}")
        return None

@router.message(AdminState.ADD_OP)
async def add_op_message(message: Message, state: FSMContext, bot: Bot):
    channel = message.text.strip()

    if not channel.startswith("-100"):
        await bot.send_message(message.from_user.id, "⚠️ Введите корректный ID канала (начинается с -100).")
        return
    
    is_admin, error_msg = await is_bot_admin_in_channel(bot, channel)

    if not is_admin:
        await bot.send_message(message.from_user.id, error_msg)
        return

    link = await create_invite_link(bot, channel, "OP_LINK")
    if link:
        add_channel(channel, link)
        await bot.send_message(message.from_user.id, "✅ Канал успешно добавлен!")
    else:
        await bot.send_message(message.from_user.id, "❌ Не удалось создать ссылку.")
    await state.clear()

@router.callback_query(F.data == "users_check")
async def users_check_callback(call: CallbackQuery, bot: Bot, state: FSMContext):
    try:
        await bot.delete_message(chat_id=call.from_user.id, message_id=call.message.message_id)
    except Exception as e:
        print(f"Ошибка при удалении сообщения: {e}")
    
    await bot.send_message(call.from_user.id, "Введите ID пользователя:")
    await state.set_state(AdminState.USERS_CHECK)
    
@router.message(AdminState.USERS_CHECK)
async def users_check_message(message: Message, state: FSMContext, bot: Bot):
    user_id = int(message.text)
    log = get_user_log_html(user_id)
    markup = InlineKeyboardBuilder()
    markup.button(text="❌ Заблокировать", callback_data=f"block_user:{user_id}")
    markup.button(text="🟢 Разблокировать", callback_data=f"unblock_user:{user_id}")
    markup.button(text="⬅️ В админ меню", callback_data="adminpanel")
    await bot.send_message(message.from_user.id, log, parse_mode='HTML', reply_markup=markup.adjust(1,1,1).as_markup())
    await state.clear()

@router.callback_query(F.data.startswith('block_user:'))
async def block_user_callback(call: CallbackQuery, bot: Bot):
    try:
        user_id = int(call.data.split(":")[1])
        banned = get_banned_user(user_id)
        if banned == 1:
            await bot.answer_callback_query(call.id, "⚠️ Пользователь уже заблокирован!", show_alert=True)
            return
        set_banned_user(user_id, 1)
        await bot.answer_callback_query(call.id, "✅ Пользователь заблокирован!", show_alert=True)
    except ValueError:
        await bot.answer_callback_query(call.id, "⚠️ Ошибка при блокировке пользователя!", show_alert=True)

@router.callback_query(F.data.startswith('unblock_user:'))
async def unblock_user_callback(call: CallbackQuery, bot: Bot):
    try:
        user_id = int(call.data.split(":")[1])
        banned = get_banned_user(user_id)
        if banned == 0:
            await bot.answer_callback_query(call.id, "⚠️ Пользователь не заблокирован!", show_alert=True)
            return
        set_banned_user(user_id, 0)
        await bot.answer_callback_query(call.id, "✅ Пользователь разблокирован!", show_alert=True)
    except ValueError:
        await bot.answer_callback_query(call.id, "⚠️ Ошибка при разблокировке пользователя!", show_alert=True)

@router.callback_query(F.data == "adminpanel")
async def adminpanel_callback(call: CallbackQuery, bot: Bot):
    try:
        await bot.delete_message(chat_id=call.from_user.id, message_id=call.message.message_id)
    except Exception as e:
        print(f"Ошибка при удалении сообщения: {e}")
    if call.from_user.id in admins_id:
        count_users = get_count_users()
        admin_builder = InlineKeyboardBuilder()
        admin_builder.button(text="⚙️ Изменить конфиг", callback_data='change_config')
        admin_builder.button(text="🎁 Пополнить баланс для автовывода", callback_data='more_balance')
        admin_builder.button(text="📄 Добавить в автовывод", callback_data='add_auto')
        admin_builder.button(text='📋 Список в автовыводе', callback_data='info_auto')
        admin_builder.button(text='❌ Удалить из автовывода', callback_data='remove_auto')
        admin_builder.button(text='🎁 Добавить промокод', callback_data='add_promo_code')
        admin_builder.button(text='📋 Список промокодов', callback_data='info_promo_codes')
        admin_builder.button(text='❌ Удалить промокод', callback_data='remove_promo_code')
        admin_builder.button(text='👤 Информация о пользователе', callback_data='users_check')
        admin_builder.button(text='➕ Добавить ОП', callback_data='add_op')
        admin_builder.button(text='📄 Список ОП', callback_data='list_op')
        admin_builder.button(text='➖ Удалить ОП', callback_data='delete_op')
        admin_builder.button(text='📤 Рассылка', callback_data='mailing')
        panel_admin = admin_builder.adjust(1, 1, 3, 3, 1, 3, 1).as_markup()
        await bot.send_message(call.from_user.id, f"<b>🎉 Вы вошли в панель администратора:</b>\n\n👥 Пользователей: {count_users}", parse_mode='HTML', reply_markup=panel_admin)
    else:
        await bot.send_message(call.from_user.id, "⚠️ Вы не администратор!", parse_mode='HTML')


@router.callback_query(F.data == "change_config")
async def config_changer(call: CallbackQuery, bot: Bot):
    user_id = call.from_user.id
    try:
        await bot.delete_message(chat_id=call.from_user.id, message_id=call.message.message_id)
    except Exception as e:
        print(f"Ошибка при удалении сообщения: {e}")

    subgram_st = "✅" if subgram_status[0] == True else "❌"
    flyer_st = "✅" if flyer_status[0] == True else "❌"
    builder_config = InlineKeyboardBuilder()

    builder_config.button(text=f"ℹ️ Subgram [{subgram_st}]", callback_data="config_subgram")
    builder_config.button(text=f"🎉 Flyer [{flyer_st}]", callback_data="config_flyer")
    builder_config.button(text="🎁 Награды", callback_data="config_awards")
    builder_config.button(text="🔄 Вывод", callback_data="config_withdraw")
    builder_config.button(text="🎰 Мини-Игры", callback_data="config_games")
    builder_config.button(text="⬅️ Назад", callback_data="adminpanel")
    
    markup_config = builder_config.adjust(2, 2, 1, 1).as_markup()
    
    await bot.send_message(
        call.message.chat.id,
        "<b>🛠️ Изменить конфиг</b>\n\nВыберите раздел для изменения настроек:",
        parse_mode="HTML",
        reply_markup=markup_config
    )

@router.callback_query(F.data == "config_games")
async def change_games(call: CallbackQuery, bot: Bot):
    user_id = call.from_user.id
    message_id = call.message.message_id

    try:
        await bot.delete_message(chat_id=user_id, message_id=message_id)
    except Exception as e:
        print(f"Не удалось удалить сообщение: {e}")

    builder = InlineKeyboardBuilder()

    builder.button(text="🎰 Слоты", callback_data="config_game:slots")

    builder.button(text="⬅️ Назад", callback_data="adminpanel")
    markup = builder.adjust(1).as_markup()

    await bot.send_message(
        chat_id=user_id,
        text="⚙️ <b>Выберите мини-игру для настройки:</b>",
        parse_mode="HTML",
        reply_markup=markup
    )

async def show_slots_config(call_or_msg: Message | CallbackQuery, bot: Bot):
    if isinstance(call_or_msg, Message):
        user_id = call_or_msg.from_user.id
    else:
        user_id = call_or_msg.from_user.id
        try:
            await bot.delete_message(chat_id=user_id, message_id=call_or_msg.message.message_id)
        except Exception as e:
            print(f"[!] Не удалось удалить сообщение: {e}")

    builder = InlineKeyboardBuilder()

    builder.button(
        text=f"💰 Стоимость прокрута: {mini_games[0]} ⭐",
        callback_data="edit_spin_cost"
    )
    slot_emojis = {
        1: "💎💎💎",
        22: "🍋🍋🍋",
        43: "🔔🔔🔔",
        64: "7️⃣7️⃣7️⃣"
    }


    for value, reward in reward_games["slots"]["reward_table"].items():
        symbol = slot_emojis.get(value, f"{value}")
        builder.button(
            text=f"🎲 Выпало {symbol} ➝ {reward} ⭐",
            callback_data=f"edit_slot_reward:{value}"
        )


    builder.button(text="⬅️ Назад", callback_data="config_games")

    markup = builder.adjust(1).as_markup()

    await bot.send_message(
        chat_id=user_id,
        text="🎰 <b>Настройка игры: Слоты</b>\n\nВыберите, что хотите изменить:",
        parse_mode="HTML",
        reply_markup=markup
    )

@router.callback_query(F.data == "edit_spin_cost")
async def edit_spin_cost(call: CallbackQuery, bot: Bot, state: FSMContext):
    await bot.delete_message(call.from_user.id, call.message.message_id)
    await bot.send_message(call.from_user.id, "💰 Введите новую стоимость прокрута в звёздах:")
    await state.set_state(AdminState.EDIT_SPIN_COST)

@router.message(AdminState.EDIT_SPIN_COST)
async def save_spin_cost(message: Message, state: FSMContext, bot: Bot):
    text = message.text.strip()
    if not text.isdigit():
        await message.answer("❌ Введите корректное число (целое):")

    mini_games[0] = int(text)
    await message.answer(f"✅ Стоимость прокрута обновлена: {mini_games[0]} ⭐️")

    await state.clear()
    await show_slots_config(message, bot)

@router.callback_query(F.data.startswith("edit_slot_reward:"))
async def edit_slot_reward(call: CallbackQuery, bot: Bot, state: FSMContext):
    value = int(call.data.split(":")[1])
    await bot.delete_message(call.from_user.id, call.message.message_id)
    await bot.send_message(
        call.from_user.id,
        f"🎲 Введите новую награду для выпадения {value}:"
    )
    await state.set_state(AdminState.EDIT_SLOT_REWARD)
    await state.update_data(editing_value=value)

@router.message(AdminState.EDIT_SLOT_REWARD)
async def save_slot_reward(message: Message, state: FSMContext, bot: Bot):
    text = message.text.strip()
    if not text.isdigit():
        await message.answer("❌ Введите корректное число (целое):")
        return

    data = await state.get_data()
    value = data.get("editing_value")

    reward_games["slots"]["reward_table"][value] = int(text)
    await message.answer(f"✅ Награда для {value} обновлена: {int(text)} ⭐️")

    await state.clear()
    await show_slots_config(message, bot)

@router.callback_query(F.data.startswith("config_game:"))
async def open_game_config(call: CallbackQuery, bot: Bot):
    game_name = call.data.split(":")[1]
    if game_name == "slots":
        await show_slots_config(call, bot)

@router.callback_query(F.data == "config_flyer")
async def change_flyer(call: CallbackQuery, bot: Bot):
    user_id = call.from_user.id
    message_id = call.message.message_id

    try:
        await bot.delete_message(chat_id=user_id, message_id=message_id)
    except Exception as e:
        print(f"Не удалось удалить сообщение: {e}")

    flyer_status[0] = not flyer_status[0]
    status_text = "включён ✅" if flyer_status[0] else "выключен ❌"

    builder = InlineKeyboardBuilder()

    builder.button(text="⬅️ Назад", callback_data="change_config")

    markup = builder.adjust(1).as_markup()

    await bot.send_message(
        user_id,
        f"🔁 Flyer теперь {status_text}!",
        reply_markup=markup
    )

@router.callback_query(F.data == "config_subgram")
async def change_subgram(call: CallbackQuery, bot: Bot):
    user_id = call.from_user.id
    message_id = call.message.message_id

    try:
        await bot.delete_message(chat_id=user_id, message_id=message_id)
    except Exception as e:
        print(f"Не удалось удалить сообщение: {e}")

    subgram_status[0] = not subgram_status[0]
    status_text = "включён ✅" if subgram_status[0] else "выключен ❌"

    builder = InlineKeyboardBuilder()

    builder.button(text="⬅️ Назад", callback_data="change_config")

    markup = builder.adjust(1).as_markup()


    await bot.send_message(
        user_id,
        f"🔁 Subgram теперь {status_text}!",
        reply_markup=markup
    )


@router.callback_query(F.data == "config_withdraw")
async def change_withdraw(call: CallbackQuery, bot: Bot, state: FSMContext):
    try:
        await bot.delete_message(chat_id=call.from_user.id, message_id=call.message.message_id)
    except Exception as e:
        print(f"Ошибка при удалении сообщения: {e}")
    await state.set_state(AdminState.WITHDRAW)
    await bot.send_message(call.from_user.id, "Введите количество звёзд и проданных фото для вывода:\n\n(Пример: 5:10)")

@router.message(AdminState.WITHDRAW)
async def change_withdraw(message: Message, bot: Bot, state: FSMContext):
    try:
        stars, photo = message.text.split(':')
        stars = int(stars)
        photo = int(photo)
        stars_to_withdraw[0] = stars
        photos_to_withdraw[0] = photo
        await bot.send_message(message.from_user.id, "✅ Количество звёзд и фото успешно изменено!")
        await state.clear()
    except Exception as e:
        print(f"error change_withdraw: {e}")

@router.callback_query(F.data == "config_awards")
async def change_awards(call: CallbackQuery, bot: Bot, state: FSMContext):
    try:
        await bot.delete_message(chat_id=call.from_user.id, message_id=call.message.message_id)
    except Exception as e:
        print(f"Ошибка при удалении сообщения: {e}")
    await state.set_state(AdminState.AWARDS)
    await bot.send_message(call.from_user.id, "Введите количество звёзд за реферала:")

@router.message(AdminState.AWARDS)
async def change_awards(message: Message, bot: Bot, state: FSMContext):
    try:
        stars = int(message.text)
        stars_reffer[0] = stars
        await bot.send_message(message.from_user.id, "✅ Количество звёзд успешно изменено!")
        await state.clear()
    except Exception as e:
        print(f"error change_awards: {e}")

@router.callback_query(F.data == "info_promo_codes")
async def info_promo_codes_callback(call: CallbackQuery, bot: Bot):

    promocodes = get_all_promocodes()

    text = "<b>🎟️ Текущие промокоды:</b>\n\n"

    for promo in promocodes:
        status = "🟢 Активен" if promo['is_active'] else "🔴 Неактивен"
        text += (f"<b>ID:</b> {promo['id']}\n"
                 f"<b>Код:</b> {promo['code']}\n"
                 f"<b>Звёзды:</b> {promo['stars']}\n"
                 f"<b>Использовано:</b> {promo['current_uses']} из {promo['max_uses']}\n"
                 f"<b>Статус:</b> {status}\n\n")

    if not promocodes:
        text += "<b>Пусто</b>\n"

    await bot.send_message(call.message.chat.id, text, parse_mode='HTML')

@router.callback_query(F.data == "add_promo_code")
async def admin_add_promo_code_callback(call: CallbackQuery, bot: Bot, state: FSMContext):
    await bot.send_message(call.from_user.id, "Введите промокод:награда:макс. пользований")
    await state.set_state(AdminState.ADD_PROMO_CODE)

@router.message(AdminState.ADD_PROMO_CODE)
async def add_promo_code_handler(message: Message, state: FSMContext, bot: Bot):
    try:
        promocode, stars_str, max_uses_str = message.text.split(":")
        stars = int(stars_str)
        max_uses = int(max_uses_str)
        add_promocode(promocode, stars, max_uses)
        await message.reply(f"<b>✅ Промокод успешно добавлен!</b>", parse_mode='HTML')
    except ValueError:
        await message.reply("<b>❌ Неверный формат ввода. Используйте промокод:награда:макс. пользований (числа).</b>", parse_mode='HTML')
    except Exception as e:
        logging.error(f"Ошибка при добавлении промокода: {e}")
        await message.reply("<b>❌ Произошла ошибка при добавлении промокода.</b>", parse_mode='HTML')
    finally:
        await state.clear()

@router.callback_query(F.data == "remove_promo_code")
async def admin_remove_promo_code_callback(call: CallbackQuery, bot: Bot, state: FSMContext):
    await bot.send_message(call.from_user.id, "Введите промокод:")
    await state.set_state(AdminState.REMOVE_PROMO_CODE)

@router.message(AdminState.REMOVE_PROMO_CODE)
async def delete_promo_code_handler(message: Message, state: FSMContext, bot: Bot):
    promocode = message.text
    try:
        delete_promocode(promocode)
        await message.reply(f"<b>✅ Промокод успешно удален!</b>", parse_mode='HTML')
    except Exception as e:
        logging.error(f"Ошибка при удалении промокода: {e}")
        await message.reply("<b>❌ Произошла ошибка при удалении промокода.</b>", parse_mode='HTML')
    finally:
        await state.clear()


@router.callback_query(F.data == "mailing")
async def admin_mailing_callback(call: CallbackQuery, bot: Bot, state: FSMContext):
    await bot.send_message(call.from_user.id, "Введите текст рассылки:\n\n(<i>Для кнопки использовать синтаксис: {названиекнопки}:url</i>)", parse_mode='HTML')
    await state.set_state(AdminState.MAILING)

async def send_message_with_retry(
    bot: Bot,
    chat_id: int,
    text: str,
    parse_mode=None,
    reply_markup=None,
    photo_file_id: Optional[str] = None,
    attempt: int = 0
):
    try:
        if photo_file_id:
            await bot.send_photo(
                chat_id,
                photo=photo_file_id,
                caption=text,
                parse_mode=parse_mode,
                reply_markup=reply_markup
            )
        else:
            await bot.send_message(
                chat_id,
                text,
                parse_mode=parse_mode,
                reply_markup=reply_markup
            )
        return True
    except (TelegramForbiddenError, TelegramNotFound) as e:
        logging.error(f"Сообщение запрещено/пользователь не найден: {chat_id}. Причина: {e}")
        return False
    except TelegramMigrateToChat as e:
        logging.info(f"Чат перенесён. Новый ID: {e.migrate_to_chat_id}")
        return await send_message_with_retry(
            bot, e.migrate_to_chat_id, text, parse_mode, reply_markup, photo_file_id, attempt + 1
        )
    except TelegramRetryAfter as e:
        logging.warning(f"Ожидаем {e.retry_after} сек. из-за лимитов.")
        await asyncio.sleep(e.retry_after)
        return await send_message_with_retry(
            bot, chat_id, text, parse_mode, reply_markup, photo_file_id, attempt + 1
        )
    except Exception as e:
        logging.exception(f"Ошибка отправки: {e}")
        return False


async def update_progress(
    progress_message: types.Message,
    current: int,
    total_users: int,
    success: int,
    semaphore_value: int,
    speed_stats: dict
):
    percent = (current / total_users) * 100
    filled = int(percent / 10)
    progress_bar = '🟩' * filled + '⬜️' * (10 - filled)
    
    current_speed = speed_stats["current_speed"]
    avg_speed = speed_stats["avg_speed"]
    
    try:
        await progress_message.edit_text(
            f"Прогресс: {progress_bar} {percent:.1f}%\n"
            f"Обработано: {current}/{total_users}\n"
            f"Успешно: {success}\n"
            f"Активные задачи: {semaphore_value}\n"
            f"Скорость: {current_speed:.1f} сообщ/сек ({current_speed*60:.1f} сообщ/мин)\n"
            f"Средняя скорость: {avg_speed:.1f} сообщ/сек ({avg_speed*60:.1f} сообщ/мин)"
        )
    except Exception as e:
        logging.error(f"Ошибка обновления прогресса: {e}")


async def broadcast(
    bot: Bot,
    start_msg: types.Message,
    users: List[Tuple[int]],
    text: str,
    photo_file_id: str = None,
    keyboard=None,
    max_concurrent: int = 25
):
    total_users = len(users)
    if not total_users:
        await start_msg.reply("<b>❌ Нет пользователей для рассылки.</b>", parse_mode="HTML")
        return

    progress_message = await start_msg.reply(
        "<b>📢 Статус рассылки:</b>\n\n"
        "Прогресс: <code>🟩⬜⬜⬜⬜⬜⬜⬜⬜⬜</code> <b>0%</b>\n"
        "Обработано: <b>0</b>/<b>{}</b>\n"
        "✅ Успешно: <b>0</b>\n"
        "⚡ Активные задачи: <b>0</b>\n"
        "📊 Скорость: <b>0.0</b> сообщ/сек (<b>0.0</b> сообщ/мин)\n"
        "📉 Средняя скорость: <b>0.0</b> сообщ/сек (<b>0.0</b> сообщ/мин)".format(total_users),
        parse_mode="HTML"
    )

    semaphore = asyncio.Semaphore(max_concurrent)
    progress_lock = asyncio.Lock()
    
    processed = 0
    success = 0
    tasks = []

    start_time = time.time()
    message_timestamps = deque(maxlen=100)
    speed_stats = {
        "current_speed": 0.0,
        "avg_speed": 0.0, 
        "last_update": start_time  
    }

    def calculate_speed():
        now = time.time()

        if len(message_timestamps) >= 2:
            time_span = message_timestamps[-1] - message_timestamps[0]
            if time_span > 0:
                current_speed = (len(message_timestamps) - 1) / time_span
            else:
                current_speed = 0
        else:
            current_speed = 0

        elapsed = now - start_time
        if elapsed > 0 and processed > 0:
            avg_speed = processed / elapsed
        else:
            avg_speed = 0
            
        return {
            "current_speed": current_speed,
            "avg_speed": avg_speed,
            "last_update": now
        }

    async def process_user(user_id):
        nonlocal processed, success
        
        async with semaphore:
            result = await send_message_with_retry(
                bot, user_id, text, "HTML", keyboard, photo_file_id
            )

            async with progress_lock:
                processed += 1
                if result:
                    success += 1
                
                message_timestamps.append(time.time())
                
                now = time.time()
                if (now - speed_stats["last_update"] > 2 or processed % 50 == 0):
                    speed_stats.update(calculate_speed())

                progress_percentage = processed / total_users * 100
                progress_blocks = int(progress_percentage // 10)
                progress_bar = "🟩" * progress_blocks + "⬜" * (10 - progress_blocks)

                if processed % max(1, total_users//20) == 0 or processed == total_users:
                    active_tasks = len(tasks) - sum(task.done() for task in tasks)
                    await progress_message.edit_text(
                        "<b>📢 Статус рассылки:</b>\n\n"
                        f"<b>📍 Прогресс:</b> <code>{progress_bar}</code> <b>{progress_percentage:.1f}%</b>\n"
                        f"<b>📌 Обработано:</b> <b>{processed}</b>/<b>{total_users}</b>\n"
                        f"<blockquote>✅ Успешно: <b>{success}</b>\n"
                        f"⚡ Активные задачи: <b>{active_tasks}</b>\n"
                        f"📊 Скорость: <b>{speed_stats['current_speed']:.1f}</b> сообщ/сек "
                        f"(<b>{speed_stats['current_speed']*60:.1f}</b> сообщ/мин)\n"
                        f"📉 Средняя скорость: <b>{speed_stats['avg_speed']:.1f}</b> сообщ/сек "
                        f"(<b>{speed_stats['avg_speed']*60:.1f}</b> сообщ/мин)</blockquote>",
                        parse_mode="HTML"
                    )

    for user_id in users:
        user_id = int(user_id)
        task = asyncio.create_task(process_user(user_id))
        tasks.append(task)

    await asyncio.gather(*tasks)

    elapsed_time = time.time() - start_time
    final_speed = processed / elapsed_time if elapsed_time > 0 else 0
    
    await progress_message.edit_text(
        "<b>✅ Рассылка завершена!</b>\n\n"
        f"📨 Успешно отправлено: <b>{success}</b>/<b>{total_users}</b> "
        f"(<b>{success/total_users*100:.1f}%</b>)\n"
        f"⏳ Время выполнения: <b>{elapsed_time:.1f}</b> сек\n"
        f"🚀 Средняя скорость: <b>{final_speed:.1f}</b> сообщ/сек "
        f"(<b>{final_speed*60:.1f}</b> сообщ/мин)",
        parse_mode="HTML"
    )

    logging.info(
        f"Рассылка завершена. Отправлено {success}/{total_users} сообщений за {elapsed_time:.1f} сек. "
        f"Средняя скорость: {final_speed:.1f} сообщ/сек"
    )



@router.message(AdminState.MAILING)
async def mailing_handler(message: types.Message, state: FSMContext):
    if message.photo:
        text = message.caption or ""
        entities = message.caption_entities or []
        photo_file_id = message.photo[-1].file_id
    else:
        text = message.text or ""
        entities = message.entities or []
        photo_file_id = None
    users = get_users_ids()

    buttons = re.findall(r"\{([^{}]+)\}:([^{}]+)", text)
    keyboard = None
    if buttons:
        kb = InlineKeyboardBuilder()
        for btn_text, btn_url in buttons:
            kb.button(text=btn_text.strip(), url=btn_url.strip())
        kb.adjust(1)
        keyboard = kb.as_markup()
        text = re.sub(r"\{[^{}]+\}:([^{}]+)", "", text).strip()

    formatted_text = apply_html_formatting(text, entities)

    logging.info(f"Начало рассылки для {len(users)} пользователей")
    
    await broadcast(
        message.bot, message, users, formatted_text, photo_file_id, keyboard
    )
    await state.clear()


def apply_html_formatting(text, entities):
    return html_detect.unparse(text, entities)


@router.callback_query(F.data == "profile")
async def profile_callback(call: CallbackQuery, bot: Bot):
    user_id = call.from_user.id
    user = call.from_user
    banned = get_banned_user(user_id)
    if banned == 1:
        await bot.answer_callback_query(call.id, "🚫 Вы заблокированы в боте!", show_alert=True)
        return

    if subgram_status[0]:
        response = await request_op(
                user_id=user_id,
                chat_id=call.message.chat.id,
                first_name=user.first_name,
                language_code=user.language_code,
                bot=bot,
                ref_id=None,
                is_premium=getattr(user, 'is_premium', None)
            )

        if response != 'ok':
            return
    if flyer_status[0] and not await flyer_check(key=FLYER_TOKEN, user_id=user_id, lang_code=user.language_code):
        return
    try:
        await bot.delete_message(chat_id=call.from_user.id, message_id=call.message.message_id)
    except Exception as e:
        print(f"Ошибка при удалении сообщения: {e}")

    builder_profile = InlineKeyboardBuilder()
    builder_profile.button(text="🎫 Промокод", callback_data="promocode")
    builder_profile.button(text="⬅️ В главное меню", callback_data="back_main")
    markup_profile = builder_profile.adjust(1).as_markup()

    nickname = call.from_user.first_name
    balance = get_balance_user(user_id)
    count_photos = get_photo_sell_count(user_id)
    ref_count = get_referral_count(user_id)
    withdrawed = get_withdrawed(user_id)

    await bot.send_photo(
        chat_id=user_id,
        photo=FSInputFile("photo/profile.png"),
        caption=(
            "<b>✨ Профиль\n"
            "──────────────\n"
            f"👤 Имя: {nickname}\n"
            f"🆔 ID: <code>{user_id}</code>\n"
            "──────────────\n"
            f"💰 Баланс:</b> {balance:.2f} ⭐️\n"
            f"<b>📸 Продано фото:</b> {count_photos}\n"
            "<b>──────────────</b>\n"
            f"👥 <b>Рефералов:</b> {ref_count}\n"
            f"💸 <b>Выведено:</b> {int(withdrawed)} ⭐️\n"
            "<b>──────────────</b>\n"
            "⬇️ <i>Используй кнопки ниже для действий.</i>"
        ),
        parse_mode='HTML',
        reply_markup=markup_profile
    )

@router.callback_query(F.data == "top")
async def top_callback(call: CallbackQuery, bot: Bot):
    user_id = call.from_user.id
    user = call.from_user
    banned = get_banned_user(user_id)
    if banned == 1:
        await bot.answer_callback_query(call.id, "🚫 Вы заблокированы в боте!", show_alert=True)
        return
    
    if subgram_status[0]:
        response = await request_op(
                user_id=user_id,
                chat_id=call.message.chat.id,
                first_name=user.first_name,
                language_code=user.language_code,
                bot=bot,
                ref_id=None,
                is_premium=getattr(user, 'is_premium', None)
            )

        if response != 'ok':
            return
    if flyer_status[0] and not await flyer_check(key=FLYER_TOKEN, user_id=user_id, lang_code=user.language_code):
        return
    try:
        await bot.delete_message(chat_id=call.from_user.id, message_id=call.message.message_id)
    except Exception as e:
        print(f"Ошибка при удалении сообщения: {e}")

    builder_top = InlineKeyboardBuilder()
    builder_top.button(text="🏆 Топ за неделю", callback_data="top_week")
    builder_top.button(text="⬅️ В главное меню", callback_data="back_main")
    markup_top = builder_top.adjust(1).as_markup()

    top_list = get_today_withdraw_top()

    text = "<b>✨ Топ выводов за день\n"

    for i, (username, count) in enumerate(top_list):
        text += f"{i + 1}. @{username} - {count} ⭐\n"

    text += "</b>\n⬇️ <i>Используй кнопки ниже для действий.</i>"

    await bot.send_photo(
        chat_id=user_id,
        photo=FSInputFile("photo/top.png"),
        caption=text,
        parse_mode='HTML',
        reply_markup=markup_top
    )

@router.callback_query(F.data == "top_week")
async def top_week_callback(call: CallbackQuery, bot: Bot):
    user_id = call.from_user.id
    user = call.from_user
    banned = get_banned_user(user_id)
    if banned == 1:
        await bot.answer_callback_query(call.id, "🚫 Вы заблокированы в боте!", show_alert=True)
        return
    
    if subgram_status[0]:
        response = await request_op(
                user_id=user_id,
                chat_id=call.message.chat.id,
                first_name=user.first_name,
                language_code=user.language_code,
                bot=bot,
                ref_id=None,
                is_premium=getattr(user, 'is_premium', None)
            )

        if response != 'ok':
            return
    if flyer_status[0] and not await flyer_check(key=FLYER_TOKEN, user_id=user_id, lang_code=user.language_code):
        return
    try:
        await bot.delete_message(chat_id=call.from_user.id, message_id=call.message.message_id)
    except Exception as e:
        print(f"Ошибка при удалении сообщения: {e}")
    
    top_list = get_week_withdraw_top()

    text = "<b>✨ Топ выводов за неделю\n"

    for i, (username, count) in enumerate(top_list):
        text += f"{i + 1}. @{username} - {count} ⭐\n"

    text += "</b>\n⬇️ <i>Используй кнопки ниже для действий.</i>"

    builder_top = InlineKeyboardBuilder()
    builder_top.button(text="🏆 Топ за день", callback_data="top")
    builder_top.button(text="⬅️ В главное меню", callback_data="back_main")
    markup_top = builder_top.adjust(1).as_markup()

    await bot.send_photo(
        chat_id=user_id,
        photo=FSInputFile("photo/top.png"),
        caption=text,
        parse_mode='HTML',
        reply_markup=markup_top
    )

@router.callback_query(F.data == "promocode")
async def promocode_callback_query(call: CallbackQuery, bot: Bot, state: FSMContext):
    user_id = call.from_user.id
    banned = get_banned_user(user_id)
    if banned == 1:
        await bot.answer_callback_query(call.id, "🚫 Вы заблокированы в боте!", show_alert=True)
        return
    await bot.delete_message(call.from_user.id, call.message.message_id)
    input_photo_promo = FSInputFile("photo/promocode.png")
    await bot.send_photo(call.from_user.id, photo=input_photo_promo, caption=f"✨ Для получения звезд на ваш баланс введите промокод:\n*<i>Найти промокоды можно в <a href='{channel_osn}'>канале</a> и <a href='{chater}'>чате</a></i>", parse_mode='HTML')
    await state.set_state(AdminState.PROMOCODE_INPUT)

@router.message(AdminState.PROMOCODE_INPUT)
async def promocode_handler(message: Message, state: FSMContext, bot: Bot):
    user_id = message.from_user.id
    markup_back_inline = InlineKeyboardBuilder()
    markup_back_inline.button(text="⬅️ В главное меню", callback_data="back_main")
    markup_back = markup_back_inline.as_markup()

    promocode_text = message.text
    try:
        success, result = use_promocode(promocode_text, message.from_user.id)
        if success:
            await message.reply(f"<b>✅ Промокод успешно активирован!\nВам начислено {result} ⭐️</b>", parse_mode='HTML', reply_markup=markup_back)
            await send_main_menu(user_id, bot, message)
        else:
            await message.reply(f"<b>❌ Ошибка: {result}</b>", parse_mode='HTML')
            await send_main_menu(user_id, bot, message)
    except Exception as e:
        print(f"Ошибка при активации промокода: {e}")
        await message.reply("<b>❌ Произошла ошибка при активации промокода.</b>", parse_mode='HTML')
        await send_main_menu(user_id, bot, message)
    finally:
        await state.clear()

@router.callback_query(F.data == "games")
async def games_callback_query(call: CallbackQuery, bot: Bot):
    user_id = call.from_user.id
    banned = get_banned_user(user_id)
    if banned == 1:
        await bot.answer_callback_query(call.id, "🚫 Вы заблокированы в боте!", show_alert=True)
        return
    try:
        await bot.delete_message(call.from_user.id, call.message.message_id)
    except Exception as e:
        print(f"Ошибка при удалении сообщения: {e}")

    await send_games_menu(user_id, bot)

@router.callback_query(F.data == "slots_game")
async def slots_game(call: CallbackQuery, bot: Bot):
    user_id = call.from_user.id
    banned = get_banned_user(user_id)

    if banned == 1:
        await bot.answer_callback_query(call.id, "🚫 Вы заблокированы в боте!", show_alert=True)
        return
    
    try:
        await bot.delete_message(call.from_user.id, call.message.message_id)
    except Exception as e:
        print(f"Ошибка при удалении сообщения: {e}")

    await send_slots_menu(user_id, bot)

@router.callback_query(F.data == "slots_info")
async def slots_info(call: CallbackQuery, bot: Bot):
    user_id = call.from_user.id
    banned = get_banned_user(user_id)

    if banned == 1:
        await bot.answer_callback_query(call.id, "🚫 Вы заблокированы в боте!", show_alert=True)
        return
    
    builder = InlineKeyboardBuilder()

    builder.button(text="⬅️ Назад", callback_data="slots_game")

    markup = builder.adjust(1).as_markup()
    
    try:
        await bot.edit_message_caption(
            chat_id=user_id,
            message_id=call.message.message_id,
            caption = (
                "ℹ️ Вы выбрали игру: 🎰 <b>Слоты</b>\n\n"
                "🎯 Правила игры:\n"
                "<blockquote>"
                "<b>Крути слоты</b> и получай ⭐️ за удачные комбинации!\n\n"
                "💥 Выпало 3 одинаковых — <b>получаешь звёзды</b>\n"
                "🎁 Чем редче символы — тем <b>больше награда</b>\n"
                "😢 Без совпадений — без приза</blockquote>\n\n"
                f"💸 Стоимость прокрута: <b>{mini_games[0]} ⭐️</b>\n"
                "<i>Готов испытать удачу? Крути и выигрывай!</i>"
            ),
            parse_mode='HTML',
            reply_markup=markup
        )
    except Exception as e:
        print(f"Ошибка при удалении сообщения: {e}")

def get_slot_reward(value: int) -> int:
    config = reward_games.get("slots", {})
    reward_table = config.get("reward_table", {})
    return reward_table.get(value, config.get("default_reward", 0))

def get_combo_text(dice_value: int) -> list[str]:
    symbols = ["BAR", "🍇", "🍋", "7️⃣"]
    dice_value -= 1
    result = []

    for _ in range(3):
        result.append(symbols[dice_value % 4])
        dice_value //= 4

    return result[::-1]


@router.callback_query(F.data == "spin_slot")
async def spin_slot(call: CallbackQuery, bot: Bot):
    user_id = call.from_user.id
    banned = get_banned_user(user_id)

    if not mini_games:
        await bot.answer_callback_query(call.id, "🚫 Ошибка: настройки мини-игры не найдены!", show_alert=True)
        return
    
    stars_to_play = mini_games[0]


    if banned == 1:
        await bot.answer_callback_query(call.id, "🚫 Вы заблокированы в боте!", show_alert=True)
        return
    
    if get_balance_user(user_id) < stars_to_play:
        await bot.answer_callback_query(call.id, "🚫 У вас недостаточно звёзд!", show_alert=True)
        return
    
    try:
        await bot.delete_message(call.from_user.id, call.message.message_id)
    except Exception as e:
        print(f"Ошибка при удалении сообщения: {e}")

    dice_msg: Message = await bot.send_dice(call.from_user.id, emoji="🎰")

    value = dice_msg.dice.value
    # print("[debug] Выпало: ", value)
    slot_text = slot_text = " | ".join(get_combo_text(value))
    reward = get_slot_reward(value)
    # print("[debug] Награда: ", reward)

    await asyncio.sleep(2)
    builder = InlineKeyboardBuilder()

    buttons = [
        ('⬅️ Назад', 'slots_game')
    ]

    for text, callback_data in buttons:
        builder.button(text=text, callback_data=callback_data)

    markup = builder.adjust(1).as_markup()

    remove_stars(user_id, stars_to_play)


    if reward > 0:
        log_slot_play(user_id, stars_to_play, reward, value, slot_text, "Выиграл")
        await bot.send_message(
            user_id,
            f'🎁 <b>Вы выиграли!</b>\n\n<i>Ваш выигрыш составил: <span class="tg-spoiler">{reward} ⭐️</span></i>',
            parse_mode='HTML',
            reply_markup=markup
        )
        add_stars(user_id, reward)
    else:
        log_slot_play(user_id, stars_to_play, 0, value, slot_text, "Проиграл")
        await bot.send_message(
            user_id,
            "<b>😢 К сожалению, вы проиграли.</b>\n\n<i>Попробуйте ещё раз!</i>",
            parse_mode='HTML',
            reply_markup=markup
        )


async def send_slots_menu(user_id: int, bot: Bot):
    builder = InlineKeyboardBuilder()

    buttons = [
        ('🔄 Крутить слот', 'spin_slot'),
        ('ℹ️ Информация', 'slots_info'),
        ('⬅️ В главное меню', 'back_main')
    ]

    for text, callback_data in buttons:
        builder.button(text=text, callback_data=callback_data)

    markup = builder.adjust(2, 1).as_markup()

    user_balance = get_balance_user(user_id)
    await bot.send_photo(
        chat_id=user_id,
        photo=FSInputFile("photo/slots.png"),
        caption=f"ℹ️ Вы выбрали игру: 🎰 <b>Слоты</b>\n\n💸 Текущий баланс: <b>{user_balance}</b> ⭐️",
        parse_mode='HTML',
        reply_markup=markup
    )

async def send_games_menu(user_id, bot):
    builder = InlineKeyboardBuilder()

    buttons = [
        ('🎰 Слоты', 'slots_game')
    ]

    for text, callback_data in buttons:
        builder.button(text=text, callback_data=callback_data)

    builder.button(text="⬅️ В главное меню", callback_data="back_main")

    markup = builder.adjust(1).as_markup()

    await bot.send_photo(
        chat_id=user_id,
        photo=FSInputFile("photo/games.png"),
        caption="<b>✨ Выберите игру</b>\n\n<i>Доступные на данный момент мини-игры:</i>\n1️⃣ <b>Слоты</b> - крути слоты и выигрывай!\n\n<i>В ближайшее время мы добавим ещё игры!</i>",
        parse_mode='HTML',
        reply_markup=markup
    )
    

async def send_main_menu(
    user_id: int,
    bot: Bot,
    call: CallbackQuery | Message | None = None
) -> None:
    if call is not None:
        try:
            chat_id = call.from_user.id
            message_id = call.message.message_id if isinstance(call, CallbackQuery) else call.message_id
            await bot.delete_message(chat_id=chat_id, message_id=message_id - 1)
            await bot.delete_message(chat_id=chat_id, message_id=message_id)
            await bot.delete_message(chat_id=chat_id, message_id=message_id + 1)
        except Exception as e:
            print(f"Ошибка при удалении сообщения: {e}")
    builder = InlineKeyboardBuilder()
    buttons = [
        ('🎰 Мини-Игры', 'games'),
        ('🌟 Купить / Продать фото', 'photo_selling'),
        ('👤 Профиль', 'profile'),
        ('🔄 Вывести звезды', 'stars_withdraw'),
        ('🔗 Получить ссылку', 'get_ref'),
        ('🏆 Топ', 'top')
    ]
    for text, callback_data in buttons:
        builder.button(text=text, callback_data=callback_data)

    builder.button(text="📕 Помощь", url=admin_url)

    builder.adjust(1, 1, 2, 2, 1)
    markup = builder.as_markup()

    sell_count = get_total_photo_selling_count()
    withdrawn_count = get_total_withdrawn()

    await bot.send_photo(
        chat_id=user_id,
        photo=FSInputFile("photo/start.png"),
        caption=(
            "<b>✨ Добро пожаловать в Главное Меню! ✨</b>\n\n"
            f"📸 <b>Всего продано фото:</b> <code>{sell_count}</code>\n"
            f"💰 <b>Всего выведено:</b> <code>{withdrawn_count}</code>\n\n"
            "<b>❔ Как заработать звёзды?</b>\n"
            "<blockquote>"
            "🔹 <i>Продавайте свои фото</i> — за продажу вы получаете звёзды.\n"
            "🔹 <i>Играйте в мини-играх</i> — вы можете выиграть или проиграть звёзды\n"
            "🔹 <i>Приглашайте друзей</i> — делитесь реферальной ссылкой и получайте бонусы.\n"
            "</blockquote>\n"
            "📲 <i>Чтобы посмотреть свой профиль и получить ссылку для приглашений, нажмите кнопку ниже.</i>"
        ),
        parse_mode='HTML',
        reply_markup=markup
    )

@router.callback_query(F.data == "get_ref")
async def get_url_callback(call: CallbackQuery, bot: Bot):

    user_id = call.from_user.id
    user = call.from_user
    banned = get_banned_user(user_id)
    if banned == 1:
        await bot.answer_callback_query(call.id, "🚫 Вы заблокированы в боте!", show_alert=True)
        return

    if subgram_status[0]:
        response = await request_op(
                user_id=user_id,
                chat_id=call.message.chat.id,
                first_name=user.first_name,
                language_code=user.language_code,
                bot=bot,
                ref_id=None,
                is_premium=getattr(user, 'is_premium', None)
            )

        if response != 'ok':
            return
    if flyer_status[0] and not await flyer_check(key=FLYER_TOKEN, user_id=user_id, lang_code=user.language_code):
        return

    try:
        await bot.delete_message(chat_id=call.from_user.id, message_id=call.message.message_id)
    except Exception as e:
        print(f"Ошибка при удалении сообщения: {e}")

    url = await create_url_referral(bot, user_id)
    markup_back_inline = InlineKeyboardBuilder()
    markup_back_inline.button(text="📤 Поделиться ссылкой", url=f"https://t.me/share/url?url={url}")
    markup_back_inline.button(text="⬅️ В главное меню", callback_data="back_main")
    markup_back = markup_back_inline.adjust(1,1).as_markup()

    await bot.send_photo(
        chat_id=user_id,
        photo=FSInputFile('photo/ref_url.png'),
        caption=f"<b>🔗 Ваша реферальная ссылка:</b>\n<code>{url}</code>",
        parse_mode='HTML',
        reply_markup=markup_back
    )

@router.callback_query(F.data == "back_main")
async def back_main(call: CallbackQuery, bot: Bot):
    user = call.from_user
    if subgram_status[0]:
        response = await request_op(
                user_id=call.from_user.id,
                chat_id=call.message.chat.id,
                first_name=user.first_name,
                language_code=user.language_code,
                bot=bot,
                ref_id=None,
                is_premium=getattr(user, 'is_premium', None)
            )

        if response != 'ok':
            return
    if flyer_status[0] and not await flyer_check(key=FLYER_TOKEN, user_id=user.id, lang_code=user.language_code):
        return
    try:
        await bot.delete_message(chat_id=call.from_user.id, message_id=call.message.message_id)
    except Exception as e:
        print(f"Ошибка при удалении сообщения: {e}")
    builder_start = InlineKeyboardBuilder()
    buttons = [
            ('🎰 Мини-Игры', 'games'),
            ('🌟 Купить / Продать фото', 'photo_selling'),
            ('👤 Профиль', 'profile'),
            ('🔄 Вывести звезды', 'stars_withdraw'),
            ('🔗 Получить ссылку', 'get_ref'),
            ('🏆 Топ', 'top')
        ]

    for text, callback_data in buttons:
        builder_start.button(text=text, callback_data=callback_data)

    builder_start.button(text="📕 Помощь", url=admin_url)

    builder_start.adjust(1, 1, 2, 2, 1)
    markup = builder_start.as_markup()
    user_id = call.from_user.id
    sell_count = get_total_photo_selling_count()
    withdrawn_count = get_total_withdrawn()
    await bot.send_photo(
            chat_id=user_id,
            photo=FSInputFile("photo/start.png"),
            caption=(
                "<b>✨ Добро пожаловать в Главное Меню! ✨</b>\n\n"
                f"📸 <b>Всего продано фото:</b> <code>{sell_count}</code>\n"
                f"💰 <b>Всего выведено:</b> <code>{withdrawn_count}</code>\n\n"
                "<b>❔ Как заработать звёзды?</b>\n"
                "<blockquote>"
                "🔹 <i>Продавайте свои фото</i> — за продажу вы получаете звёзды.\n"
                "🔹 <i>Играйте в мини-играх</i> — вы можете выиграть или проиграть звёзды\n"
                "🔹 <i>Приглашайте друзей</i> — делитесь реферальной ссылкой и получайте бонусы.\n"
                "</blockquote>\n"
                "📲 <i>Чтобы посмотреть свой профиль и получить ссылку для приглашений, нажмите кнопку ниже.</i>"
            ),
            parse_mode='HTML',
            reply_markup=markup
        )

@router.callback_query(F.data.startswith("check_subs"))
async def check_subs_callback(call: CallbackQuery, bot: Bot):
    user = call.from_user
    user_id = call.from_user.id
    refferal_id = None
    try:
        refferal_id = int(call.data.split(":")[1])
    except IndexError:
        pass
    banned = get_banned_user(user_id)
    if banned == 1:
        await bot.answer_callback_query(call.id, "🚫 Вы заблокированы в боте!", show_alert=True)
        return

    try:
        await bot.delete_message(chat_id=call.from_user.id, message_id=call.message.message_id)
    except Exception as e:
        print(f"Ошибка при удалении сообщения: {e}")
    
    channels = get_channels_ids()
    

    if await check_subscription(user_id, channels, bot, refferal_id):
        if not user_exists(user_id):
            if refferal_id and user_exists(refferal_id):
                add_user(user_id, user.first_name, refferal_id)
                await handle_referral_bonus(refferal_id, user_id, bot)
            else:
                add_user(user_id, user.first_name, None)
            
        sell_count = get_total_photo_selling_count()
        withdrawn_count = get_total_withdrawn()
        builder_start = InlineKeyboardBuilder()
        buttons = [
            ('🎰 Мини-Игры', 'games'),
            ('🌟 Купить / Продать фото', 'photo_selling'),
            ('👤 Профиль', 'profile'),
            ('🔄 Вывести звезды', 'stars_withdraw'),
            ('🔗 Получить ссылку', 'get_ref'),
            ('🏆 Топ', 'top')
        ]

        for text, callback_data in buttons:
            builder_start.button(text=text, callback_data=callback_data)

        builder_start.button(text="📕 Помощь", url=admin_url)

        builder_start.adjust(1, 1, 2, 2, 1)
        markup = builder_start.as_markup()
        await bot.send_message(user_id, "⭐")
        await bot.send_photo(
            chat_id=user_id,
            photo=FSInputFile("photo/start.png"),
            caption=(
                "<b>✨ Добро пожаловать в Главное Меню! ✨</b>\n\n"
                f"📸 <b>Всего продано фото:</b> <code>{sell_count}</code>\n"
                f"💰 <b>Всего выведено:</b> <code>{withdrawn_count}</code>\n\n"
                "<b>❔ Как заработать звёзды?</b>\n"
                "<blockquote>"
                "🔹 <i>Продавайте свои фото</i> — за продажу вы получаете звёзды.\n"
                "🔹 <i>Играйте в мини-играх</i> — вы можете выиграть или проиграть звёзды\n"
                "🔹 <i>Приглашайте друзей</i> — делитесь реферальной ссылкой и получайте бонусы.\n"
                "</blockquote>\n"
                "📲 <i>Чтобы посмотреть свой профиль и получить ссылку для приглашений, нажмите кнопку ниже.</i>"
            ),
            parse_mode='HTML',
            reply_markup=markup
        )
    else:
        await bot.answer_callback_query(call.id, "❌ Подписка не найдена")

async def check_subscription(user_id: int, channel_ids: list, bot: Bot, referral_id: str = None) -> bool:
    if not channel_ids:
        return True

    builder = InlineKeyboardBuilder()

    for channel_id in channel_ids:
        try:
            chat_member = await bot.get_chat_member(chat_id=channel_id, user_id=user_id)
            if chat_member.status not in ['member', 'administrator', 'creator']:
                invite_link = get_channel(channel_id)
                subscribe_button = InlineKeyboardButton(text="Подписаться", url=invite_link['link_invite'])
                builder.add(subscribe_button)
        except Exception as e:
            print(f"Ошибка при проверке подписки: {e}")
            await bot.send_message(user_id, "Ошибка при проверке подписки. Пожалуйста, попробуйте позже.")
            return False

    if builder.export():
        markup: InlineKeyboardMarkup = builder.as_markup()
        check_data = f"check_subs:{referral_id}" if referral_id else "check_subs"
        check_button = InlineKeyboardButton(text="✅ Проверить подписку", callback_data=check_data)
        markup.inline_keyboard.append([check_button])

        await bot.send_photo(
            chat_id=user_id,
            photo=FSInputFile('photo/check_sub.png'),
            caption="<b>👋🏻 Добро пожаловать\n\nПодпишитесь на каналы, чтобы продолжить!</b>",
            parse_mode='HTML',
            reply_markup=markup
        )
        return False

    return True

async def flyer_check(key: str, user_id: int, lang_code: str):
    if user_id in admins_id:
        return True
    
    async with aiohttp.ClientSession() as session:
        payload = {
            "key": key,
            "user_id": user_id,
            "language_code": lang_code,
            "message": {
                "text": "<b>Для продолжения использования бота подпишись на следующие каналы наших спонсоров</b>\n\n<blockquote><b>💜Спасибо за то что вы выбрали НАС</b></blockquote>",
                "button_bot": "Запустить",
                "button_channel": "Подписаться",
                "button_url": "Перейти",
                "button_boost": "Забустить",
                "button_fp": "Выполнить"
            }
        }

        async with session.post("https://api.flyerservice.io/check", json=payload) as response:

            if response.status != 200:
                print(f"Flyer API error: status {response.status}")
                return False
            
            data = await response.json()

            if data.get("skip"):
                return True
            else:
                return False

    

async def handle_referral_bonus(ref_id: int, user_id: int, bot: Bot):
    try:
        add_stars(ref_id, stars_reffer[0])
        markup_back_inline = InlineKeyboardBuilder()
        markup_back_inline.button(text="📤 Поделиться ссылкой", url=f"https://t.me/share/url?url={(await create_url_referral(bot, ref_id))}")
        markup_back = markup_back_inline.as_markup()
        await bot.send_message(ref_id, f"🎉 Пользователь <code>{user_id}</code> зарегистрировался по вашей реферальной ссылке!\n\nВы получили <code>{stars_reffer[0]}</code>⭐️ за реферала!\n\n<b>Ссылка для приглашения:</b> \n<code>{(await create_url_referral(bot, ref_id))}</code>", parse_mode='HTML', reply_markup=markup_back)
    except Exception as e:
        print(f"Referral bonus error: {e}")

async def create_url_referral(bot: Bot, user_id: int):
    return f"https://t.me/{(await bot.me()).username}?start={user_id}"

async def safe_edit_message(bot, chat_id, message_id, new_text, reply_markup=None):
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=new_text,
            parse_mode='HTML',
            disable_web_page_preview=True,
            reply_markup=reply_markup
        )
    except TelegramBadRequest as e:
        print("error")
        if "message is not modified" not in str(e):
            raise

async def main():
    bot = Bot(token=TOKEN)
    dp = Dispatcher()
    dp.message.middleware(AntiFloodMiddleware(limit=1))
    dp.callback_query.middleware(AntiFloodMiddleware(limit=1))
    dp.include_router(router)
    await dp.start_polling(bot)

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("*" * 100)
        print("Бот остановлен. Спасибо за использование. Developed by @kalipsom | @meroqty")
        print("*" * 100)