import logging
import os
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Updater, CommandHandler, MessageHandler, Filters, 
    CallbackContext, ConversationHandler, CallbackQueryHandler, JobQueue
)
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
import pytz
import sqlite3
import types
import re
import sys
import atexit

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
    
    # Проверяем, не является ли это сообщением таймера
    if "таймер остановлен" in user_input.lower() and "затрачено" in user_input.lower():
        logger.info(f"Обнаружено сообщение таймера в состоянии RATE, обрабатываем напрямую")
        
        # Очищаем состояние пользователя
        if 'state' in context.user_data:
            del context.user_data['state']
            
        # Парсим время из сообщения таймера
        minutes = parse_timer_message(user_input)
        
        if minutes:
            # Обрабатываем таймер напрямую
            process_single_timer(update, context, minutes)
            return ConversationHandler.END
        else:
            # Если не удалось распознать время, продолжаем с запросом ставки
            update.message.reply_text(
                "Не удалось распознать время из сообщения таймера.\n\n"
                "Пожалуйста, введите почасовую ставку (например, 500₽):"
            )
            return RATE
    
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
    
    # Проверяем, не является ли это сообщением таймера
    if "таймер остановлен" in user_input.lower() and "затрачено" in user_input.lower():
        logger.info(f"Обнаружено сообщение таймера в состоянии GOAL, обрабатываем напрямую")
        
        # Очищаем состояние пользователя
        if 'state' in context.user_data:
            del context.user_data['state']
            
        # Парсим время из сообщения таймера
        minutes = parse_timer_message(user_input)
        
        if minutes:
            # Обрабатываем таймер напрямую
            process_single_timer(update, context, minutes)
            return ConversationHandler.END
        else:
            # Если не удалось распознать время, продолжаем с запросом цели
            update.message.reply_text(
                "Не удалось распознать время из сообщения таймера.\n\n"
                "Пожалуйста, введите цель заработка (например, 50000₽):"
            )
            return GOAL
    
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
    
    # Создаем клавиатуру (убираем кнопку "Мой прогресс")
    keyboard = [
        [
            InlineKeyboardButton("Добавить время", callback_data='add_time')
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
        
        # Создаем диаграмму прогресса, если есть данные прогресса
        if progress:
            chart_buf = create_progress_chart(progress)
            
            # Отправляем фото с диаграммой и текстом меню
            if update.callback_query and update.callback_query.message:
                chat_id = update.callback_query.message.chat_id
                message = context.bot.send_photo(
                    chat_id=chat_id,
                    photo=chart_buf,
                    caption=menu_text,
                    reply_markup=reply_markup
                )
            elif update.message:
                message = update.message.reply_photo(
                    photo=chart_buf,
                    caption=menu_text,
                    reply_markup=reply_markup
                )
            else:
                # Если ни один из вариантов не подходит, получаем chat_id из контекста
                chat_id = context.user_data.get('user_chat_id')
                if chat_id:
                    message = context.bot.send_photo(
                        chat_id=chat_id,
                        photo=chart_buf,
                        caption=menu_text,
                        reply_markup=reply_markup
                    )
        else:
            # Если данных прогресса нет, отправляем обычное текстовое сообщение
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
            # Обработка нажатия на кнопку "Ежедневно" - показываем выбор времени
            if freq == 'day' and len(data.split('_')) <= 2:
                # Показываем инлайн клавиатуру с выбором времени
                keyboard = [
                    [
                        InlineKeyboardButton("09:00", callback_data='notify_day_time_9_00'),
                        InlineKeyboardButton("12:00", callback_data='notify_day_time_12_00'),
                    ],
                    [
                        InlineKeyboardButton("15:00", callback_data='notify_day_time_15_00'),
                        InlineKeyboardButton("18:00", callback_data='notify_day_time_18_00'),
                    ],
                    [
                        InlineKeyboardButton("21:00", callback_data='notify_day_time_21_00'),
                        InlineKeyboardButton("23:00", callback_data='notify_day_time_23_00'),
                    ],
                    [
                        InlineKeyboardButton("Своё время", callback_data='notify_day_custom'),
                        InlineKeyboardButton("« Назад", callback_data='notify_settings')
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
                    text="Выберите время для ежедневных уведомлений:",
                    reply_markup=reply_markup
                )
                
                # Сохраняем ID сообщения
                if message:
                    context.user_data['last_bot_message'] = (message.chat_id, message.message_id)
                return
            
            # Обработка выбора конкретного времени для ежедневных уведомлений
            elif freq == 'day' and len(data.split('_')) > 2 and data.split('_')[2] == 'time':
                # Получаем время из callback_data (например, notify_day_time_9_00 -> 9:00)
                hour = int(data.split('_')[3])
                minute = int(data.split('_')[4])
                time_str = f"{hour:02d}:{minute:02d}"
                
                # Удаляем существующие задачи для пользователя
                for job in scheduler.get_jobs():
                    if job.id.startswith(f"notify_{user_id}"):
                        job.remove()
                
                # Настраиваем ежедневное уведомление в указанное время
                scheduler.add_job(
                    send_notification, 'cron', hour=hour, minute=minute, id=f"notify_{user_id}_day",
                    args=(context, user_id), timezone=pytz.UTC
                )
                
                # Обновляем настройку в базе данных
                db.update_notify_freq(user_id, f"day_{time_str}")
                
                freq_text = f"ежедневно в {time_str}"
            
            # Обработка еженедельных уведомлений с выбором дня недели
            elif freq == 'week' and len(data.split('_')) > 2:
                day_of_week = int(data.split('_')[2])
                
                # Удаляем все задачи для пользователя
                for job in scheduler.get_jobs():
                    if job.id.startswith(f"notify_{user_id}"):
                        job.remove()
                
                # Настраиваем еженедельное уведомление в указанный день недели
                scheduler.add_job(
                    send_notification, 'cron', day_of_week=day_of_week, hour=9, minute=0,
                    id=f"notify_{user_id}_week",
                    args=(context, user_id), timezone=pytz.UTC
                )
                
                # Обновляем настройку в базе данных
                db.update_notify_freq(user_id, f"week_{day_of_week}")
                
                # Преобразование числового дня недели в название
                day_names = ["понедельник", "вторник", "среду", "четверг", "пятницу", "субботу", "воскресенье"]
                day_name = day_names[day_of_week]
                
                freq_text = f"еженедельно в {day_name}"
            
            # Обработка отключения уведомлений
            elif freq == 'off':
                # Удаляем все задачи для пользователя
                for job in scheduler.get_jobs():
                    if job.id.startswith(f"notify_{user_id}"):
                        job.remove()
                
                # Обновляем настройку в базе данных
                db.update_notify_freq(user_id, 'off')
                
                freq_text = "отключены"
            elif freq == 'day_multi':
                # Удаляем существующие задачи для пользователя
                for job in scheduler.get_jobs():
                    if job.id.startswith(f"notify_{user_id}"):
                        job.remove()
                
                # Фиксированные времена для уведомлений
                times = [
                    (9, 0),   # 09:00
                    (18, 0),  # 18:00
                    (22, 0)   # 22:00
                ]
                
                # Настраиваем уведомления на каждое время
                for i, (hour, minute) in enumerate(times):
                    scheduler.add_job(
                        send_notification, 'cron', hour=hour, minute=minute,
                        id=f"notify_{user_id}_daily_{i}",
                        args=(context, user_id), timezone=pytz.UTC
                    )
                
                # Обновляем настройку в базе данных
                db.update_notify_freq(user_id, "day_multi")
                
                freq_text = "ежедневно в 09:00, 18:00 и 22:00"
            else:
                # Настраиваем уведомления с указанной частотой
                setup_notification(context, user_id, freq)
                
                # Обновляем настройку в базе данных
                db.update_notify_freq(user_id, freq)
                
                freq_text = {
                    'hour': 'ежечасно',
                    'day': 'ежедневно в 09:00',
                    'week': 'еженедельно в понедельник'
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
                    # Используем соединение с базой данных через тот же путь, что использует объект db
                    conn = sqlite3.connect(db.db_name)
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
    
    # Отображение настроек уведомлений
    elif data == 'notify_settings':
        try:
            keyboard = [
                [
                    InlineKeyboardButton("Каждый час", callback_data='notify_hour'),
                    InlineKeyboardButton("Ежедневно (09:00)", callback_data='notify_day')
                ],
                [
                    InlineKeyboardButton("Трижды в день", callback_data='notify_day_multi'),
                    InlineKeyboardButton("Еженедельно (Пн)", callback_data='notify_week')
                ],
                [
                    InlineKeyboardButton("Настройка времени", callback_data='notify_custom'),
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
    
    # Обработка настройки уведомлений
    elif data == 'notify_custom':
        try:
            keyboard = [
                [
                    InlineKeyboardButton("День и время", callback_data='notify_day_custom'),
                    InlineKeyboardButton("День недели", callback_data='notify_week_custom')
                ],
                [
                    InlineKeyboardButton("« Назад", callback_data='notify_settings')
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
                text="Выберите, что настроить:",
                reply_markup=reply_markup
            )
            
            # Сохраняем ID сообщения
            if message:
                context.user_data['last_bot_message'] = (message.chat_id, message.message_id)
        except Exception as e:
            logger.error(f"Ошибка при отображении настроек пользовательских уведомлений: {e}")
            show_main_menu(update, context)
    
    # Настройка ежедневных уведомлений с указанием времени
    elif data == 'notify_day_custom':
        try:
            # Удаляем предыдущее сообщение
            try:
                query.message.delete()
            except Exception as e:
                logger.error(f"Не удалось удалить сообщение: {e}")
            
            message = context.bot.send_message(
                chat_id=chat_id,
                text="Введите время для ежедневных уведомлений в формате ЧЧ:ММ (например, 09:00):"
            )
            
            # Устанавливаем состояние для обработки ввода
            context.user_data['state'] = CHANGE_NOTIFY
            context.user_data['notify_type'] = 'day'
            
            # Сохраняем ID сообщения
            if message:
                context.user_data['last_bot_message'] = (message.chat_id, message.message_id)
        except Exception as e:
            logger.error(f"Ошибка при настройке ежедневных уведомлений: {e}")
            show_main_menu(update, context)
    
    # Настройка еженедельных уведомлений с указанием дня недели
    elif data == 'notify_week_custom':
        try:
            keyboard = [
                [
                    InlineKeyboardButton("Понедельник", callback_data='notify_week_0'),
                    InlineKeyboardButton("Вторник", callback_data='notify_week_1')
                ],
                [
                    InlineKeyboardButton("Среда", callback_data='notify_week_2'),
                    InlineKeyboardButton("Четверг", callback_data='notify_week_3')
                ],
                [
                    InlineKeyboardButton("Пятница", callback_data='notify_week_4'),
                    InlineKeyboardButton("Суббота", callback_data='notify_week_5')
                ],
                [
                    InlineKeyboardButton("Воскресенье", callback_data='notify_week_6')
                ],
                [
                    InlineKeyboardButton("« Назад", callback_data='notify_custom')
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
                text="Выберите день недели для еженедельных уведомлений:",
                reply_markup=reply_markup
            )
            
            # Сохраняем ID сообщения
            if message:
                context.user_data['last_bot_message'] = (message.chat_id, message.message_id)
        except Exception as e:
            logger.error(f"Ошибка при настройке еженедельных уведомлений: {e}")
            show_main_menu(update, context)
    
    elif data.startswith('notify_'):
        freq = data.split('_')[1]
        
        try:
            # Обработка еженедельных уведомлений с выбором дня недели
            if freq == 'week' and len(data.split('_')) > 2:
                day_of_week = int(data.split('_')[2])
                
                # Удаляем все задачи для пользователя
                for job in scheduler.get_jobs():
                    if job.id.startswith(f"notify_{user_id}"):
                        job.remove()
                
                # Настраиваем еженедельное уведомление в указанный день недели
                scheduler.add_job(
                    send_notification, 'cron', day_of_week=day_of_week, hour=9, minute=0,
                    id=f"notify_{user_id}_week",
                    args=(context, user_id), timezone=pytz.UTC
                )
                
                # Обновляем настройку в базе данных
                db.update_notify_freq(user_id, f"week_{day_of_week}")
                
                # Преобразование числового дня недели в название
                day_names = ["понедельник", "вторник", "среду", "четверг", "пятницу", "субботу", "воскресенье"]
                day_name = day_names[day_of_week]
                
                freq_text = f"еженедельно в {day_name}"
            elif freq == 'off':
                # Удаляем все задачи для пользователя
                for job in scheduler.get_jobs():
                    if job.id.startswith(f"notify_{user_id}"):
                        job.remove()
                
                # Обновляем настройку в базе данных
                db.update_notify_freq(user_id, 'off')
                
                freq_text = "отключены"
            elif freq == 'day_multi':
                # Удаляем существующие задачи для пользователя
                for job in scheduler.get_jobs():
                    if job.id.startswith(f"notify_{user_id}"):
                        job.remove()
                
                # Фиксированные времена для уведомлений
                times = [
                    (9, 0),   # 09:00
                    (18, 0),  # 18:00
                    (22, 0)   # 22:00
                ]
                
                # Настраиваем уведомления на каждое время
                for i, (hour, minute) in enumerate(times):
                    scheduler.add_job(
                        send_notification, 'cron', hour=hour, minute=minute,
                        id=f"notify_{user_id}_daily_{i}",
                        args=(context, user_id), timezone=pytz.UTC
                    )
                
                # Обновляем настройку в базе данных
                db.update_notify_freq(user_id, "day_multi")
                
                freq_text = "ежедневно в 09:00, 18:00 и 22:00"
            else:
                # Настраиваем уведомления с указанной частотой
                setup_notification(context, user_id, freq)
                
                # Обновляем настройку в базе данных
                db.update_notify_freq(user_id, freq)
                
                freq_text = {
                    'hour': 'ежечасно',
                    'day': 'ежедневно в 09:00',
                    'week': 'еженедельно в понедельник'
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
            show_main_menu(update, context)


def help_command(update: Update, context: CallbackContext) -> None:
    """Отправка справки по командам"""
    update.message.reply_text(
        "Список доступных команд:\n\n"
        "/start - Начать использование бота\n"
        "/rate - Установить новую ставку\n"
        "/goal - Установить новую цель\n"
        "/notify - Управление уведомлениями\n"
        "  Примеры:\n"
        "  /notify hour - уведомления каждый час\n"
        "  /notify day 09:00 - ежедневно в указанное время\n"
        "  /notify day_multi - ежедневно в 09:00, 18:00 и 22:00\n"
        "  /notify week 0 - еженедельно в понедельник\n"
        "  /notify off - отключить уведомления\n"
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
    """Обработка сообщений с таймерами"""
    message_text = update.message.text
    user_id = update.effective_user.id
    
    logger.info(f"Получено сообщение с возможным таймером от пользователя {user_id}: '{message_text}'")
    
    # Проверяем, что это сообщение от таймера
    if "таймер остановлен" in message_text.lower() and "затрачено" in message_text.lower():
        # Парсим время из сообщения таймера
        minutes = parse_timer_message(message_text)
        
        if minutes:
            logger.info(f"Распознано время из сообщения таймера: {minutes} минут")
            
            # Проверяем, зарегистрирован ли пользователь
            if not db.user_exists(user_id):
                logger.info(f"Пользователь {user_id} не зарегистрирован")
                update.message.reply_text(
                    "Для использования бота необходимо сначала настроить свой профиль.\n"
                    "Используйте команду /start для настройки."
                )
                return
                
            # Проверяем, находится ли пользователь в режиме группировки таймеров
            chat_id = update.message.chat_id
            
            try:
                if context.user_data.get('grouping_timers'):
                    # Добавляем таймер в группу
                    if 'timer_buffer' not in context.user_data:
                        context.user_data['timer_buffer'] = []
                        
                    context.user_data['timer_buffer'].append(minutes)
                    logger.info(f"Добавлен таймер в группу: {minutes} минут. Всего: {len(context.user_data['timer_buffer'])}")
                    
                    # Обновляем время последнего таймера
                    context.user_data['last_timer_time'] = context.dispatcher.bot.get_me().id
                    
                    # Пытаемся удалить исходное сообщение
                    try:
                        update.message.delete()
                    except Exception as e:
                        logger.error(f"Не удалось удалить исходное сообщение: {e}")
                        
                    # Отправляем сообщение о добавлении в группу и удаляем его через 3 секунды
                    message = update.message.reply_text(
                        f"✅ Таймер {format_time(minutes)} добавлен в группу (всего: {len(context.user_data['timer_buffer'])})"
                    )
                    
                    if message:
                        context.job_queue.run_once(
                            delete_message_later,
                            3,
                            context=(chat_id, message.message_id)
                        )
                    
                    # Запланировать обработку группы через 2 секунды
                    # Проверяем, не завершается ли интерпретатор
                    if not (hasattr(sys, '_shutdown_thread') and sys._shutdown_thread):
                        context.job_queue.run_once(
                            process_grouped_timers,
                            2,
                            context=(user_id, chat_id, context.user_data['timer_buffer'])
                        )
                    else:
                        logger.info("Пропускаем добавление задачи process_grouped_timers - интерпретатор завершает работу")
                    
                else:
                    # Добавляем одиночный таймер напрямую
                    logger.info(f"Добавлено пересланное сообщение с таймером: {minutes} минут")
                    
                    # Это обычное сообщение с таймером, обрабатываем сразу
                    process_single_timer(update, context, minutes)
                
                # Очищаем состояние пользователя, если оно было
                if 'state' in context.user_data:
                    old_state = context.user_data.get('state')
                    del context.user_data['state']
                    logger.info(f"Очищено состояние пользователя {old_state} после обработки таймера")
                    
                return
            except Exception as e:
                logger.error(f"Ошибка при обработке сообщения с таймером: {e}")
                # В случае ошибки всё равно пытаемся обработать одиночный таймер
                process_single_timer(update, context, minutes)
                return
    
    # Для обычных сообщений (не таймеров) проверяем состояние пользователя
    if context.user_data.get('state') in [CHANGE_RATE, CHANGE_GOAL, RATE, GOAL, CONFIRM_TIME, CHANGE_NOTIFY]:
        # В этом случае обработка переадресуется соответствующей функции ввода
        state = context.user_data.get('state')
        logger.info(f"Пропускаем обработку обычного сообщения, т.к. пользователь в состоянии ввода: {state}")
        
        # Проверяем, что сообщение не является просто числом, которое может быть целью или ставкой
        if state in [CHANGE_GOAL, GOAL, CHANGE_RATE, RATE] and message_text.strip().replace('.', '').isdigit():
            logger.info(f"Обнаружен числовой ввод '{message_text}' в состоянии {state}, обрабатываем как числовой ввод")
            # Не возвращаем здесь ничего, чтобы продолжить нормальную обработку в соответствующем обработчике
            return
        
        return


def process_grouped_timers(context):
    """Обработка группы пересланных таймеров"""
    try:
        # В начале функции проверяем, не завершается ли интерпретатор
        if hasattr(sys, '_shutdown_thread') and sys._shutdown_thread:
            logger.info("Пропускаем обработку групповых таймеров - интерпретатор завершает работу")
            return
            
        # Проверяем, вызвана ли функция из планировщика или из обработчика сообщений
        if hasattr(context, 'job') and hasattr(context.job, 'context') and context.job.context is not None and isinstance(context.job.context, tuple):
            # Вызов из обработчика сообщений через job_queue.run_once
            user_id, chat_id, timer_buffer = context.job.context
        elif isinstance(context, CallbackContext) and isinstance(context.job_queue, JobQueue):
            # Вызов из планировщика по расписанию
            logger.info("Обработка групповых таймеров из планировщика - нет таймеров для обработки")
            return
        elif context is None:
            # Вызов из основного планировщика (APScheduler)
            logger.info("Проверка групповых таймеров из основного планировщика")
            return
        else:
            logger.error(f"Неизвестный контекст вызова process_grouped_timers: {type(context)}")
            return
        
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
        if message:
            # Получаем доступ к user_data через специальный обработчик
            from telegram.ext.dispatcher import run_async
            
            @run_async
            def update_user_data(bot, user_id, message):
                try:
                    # Получаем dispatcher из bot
                    dispatcher = bot.dispatcher
                    if dispatcher and dispatcher.user_data.get(user_id) is not None:
                        dispatcher.user_data[user_id]['last_bot_message'] = (message.chat_id, message.message_id)
                        dispatcher.user_data[user_id]['timer_group_minutes'] = total_minutes
                        
                        # Очищаем состояние пользователя, если оно было
                        if 'state' in dispatcher.user_data[user_id]:
                            old_state = dispatcher.user_data[user_id].get('state')
                            del dispatcher.user_data[user_id]['state']
                            logger.info(f"Очищено состояние пользователя {old_state} после обработки группы таймеров")
                        
                        logger.info(f"Отправлено сообщение с подтверждением группы таймеров, ID: {message.message_id}")
                except Exception as e:
                    logger.error(f"Ошибка при обновлении user_data: {e}")
            
            update_user_data(context.bot, user_id, message)
    
    except RuntimeError as e:
        if "shutdown" in str(e).lower():
            logger.info("Пропускаем обработку групповых таймеров из-за завершения работы интерпретатора")
        else:
            logger.error(f"Ошибка при обработке группы таймеров: {e}")
    except Exception as e:
        logger.error(f"Ошибка при обработке группы таймеров: {e}")
        try:
            if 'chat_id' in locals():
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
        
        # Очищаем состояние пользователя, если оно было
        if 'state' in context.user_data:
            old_state = context.user_data.get('state')
            del context.user_data['state']
            logger.info(f"Очищено состояние пользователя {old_state} после обработки одиночного таймера")
        
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


def change_notify_input(update: Update, context: CallbackContext) -> int:
    """Обработка ввода настроек уведомлений"""
    user_id = update.effective_user.id
    user_text = update.message.text.strip()
    
    # Проверяем, есть ли сохраненный тип уведомления
    notify_type = context.user_data.get('notify_type')
    if notify_type != 'day':
        # Если тип уведомления не day, возвращаемся в главное меню
        update.message.reply_text(
            "Произошла ошибка при настройке уведомлений. Пожалуйста, попробуйте позже."
        )
        show_main_menu(update, context)
        return ConversationHandler.END
    
    # Обрабатываем ввод времени для ежедневных уведомлений
    if not re.match(r'^\d{1,2}:\d{2}$', user_text):
        update.message.reply_text(
            "Неверный формат времени. Пожалуйста, используйте формат ЧЧ:ММ (например, 09:00)."
        )
        return CHANGE_NOTIFY
    
    try:
        # Разбираем время
        hour, minute = map(int, user_text.split(':'))
        
        # Проверяем корректность времени
        if hour < 0 or hour > 23 or minute < 0 or minute > 59:
            update.message.reply_text(
                "Неверное время. Часы должны быть от 0 до 23, минуты от 0 до 59."
            )
            return CHANGE_NOTIFY
        
        # Удаляем существующие задачи для пользователя
        for job in scheduler.get_jobs():
            if job.id.startswith(f"notify_{user_id}"):
                job.remove()
        
        # Настраиваем ежедневное уведомление в указанное время
        scheduler.add_job(
            send_notification, 'cron', hour=hour, minute=minute, id=f"notify_{user_id}_day",
            args=(context, user_id), timezone=pytz.UTC
        )
        
        # Обновляем настройку в базе данных
        db.update_notify_freq(user_id, f"day_{user_text}")
        
        # Удаляем предыдущее сообщение с запросом и сообщение пользователя
        try:
            if hasattr(update, 'message') and update.message:
                update.message.delete()
            
            if 'last_bot_message' in context.user_data:
                chat_id, message_id = context.user_data['last_bot_message']
                context.bot.delete_message(chat_id=chat_id, message_id=message_id)
                del context.user_data['last_bot_message']
        except Exception as e:
            logger.error(f"Ошибка при удалении сообщений: {e}")
        
        # Очищаем состояние пользователя
        if 'state' in context.user_data:
            del context.user_data['state']
        if 'notify_type' in context.user_data:
            del context.user_data['notify_type']
        
        # Отправляем подтверждение
        update.message.reply_text(
            f"✅ Уведомления настроены на ежедневную отправку в {user_text}."
        )
        
        # Показываем главное меню
        show_main_menu(update, context)
        
        return ConversationHandler.END
    
    except Exception as e:
        logger.error(f"Ошибка при настройке уведомлений: {e}")
        update.message.reply_text(
            "Произошла ошибка при настройке уведомлений. Пожалуйста, попробуйте позже."
        )
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


def change_rate_input(update: Update, context: CallbackContext) -> int:
    """Обработка ввода новой ставки"""
    user_id = update.effective_user.id
    user_text = update.message.text
    
    # Сохраняем текущее состояние в контексте пользователя
    context.user_data['state'] = CHANGE_RATE
    
    logger.info(f"Обработка ввода новой ставки от пользователя {user_id}: '{user_text}'")
    
    # Проверяем, не является ли это сообщением таймера
    if "таймер остановлен" in user_text.lower() and "затрачено" in user_text.lower():
        logger.info(f"Обнаружено сообщение таймера в состоянии CHANGE_RATE, обрабатываем напрямую")
        
        # Очищаем состояние пользователя
        if 'state' in context.user_data:
            del context.user_data['state']
            
        # Парсим время из сообщения таймера
        minutes = parse_timer_message(user_text)
        
        if minutes:
            # Обрабатываем таймер напрямую
            process_single_timer(update, context, minutes)
            return ConversationHandler.END
        else:
            # Если не удалось распознать время, продолжаем с запросом новой ставки
            update.message.reply_text(
                "Не удалось распознать время из сообщения таймера.\n\n"
                "Пожалуйста, введите новую почасовую ставку (например, 800₽):"
            )
            return CHANGE_RATE
    
    try:
        # Очищаем ввод от лишних символов
        rate_text = user_text.replace('₽', '').replace('р', '').replace('руб', '')
        rate_text = rate_text.replace(',', '.').strip()
        
        logger.info(f"Очищенный текст ставки: '{rate_text}'")
        
        rate = float(rate_text)
        
        # Обновляем ставку в базе данных
        logger.info(f"Попытка обновить ставку для пользователя {user_id} на {rate}")
        db.update_rate(user_id, rate)
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
    
    # Проверяем, не является ли это сообщением таймера
    if "таймер остановлен" in user_text.lower() and "затрачено" in user_text.lower():
        logger.info(f"Обнаружено сообщение таймера в состоянии CHANGE_GOAL, обрабатываем напрямую")
        
        # Очищаем состояние пользователя
        if 'state' in context.user_data:
            del context.user_data['state']
            
        # Парсим время из сообщения таймера
        minutes = parse_timer_message(user_text)
        
        if minutes:
            # Обрабатываем таймер напрямую
            process_single_timer(update, context, minutes)
            return ConversationHandler.END
        else:
            # Если не удалось распознать время, продолжаем с запросом новой цели
            update.message.reply_text(
                "Не удалось распознать время из сообщения таймера.\n\n"
                "Пожалуйста, введите новую цель заработка (например, 50000₽):"
            )
            return CHANGE_GOAL
    
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
    
    # Проверяем, не является ли это сообщением таймера
    if "таймер остановлен" in time_input.lower() and "затрачено" in time_input.lower():
        logger.info(f"Обнаружено сообщение таймера в состоянии CONFIRM_TIME, обрабатываем напрямую")
        
        # Очищаем состояние пользователя
        if 'state' in context.user_data:
            del context.user_data['state']
            
        # Парсим время из сообщения таймера
        minutes = parse_timer_message(time_input)
        
        if minutes:
            # Обрабатываем таймер напрямую
            process_single_timer(update, context, minutes)
            return ConversationHandler.END
        else:
            # Если не удалось распознать время, продолжаем с запросом времени
            send_message_with_auto_delete(
                update, context,
                "Не удалось распознать время из сообщения таймера.\n\n"
                "Введите время в одном из форматов:\n"
                "• 2ч 20м\n"
                "• 140мин\n"
                "• 2.33 (часы)",
                delete_seconds=10
            )
            return CONFIRM_TIME
    
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
    
    # Очищаем состояние пользователя
    if 'state' in context.user_data:
        del context.user_data['state']
    
    # Показываем главное меню
    show_main_menu(update, context)
    
    return ConversationHandler.END


def main() -> None:
    """Основная функция запуска бота"""
    # Получаем токен из переменных окружения
    TOKEN = os.getenv('TELEGRAM_TOKEN')
    if not TOKEN:
        logger.error("Не задан токен бота. Установите переменную окружения TELEGRAM_TOKEN.")
        return

    # Создаем экземпляр бота и диспетчера
    updater = Updater(TOKEN)
    dispatcher = updater.dispatcher

    # Создаем обработчик разговора для регистрации
    registration_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            RATE: [MessageHandler(Filters.text & ~Filters.command, rate_input)],
            GOAL: [MessageHandler(Filters.text & ~Filters.command, goal_input)],
            CHANGE_RATE: [MessageHandler(Filters.text & ~Filters.command, change_rate_input)],
            CHANGE_GOAL: [MessageHandler(Filters.text & ~Filters.command, change_goal_input)],
            CHANGE_NOTIFY: [MessageHandler(Filters.text & ~Filters.command, change_notify_input)],
            ADD_TIME: [MessageHandler(Filters.text & ~Filters.command, manual_time_input)],
            CONFIRM_TIME: [MessageHandler(Filters.text & ~Filters.command, process_timer_message)],
            RESET_GOAL_CONFIRM: [
                CallbackQueryHandler(button_callback, pattern='^reset_goal_confirm$'),
                CallbackQueryHandler(button_callback, pattern='^reset_goal_cancel$')
            ],
        },
        fallbacks=[CommandHandler('cancel', cancel_command)],
        allow_reentry=True
    )

    # Добавляем обработчики команд
    dispatcher.add_handler(registration_handler)
    dispatcher.add_handler(CommandHandler('help', help_command))
    dispatcher.add_handler(CommandHandler('stats', lambda update, context: show_main_menu(update, context)))
    dispatcher.add_handler(CommandHandler('rate', rate_command))
    dispatcher.add_handler(CommandHandler('goal', goal_command))
    dispatcher.add_handler(CommandHandler('notify', notify_command))
    dispatcher.add_handler(CommandHandler('add', lambda update, context: manual_time_input(update, context, is_command=True)))
    
    # Обработчик кнопок
    dispatcher.add_handler(CallbackQueryHandler(button_callback))
    
    # Обработчик обычных сообщений
    dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, process_timer_message))
    
    # Добавляем обработчик ошибок
    dispatcher.add_error_handler(error_handler)
    
    # Регистрируем функцию очистки при завершении работы
    import atexit
    
    def shutdown_hook():
        """Функция, которая будет вызвана при завершении работы интерпретатора"""
        logger.info("Завершение работы бота")
        
        # Останавливаем планировщик, если он запущен
        try:
            if scheduler and scheduler.running:
                scheduler.shutdown(wait=False)
                logger.info("Планировщик остановлен")
        except Exception as e:
            logger.error(f"Ошибка при остановке планировщика: {e}")
    
    # Регистрируем функцию очистки
    atexit.register(shutdown_hook)
    
    # Проверяем, не завершается ли интерпретатор перед запуском бота
    if hasattr(sys, '_shutdown_thread') and sys._shutdown_thread:
        logger.error("Не удается запустить бота - интерпретатор завершает работу")
        return

    # Инициализируем обработчик групповых таймеров только если интерпретатор не завершается
    if not (hasattr(sys, '_shutdown_thread') and sys._shutdown_thread):
        scheduler.add_job(
            process_grouped_timers,
            'interval',
            minutes=1,
            id="process_grouped_timers",
            args=(None,),  # Передаем None как признак вызова из планировщика
            timezone=pytz.UTC
        )

    # Запускаем бота
    updater.start_polling()
    logger.info("Бот запущен и ожидает сообщений")
    
    # Ожидаем остановки
    updater.idle()


def notify_command(update: Update, context: CallbackContext) -> None:
    """Настройка уведомлений через команду"""
    user_id = update.effective_user.id
    
    # Проверяем, есть ли аргументы команды
    if not context.args or len(context.args) < 1:
        update.message.reply_text(
            "Использование:\n"
            "/notify hour - уведомления каждый час\n"
            "/notify day [HH:MM] - ежедневно (опционально в указанное время)\n"
            "/notify day_multi - ежедневно в 09:00, 18:00 и 22:00\n"
            "/notify week [0-6] - еженедельно (опционально с указанием дня недели 0-6, где 0=пн)\n"
            "/notify off - отключить уведомления"
        )
        return
    
    freq = context.args[0].lower()
    
    # Проверка на базовые частоты уведомлений
    if freq not in ['hour', 'day', 'day_multi', 'week', 'off']:
        update.message.reply_text(
            "Неверный параметр частоты. Используйте: hour, day, day_multi, week или off."
        )
        return
    
    # Обработка отключения уведомлений
    if freq == 'off':
        # Удаляем все задачи для пользователя
        for job in scheduler.get_jobs():
            if job.id.startswith(f"notify_{user_id}"):
                job.remove()
        
        # Обновляем настройку в базе данных
        db.update_notify_freq(user_id, 'off')
        
        update.message.reply_text(
            "Уведомления отключены."
        )
        return
        
    # Обработка ежечасных уведомлений
    if freq == 'hour':
        # Настраиваем уведомления ежечасно
        setup_notification(context, user_id, 'hour')
        
        # Обновляем настройку в базе данных
        db.update_notify_freq(user_id, 'hour')
        
        update.message.reply_text(
            "Уведомления настроены на ежечасную отправку."
        )
        return
        
    # Обработка ежедневных уведомлений с указанным временем
    if freq == 'day':
        time_str = "09:00"  # время по умолчанию
        
        # Если указано конкретное время
        if len(context.args) > 1:
            time_str = context.args[1]
            
            # Проверка формата времени
            import re
            if not re.match(r'^\d{1,2}:\d{2}$', time_str):
                update.message.reply_text(
                    "Неверный формат времени. Используйте формат HH:MM (например, 09:00)."
                )
                return
        
        try:
            # Удаляем существующие задачи для пользователя
            for job in scheduler.get_jobs():
                if job.id.startswith(f"notify_{user_id}"):
                    job.remove()
            
            # Разбираем время
            hour, minute = map(int, time_str.split(':'))
            
            # Настраиваем ежедневное уведомление в указанное время
            scheduler.add_job(
                send_notification, 'cron', hour=hour, minute=minute, id=f"notify_{user_id}_day",
                args=(context, user_id), timezone=pytz.UTC
            )
            
            # Обновляем настройку в базе данных
            db.update_notify_freq(user_id, f"day_{time_str}")
            
            update.message.reply_text(
                f"Уведомления настроены на ежедневную отправку в {time_str}."
            )
        except Exception as e:
            logger.error(f"Ошибка при настройке ежедневных уведомлений: {e}")
            update.message.reply_text(
                "Произошла ошибка при настройке уведомлений. Пожалуйста, попробуйте позже."
            )
        return
        
    # Обработка множественных ежедневных уведомлений
    if freq == 'day_multi':
        try:
            # Удаляем существующие задачи для пользователя
            for job in scheduler.get_jobs():
                if job.id.startswith(f"notify_{user_id}"):
                    job.remove()
            
            # Фиксированные времена для уведомлений
            times = [
                (9, 0),   # 09:00
                (18, 0),  # 18:00
                (22, 0)   # 22:00
            ]
            
            # Настраиваем уведомления на каждое время
            for i, (hour, minute) in enumerate(times):
                scheduler.add_job(
                    send_notification, 'cron', hour=hour, minute=minute,
                    id=f"notify_{user_id}_daily_{i}",
                    args=(context, user_id), timezone=pytz.UTC
                )
            
            # Обновляем настройку в базе данных
            db.update_notify_freq(user_id, "day_multi")
            
            update.message.reply_text(
                "Уведомления настроены на ежедневную отправку в 09:00, 18:00 и 22:00."
            )
        except Exception as e:
            logger.error(f"Ошибка при настройке множественных ежедневных уведомлений: {e}")
            update.message.reply_text(
                "Произошла ошибка при настройке уведомлений. Пожалуйста, попробуйте позже."
            )
        return
    
    # Обработка еженедельных уведомлений
    if freq == 'week':
        day_of_week = 0  # Понедельник по умолчанию
        
        # Если указан день недели
        if len(context.args) > 1:
            try:
                day_of_week = int(context.args[1])
                if day_of_week < 0 or day_of_week > 6:
                    raise ValueError("День недели должен быть от 0 до 6")
            except ValueError:
                update.message.reply_text(
                    "Неверный формат дня недели. Используйте число от 0 до 6 (0=пн, 1=вт, 2=ср, 3=чт, 4=пт, 5=сб, 6=вс)."
                )
                return
        
        try:
            # Удаляем существующие задачи для пользователя
            for job in scheduler.get_jobs():
                if job.id.startswith(f"notify_{user_id}"):
                    job.remove()
            
            # Настраиваем еженедельное уведомление в указанный день недели
            scheduler.add_job(
                send_notification, 'cron', day_of_week=day_of_week, hour=9, minute=0,
                id=f"notify_{user_id}_week",
                args=(context, user_id), timezone=pytz.UTC
            )
            
            # Преобразование числового дня недели в название
            day_names = ["понедельник", "вторник", "среду", "четверг", "пятницу", "субботу", "воскресенье"]
            day_name = day_names[day_of_week]
            
            # Обновляем настройку в базе данных
            db.update_notify_freq(user_id, f"week_{day_of_week}")
            
            update.message.reply_text(
                f"Уведомления настроены на еженедельную отправку в {day_name} в 09:00."
            )
        except Exception as e:
            logger.error(f"Ошибка при настройке еженедельных уведомлений: {e}")
            update.message.reply_text(
                "Произошла ошибка при настройке уведомлений. Пожалуйста, попробуйте позже."
            )
        return


if __name__ == "__main__":
    main() 