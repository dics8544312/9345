"""
Обработчик команд для администраторов
"""

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime, timedelta

from services import AccessService, UserService, StatisticsService, manual_backup
from keyboards import get_admin_menu, get_codes_admin_menu, get_users_admin_menu, get_cancel_keyboard_inline
from models import User
from config import settings

router = Router(name="admin")


class AdminStates(StatesGroup):
    """Состояния администратора"""
    waiting_code_name = State()
    waiting_code_duration = State()
    waiting_code_to_delete = State()
    waiting_backup_file = State()  # Ожидание файла бэкапа


def is_admin(user_id: int) -> bool:
    """Проверка прав администратора"""
    return user_id in settings.admin_ids_list


@router.callback_query(F.data == "admin_panel")
async def admin_panel_callback(callback: CallbackQuery):
    """Главная админ-панель через callback"""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ У вас нет доступа к админ-панели", show_alert=True)
        return
    
    await callback.message.edit_text(
        "🔧 Админ-панель\n\n"
        "Выберите раздел:",
        reply_markup=get_admin_menu()
    )
    await callback.answer()


@router.message(F.text == "🔧 Админ-панель")
@router.message(Command("admin"))
async def admin_panel(message: Message):
    """Главная админ-панель"""
    if not is_admin(message.from_user.id):
        await message.answer("❌ У вас нет доступа к админ-панели")
        return
    
    await message.answer(
        "🔧 Админ-панель\n\n"
        "Выберите раздел:",
        reply_markup=get_admin_menu()
    )


@router.callback_query(F.data == "admin_menu")
async def show_admin_menu(callback: CallbackQuery):
    """Показать главное меню админки"""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    await callback.message.edit_text(
        "🔧 Админ-панель\n\n"
        "Выберите раздел:",
        reply_markup=get_admin_menu()
    )
    await callback.answer()


# ===== УПРАВЛЕНИЕ КОДАМИ ДОСТУПА =====

@router.callback_query(F.data == "admin_codes")
async def codes_menu(callback: CallbackQuery):
    """Меню управления кодами"""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    await callback.message.edit_text(
        "🔑 Управление кодами доступа\n\n"
        "Выберите действие:",
        reply_markup=get_codes_admin_menu()
    )
    await callback.answer()


@router.callback_query(F.data == "code_create")
async def start_code_creation(callback: CallbackQuery, state: FSMContext):
    """Начало создания кода"""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    await callback.message.answer(
        "➕ Создание нового кода\n\n"
        "Введите название кода (например: MATH2026, PROMO30):",
        reply_markup=get_cancel_keyboard_inline()
    )
    await state.set_state(AdminStates.waiting_code_name)
    await callback.answer()


@router.message(AdminStates.waiting_code_name, F.text != "❌ Отмена")
async def get_code_name(message: Message, state: FSMContext):
    """Получение названия кода"""
    if not is_admin(message.from_user.id):
        return
    
    code_name = message.text.strip().upper()
    
    # Проверяем длину кода
    if len(code_name) < 4 or len(code_name) > 20:
        await message.answer(
            "❌ Код должен быть от 4 до 20 символов.\n\n"
            "Попробуйте еще раз:",
            reply_markup=get_cancel_keyboard_inline()
        )
        return
    
    await state.update_data(code_name=code_name)
    await message.answer(
        f"Код: {code_name}\n\n"
        f"Теперь введите срок действия в днях (например: 30, 90, 365):",
        reply_markup=get_cancel_keyboard_inline()
    )
    await state.set_state(AdminStates.waiting_code_duration)


@router.message(AdminStates.waiting_code_duration, F.text != "❌ Отмена")
async def create_code(message: Message, session: AsyncSession, state: FSMContext):
    """Создание кода"""
    if not is_admin(message.from_user.id):
        return
    
    try:
        duration = int(message.text.strip())
        if duration <= 0 or duration > 3650:
            raise ValueError()
    except ValueError:
        await message.answer(
            "❌ Введите корректное количество дней (от 1 до 3650):",
            reply_markup=get_cancel_keyboard_inline()
        )
        return
    
    data = await state.get_data()
    code_name = data.get("code_name")
    
    # Проверяем существование кода
    existing_code = await AccessService.get_code(session, code_name)
    if existing_code:
        await message.answer(
            f"❌ Код {code_name} уже существует.\n\n"
            f"Введите другое название:",
            reply_markup=get_cancel_keyboard_inline()
        )
        await state.set_state(AdminStates.waiting_code_name)
        return
    
    # Создаем код
    code = await AccessService.create_access_code(
        session,
        code_name,
        duration,
        message.from_user.id
    )
    await session.commit()
    
    await message.answer(
        f"✅ Код успешно создан!\n\n"
        f"🔑 Код: {code.code}\n"
        f"📅 Срок действия: {code.duration_days} дней\n\n"
        f"Отправьте этот код пользователю для активации."
    )
    
    await state.clear()


@router.callback_query(F.data == "code_list_active")
async def list_active_codes(callback: CallbackQuery, session: AsyncSession):
    """Список активных кодов - БЫСТРО"""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    # МГНОВЕННЫЙ ОТВЕТ
    await callback.answer()
    
    try:
        msg = await callback.message.edit_text("⏳ Загрузка...")
        
        codes = await AccessService.get_active_codes(session)
        
        if not codes:
            await msg.edit_text(
                "❌ Нет активных кодов",
                reply_markup=get_codes_admin_menu()
            )
            return
        
        response = "🔑 Активные коды:\n\n"
        for code in codes[:10]:  # Только 10 для скорости
            response += f"• {code.code}\n"
            response += f"  {code.duration_days} дней\n\n"
        
        if len(codes) > 10:
            response += f"... и еще {len(codes) - 10}\n"
        
        await msg.edit_text(response, reply_markup=get_codes_admin_menu())
    
    except Exception as e:
        await callback.message.edit_text(
            f"❌ Ошибка: {str(e)[:100]}",
            reply_markup=get_codes_admin_menu()
        )



@router.callback_query(F.data == "code_list_all")
async def list_all_codes(callback: CallbackQuery, session: AsyncSession):
    """Список всех кодов - БЫСТРО"""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    # МГНОВЕННЫЙ ОТВЕТ
    await callback.answer()
    
    try:
        msg = await callback.message.edit_text("⏳ Загрузка...")
        
        codes = await AccessService.get_all_codes(session)
        
        if not codes:
            await msg.edit_text(
                "❌ Нет кодов",
                reply_markup=get_codes_admin_menu()
            )
            return
        
        response = "📜 Все коды:\n\n"
        for code in codes[:10]:  # Только 10 для скорости
            status = "✅" if code.is_active else "❌"
            response += f"{status} {code.code}\n"
            response += f"  {code.duration_days} дней\n\n"
        
        if len(codes) > 10:
            response += f"... и еще {len(codes) - 10}\n"
        
        await msg.edit_text(response, reply_markup=get_codes_admin_menu())
    
    except Exception as e:
        await callback.message.edit_text(
            f"❌ Ошибка: {str(e)[:100]}",
            reply_markup=get_codes_admin_menu()
        )



# ===== УПРАВЛЕНИЕ ПОЛЬЗОВАТЕЛЯМИ =====

# Сколько пользователей показываем кнопками на одной странице
USERS_PAGE_SIZE = 8


def _user_button_label(user: User) -> str:
    """Текст кнопки пользователя: @username, иначе имя, иначе ID."""
    if user.username:
        label = f"@{user.username}"
    elif user.full_name:
        label = f"👤 {user.full_name}"
    else:
        label = f"👤 ID {user.telegram_id}"
    return label[:64]  # Telegram ограничивает текст кнопки 64 символами


def _users_list_markup(users, page: int, page_prefix: str):
    """Клавиатура: по кнопке на каждого пользователя текущей страницы + навигация."""
    kb = InlineKeyboardBuilder()
    total = len(users)
    start = page * USERS_PAGE_SIZE
    for user in users[start:start + USERS_PAGE_SIZE]:
        kb.row(InlineKeyboardButton(
            text=_user_button_label(user),
            callback_data=f"view_user_{user.telegram_id}"
        ))

    # Навигация по страницам
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️ Назад", callback_data=f"{page_prefix}_{page - 1}"))
    if start + USERS_PAGE_SIZE < total:
        nav.append(InlineKeyboardButton(text="Вперёд ▶️", callback_data=f"{page_prefix}_{page + 1}"))
    if nav:
        kb.row(*nav)

    kb.row(InlineKeyboardButton(text="◀️ В админ-панель", callback_data="admin_panel"))
    return kb.as_markup()


async def _show_users_list(callback: CallbackQuery, users, page: int, page_prefix: str,
                           title_emoji: str, title_word: str):
    """Показать страницу списка пользователей кнопками."""
    total = len(users)
    if total == 0:
        await callback.message.edit_text(
            f"{title_emoji} {title_word}: список пуст",
            reply_markup=get_users_admin_menu()
        )
        return

    total_pages = (total + USERS_PAGE_SIZE - 1) // USERS_PAGE_SIZE
    # Защита от выхода за границы (например, после удалений)
    page = max(0, min(page, total_pages - 1))

    text = (
        f"{title_emoji} <b>{title_word}</b> — всего {total}\n"
        f"Страница {page + 1}/{total_pages}\n\n"
        f"👇 Нажмите на пользователя, чтобы открыть карточку:"
    )
    await callback.message.edit_text(
        text,
        reply_markup=_users_list_markup(users, page, page_prefix),
        parse_mode="HTML"
    )


@router.callback_query(F.data == "admin_users")
async def users_menu(callback: CallbackQuery, session: AsyncSession):
    """Список всех пользователей — кнопками"""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return

    await callback.answer()

    result = await session.execute(
        select(User).order_by(User.created_at.desc())
    )
    users = result.scalars().all()
    await _show_users_list(callback, users, 0, "users_page", "👥", "Пользователи")


@router.callback_query(F.data.startswith("users_page_"))
async def users_page(callback: CallbackQuery, session: AsyncSession):
    """Переключение страниц списка всех пользователей"""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return

    await callback.answer()

    try:
        page = int(callback.data.rsplit("_", 1)[1])
    except (ValueError, IndexError):
        page = 0

    result = await session.execute(
        select(User).order_by(User.created_at.desc())
    )
    users = result.scalars().all()
    await _show_users_list(callback, users, page, "users_page", "👥", "Пользователи")


@router.callback_query(F.data == "users_students")
async def list_students(callback: CallbackQuery, session: AsyncSession):
    """Список учеников — кнопками"""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return

    await callback.answer()

    students = await UserService.get_all_students(session)
    students = sorted(students, key=lambda u: u.created_at or datetime.min, reverse=True)
    await _show_users_list(callback, students, 0, "students_page", "👨‍🎓", "Ученики")


@router.callback_query(F.data.startswith("students_page_"))
async def students_page(callback: CallbackQuery, session: AsyncSession):
    """Переключение страниц списка учеников"""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return

    await callback.answer()

    try:
        page = int(callback.data.rsplit("_", 1)[1])
    except (ValueError, IndexError):
        page = 0

    students = await UserService.get_all_students(session)
    students = sorted(students, key=lambda u: u.created_at or datetime.min, reverse=True)
    await _show_users_list(callback, students, page, "students_page", "👨‍🎓", "Ученики")



# ===== СТАТИСТИКА =====

@router.callback_query(F.data == "admin_stats")
async def show_statistics(callback: CallbackQuery, session: AsyncSession):
    """Глобальная статистика"""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    stats = await StatisticsService.get_global_statistics(session)
    
    response = (
        f"📊 Общая статистика проекта\n\n"
        f"👨‍🎓 Учеников: {stats.get('students')}\n"
        f"✅ Активных подписок: {stats.get('active_subscriptions')}\n"
        f"📝 Всего решено задач: {stats.get('total_tasks_solved')}\n\n"
    )
    
    # Распределение по классам
    class_dist = stats.get('class_distribution', {})
    if class_dist:
        response += "🎓 Распределение по классам:\n"
        for class_num in sorted(class_dist.keys()):
            if class_num:
                response += f"   {class_num} класс: {class_dist[class_num]} учеников\n"
    
    await callback.message.edit_text(response, reply_markup=get_admin_menu())
    await callback.answer()


# ===== РЕЗЕРВНОЕ КОПИРОВАНИЕ =====

@router.callback_query(F.data == "admin_backup")
async def create_manual_backup(callback: CallbackQuery):
    """Создание резервной копии вручную"""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    await callback.answer("⏳ Создаю резервную копию...", show_alert=True)
    
    try:
        from utils import setup_logger
        logger = setup_logger()
        logger.info(f"Запрос на ручной бэкап от пользователя {callback.from_user.id}")
        
        await manual_backup()
        
        logger.info("Ручной бэкап успешно создан")
        await callback.message.answer(
            "✅ <b>Резервная копия создана</b>\n\n"
            "Файл отправлен в канал для бэкапов.",
            parse_mode="HTML"
        )
    except Exception as e:
        from utils import setup_logger
        logger = setup_logger()
        logger.error(f"Ошибка при создании ручного бэкапа: {e}", exc_info=True)
        
        # Отправляем в канал ошибок
        from services import log_error
        await log_error(
            error=e,
            context="Создание ручного бэкапа (admin.py)",
            user_id=callback.from_user.id,
            username=callback.from_user.username
        )
        
        await callback.message.answer(
            f"❌ <b>Ошибка при создании бэкапа</b>\n\n"
            f"Детали: {str(e)[:500]}",
            parse_mode="HTML"
        )


@router.callback_query(F.data == "admin_restore_backup")
async def start_restore_backup(callback: CallbackQuery, state: FSMContext):
    """Начало процесса восстановления из бэкапа"""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    await callback.message.edit_text(
        "📥 <b>Восстановление из бэкапа</b>\n\n"
        "⚠️ <b>ВНИМАНИЕ!</b> Это действие:\n"
        "• Добавит пользователей из бэкапа в текущую БД\n"
        "• НЕ удалит существующих пользователей\n"
        "• Обновит данные пользователей если они уже есть\n\n"
        "📎 Отправьте файл бэкапа (.db файл) из канала с бэкапами\n\n"
        "💡 Чтобы скачать последний бэкап:\n"
        "1. Зайдите в канал с бэкапами\n"
        "2. Найдите последний файл .db\n"
        "3. Перешлите его сюда",
        parse_mode="HTML",
        reply_markup=get_cancel_keyboard_inline()
    )
    
    await state.set_state(AdminStates.waiting_backup_file)
    await callback.answer()


@router.message(AdminStates.waiting_backup_file, F.document)
async def restore_from_backup(message: Message, session: AsyncSession, state: FSMContext):
    """Восстановление данных из файла бэкапа"""
    if not is_admin(message.from_user.id):
        return
    
    document = message.document
    
    # Проверяем формат файла - поддерживаем .json (новый формат) и .db (старый)
    if document.file_name.endswith('.json'):
        # Новый формат JSON
        await restore_from_json_backup(message, session, state, document)
        return
    elif document.file_name.endswith('.db'):
        # Старый формат SQLite
        await restore_from_db_backup(message, session, state, document)
        return
    else:
        await message.answer(
            "❌ Неверный формат файла!\n\n"
            "Отправьте файл с расширением .json (новый формат) или .db (старый формат)",
            reply_markup=get_cancel_keyboard_inline()
        )
        return


async def restore_from_json_backup(message: Message, session: AsyncSession, state: FSMContext, document):
    """Восстановление из JSON бэкапа (новый формат)"""
    msg = await message.answer("⏳ Загружаю и обрабатываю JSON бэкап...")
    
    try:
        import aiohttp
        import json
        import os
        from datetime import datetime
        
        # Скачиваем файл
        file = await message.bot.get_file(document.file_id)
        file_url = f"https://api.telegram.org/file/bot{message.bot.token}/{file.file_path}"
        
        # Сохраняем во временный файл
        temp_backup_path = f"temp_restore_{datetime.now().timestamp()}.json"
        
        async with aiohttp.ClientSession() as http_session:
            async with http_session.get(file_url) as resp:
                if resp.status == 200:
                    with open(temp_backup_path, 'wb') as f:
                        f.write(await resp.read())
        
        await msg.edit_text("📊 Анализирую бэкап...")
        
        # Читаем JSON
        with open(temp_backup_path, 'r', encoding='utf-8') as f:
            backup_data = json.load(f)
        
        # Проверяем структуру
        if 'users' not in backup_data:
            await msg.edit_text("⚠️ Неверный формат бэкапа!")
            os.remove(temp_backup_path)
            await state.clear()
            return
        
        backup_users = backup_data.get('users', [])
        backup_tasks = backup_data.get('tasks', [])
        backup_progress = backup_data.get('progress', [])
        backup_codes = backup_data.get('access_codes', [])
        
        if not backup_users:
            await msg.edit_text("⚠️ В бэкапе нет пользователей!")
            os.remove(temp_backup_path)
            await state.clear()
            return
        
        await msg.edit_text(f"🔄 Восстанавливаю {len(backup_users)} пользователей...")
        
        # Статистика
        added_count = 0
        updated_count = 0
        errors_count = 0
        
        # Импортируем пользователей
        from models.user import UserRole
        
        for user_data in backup_users:
            try:
                telegram_id = user_data.get('telegram_id')
                
                if not telegram_id:
                    errors_count += 1
                    continue
                
                # Проверяем существует ли пользователь
                result = await session.execute(
                    select(User).where(User.telegram_id == telegram_id)
                )
                existing_user = result.scalar_one_or_none()
                
                if existing_user:
                    # Обновляем существующего
                    existing_user.username = user_data.get('username')
                    existing_user.first_name = user_data.get('first_name')
                    existing_user.last_name = user_data.get('last_name')
                    if user_data.get('role'):
                        existing_user.role = UserRole(user_data['role'])
                    existing_user.class_number = user_data.get('class_number')
                    # Обновляем токены если они есть в бэкапе
                    if 'tokens_limit' in user_data:
                        existing_user.tokens_limit = user_data.get('tokens_limit', 1000000)
                    if 'tokens_used' in user_data:
                        existing_user.tokens_used = user_data.get('tokens_used', 0)
                    if 'tokens_frozen' in user_data:
                        existing_user.tokens_frozen = user_data.get('tokens_frozen', False)
                    if 'first_tutor_usage' in user_data:
                        existing_user.first_tutor_usage = user_data.get('first_tutor_usage', True)
                    if user_data.get('tokens_reset_date'):
                        existing_user.tokens_reset_date = datetime.fromisoformat(user_data['tokens_reset_date'])

                    # Подписка восстанавливается ниже — из реальных кодов доступа (access_codes)
                    updated_count += 1
                else:
                    # Создаём нового пользователя
                    new_user = User(
                        telegram_id=telegram_id,
                        username=user_data.get('username'),
                        first_name=user_data.get('first_name'),
                        last_name=user_data.get('last_name'),
                        role=UserRole(user_data['role']) if user_data.get('role') else None,
                        class_number=user_data.get('class_number'),
                        # ТОКЕНЫ - восстанавливаем из бэкапа
                        tokens_limit=user_data.get('tokens_limit', 1000000),
                        tokens_used=user_data.get('tokens_used', 0),
                        tokens_frozen=user_data.get('tokens_frozen', False),
                        first_tutor_usage=user_data.get('first_tutor_usage', True)
                    )
                    
                    if user_data.get('created_at'):
                        new_user.created_at = datetime.fromisoformat(user_data['created_at'])
                    if user_data.get('tokens_reset_date'):
                        new_user.tokens_reset_date = datetime.fromisoformat(user_data['tokens_reset_date'])
                    
                    session.add(new_user)
                    await session.flush()  # Получаем ID нового пользователя

                    # Подписка восстанавливается ниже — из реальных кодов доступа (access_codes)
                    added_count += 1
            
            except Exception as e:
                errors_count += 1
                print(f"Ошибка при импорте пользователя: {e}")
                continue
        
        # Сохраняем изменения пользователей
        await session.commit()

        # ВАЖНО: задачи/прогресс/коды в бэкапе ссылаются на СТАРЫЙ user.id.
        # В новой БД у пользователей могут быть ДРУГИЕ id, поэтому строим карту
        # старый user_id -> актуальный user.id (сопоставляя по стабильному telegram_id).
        old_to_new_user_id = {}
        for u in backup_users:
            old_uid = u.get('id')
            tg = u.get('telegram_id')
            if old_uid is None or tg is None:
                continue
            uid_result = await session.execute(
                select(User.id).where(User.telegram_id == tg)
            )
            new_uid = uid_result.scalar_one_or_none()
            if new_uid is not None:
                old_to_new_user_id[old_uid] = new_uid

        # ===== ВОССТАНОВЛЕНИЕ КОДОВ ДОСТУПА =====
        if backup_codes:
            await msg.edit_text(f"🔑 Восстанавливаю {len(backup_codes)} кодов доступа...")

            codes_added = 0
            codes_updated = 0
            codes_errors = 0

            from models import AccessCode

            for code_data in backup_codes:
                try:
                    # Пропускаем если код уже есть
                    existing_code = await AccessService.get_code(session, code_data.get('code'))
                    if existing_code:
                        codes_updated += 1
                        continue

                    # Привязываем код к актуальному пользователю (по карте id)
                    old_activated_by = code_data.get('activated_by')
                    if old_activated_by is not None:
                        new_activated_by = old_to_new_user_id.get(old_activated_by)
                        if new_activated_by is None:
                            # Пользователь этого кода не восстановлен — пропускаем код
                            codes_errors += 1
                            continue
                    else:
                        new_activated_by = None

                    # Создаём код доступа
                    new_code = AccessCode(
                        code=code_data.get('code'),
                        code_name=code_data.get('code_name'),
                        duration_days=code_data.get('duration_days'),
                        created_by=code_data.get('created_by'),
                        activated_by=new_activated_by,
                        is_active=code_data.get('is_active', False),
                        is_blocked=code_data.get('is_blocked', False)
                    )
                    
                    if code_data.get('created_at'):
                        new_code.created_at = datetime.fromisoformat(code_data['created_at'])
                    if code_data.get('activated_at'):
                        new_code.activated_at = datetime.fromisoformat(code_data['activated_at'])
                    if code_data.get('expires_at'):
                        new_code.expires_at = datetime.fromisoformat(code_data['expires_at'])
                    
                    session.add(new_code)
                    codes_added += 1
                
                except Exception as e:
                    codes_errors += 1
                    print(f"Ошибка при импорте кода: {e}")
                    continue
            
            await session.commit()
        
        # ===== ВОССТАНОВЛЕНИЕ ЗАДАЧ =====
        if backup_tasks:
            await msg.edit_text(f"📝 Восстанавливаю {len(backup_tasks)} задач...")
            
            tasks_added = 0
            tasks_errors = 0
            
            from models import Task
            from models.task import TaskDifficulty
            
            for task_data in backup_tasks:
                try:
                    old_user_id = task_data.get('user_id')
                    # Сопоставляем со старым id пользователя из бэкапа
                    new_user_id = old_to_new_user_id.get(old_user_id) if old_user_id is not None else None
                    if not new_user_id:
                        tasks_errors += 1
                        continue

                    # Создаём задачу
                    new_task = Task(
                        user_id=new_user_id,
                        task_text=task_data.get('task_text'),
                        topic=task_data.get('topic'),
                        difficulty=TaskDifficulty(task_data['difficulty']) if task_data.get('difficulty') else TaskDifficulty.MEDIUM,
                        student_answer=task_data.get('student_answer'),
                        is_correct=task_data.get('is_correct'),
                        ai_explanation=task_data.get('ai_explanation')
                    )
                    
                    if task_data.get('created_at'):
                        new_task.created_at = datetime.fromisoformat(task_data['created_at'])
                    if task_data.get('completed_at'):
                        new_task.completed_at = datetime.fromisoformat(task_data['completed_at'])
                    
                    session.add(new_task)
                    tasks_added += 1
                
                except Exception as e:
                    tasks_errors += 1
                    print(f"Ошибка при импорте задачи: {e}")
                    continue
            
            await session.commit()
        
        # ===== ВОССТАНОВЛЕНИЕ ПРОГРЕССА =====
        if backup_progress:
            await msg.edit_text(f"📊 Восстанавливаю {len(backup_progress)} записей прогресса...")
            
            progress_added = 0
            progress_updated = 0
            progress_errors = 0
            
            from models import Progress
            
            for progress_data in backup_progress:
                try:
                    old_user_id = progress_data.get('user_id')
                    # Сопоставляем со старым id пользователя из бэкапа
                    new_user_id = old_to_new_user_id.get(old_user_id) if old_user_id is not None else None
                    if not new_user_id:
                        progress_errors += 1
                        continue

                    # Проверяем есть ли уже прогресс
                    result = await session.execute(
                        select(Progress).where(Progress.user_id == new_user_id)
                    )
                    existing_progress = result.scalar_one_or_none()

                    if existing_progress:
                        # Обновляем
                        existing_progress.total_tasks = progress_data.get('total_tasks', 0)
                        existing_progress.solved_tasks = progress_data.get('solved_tasks', 0)
                        existing_progress.correct_answers = progress_data.get('correct_answers', 0)
                        existing_progress.mistakes = progress_data.get('mistakes', 0)
                        if progress_data.get('last_activity'):
                            existing_progress.last_activity = datetime.fromisoformat(progress_data['last_activity'])
                        progress_updated += 1
                    else:
                        # Создаём новый
                        new_progress = Progress(
                            user_id=new_user_id,
                            total_tasks=progress_data.get('total_tasks', 0),
                            solved_tasks=progress_data.get('solved_tasks', 0),
                            correct_answers=progress_data.get('correct_answers', 0),
                            mistakes=progress_data.get('mistakes', 0)
                        )
                        
                        if progress_data.get('last_activity'):
                            new_progress.last_activity = datetime.fromisoformat(progress_data['last_activity'])
                        if progress_data.get('created_at'):
                            new_progress.created_at = datetime.fromisoformat(progress_data['created_at'])
                        
                        session.add(new_progress)
                        progress_added += 1
                
                except Exception as e:
                    progress_errors += 1
                    print(f"Ошибка при импорте прогресса: {e}")
                    continue
            
            await session.commit()
        
        # Удаляем временный файл
        os.remove(temp_backup_path)
        
        # Итоговая статистика
        result_text = (
            f"✅ <b>Восстановление из JSON завершено!</b>\n\n"
            f"📊 <b>Пользователи:</b>\n"
            f"➕ Добавлено: {added_count}\n"
            f"🔄 Обновлено: {updated_count}\n"
        )
        
        if errors_count > 0:
            result_text += f"⚠️ Ошибок: {errors_count}\n"
        
        if backup_codes:
            result_text += f"\n🔑 <b>Коды доступа:</b>\n"
            result_text += f"➕ Добавлено: {codes_added}\n"
            result_text += f"🔄 Уже были: {codes_updated}\n"
            if codes_errors > 0:
                result_text += f"⚠️ Ошибок: {codes_errors}\n"
        
        if backup_tasks:
            result_text += f"\n📝 <b>Задачи:</b>\n"
            result_text += f"➕ Добавлено: {tasks_added}\n"
            if tasks_errors > 0:
                result_text += f"⚠️ Ошибок: {tasks_errors}\n"
        
        if backup_progress:
            result_text += f"\n📊 <b>Прогресс:</b>\n"
            result_text += f"➕ Создано: {progress_added}\n"
            result_text += f"🔄 Обновлено: {progress_updated}\n"
            if progress_errors > 0:
                result_text += f"⚠️ Ошибок: {progress_errors}\n"
        
        await msg.edit_text(result_text, parse_mode="HTML")
        await state.clear()
        
        from utils import setup_logger
        logger = setup_logger()
        logger.info(f"Восстановление из JSON: добавлено {added_count}, обновлено {updated_count}")
    
    except Exception as e:
        # Удаляем временный файл
        try:
            if 'temp_backup_path' in locals() and os.path.exists(temp_backup_path):
                os.remove(temp_backup_path)
        except:
            pass
        
        from services import log_error
        await log_error(
            error=e,
            context="Восстановление из JSON бэкапа",
            user_id=message.from_user.id
        )
        
        await msg.edit_text(
            f"❌ <b>Ошибка при восстановлении!</b>\n\n"
            f"Детали: {str(e)[:500]}",
            parse_mode="HTML"
        )
        await state.clear()


@router.callback_query(AdminStates.waiting_backup_file, F.data == "cancel_action")
async def cancel_restore_backup(callback: CallbackQuery, state: FSMContext):
    """Отмена восстановления"""
    await state.clear()
    await callback.message.edit_text(
        "❌ Восстановление отменено\n\n"
        "🔧 Админ-панель\n\n"
        "Выберите раздел:",
        reply_markup=get_admin_menu()
    )
    await callback.answer("Отменено")


# ===== ОТМЕНА ДЕЙСТВИЙ =====

@router.callback_query(AdminStates.waiting_code_name, F.data == "cancel_action")
@router.callback_query(AdminStates.waiting_code_duration, F.data == "cancel_action")
@router.callback_query(AdminStates.waiting_backup_file, F.data == "cancel_action")
async def cancel_admin_action_callback(callback: CallbackQuery, state: FSMContext):
    """Отмена действия администратора через callback"""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    await state.clear()
    await callback.message.edit_text(
        "Действие отменено.\n\n"
        "🔧 Админ-панель\n\n"
        "Выберите раздел:",
        reply_markup=get_admin_menu()
    )
    await callback.answer("Действие отменено")


@router.message(AdminStates.waiting_code_name, F.text == "❌ Отмена")
@router.message(AdminStates.waiting_code_duration, F.text == "❌ Отмена")
@router.message(AdminStates.waiting_backup_file, F.text == "❌ Отмена")
async def cancel_admin_action(message: Message, state: FSMContext):
    """Отмена действия администратора через текст"""
    if not is_admin(message.from_user.id):
        return
    
    await state.clear()
    await message.answer(
        "Действие отменено.\n\n"
        "🔧 Админ-панель\n\n"
        "Выберите раздел:",
        reply_markup=get_admin_menu()
    )



async def restore_from_db_backup(message: Message, session: AsyncSession, state: FSMContext, document):
    """Восстановление из старого .db формата"""
    msg = await message.answer("⏳ Загружаю и обрабатываю .db бэкап...")
    
    await msg.edit_text(
        "⚠️ <b>Старый формат .db больше не поддерживается</b>\n\n"
        "📦 Используйте новый формат бэкапов .json\n"
        "Эти бэкапы создаются автоматически каждые 6 часов\n\n"
        "💡 Чтобы получить актуальный бэкап:\n"
        "1. Создайте новый бэкап через админ-панель\n"
        "2. Используйте полученный .json файл для восстановления",
        parse_mode="HTML"
    )
    await state.clear()


# ===== ПОИСК ПОЛЬЗОВАТЕЛЯ =====

class SearchUserStates(StatesGroup):
    """Состояния поиска пользователя"""
    waiting_username = State()


@router.callback_query(F.data == "admin_search_user")
async def start_search_user(callback: CallbackQuery, state: FSMContext):
    """Начало поиска пользователя"""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    await callback.message.edit_text(
        "🔍 Поиск пользователя\n\n"
        "Введите username пользователя (без @) или его Telegram ID:",
        reply_markup=get_cancel_keyboard_inline()
    )
    await state.set_state(SearchUserStates.waiting_username)
    await callback.answer()


@router.message(SearchUserStates.waiting_username)
async def search_user_by_username(message: Message, session: AsyncSession, state: FSMContext):
    """Поиск пользователя по username или ID"""
    if not is_admin(message.from_user.id):
        return
    
    query = message.text.strip().replace('@', '')
    
    # Пробуем найти по username
    result = await session.execute(
        select(User).where(User.username == query)
    )
    user = result.scalar_one_or_none()
    
    # Если не нашли, пробуем по telegram_id
    if not user:
        try:
            telegram_id = int(query)
            result = await session.execute(
                select(User).where(User.telegram_id == telegram_id)
            )
            user = result.scalar_one_or_none()
        except ValueError:
            pass
    
    if not user:
        await message.answer(
            f"❌ Пользователь не найден\n\n"
            f"Попробуйте еще раз или нажмите ❌ Отменить",
            reply_markup=get_cancel_keyboard_inline()
        )
        return
    
    await state.clear()
    await show_user_details(message, session, user)


async def _render_user_card(session: AsyncSession, user: User,
                            back_callback: str = "admin_users",
                            back_text: str = "◀️ К списку пользователей"):
    """Собирает текст карточки пользователя и клавиатуру управления."""
    from models import Progress
    import asyncio

    # Параллельно тянем прогресс и активную подписку
    progress_result, active_code = await asyncio.gather(
        session.execute(select(Progress).where(Progress.user_id == user.id)),
        AccessService.get_user_active_code(session, user.telegram_id)
    )
    progress = progress_result.scalar_one_or_none()

    # Ник + имя
    text = "👤 <b>Карточка пользователя</b>\n\n"
    if user.username:
        text += f"🆔 Ник: @{user.username}\n"
    text += f"📛 Имя: {user.full_name}\n"
    text += f"🔢 Telegram ID: <code>{user.telegram_id}</code>\n"
    if user.class_number:
        text += f"🎓 Класс: {user.class_number}\n"

    # Подписка (сколько дней осталось)
    text += "\n<b>💳 Подписка:</b>\n"
    if active_code and active_code.expires_at:
        days_left = max(0, (active_code.expires_at - datetime.utcnow()).days)
        text += f"✅ Активна — осталось {days_left} дн.\n"
        text += f"📆 До: {active_code.expires_at.strftime('%d.%m.%Y')}\n"
    else:
        text += "❌ Нет активной подписки\n"

    # Токены
    text += "\n<b>💰 Токены:</b>\n"
    tokens_used = user.tokens_used or 0
    tokens_limit = user.tokens_limit or 0
    tokens_left = max(0, tokens_limit - tokens_used)
    text += f"📊 Использовано: {tokens_used:,} / {tokens_limit:,}\n"
    text += f"✨ Осталось: {tokens_left:,}\n"
    if user.tokens_frozen:
        text += "❄️ Заморожены (нет подписки)\n"
    if user.tokens_reset_date:
        text += f"🔄 Обновление: {user.tokens_reset_date.strftime('%d.%m.%Y')}\n"

    # Решённые задачи
    text += "\n<b>📊 Задачи:</b>\n"
    if progress:
        text += f"✅ Решено: {progress.solved_tasks}\n"
        text += f"🎯 Правильно: {progress.correct_answers}\n"
        text += f"❌ Ошибок: {progress.mistakes}\n"
        text += f"📈 Успех: {progress.success_rate}%\n"
    else:
        text += "Пользователь ещё не решал задачи\n"

    # Клавиатура управления
    kb = InlineKeyboardBuilder()
    kb.button(text="⏰ Продлить доступ", callback_data=f"extend_access_{user.telegram_id}")
    kb.button(text="🚫 Заблокировать", callback_data=f"block_user_{user.telegram_id}")
    kb.button(text="📊 Все задачи", callback_data=f"user_tasks_{user.telegram_id}")
    kb.button(text="🔄 Обновить", callback_data=f"refresh_user_{user.telegram_id}")
    kb.button(text=back_text, callback_data=back_callback)
    kb.adjust(2, 2, 1)
    return text, kb.as_markup()


async def show_user_details(message: Message, session: AsyncSession, user: User):
    """Карточка пользователя новым сообщением (используется при поиске)."""
    text, markup = await _render_user_card(session, user, "admin_panel", "◀️ В админ-панель")
    await message.answer(text, reply_markup=markup, parse_mode="HTML")


@router.callback_query(F.data.startswith("view_user_"))
async def view_user(callback: CallbackQuery, session: AsyncSession):
    """Открыть карточку пользователя по кнопке из списка"""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return

    await callback.answer()

    try:
        telegram_id = int(callback.data.rsplit("_", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("❌ Ошибка", show_alert=True)
        return

    result = await session.execute(select(User).where(User.telegram_id == telegram_id))
    user = result.scalar_one_or_none()
    if not user:
        await callback.message.edit_text("❌ Пользователь не найден", reply_markup=get_users_admin_menu())
        return

    text, markup = await _render_user_card(session, user, "admin_users", "◀️ К списку")
    await callback.message.edit_text(text, reply_markup=markup, parse_mode="HTML")


# ===== ОБНОВЛЕНИЕ ИНФОРМАЦИИ О ПОЛЬЗОВАТЕЛЕ =====

@router.callback_query(F.data.startswith("refresh_user_"))
async def refresh_user_info(callback: CallbackQuery, session: AsyncSession):
    """Обновить карточку пользователя на месте"""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return

    await callback.answer("🔄 Обновляю...")

    try:
        telegram_id = int(callback.data.rsplit("_", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("❌ Ошибка", show_alert=True)
        return

    result = await session.execute(select(User).where(User.telegram_id == telegram_id))
    user = result.scalar_one_or_none()
    if not user:
        await callback.message.edit_text("❌ Пользователь не найден", reply_markup=get_users_admin_menu())
        return

    text, markup = await _render_user_card(session, user, "admin_users", "◀️ К списку")
    try:
        await callback.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
    except Exception:
        # Телеграм кидает "message is not modified", если ничего не поменялось — это ок
        pass


# ===== ПОКАЗ ЗАДАЧ ПОЛЬЗОВАТЕЛЯ =====

@router.callback_query(F.data.startswith("user_tasks_"))
async def show_user_tasks(callback: CallbackQuery, session: AsyncSession):
    """Показать все задачи пользователя - ОПТИМИЗИРОВАНО"""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    # БЫСТРЫЙ ОТВЕТ
    await callback.answer()
    
    # Индикатор загрузки
    loading_msg = await callback.message.edit_text("⏳ Загружаю задачи...")
    
    try:
        telegram_id = int(callback.data.split("_")[2])
        
        result = await session.execute(
            select(User).where(User.telegram_id == telegram_id)
        )
        user = result.scalar_one_or_none()
        
        if not user:
            await loading_msg.edit_text(
                "❌ Пользователь не найден",
                reply_markup=get_admin_menu()
            )
            return
        
        from models import Task
        tasks_result = await session.execute(
            select(Task)
            .where(Task.user_id == user.id)
            .order_by(Task.created_at.desc())
            .limit(20)
        )
        tasks = tasks_result.scalars().all()
        
        if not tasks:
            await loading_msg.edit_text(
                f"У пользователя {user.full_name} нет задач",
                reply_markup=InlineKeyboardBuilder().button(
                    text="◀️ Назад", 
                    callback_data=f"refresh_user_{telegram_id}"
                ).adjust(1).as_markup()
            )
            return
        
        response = f"📝 <b>Задачи пользователя {user.full_name}</b>\n\n"
        response += f"Показано последних {len(tasks)} задач:\n\n"
        
        for idx, task in enumerate(tasks, 1):
            status_emoji = "✅" if task.is_correct else "❌" if task.is_correct is False else "⏳"
            task_preview = task.task_text[:50] + "..." if len(task.task_text) > 50 else task.task_text
            response += f"{idx}. {status_emoji} {task_preview}\n"
            response += f"   📅 {task.created_at.strftime('%d.%m.%Y %H:%M')}\n"
            
            if task.completed_at:
                response += f"   ✓ Завершено: {task.completed_at.strftime('%d.%m.%Y %H:%M')}\n"
            
            response += "\n"
        
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        kb = InlineKeyboardBuilder()
        kb.button(text="◀️ Назад", callback_data=f"refresh_user_{telegram_id}")
        kb.adjust(1)
        
        await loading_msg.edit_text(
            response,
            reply_markup=kb.as_markup(),
            parse_mode="HTML"
        )
    
    except Exception as e:
        await loading_msg.edit_text(
            f"❌ Ошибка: {str(e)[:100]}",
            reply_markup=get_admin_menu()
        )


# ===== ПРОДЛЕНИЕ ДОСТУПА =====

class ExtendAccessStates(StatesGroup):
    """Состояния продления доступа"""
    waiting_days = State()
    user_telegram_id = None


@router.callback_query(F.data.startswith("extend_access_"))
async def start_extend_access(callback: CallbackQuery, state: FSMContext):
    """Начало продления доступа"""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    telegram_id = int(callback.data.split("_")[2])
    
    await state.update_data(user_telegram_id=telegram_id)
    await state.set_state(ExtendAccessStates.waiting_days)
    
    await callback.message.answer(
        "⏰ <b>Продление доступа</b>\n\n"
        "Введите количество дней для продления (например: 30, 90, 365):",
        reply_markup=get_cancel_keyboard_inline(),
        parse_mode="HTML"
    )
    await callback.answer()


@router.message(ExtendAccessStates.waiting_days)
async def extend_user_access(message: Message, session: AsyncSession, state: FSMContext):
    """Продление доступа пользователю"""
    if not is_admin(message.from_user.id):
        return
    
    try:
        days = int(message.text.strip())
        if days <= 0 or days > 3650:
            raise ValueError()
    except ValueError:
        await message.answer(
            "❌ Введите корректное количество дней (от 1 до 3650):",
            reply_markup=get_cancel_keyboard_inline()
        )
        return
    
    data = await state.get_data()
    telegram_id = data.get("user_telegram_id")
    
    # Находим пользователя
    result = await session.execute(
        select(User).where(User.telegram_id == telegram_id)
    )
    user = result.scalar_one_or_none()
    
    if not user:
        await message.answer("❌ Пользователь не найден")
        await state.clear()
        return
    
    # Выдаём прямой доступ
    success, msg = await AccessService.give_direct_access(
        session,
        telegram_id,
        days,
        message.from_user.id
    )
    
    await session.commit()
    
    if not success:
        await message.answer(f"❌ Ошибка: {msg}")
        await state.clear()
        return
    
    await message.answer(
        f"✅ <b>Доступ продлен!</b>\n\n"
        f"👤 Пользователь: {user.full_name}\n"
        f"⏰ Продлено на: {days} дней\n"
        f"📅 До: {(datetime.utcnow() + timedelta(days=days)).strftime('%d.%m.%Y')}\n\n"
        f"Пользователь получил уведомление.",
        parse_mode="HTML"
    )
    
    # Отправляем уведомление пользователю
    try:
        expires_date = (datetime.utcnow() + timedelta(days=days)).strftime('%d.%m.%Y')
        await message.bot.send_message(
            chat_id=telegram_id,
            text=(
                f"✅ <b>Ваша подписка продлена!</b>\n\n"
                f"⏰ Продлено на: {days} дней\n"
                f"📅 Активна до: {expires_date}\n\n"
                f"Спасибо что пользуетесь нашим ботом! 🎉\n\n"
                f"▶️ Чтобы продолжить работу, нажмите /start"
            ),
            parse_mode="HTML"
        )
    except Exception as e:
        print(f"Не удалось отправить уведомление пользователю: {e}")
    
    await state.clear()


# ===== ОТМЕНА ДЕЙСТВИЙ =====

@router.callback_query(F.data == "cancel_action")
async def cancel_action(callback: CallbackQuery, state: FSMContext):
    """Отмена текущего действия"""
    await state.clear()
    await callback.message.edit_text(
        "❌ Действие отменено\n\n"
        "🔧 Админ-панель\n\n"
        "Выберите раздел:",
        reply_markup=get_admin_menu()
    )
    await callback.answer()


# ===== ДОБАВЛЕНИЕ ПОЛЬЗОВАТЕЛЯ =====
# Интерактивный сценарий добавления пользователя реализован в admin_additions.py
# (позволяет выдавать доступ в том числе тем, кто ещё не запускал бота).
