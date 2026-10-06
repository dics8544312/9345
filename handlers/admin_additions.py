"""
Дополнительные обработчики для админ-панели
Добавление и управление пользователями
"""

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timedelta

from services import AccessService, UserService
from keyboards import get_admin_menu, get_cancel_keyboard_inline
from config import settings

router = Router(name="admin_additions")


class AdminUserStates(StatesGroup):
    """Состояния администратора при работе с пользователями"""
    waiting_username_to_add = State()
    waiting_days_for_new_user = State()
    waiting_username_to_search = State()
    waiting_days_to_extend = State()


def is_admin(user_id: int) -> bool:
    """Проверка прав администратора"""
    return user_id in settings.admin_ids_list


# ===== ДОБАВЛЕНИЕ ПОЛЬЗОВАТЕЛЯ =====

@router.callback_query(F.data == "admin_add_user")
async def start_add_user(callback: CallbackQuery, state: FSMContext):
    """Начало процесса добавления пользователя"""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    await callback.message.edit_text(
        "➕ Добавление пользователя\n\n"
        "Введите username пользователя (с @ или без):\n"
        "Например: @username или username\n\n"
        "✅ Можно добавить пользователя даже если он еще не запускал бота!",
        reply_markup=get_cancel_keyboard_inline()
    )
    await state.set_state(AdminUserStates.waiting_username_to_add)
    await callback.answer()


@router.message(AdminUserStates.waiting_username_to_add)
async def get_username_to_add(message: Message, session: AsyncSession, state: FSMContext):
    """Получение username для добавления"""
    if not is_admin(message.from_user.id):
        return
    
    username = message.text.strip().lstrip('@')
    
    if not username:
        await message.answer(
            "❌ Username не может быть пустым\n\nПопробуйте еще раз:",
            reply_markup=get_cancel_keyboard_inline()
        )
        return
    
    # Сохраняем данные в состояние
    await state.update_data(
        target_username=username
    )
    
    # Проверяем существует ли пользователь
    user = await UserService.get_user_by_username(session, username)
    
    if user:
        # Пользователь существует - показываем информацию
        has_access, _ = await AccessService.check_user_access(session, user.telegram_id)
        active_code = await AccessService.get_user_active_code(session, user.telegram_id)
        
        access_info = ""
        if has_access and active_code:
            days_left = (active_code.expires_at - datetime.utcnow()).days
            access_info = f"\n⚠️ Текущий доступ:\n   • Активен: да\n   • Осталось: {days_left} дней\n   • До: {active_code.expires_at.strftime('%d.%m.%Y')}"
        else:
            access_info = "\n✅ Текущий доступ: нет"
        
        await state.update_data(user_found=True, telegram_id=user.telegram_id)
        
        await message.answer(
            f"👤 Пользователь найден!\n\n"
            f"📋 Информация:\n"
            f"• Имя: {user.full_name}\n"
            f"• Username: @{username}\n"
            f"• ID: {user.telegram_id}\n"
            f"• Регистрация: {user.created_at.strftime('%d.%m.%Y')}"
            f"{access_info}\n\n"
            f"➡️ Введите количество дней доступа (например: 30, 90, 365):",
            reply_markup=get_cancel_keyboard_inline()
        )
    else:
        # Пользователь не найден - отложенный доступ
        await state.update_data(user_found=False)
        
        await message.answer(
            f"ℹ️ Пользователь @{username} не найден\n\n"
            f"❗ Пользователь еще не запускал бота\n\n"
            f"💡 Что произойдет:\n"
            f"1. Вы выдадите доступ заранее\n"
            f"2. Доступ будет ждать активации\n"
            f"3. Когда @{username} запустит /start - доступ активируется автоматически\n\n"
            f"➡️ Введите количество дней доступа или нажмите Отмена:",
            reply_markup=get_cancel_keyboard_inline()
        )
    
    await state.set_state(AdminUserStates.waiting_days_for_new_user)


@router.message(AdminUserStates.waiting_days_for_new_user)
async def give_access_to_user(message: Message, session: AsyncSession, state: FSMContext):
    """Выдача доступа пользователю"""
    if not is_admin(message.from_user.id):
        return
    
    # Проверяем ввод
    try:
        days = int(message.text.strip())
        if days <= 0 or days > 3650:
            raise ValueError("Дни вне диапазона")
    except ValueError:
        await message.answer(
            "❌ Некорректный ввод!\n\n"
            "Введите число от 1 до 3650:",
            reply_markup=get_cancel_keyboard_inline()
        )
        return
    
    # Получаем данные
    data = await state.get_data()
    username = data.get("target_username")
    user_found = data.get("user_found", False)
    telegram_id = data.get("telegram_id")
    
    try:
        if user_found and telegram_id:
            # Пользователь существует - выдаём напрямую
            success, msg = await AccessService.give_direct_access(
                session,
                telegram_id,
                days,
                message.from_user.id
            )
            
            if success:
                await message.answer(
                    f"✅ Доступ успешно выдан!\n\n"
                    f"👤 Пользователь: @{username}\n"
                    f"⏰ Период: {days} дней\n"
                    f"📅 До: {(datetime.utcnow() + timedelta(days=days)).strftime('%d.%m.%Y')}\n\n"
                    f"💬 Пользователь может сразу начать работу!",
                    reply_markup=get_admin_menu()
                )
            else:
                await message.answer(
                    f"❌ Ошибка при выдаче доступа:\n{msg}",
                    reply_markup=get_admin_menu()
                )
        else:
            # Пользователь не существует - отложенный доступ
            success, msg, _ = await AccessService.give_access_by_username(
                session,
                username,
                days,
                message.from_user.id
            )
            
            if success:
                await message.answer(
                    f"✅ Отложенный доступ создан!\n\n"
                    f"👤 Username: @{username}\n"
                    f"⏰ Период: {days} дней\n\n"
                    f"💡 Активация:\n"
                    f"Когда @{username} запустит /start,\n"
                    f"доступ активируется автоматически!",
                    reply_markup=get_admin_menu()
                )
            else:
                await message.answer(
                    f"❌ Ошибка:\n{msg}",
                    reply_markup=get_admin_menu()
                )
        
        await session.commit()
    except Exception as e:
        await session.rollback()
        await message.answer(
            f"❌ Произошла ошибка:\n{str(e)}\n\n"
            f"Попробуйте еще раз или обратитесь к разработчику.",
            reply_markup=get_admin_menu()
        )
    
    await state.clear()


# ===== БЛОКИРОВКА ПОЛЬЗОВАТЕЛЯ =====

@router.callback_query(F.data.startswith("block_user_"))
async def block_user_access(callback: CallbackQuery, session: AsyncSession):
    """Заблокировать доступ пользователя"""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    telegram_id = int(callback.data.split("_")[2])
    
    # Получаем пользователя
    user = await UserService.get_user(session, telegram_id)
    if not user:
        await callback.answer("❌ Пользователь не найден", show_alert=True)
        return
    
    # Получаем активный код доступа
    active_code = await AccessService.get_user_active_code(session, telegram_id)
    
    if not active_code:
        await callback.answer(
            "⚠️ У пользователя нет активного доступа",
            show_alert=True
        )
        return
    
    # Блокируем код доступа
    active_code.is_blocked = True
    active_code.block()  # Метод из модели
    
    # Замораживаем токены
    from services.token_service import TokenService
    await TokenService.freeze_tokens(session, user)
    
    await session.commit()
    
    # Уведомление админу
    await callback.message.edit_text(
        f"🚫 <b>Доступ заблокирован</b>\n\n"
        f"👤 Пользователь: {user.full_name}\n"
        f"📱 Username: @{user.username}\n"
        f"🆔 ID: {telegram_id}\n\n"
        f"❌ Подписка заблокирована\n"
        f"❄️ Токены заморожены\n\n"
        f"💡 Пользователь больше не может использовать бота\n"
        f"Для разблокировки выдайте новый доступ через\n"
        f"<b>👥 Управление → ➕ Добавить пользователя</b>",
        reply_markup=get_admin_menu(),
        parse_mode="HTML"
    )
    
    # Пытаемся уведомить пользователя
    try:
        await callback.bot.send_message(
            chat_id=telegram_id,
            text=(
                "🚫 <b>Ваш доступ заблокирован</b>\n\n"
                "❌ Подписка прекращена администратором\n\n"
                "📝 Для восстановления доступа:\n"
                "• Напишите администратору: @dvedian\n"
                "• Уточните причину блокировки\n\n"
                "🛠 График работы тех поддержки:\n"
                "📅 Пн-Пт: 15:00 - 21:00\n"
                "🚫 Сб-Вс: выходной\n\n"
                "💬 Тех поддержка: @DICSITRen2200"
            ),
            parse_mode="HTML"
        )
    except Exception as e:
        # Если не удалось отправить - не критично
        pass
    
    await callback.answer("✅ Доступ заблокирован", show_alert=True)


# ===== ОТМЕНА ДЕЙСТВИЙ =====

@router.callback_query(AdminUserStates.waiting_username_to_add, F.data == "cancel_action")
@router.callback_query(AdminUserStates.waiting_days_for_new_user, F.data == "cancel_action")
@router.callback_query(AdminUserStates.waiting_username_to_search, F.data == "cancel_action")
@router.callback_query(AdminUserStates.waiting_days_to_extend, F.data == "cancel_action")
async def cancel_admin_user_action(callback: CallbackQuery, state: FSMContext):
    """Отмена действия с пользователем"""
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
