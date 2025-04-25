import logging
import os
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Updater, CommandHandler, MessageHandler, Filters, 
    CallbackContext, ConversationHandler, CallbackQueryHandler
)
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
import pytz
import sqlite3
import types

from database import Database
from utils import (
    parse_time_input, format_progress_message, format_notification_message,
    format_time_record, format_time, format_money, parse_timer_message,
    create_progress_chart
)

# Загрузка переменных окружения из .env файла
load_dotenv()

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

# Состояния для ConversationHandler
(
    RATE, GOAL, 
    ADD_TIME, CONFIRM_TIME, 
    CHANGE_RATE, CHANGE_GOAL, CHANGE_NOTIFY,
    RESET_GOAL_CONFIRM
) = range(8)

# Инициализация базы данных
db = Database()

# Инициализация планировщика
scheduler = BackgroundScheduler()
scheduler.start()


def send_notification(context: CallbackContext, user_id):
    """Отправка уведомления пользователю"""
    progress = db.get_progress(user_id)
    if progress:
        message = format_notification_message(progress)
        context.bot.send_message(chat_id=user_id, text=message)


def setup_notification(context: CallbackContext, user_id, freq):
    """Настройка периодичности уведомлений"""
    # Удаляем существующие задачи для пользователя
    for job in scheduler.get_jobs():
        if job.id == f"notify_{user_id}":
            job.remove()
    
    # Устанавливаем новую задачу
    if freq == 'hour':
        scheduler.add_job(
            send_notification, 'interval', hours=1, id=f"notify_{user_id}",
            args=(context, user_id), timezone=pytz.UTC
        )
    elif freq == 'day':
        scheduler.add_job(
            send_notification, 'interval', days=1, id=f"notify_{user_id}",
            args=(context, user_id), timezone=pytz.UTC
        )
    elif freq == 'week':
        scheduler.add_job(
            send_notification, 'interval', weeks=1, id=f"notify_{user_id}",
            args=(context, user_id), timezone=pytz.UTC
        )


# Функции обработчики команд
def start(update: Update, context: CallbackContext) -> int:
    """Обработчик команды /start"""
    user_id = update.effective_user.id
    
    # Проверяем, зарегистрирован ли пользователь
    if db.user_exists(user_id):
        # Если пользователь уже зарегистрирован, показываем главное меню
        show_main_menu(update, context)
        return ConversationHandler.END
    
    # Если нет, начинаем регистрацию
    update.message.reply_text(
        "Приветствую! Я бот для отслеживания рабочего времени и заработка.\n\n"
        "Для начала, укажите свою почасовую ставку (например, 500₽):"
    )
    
    return RATE


def rate_input(update: Update, context: CallbackContext) -> int:
    """Обработка ввода почасовой ставки"""
    user_id = update.effective_user.id
    user_input = update.message.text
    
    # Сохраняем текущее состояние в контексте пользователя
    context.user_data['state'] = RATE
    
    logger.info(f"Получен ввод ставки от пользователя {user_id}: '{user_input}'")
    
    # Пытаемся получить числовое значение ставки
    try:
        # Очищаем ввод от лишних символов
        rate_text = user_input.replace('₽', '').replace('р', '').replace('руб', '')
        rate_text = rate_text.replace(',', '.').strip()
        
        logger.info(f"Очищенный текст ставки: '{rate_text}'")
        
        rate = float(rate_text)
        
        # Сохраняем временно в контексте
        context.user_data['rate'] = rate
        logger.info(f"Ставка сохранена в контексте: {rate}")
        
        update.message.reply_text(
            f"Отлично! Ваша почасовая ставка: {rate:.0f}₽\n\n"
            f"Теперь укажите цель заработка:"
        )
        
        return GOAL
    
    except ValueError as e:
        logger.error(f"Ошибка преобразования ставки: {e}")
        update.message.reply_text(
            "Пожалуйста, введите корректное числовое значение для почасовой ставки.\n"
            "Например: 500 или 500₽"
        )
        
        return RATE


def goal_input(update: Update, context: CallbackContext) -> int:
    """Обработка ввода цели заработка"""
    user_id = update.effective_user.id
    user_input = update.message.text
    
    # Сохраняем текущее состояние в контексте пользователя
    context.user_data['state'] = GOAL
    
    logger.info(f"Получен ввод цели от пользователя {user_id}: '{user_input}'")
    
    # Пытаемся получить числовое значение цели
    try:
        # Очищаем ввод от лишних символов
        goal_text = user_input.replace('₽', '').replace('р', '').replace('руб', '')
        goal_text = goal_text.replace(',', '.').strip()
        
        logger.info(f"Очищенный текст цели: '{goal_text}'")
        
        goal = float(goal_text)
        
        # Получаем сохраненную ставку из контекста
        rate = context.user_data.get('rate')
        
        logger.info(f"Полученная из контекста ставка: {rate}")
        
        if rate is None:
            update.message.reply_text(
                "Произошла ошибка при сохранении ставки. Пожалуйста, начните сначала с команды /start"
            )
            return ConversationHandler.END
        
        # Сохраняем пользователя в базу данных
        try:
            db.add_user(user_id, rate, goal)
            logger.info(f"Пользователь {user_id} добавлен в базу данных")
        except Exception as e:
            logger.error(f"Ошибка при добавлении пользователя в БД: {e}")
            update.message.reply_text(
                "Произошла ошибка при сохранении данных. Пожалуйста, попробуйте позже."
            )
            return ConversationHandler.END
        
        # Настройка уведомлений (по умолчанию ежедневно)
        try:
            setup_notification(context, user_id, 'day')
            logger.info(f"Настроены уведомления для пользователя {user_id}")
        except Exception as e:
            logger.error(f"Ошибка при настройке уведомлений: {e}")
        
        update.message.reply_text(
            f"Отлично! Ваша цель заработка: {goal:.0f}₽\n\n"
            f"Настройка завершена, теперь вы можете использовать бот для отслеживания времени."
        )
        
        # Показываем главное меню
        show_main_menu(update, context)
        
        return ConversationHandler.END
    
    except ValueError as e:
        logger.error(f"Ошибка преобразования цели: {e}")
        update.message.reply_text(
            "Пожалуйста, введите корректное числовое значение для цели заработка.\n"
            "Например: 50000 или 50000₽"
        )
        
        return GOAL


def show_main_menu(update: Update, context: CallbackContext):
    """Показать главное меню с информацией о прогрессе"""
    user_id = update.effective_user.id
    
    # Получаем данные о прогрессе
    progress = db.get_progress(user_id)
    
    # Получаем данные пользователя для отображения ставки
    user_data = db.get_user_data(user_id)
    
    # Формируем текст меню
    if progress and user_data:
        # Получаем общее количество затраченных часов
        total_hours = db.get_total_hours(user_id)
        
        # Проверка на сброшенный прогресс (если earned = 0, но есть часы, показываем только earned)
        if progress['earned'] == 0 and total_hours > 0:
            menu_text = (
                f"🎯 Цель: {format_money(progress['goal'])}\n"
                f"💰 Заработано: {format_money(progress['earned'])} ({progress['percent']}%)\n"
                f"\n"
                f"⌛ Осталось: {progress['hours_left']:.1f}ч\n"
                f"💵 Ставка: {format_money(user_data['rate'])}/час\n"
                f"\n"
                f"Выберите действие:"
            )
        else:
            menu_text = (
                f"🎯 Цель: {format_money(progress['goal'])}\n"
                f"💰 Заработано: {format_money(progress['earned'])} ({progress['percent']}%)\n"
                f"\n"
                f"⏱️ Отработано: {total_hours:.1f}ч\n"
                f"⌛ Осталось: {progress['hours_left']:.1f}ч\n"
                f"💵 Ставка: {format_money(user_data['rate'])}/час\n"
                f"\n"
                f"Выберите действие:"
            )
    else:
        menu_text = "Главное меню:"
    
    # Создаем клавиатуру
    keyboard = [
        [
            InlineKeyboardButton("Добавить время", callback_data='add_time'),
            InlineKeyboardButton("Мой прогресс", callback_data='progress')
        ],
        [
            InlineKeyboardButton("История", callback_data='history'),
            InlineKeyboardButton("Настройки", callback_data='settings')
        ]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Отправляем сообщение с главным меню (не редактируем старое)
    message = None
    
    try:
        # Пытаемся удалить предыдущее сообщение, если оно существует
        if 'last_bot_message' in context.user_data:
            try:
                chat_id, message_id = context.user_data['last_bot_message']
                context.bot.delete_message(chat_id=chat_id, message_id=message_id)
                logger.info(f"Успешно удалено предыдущее сообщение бота: {message_id}")
                # Очищаем ссылку на удаленное сообщение
                del context.user_data['last_bot_message']
            except Exception as e:
                logger.error(f"Не удалось удалить предыдущее сообщение бота: {e}")
                # Очищаем ссылку на недоступное сообщение
                del context.user_data['last_bot_message']
        
        # Отправляем новое сообщение вместо редактирования
        if update.callback_query and update.callback_query.message:
            chat_id = update.callback_query.message.chat_id
            message = context.bot.send_message(
                chat_id=chat_id,
                text=menu_text,
                reply_markup=reply_markup
            )
        elif update.message:
            message = update.message.reply_text(
                text=menu_text,
                reply_markup=reply_markup
            )
        else:
            # Если ни один из вариантов не подходит, получаем chat_id из контекста
            chat_id = context.user_data.get('user_chat_id')
            if chat_id:
                message = context.bot.send_message(
                    chat_id=chat_id,
                    text=menu_text,
                    reply_markup=reply_markup
                )
        
        # Сохраняем данные сообщения для возможного удаления
        if message:
            context.user_data['last_bot_message'] = (message.chat_id, message.message_id)
            context.user_data['user_chat_id'] = message.chat_id
            logger.info(f"Показано главное меню, ID сообщения: {message.message_id}")
            
    except Exception as e:
        logger.error(f"Ошибка при отображении главного меню: {e}")
        # Пытаемся уведомить пользователя о проблеме
        try:
            if update.callback_query and update.callback_query.message:
                update.callback_query.message.reply_text("Произошла ошибка. Используйте /start для перезапуска.")
            elif update.message:
                update.message.reply_text("Произошла ошибка. Используйте /start для перезапуска.")
        except:
            pass


def button_callback(update: Update, context: CallbackContext) -> int:
    """Обработка нажатий на кнопки меню"""
    query = update.callback_query
    user_id = update.effective_user.id
    
    try:
        query.answer()
    except Exception as e:
        logger.error(f"Не удалось ответить на callback_query: {e}")
    
    data = query.data
    chat_id = query.message.chat_id
    
    # Сохраняем chat_id для последующего использования
    context.user_data['user_chat_id'] = chat_id
    
    logger.info(f"Обработка кнопки: {data} от пользователя {user_id}")
    
    if data == 'main_menu':
        show_main_menu(update, context)
        return ConversationHandler.END
    
    elif data == 'add_time':
        # Показываем кнопки быстрого добавления времени
        keyboard = [
            [
                InlineKeyboardButton("15 мин", callback_data='time_15'),
                InlineKeyboardButton("30 мин", callback_data='time_30')
            ],
            [
                InlineKeyboardButton("1 час", callback_data='time_60'),
                InlineKeyboardButton("2 часа", callback_data='time_120')
            ],
            [
                InlineKeyboardButton("Ввести вручную", callback_data='time_manual')
            ],
            [
                InlineKeyboardButton("« Назад", callback_data='main_menu')
            ]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        try:
            # Удаляем предыдущее сообщение и отправляем новое
            try:
                query.message.delete()
            except Exception as e:
                logger.error(f"Не удалось удалить сообщение: {e}")
                
            message = context.bot.send_message(
                chat_id=chat_id,
                text="Выберите время или введите вручную:",
                reply_markup=reply_markup
            )
            
            # Сохраняем ID сообщения
            if message:
                context.user_data['last_bot_message'] = (message.chat_id, message.message_id)
        except Exception as e:
            logger.error(f"Ошибка при отображении меню добавления времени: {e}")
            show_main_menu(update, context)
            return ConversationHandler.END
        
        return ADD_TIME
    
    elif data.startswith('time_'):
        if data == 'time_manual':
            try:
                # Удаляем предыдущее сообщение и отправляем новое
                try:
                    query.message.delete()
                except Exception as e:
                    logger.error(f"Не удалось удалить сообщение: {e}")
                    
                message = context.bot.send_message(
                    chat_id=chat_id,
                    text="Введите время в одном из форматов:\n"
                    "• 2ч 20м\n"
                    "• 140мин\n"
                    "• 2.33 (часы)"
                )
                
                # Сохраняем ID сообщения
                if message:
                    context.user_data['last_bot_message'] = (message.chat_id, message.message_id)
            except Exception as e:
                logger.error(f"Ошибка при отображении формата ввода времени: {e}")
                show_main_menu(update, context)
                return ConversationHandler.END
                
            return CONFIRM_TIME
        else:
            # Получаем минуты из data (например, time_15 -> 15 минут)
            minutes = int(data.split('_')[1])
            
            try:
                # Получаем данные пользователя
                user_data = db.get_user_data(user_id)
                if not user_data:
                    context.bot.send_message(
                        chat_id=chat_id,
                        text="Не удалось получить данные пользователя. Используйте /start для настройки."
                    )
                    return ConversationHandler.END
                    
                rate = user_data['rate']
                
                # Расчет заработка
                earnings = (minutes / 60) * rate
                
                # Предпросмотр добавления
                keyboard = [
                    [
                        InlineKeyboardButton("Подтвердить", callback_data=f'confirm_{minutes}'),
                        InlineKeyboardButton("Отмена", callback_data='add_time')
                    ]
                ]
                
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                # Удаляем предыдущее сообщение и отправляем новое
                try:
                    query.message.delete()
                except Exception as e:
                    logger.error(f"Не удалось удалить сообщение: {e}")
                    
                message = context.bot.send_message(
                    chat_id=chat_id,
                    text=f"Вы хотите добавить: {format_time(minutes)}\n"
                    f"Заработок: {format_money(earnings)}\n\n"
                    f"Подтвердите добавление:",
                    reply_markup=reply_markup
                )
                
                # Сохраняем ID сообщения
                if message:
                    context.user_data['last_bot_message'] = (message.chat_id, message.message_id)
            except Exception as e:
                logger.error(f"Ошибка при расчете заработка: {e}")
                context.bot.send_message(
                    chat_id=chat_id,
                    text="Произошла ошибка при расчете заработка. Пожалуйста, попробуйте снова."
                )
                show_main_menu(update, context)
                return ConversationHandler.END
                
            return CONFIRM_TIME
    
    elif data.startswith('confirm_'):
        # Получаем минуты из data (например, confirm_15 -> 15 минут)
        minutes = int(data.split('_')[1])
        
        try:
            # Добавляем запись в базу данных
            earnings = db.add_time_record(user_id, minutes)
            
            # Сначала пытаемся удалить сообщение с кнопками
            try:
                query.message.delete()
            except Exception as e:
                logger.error(f"Не удалось удалить сообщение с кнопками: {e}")
            
            # Отправляем новое сообщение с подтверждением
            message = query.message.reply_text(
                f"✅\nВремя добавлено: {format_time(minutes)}\n"
                f"Заработано: {format_money(earnings)}"
            )
            
            # Планируем удаление сообщения
            context.job_queue.run_once(
                delete_message_later, 
                5, 
                context=(message.chat_id, message.message_id)
            )
            
            # Показываем обновленное главное меню
            show_main_menu(update, context)
        except Exception as e:
            logger.error(f"Ошибка при обработке подтверждения времени: {e}")
            try:
                query.message.reply_text(
                    "Произошла ошибка при добавлении времени. Пожалуйста, попробуйте снова."
                )
            except:
                pass
        
        return ConversationHandler.END
    
    elif data == 'progress':
        # Получаем прогресс пользователя
        progress = db.get_progress(user_id)
        
        if progress:
            # Создаем диаграмму прогресса
            chart_buf = create_progress_chart(progress)
            
            # Отправляем диаграмму как фото
            query.message.reply_photo(
                photo=chart_buf,
                caption=f"Ваш прогресс: {progress['percent']}% от цели"
            )
            
            # Возвращаемся в главное меню
            show_main_menu(update, context)
        else:
            query.edit_message_text(
                "Произошла ошибка при получении данных о прогрессе."
            )
    
    elif data == 'history':
        # Получаем историю записей
        try:
            records = db.get_time_history(user_id)
            
            if records:
                message_text = "📋 История:\n\n"
                for record in records:
                    message_text += format_time_record(record) + "\n"
            else:
                message_text = "История пуста."
            
            keyboard = [
                [InlineKeyboardButton("« Назад", callback_data='main_menu')]
            ]
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            # Удаляем предыдущее сообщение и отправляем новое
            try:
                query.message.delete()
            except Exception as e:
                logger.error(f"Не удалось удалить сообщение: {e}")
                
            message = context.bot.send_message(
                chat_id=chat_id,
                text=message_text,
                reply_markup=reply_markup
            )
            
            # Сохраняем ID сообщения
            if message:
                context.user_data['last_bot_message'] = (message.chat_id, message.message_id)
        except Exception as e:
            logger.error(f"Ошибка при получении истории: {e}")
            context.bot.send_message(
                chat_id=chat_id,
                text="Произошла ошибка при получении истории. Пожалуйста, попробуйте позже."
            )
            show_main_menu(update, context)
    
    elif data == 'settings':
        # Меню настроек
        try:
            keyboard = [
                [
                    InlineKeyboardButton("Изменить ставку", callback_data='change_rate'),
                    InlineKeyboardButton("Изменить цель", callback_data='change_goal')
                ],
                [
                    InlineKeyboardButton("Уведомления", callback_data='notifications'),
                    InlineKeyboardButton("Сбросить прогресс", callback_data='reset_goal')
                ],
                [
                    InlineKeyboardButton("« Назад", callback_data='main_menu')
                ]
            ]
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            # Удаляем предыдущее сообщение и отправляем новое
            try:
                query.message.delete()
            except Exception as e:
                logger.error(f"Не удалось удалить сообщение: {e}")
                
            message = context.bot.send_message(
                chat_id=chat_id,
                text="Настройки:",
                reply_markup=reply_markup
            )
            
            # Сохраняем ID сообщения
            if message:
                context.user_data['last_bot_message'] = (message.chat_id, message.message_id)
        except Exception as e:
            logger.error(f"Ошибка при отображении настроек: {e}")
            show_main_menu(update, context)
    
    elif data == 'change_rate':
        # Запрос новой ставки
        try:
            # Устанавливаем состояние в контексте пользователя
            context.user_data['state'] = CHANGE_RATE
            logger.info(f"Установлено состояние CHANGE_RATE для пользователя {user_id}")
            
            # Удаляем предыдущее сообщение и отправляем новое
            try:
                query.message.delete()
            except Exception as e:
                logger.error(f"Не удалось удалить сообщение: {e}")
                
            message = context.bot.send_message(
                chat_id=chat_id,
                text="Введите новую почасовую ставку:"
            )
            
            # Сохраняем ID сообщения
            if message:
                context.user_data['last_bot_message'] = (message.chat_id, message.message_id)
                
            return CHANGE_RATE
        except Exception as e:
            logger.error(f"Ошибка при запросе новой ставки: {e}")
            show_main_menu(update, context)
            return ConversationHandler.END
    
    elif data == 'change_goal':
        # Запрос новой цели
        try:
            # Устанавливаем состояние в контексте пользователя
            context.user_data['state'] = CHANGE_GOAL
            logger.info(f"Установлено состояние CHANGE_GOAL для пользователя {user_id}")
            
            # Удаляем предыдущее сообщение и отправляем новое
            try:
                query.message.delete()
            except Exception as e:
                logger.error(f"Не удалось удалить сообщение: {e}")
                
            message = context.bot.send_message(
                chat_id=chat_id,
                text="Введите новую цель заработка:"
            )
            
            # Сохраняем ID сообщения
            if message:
                context.user_data['last_bot_message'] = (message.chat_id, message.message_id)
                
            return CHANGE_GOAL
        except Exception as e:
            logger.error(f"Ошибка при запросе новой цели: {e}")
            show_main_menu(update, context)
            return ConversationHandler.END
    
    elif data == 'notifications':
        # Настройка уведомлений
        try:
            keyboard = [
                [
                    InlineKeyboardButton("Каждый час", callback_data='notify_hour'),
                    InlineKeyboardButton("Ежедневно", callback_data='notify_day')
                ],
                [
                    InlineKeyboardButton("Еженедельно", callback_data='notify_week'),
                    InlineKeyboardButton("Отключить", callback_data='notify_off')
                ],
                [
                    InlineKeyboardButton("« Назад", callback_data='settings')
                ]
            ]
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            # Удаляем предыдущее сообщение и отправляем новое
            try:
                query.message.delete()
            except Exception as e:
                logger.error(f"Не удалось удалить сообщение: {e}")
                
            message = context.bot.send_message(
                chat_id=chat_id,
                text="Настройка уведомлений:",
                reply_markup=reply_markup
            )
            
            # Сохраняем ID сообщения
            if message:
                context.user_data['last_bot_message'] = (message.chat_id, message.message_id)
        except Exception as e:
            logger.error(f"Ошибка при отображении настроек уведомлений: {e}")
            show_main_menu(update, context)
    
    elif data.startswith('notify_'):
        freq = data.split('_')[1]
        
        try:
            if freq == 'off':
                # Удаляем все задачи для пользователя
                for job in scheduler.get_jobs():
                    if job.id == f"notify_{user_id}":
                        job.remove()
                
                # Обновляем настройку в базе данных
                db.update_notify_freq(user_id, 'off')
                
                freq_text = "отключены"
            else:
                # Настраиваем уведомления с указанной частотой
                setup_notification(context, user_id, freq)
                
                # Обновляем настройку в базе данных
                db.update_notify_freq(user_id, freq)
                
                freq_text = {
                    'hour': 'ежечасно',
                    'day': 'ежедневно',
                    'week': 'еженедельно'
                }.get(freq, freq)
            
            # Удаляем предыдущее сообщение и отправляем новое
            try:
                query.message.delete()
            except Exception as e:
                logger.error(f"Не удалось удалить сообщение: {e}")
            
            keyboard = [
                [InlineKeyboardButton("« Назад", callback_data='settings')]
            ]
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            message = context.bot.send_message(
                chat_id=chat_id,
                text=f"Уведомления будут приходить {freq_text}.",
                reply_markup=reply_markup
            )
            
            # Сохраняем ID сообщения
            if message:
                context.user_data['last_bot_message'] = (message.chat_id, message.message_id)
        except Exception as e:
            logger.error(f"Ошибка при настройке уведомлений: {e}")
            context.bot.send_message(
                chat_id=chat_id,
                text="Произошла ошибка при настройке уведомлений. Пожалуйста, попробуйте позже."
            )
            show_main_menu(update, context)
    
    elif data.startswith('timer_confirm_'):
        # Получаем минуты из data
        minutes = int(data.split('_')[2])
        
        try:
            # Добавляем запись в базу данных
            earnings = db.add_time_record(user_id, minutes)
            
            # Сначала пытаемся удалить сообщение с кнопками
            try:
                query.message.delete()
            except Exception as e:
                logger.error(f"Не удалось удалить сообщение с кнопками: {e}")
            
            # Отправляем новое сообщение с подтверждением
            message = query.message.reply_text(
                f"✅ Время из таймера добавлено: {format_time(minutes)}\n"
                f"Заработано: {format_money(earnings)}"
            )
            
            # Планируем удаление сообщения
            context.job_queue.run_once(
                delete_message_later, 
                5, 
                context=(message.chat_id, message.message_id)
            )
            
            # Обновляем главное меню
            show_main_menu(update, context)
        except Exception as e:
            logger.error(f"Ошибка при обработке подтверждения таймера: {e}")
            try:
                query.message.reply_text(
                    "Произошла ошибка при добавлении времени. Пожалуйста, попробуйте снова."
                )
            except:
                pass
            
        return ConversationHandler.END
    
    elif data.startswith('timer_group_confirm_'):
        # Получаем общее количество минут из data
        minutes = int(data.split('_')[3])
        
        try:
            # Добавляем запись в базу данных
            earnings = db.add_time_record(user_id, minutes)
            
            # Сначала пытаемся удалить сообщение с кнопками
            try:
                query.message.delete()
            except Exception as e:
                logger.error(f"Не удалось удалить сообщение с кнопками: {e}")
            
            # Отправляем новое сообщение с подтверждением
            message = query.message.reply_text(
                f"✅ Добавлено общее время из таймеров: {format_time(minutes)}\n"
                f"Заработано: {format_money(earnings)}"
            )
            
            # Планируем удаление сообщения
            context.job_queue.run_once(
                delete_message_later, 
                5, 
                context=(message.chat_id, message.message_id)
            )
            
            # Обновляем главное меню
            show_main_menu(update, context)
        except Exception as e:
            logger.error(f"Ошибка при обработке подтверждения группы таймеров: {e}")
            try:
                query.message.reply_text(
                    "Произошла ошибка при добавлении времени. Пожалуйста, попробуйте снова."
                )
            except:
                pass
            
        return ConversationHandler.END
    
    elif data == 'timer_cancel':
        try:
            # Сначала пытаемся удалить сообщение с кнопками
            try:
                query.message.delete()
            except Exception as e:
                logger.error(f"Не удалось удалить сообщение с кнопками: {e}")
            
            # Отправляем новое сообщение с отменой
            message = query.message.reply_text("❌ Добавление времени отменено.")
            
            # Планируем удаление сообщения
            context.job_queue.run_once(
                delete_message_later, 
                5, 
                context=(message.chat_id, message.message_id)
            )
            
            # Обновляем главное меню
            show_main_menu(update, context)
        except Exception as e:
            logger.error(f"Ошибка при обработке отмены таймера: {e}")
        
        return ConversationHandler.END
    
    elif data == 'reset_goal':
        # Предупреждение о сбросе цели
        try:
            # Получаем данные о прогрессе
            progress = db.get_progress(user_id)
            
            if not progress:
                context.bot.send_message(
                    chat_id=chat_id,
                    text="Не удалось получить данные о прогрессе. Используйте /start для настройки."
                )
                return ConversationHandler.END
            
            # Формируем сообщение с предупреждением
            warning_text = (
                f"⚠️ ВНИМАНИЕ! ⚠️\n\n"
                f"Вы собираетесь сбросить свой прогресс.\n"
                f"Текущий заработок: {format_money(progress['earned'])}\n\n"
                f"Вся история записей останется, но счётчик заработка будет обнулён.\n"
                f"Это действие нельзя отменить."
            )
            
            keyboard = [
                [
                    InlineKeyboardButton("Отмена", callback_data='settings'),
                    InlineKeyboardButton("Да, сбросить", callback_data='reset_goal_confirm')
                ]
            ]
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            # Удаляем предыдущее сообщение и отправляем новое
            try:
                query.message.delete()
            except Exception as e:
                logger.error(f"Не удалось удалить сообщение: {e}")
                
            message = context.bot.send_message(
                chat_id=chat_id,
                text=warning_text,
                reply_markup=reply_markup
            )
            
            # Сохраняем ID сообщения
            if message:
                context.user_data['last_bot_message'] = (message.chat_id, message.message_id)
                
            return RESET_GOAL_CONFIRM
        except Exception as e:
            logger.error(f"Ошибка при запросе сброса цели: {e}")
            context.bot.send_message(
                chat_id=chat_id,
                text="Произошла ошибка. Пожалуйста, попробуйте позже."
            )
            show_main_menu(update, context)
            return ConversationHandler.END
            
    elif data == 'reset_goal_confirm':
        # Выполняем сброс прогресса
        try:
            # Удаляем все записи о времени для пользователя и сбрасываем прогресс
            # Получаем текущие данные пользователя
            user_data = db.get_user_data(user_id)
            if not user_data:
                context.bot.send_message(
                    chat_id=chat_id,
                    text="Не удалось получить данные пользователя. Используйте /start для настройки."
                )
                show_main_menu(update, context)
                return ConversationHandler.END
                
            # Функция для сброса данных
            def delete_user_time_records(user_id):
                """Удаляет все записи о времени для заданного пользователя"""
                try:
                    logger.info(f"Удаление всех записей времени для пользователя {user_id}")
                    # Открываем соединение с базой данных
                    conn = sqlite3.connect("time_tracker.db")
                    cursor = conn.cursor()
                    
                    # Удаляем все записи времени для пользователя
                    cursor.execute("DELETE FROM time_records WHERE user_id = ?", (user_id,))
                    
                    # Сбрасываем earned до 0
                    cursor.execute("UPDATE users SET earned = 0 WHERE user_id = ?", (user_id,))
                    
                    # Сохраняем изменения
                    conn.commit()
                    conn.close()
                    
                    logger.info(f"Удалены все записи времени для пользователя {user_id}")
                    return True
                except Exception as e:
                    logger.error(f"Ошибка при удалении записей времени: {e}")
                    return False
            
            # Сбрасываем данные
            if delete_user_time_records(user_id):
                # Удаляем предыдущее сообщение
                try:
                    query.message.delete()
                except Exception as e:
                    logger.error(f"Не удалось удалить сообщение: {e}")
                
                # Отправляем подтверждение со ссылкой на установку новой цели
                keyboard = [
                    [InlineKeyboardButton("Установить цель заработка", callback_data='change_goal')],
                    [InlineKeyboardButton("Вернуться в меню", callback_data='main_menu')]
                ]
                
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                message = context.bot.send_message(
                    chat_id=chat_id,
                    text="✅ Все данные успешно сброшены!\n"
                         "История записей и счётчик заработка удалены.\n\n"
                         "Желаете установить новую цель заработка?",
                    reply_markup=reply_markup
                )
                
                # Сохраняем ID сообщения
                if message:
                    context.user_data['last_bot_message'] = (message.chat_id, message.message_id)
            else:
                context.bot.send_message(
                    chat_id=chat_id,
                    text="❌ Произошла ошибка при сбросе данных. Пожалуйста, попробуйте позже."
                )
                show_main_menu(update, context)
        except Exception as e:
            logger.error(f"Ошибка при сбросе данных: {e}")
            context.bot.send_message(
                chat_id=chat_id,
                text="Произошла ошибка. Пожалуйста, попробуйте позже."
            )
            show_main_menu(update, context)
            
        return ConversationHandler.END
    
    return ConversationHandler.END


def change_rate_input(update: Update, context: CallbackContext) -> int:
    """Обработка ввода новой ставки"""
    user_id = update.effective_user.id
    user_text = update.message.text
    
    # Сохраняем текущее состояние в контексте пользователя
    context.user_data['state'] = CHANGE_RATE
    
    logger.info(f"Обработка ввода новой ставки от пользователя {user_id}: '{user_text}'")
    
    try:
        # Очищаем ввод от лишних символов
        rate_text = user_text.replace('₽', '').replace('р', '').replace('руб', '')
        rate_text = rate_text.replace(',', '.').strip()
        
        logger.info(f"Очищенный текст ставки: '{rate_text}'")
        
        rate = float(rate_text)
        
        # Обновляем ставку в базе данных
        logger.info(f"Попытка обновить ставку для пользователя {user_id} на {rate}")
        db.update_rate(user_id, rate)
        # Не проверяем возвращаемое значение, так как метод всегда возвращает None
        # но фактически обновляет ставку как видно из логов
        logger.info(f"Ставка успешно обновлена для пользователя {user_id}: {rate}")
        
        # Удаляем предыдущее сообщение с запросом и сообщение пользователя
        try:
            logger.info("Попытка удалить сообщения")
            if hasattr(update, 'message') and update.message:
                update.message.delete()
                logger.info("Сообщение пользователя удалено")
            
            if 'last_bot_message' in context.user_data:
                chat_id, message_id = context.user_data['last_bot_message']
                context.bot.delete_message(chat_id=chat_id, message_id=message_id)
                logger.info(f"Сообщение бота {message_id} удалено")
                del context.user_data['last_bot_message']
        except Exception as e:
            logger.error(f"Ошибка при удалении сообщений: {e}")
        
        # Отправляем подтверждение
        logger.info("Отправка подтверждения")
        message = update.message.reply_text(f"✅ Ставка успешно обновлена: {rate:.0f}₽")
        
        # Планируем удаление сообщения
        context.job_queue.run_once(
            delete_message_later, 
            5, 
            context=(message.chat_id, message.message_id)
        )
        
        # Очищаем состояние пользователя
        if 'state' in context.user_data:
            del context.user_data['state']
            logger.info("Состояние пользователя очищено")
        
        # Показываем главное меню
        logger.info("Показываем главное меню")
        show_main_menu(update, context)
        
        return ConversationHandler.END
    
    except ValueError as e:
        logger.error(f"Ошибка преобразования ставки: {e}")
        update.message.reply_text(
            "Пожалуйста, введите корректное числовое значение для почасовой ставки.\n"
            "Например: 500 или 500₽"
        )
        
        return CHANGE_RATE
    except Exception as e:
        logger.error(f"Общая ошибка при обновлении ставки: {e}")
        update.message.reply_text(
            "Произошла ошибка при обработке вашего запроса. Пожалуйста, попробуйте позже."
        )
        show_main_menu(update, context)
        return ConversationHandler.END


def change_goal_input(update: Update, context: CallbackContext) -> int:
    """Обработка ввода новой цели"""
    user_id = update.effective_user.id
    user_text = update.message.text
    
    # Сохраняем текущее состояние в контексте пользователя
    context.user_data['state'] = CHANGE_GOAL
    
    logger.info(f"Обработка ввода новой цели от пользователя {user_id}: '{user_text}'")
    
    try:
        # Очищаем ввод от лишних символов
        goal_text = user_text.replace('₽', '').replace('р', '').replace('руб', '')
        goal_text = goal_text.replace(',', '.').strip()
        
        logger.info(f"Очищенный текст цели: '{goal_text}'")
        
        goal = float(goal_text)
        
        # Обновляем цель в базе данных
        logger.info(f"Попытка обновить цель для пользователя {user_id} на {goal}")
        db.update_goal(user_id, goal)
        logger.info(f"Цель успешно обновлена для пользователя {user_id}: {goal}")
        
        # Удаляем предыдущее сообщение с запросом и сообщение пользователя
        try:
            logger.info("Попытка удалить сообщения")
            if hasattr(update, 'message') and update.message:
                update.message.delete()
                logger.info("Сообщение пользователя удалено")
            
            if 'last_bot_message' in context.user_data:
                chat_id, message_id = context.user_data['last_bot_message']
                context.bot.delete_message(chat_id=chat_id, message_id=message_id)
                logger.info(f"Сообщение бота {message_id} удалено")
                del context.user_data['last_bot_message']
        except Exception as e:
            logger.error(f"Ошибка при удалении сообщений: {e}")
        
        # Отправляем подтверждение и сразу показываем главное меню
        logger.info("Отправка подтверждения")
        message = update.message.reply_text(f"✅ Цель успешно обновлена: {goal:.0f}₽")
        
        # Планируем удаление сообщения
        context.job_queue.run_once(
            delete_message_later, 
            5, 
            context=(message.chat_id, message.message_id)
        )
        
        # Очищаем состояние пользователя
        if 'state' in context.user_data:
            del context.user_data['state']
            logger.info("Состояние пользователя очищено")
        
        # Показываем главное меню
        logger.info("Показываем главное меню")
        show_main_menu(update, context)
        
        return ConversationHandler.END
    
    except ValueError as e:
        logger.error(f"Ошибка преобразования цели: {e}")
        update.message.reply_text(
            "Пожалуйста, введите корректное числовое значение для цели заработка.\n"
            "Например: 50000 или 50000₽"
        )
        
        return CHANGE_GOAL
    except Exception as e:
        logger.error(f"Общая ошибка при обновлении цели: {e}")
        update.message.reply_text(
            "Произошла ошибка при обработке вашего запроса. Пожалуйста, попробуйте позже."
        )
        show_main_menu(update, context)
        return ConversationHandler.END


def manual_time_input(update: Update, context: CallbackContext) -> int:
    """Обработка ручного ввода времени"""
    user_id = update.effective_user.id
    time_input = update.message.text.strip()
    
    # Сохраняем текущее состояние в контексте пользователя
    context.user_data['state'] = CONFIRM_TIME
    
    # Парсим ввод времени
    minutes = parse_time_input(time_input)
    
    if minutes is None:
        send_message_with_auto_delete(
            update, context,
            "Не удалось распознать формат времени. Попробуйте еще раз.\n"
            "Примеры форматов:\n"
            "• 2ч 20м\n"
            "• 140мин\n"
            "• 2.33 (часы)",
            delete_seconds=10
        )
        return CONFIRM_TIME
    
    # Получаем данные пользователя
    user_data = db.get_user_data(user_id)
    rate = user_data['rate']
    
    # Расчет заработка
    earnings = (minutes / 60) * rate
    
    # Добавляем запись в базу данных
    db.add_time_record(user_id, minutes)
    
    # Удаляем сообщение пользователя
    try:
        update.message.delete()
    except Exception as e:
        logger.error(f"Не удалось удалить сообщение пользователя: {e}")
    
    # Отправляем подтверждение с автоудалением
    send_message_with_auto_delete(
        update, context,
        f"✅\nВремя добавлено: {format_time(minutes)}\n"
        f"Заработано: {format_money(earnings)}",
        delete_seconds=5
    )
    
    # Показываем главное меню
    show_main_menu(update, context)
    
    return ConversationHandler.END


def rate_command(update: Update, context: CallbackContext) -> int:
    """Обработчик команды /rate"""
    update.message.reply_text(
        "Введите новую почасовую ставку:"
    )
    
    return CHANGE_RATE


def goal_command(update: Update, context: CallbackContext) -> int:
    """Обработчик команды /goal"""
    update.message.reply_text(
        "Введите новую цель заработка:"
    )
    
    return CHANGE_GOAL


def notify_command(update: Update, context: CallbackContext) -> None:
    """Обработчик команды /notify"""
    user_id = update.effective_user.id
    
    if not context.args:
        update.message.reply_text(
            "Использование: /notify [hour/day/week/off]"
        )
        return
    
    freq = context.args[0].lower()
    
    if freq not in ['hour', 'day', 'week', 'off']:
        update.message.reply_text(
            "Неверный параметр частоты. Используйте: hour, day, week или off."
        )
        return
    
    if freq == 'off':
        # Удаляем все задачи для пользователя
        for job in scheduler.get_jobs():
            if job.id == f"notify_{user_id}":
                job.remove()
        
        # Обновляем настройку в базе данных
        db.update_notify_freq(user_id, 'off')
        
        update.message.reply_text(
            "Уведомления отключены."
        )
    else:
        # Настраиваем уведомления с указанной частотой
        setup_notification(context, user_id, freq)
        
        # Обновляем настройку в базе данных
        db.update_notify_freq(user_id, freq)
        
        freq_text = {
            'hour': 'ежечасно',
            'day': 'ежедневно',
            'week': 'еженедельно'
        }.get(freq, freq)
        
        update.message.reply_text(
            f"Уведомления настроены на {freq_text}."
        )


def help_command(update: Update, context: CallbackContext) -> None:
    """Отправка справки по командам"""
    update.message.reply_text(
        "Список доступных команд:\n\n"
        "/start - Начать использование бота\n"
        "/rate - Изменить почасовую ставку\n"
        "/goal - Установить новую цель\n"
        "/notify [hour/day/week/off] - Управление уведомлениями\n"
        "/cancel - Отменить текущее действие\n"
        "/help - Показать справку"
    )


def cancel_command(update: Update, context: CallbackContext) -> int:
    """Отмена текущего действия"""
    update.message.reply_text(
        "Действие отменено. Возвращаемся в главное меню."
    )
    
    # Показываем главное меню
    show_main_menu(update, context)
    
    return ConversationHandler.END


def error_handler(update, context):
    """Обработчик ошибок"""
    try:
        logger.error(f"Обновление {update} вызвало ошибку {context.error}")
        
        # Отправка сообщения пользователю
        if update and hasattr(update, 'effective_chat') and update.effective_chat:
            text = "Произошла ошибка при обработке вашего запроса. Пожалуйста, попробуйте позже."
            context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=text
            )
    except Exception as e:
        logger.error(f"Ошибка в обработчике ошибок: {e}")


def process_timer_message(update: Update, context: CallbackContext) -> None:
    """Обработка сообщений с таймером"""
    message_text = update.message.text
    user_id = update.effective_user.id
    
    # Проверяем, не находится ли пользователь в одном из состояний ввода
    if context.user_data.get('state') in [CHANGE_RATE, CHANGE_GOAL, RATE, GOAL, CONFIRM_TIME]:
        state = context.user_data.get('state')
        logger.info(f"Перенаправляем сообщение в соответствующий обработчик для состояния {state}")
        
        # Перенаправляем сообщение в соответствующий обработчик в зависимости от состояния
        try:
            if state == CHANGE_RATE:
                return change_rate_input(update, context)
            elif state == CHANGE_GOAL:
                return change_goal_input(update, context)
            elif state == RATE:
                return rate_input(update, context)
            elif state == GOAL:
                return goal_input(update, context)
            elif state == CONFIRM_TIME:
                return manual_time_input(update, context)
        except Exception as e:
            logger.error(f"Ошибка при перенаправлении сообщения в обработчик для состояния {state}: {e}")
        
        # Для безопасности логируем, но не продолжаем обработку таймера
        logger.info(f"Пропускаем обработку сообщения как таймера, т.к. пользователь в состоянии ввода: {state}")
        return
    
    logger.info(f"Получено сообщение с возможным таймером от пользователя {user_id}: '{message_text}'")
    
    # Проверяем, есть ли в сообщении текст о таймере
    if "таймер остановлен" in message_text.lower() and "затрачено" in message_text.lower():
        # Парсим время из сообщения
        minutes = parse_timer_message(message_text)
        
        if minutes:
            logger.info(f"Распознано время из сообщения таймера: {minutes} минут")
            
            # Проверяем, зарегистрирован ли пользователь
            if not db.user_exists(user_id):
                try:
                    update.message.reply_text(
                        "Для учета времени необходимо сначала настроить ставку и цель с помощью команды /start"
                    )
                except Exception as e:
                    logger.error(f"Ошибка при отправке сообщения о регистрации: {e}")
                return
            
            # Проверка на наличие буфера таймеров в контексте
            if 'timer_buffer' not in context.user_data:
                context.user_data['timer_buffer'] = []
                context.user_data['forwarded_messages'] = []
            
            # Добавляем текущее время в буфер
            context.user_data['timer_buffer'].append(minutes)
            
            # Добавляем сообщение в список пересланных
            try:
                if update.message.forward_date:
                    # Это пересланное сообщение
                    context.user_data['forwarded_messages'].append(update.message.message_id)
                    logger.info(f"Добавлено пересланное сообщение с таймером: {minutes} минут")
                    
                    # Планируем обработку группы таймеров через 2 секунды после последнего сообщения
                    if 'timer_group_job' in context.user_data:
                        context.user_data['timer_group_job'].schedule_removal()
                    
                    # Получаем копию буфера таймеров для передачи в задачу
                    timer_buffer = context.user_data['timer_buffer'].copy()
                    
                    context.user_data['timer_group_job'] = context.job_queue.run_once(
                        process_grouped_timers, 
                        2, 
                        context=(user_id, update.message.chat_id, timer_buffer)
                    )
                    
                    # Очищаем буфер в контексте пользователя после передачи в задачу
                    context.user_data['timer_buffer'] = []
                    context.user_data['forwarded_messages'] = []
                else:
                    # Это обычное сообщение с таймером, обрабатываем сразу
                    process_single_timer(update, context, minutes)
            except Exception as e:
                logger.error(f"Ошибка при обработке сообщения с таймером: {e}")
                # В случае ошибки всё равно пытаемся обработать одиночный таймер
                process_single_timer(update, context, minutes)


def process_grouped_timers(context: CallbackContext):
    """Обработка группы пересланных таймеров"""
    try:
        # Получаем данные из контекста задачи
        user_id, chat_id, timer_buffer = context.job.context
        
        logger.info(f"Обработка группы таймеров для пользователя {user_id}")
        
        if not timer_buffer:
            logger.info(f"Буфер таймеров пуст для пользователя {user_id}")
            return
        
        # Если только один таймер, обрабатываем его как одиночный
        if len(timer_buffer) == 1:
            # Создаем псевдо-апдейт для совместимости
            class PseudoUpdate:
                def __init__(self, user_id, chat_id):
                    self.effective_user = type('obj', (object,), {'id': user_id})
                    self.message = type('obj', (object,), {'chat_id': chat_id, 'reply_text': lambda text, **kwargs: None})
                    
            pseudo_update = PseudoUpdate(user_id, chat_id)
            process_single_timer(pseudo_update, context, timer_buffer[0])
            return
        
        # Получаем данные пользователя
        user_data = db.get_user_data(user_id)
        if not user_data:
            context.bot.send_message(
                chat_id=chat_id,
                text="Не удалось получить данные пользователя. Используйте /start для настройки."
            )
            return
                
        rate = user_data['rate']
        
        # Формируем текст о группе таймеров
        total_minutes = sum(timer_buffer)
        total_earnings = (total_minutes / 60) * rate
        
        message_text = "Я обнаружил несколько сообщений с таймерами:\n\n"
        
        for i, minutes in enumerate(timer_buffer):
            earnings = (minutes / 60) * rate
            message_text += f"{i+1}) {format_time(minutes)} ({format_money(earnings)})\n"
        
        message_text += f"\nИтого: {format_time(total_minutes)} ({format_money(total_earnings)})"
        
        # Создаем клавиатуру с кнопками для подтверждения
        keyboard = [
            [
                InlineKeyboardButton("Добавить всё", callback_data=f'timer_group_confirm_{total_minutes}'),
                InlineKeyboardButton("Отмена", callback_data='timer_cancel')
            ]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Отправляем сообщение с подтверждением
        message = context.bot.send_message(
            chat_id=chat_id,
            text=message_text,
            reply_markup=reply_markup
        )
        
        # Сохраняем ID сообщения и общее время для использования при подтверждении
        if message and hasattr(context, 'user_data') and context.user_data is not None:
            context.user_data['last_bot_message'] = (message.chat_id, message.message_id)
            context.user_data['timer_group_minutes'] = total_minutes
            logger.info(f"Отправлено сообщение с подтверждением группы таймеров, ID: {message.message_id}")
    
    except Exception as e:
        logger.error(f"Ошибка при обработке группы таймеров: {e}")
        try:
            if chat_id:
                context.bot.send_message(
                    chat_id=chat_id,
                    text="Произошла ошибка при обработке таймеров. Пожалуйста, попробуйте позже."
                )
        except:
            logger.error("Не удалось отправить сообщение об ошибке")


def process_single_timer(update, context, minutes):
    """Обработка одиночного таймера"""
    user_id = update.effective_user.id
    
    try:
        # Получаем данные пользователя
        user_data = db.get_user_data(user_id)
        rate = user_data['rate']
        
        # Расчет заработка
        earnings = (minutes / 60) * rate
        
        # Создаем клавиатуру с кнопками для подтверждения
        keyboard = [
            [
                InlineKeyboardButton("Да, добавить", callback_data=f'timer_confirm_{minutes}'),
                InlineKeyboardButton("Нет, отмена", callback_data='timer_cancel')
            ]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Пытаемся удалить исходное сообщение
        try:
            # Сохраняем копию текста сообщения для логов
            original_msg = getattr(update.message, 'text', '')[:100]
            original_msg = original_msg + "..." if len(original_msg) > 100 else original_msg
            
            if hasattr(update.message, 'delete'):
                update.message.delete()
                logger.info(f"Удалено исходное сообщение с таймером: '{original_msg}'")
        except Exception as e:
            logger.error(f"Не удалось удалить исходное сообщение с таймером: {e}")
        
        # Отправляем новое сообщение с подтверждением
        message = None
        try:
            if hasattr(update.message, 'reply_text'):
                message = update.message.reply_text(
                    f"Обнаружено время: {format_time(minutes)}\n"
                    f"Заработок: {format_money(earnings)}\n\n"
                    f"Добавить это время в учет?",
                    reply_markup=reply_markup
                )
            else:
                # Для псевдо-апдейта
                message = context.bot.send_message(
                    chat_id=update.message.chat_id,
                    text=f"Обнаружено время: {format_time(minutes)}\n"
                    f"Заработок: {format_money(earnings)}\n\n"
                    f"Добавить это время в учет?",
                    reply_markup=reply_markup
                )
            
            # Если сообщение успешно отправлено, сохраняем его для возможного удаления
            if message:
                context.user_data['last_bot_message'] = (message.chat_id, message.message_id)
                logger.info(f"Отправлено сообщение с подтверждением, ID: {message.message_id}")
        except Exception as e:
            logger.error(f"Ошибка при отправке сообщения с подтверждением: {e}")
        
        # Сохраняем минуты в контексте для использования при подтверждении
        context.user_data['timer_minutes'] = minutes
        
    except Exception as e:
        logger.error(f"Общая ошибка при обработке таймера: {e}")
        try:
            if hasattr(update.message, 'reply_text'):
                update.message.reply_text(
                    "Произошла ошибка при обработке таймера. Пожалуйста, попробуйте позже."
                )
        except:
            pass


def delete_message_later(context: CallbackContext):
    """Удаляет сообщение бота через некоторое время"""
    chat_id, message_id = context.job.context
    try:
        context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception as e:
        logger.error(f"Ошибка при удалении сообщения: {e}")


def send_message_with_auto_delete(update, context, text, reply_markup=None, delete_seconds=60):
    """Отправляет сообщение и планирует его автоудаление"""
    message = None
    try:
        if update.callback_query:
            # Редактируем существующее сообщение
            try:
                message = update.callback_query.edit_message_text(
                    text=text, reply_markup=reply_markup
                )
            except Exception as e:
                logger.error(f"Не удалось отредактировать сообщение: {e}")
                # Если не удалось отредактировать, отправляем новое
                message = update.callback_query.message.reply_text(
                    text=text, reply_markup=reply_markup
                )
        else:
            # Отправляем новое сообщение
            message = update.message.reply_text(
                text=text, reply_markup=reply_markup
            )
        
        # Планируем удаление сообщения
        if delete_seconds > 0 and message:
            context.job_queue.run_once(
                delete_message_later, 
                delete_seconds, 
                context=(message.chat_id, message.message_id)
            )
        
        return message
    except Exception as e:
        logger.error(f"Ошибка в функции send_message_with_auto_delete: {e}")
        return None


def delete_message_if_exists(update, context):
    """Пытается удалить предыдущее сообщение, если оно существует"""
    if hasattr(update, 'message') and update.message:
        try:
            # Пытаемся удалить сообщение пользователя
            update.message.delete()
        except Exception as e:
            logger.error(f"Не удалось удалить сообщение пользователя: {e}")
    
    if 'last_bot_message' in context.user_data:
        try:
            chat_id, message_id = context.user_data['last_bot_message']
            context.bot.delete_message(chat_id=chat_id, message_id=message_id)
        except Exception as e:
            logger.error(f"Не удалось удалить предыдущее сообщение бота: {e}")
            # Удаляем ссылку на недоступное сообщение
            if 'last_bot_message' in context.user_data:
                del context.user_data['last_bot_message']


def main() -> None:
    """Запуск бота"""
    # Получаем токен из переменной окружения или файла
    token = os.environ.get("TELEGRAM_TOKEN")
    
    if not token:
        logging.error("Требуется токен телеграм-бота. Укажите TELEGRAM_TOKEN в переменных окружения.")
        return
    
    # Создаем Updater
    updater = Updater(token)
    
    # Получаем диспетчер для регистрации обработчиков
    dispatcher = updater.dispatcher
    
    # Добавляем глобальный обработчик ошибок
    dispatcher.add_error_handler(error_handler)
    
    # Создаем обработчик разговора для начальной настройки
    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            CommandHandler("rate", rate_command),
            CommandHandler("goal", goal_command)
        ],
        states={
            RATE: [MessageHandler(Filters.text & ~Filters.command, rate_input)],
            GOAL: [MessageHandler(Filters.text & ~Filters.command, goal_input)],
            ADD_TIME: [CallbackQueryHandler(button_callback)],
            CONFIRM_TIME: [
                CallbackQueryHandler(button_callback),
                MessageHandler(Filters.text & ~Filters.command, manual_time_input)
            ],
            CHANGE_RATE: [
                CallbackQueryHandler(button_callback),
                MessageHandler(Filters.text & ~Filters.command, change_rate_input)
            ],
            CHANGE_GOAL: [
                CallbackQueryHandler(button_callback),
                MessageHandler(Filters.text & ~Filters.command, change_goal_input)
            ],
            RESET_GOAL_CONFIRM: [CallbackQueryHandler(button_callback)],
        },
        fallbacks=[
            CommandHandler("start", start),
            CommandHandler("help", help_command),
            CommandHandler("cancel", cancel_command)
        ],
        allow_reentry=True
    )
    
    # ВАЖНО: сначала регистрируем ConversationHandler, затем остальные обработчики
    dispatcher.add_handler(conv_handler)
    
    # Добавляем обработчик для кнопок меню
    dispatcher.add_handler(CallbackQueryHandler(button_callback))
    
    # Обработчики команд
    dispatcher.add_handler(CommandHandler("help", help_command))
    dispatcher.add_handler(CommandHandler("notify", notify_command))
    
    # Обработчик сообщений с таймером (для обычных и пересланных сообщений)
    # НЕ ИЗМЕНЯЕМ ПОРЯДОК - этот обработчик должен быть последним!
    dispatcher.add_handler(MessageHandler(
        (Filters.text | Filters.forwarded) & ~Filters.command & ~Filters.update.edited_message,
        process_timer_message
    ))
    
    # Запускаем бота
    updater.start_polling()
    
    # Логируем информацию о запуске
    logger.info("Бот запущен и ожидает сообщений.")
    
    updater.idle()


if __name__ == "__main__":
    main() 