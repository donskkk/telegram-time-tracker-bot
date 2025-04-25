import re
import io
import os
from datetime import datetime, timedelta
import matplotlib.pyplot as plt
import matplotlib
matplotlib.use('Agg')  # Используем Agg бэкенд, не требующий GUI


def parse_time_input(time_input):
    """Парсинг ввода времени в различных форматах"""
    
    # Проверка формата "2ч 20м" или "2ч" или "20м"
    if re.search(r'(\d+)ч', time_input) or re.search(r'(\d+)м', time_input):
        hours = re.search(r'(\d+)ч', time_input)
        minutes = re.search(r'(\d+)м', time_input)
        
        total_minutes = 0
        if hours:
            total_minutes += int(hours.group(1)) * 60
        if minutes:
            total_minutes += int(minutes.group(1))
        
        return total_minutes
    
    # Проверка формата "140мин"
    if re.search(r'(\d+)мин', time_input):
        minutes = re.search(r'(\d+)мин', time_input)
        return int(minutes.group(1))
    
    # Проверка формата числа с плавающей точкой (часы)
    if re.match(r'^(\d+(\.\d+)?)$', time_input):
        hours = float(time_input)
        return int(hours * 60)
    
    # Проверка формата целого числа (предполагается минуты)
    if time_input.isdigit():
        return int(time_input)
    
    # Если ничего не подошло
    return None


def parse_timer_message(message_text):
    """Парсинг сообщения от таймера в формате "🛑 таймер остановлен ... Затрачено HH:MM:SS" """
    # Регулярное выражение для извлечения времени в формате HH:MM:SS
    time_pattern = r'Затрачено\s+(\d{2}):(\d{2}):(\d{2})'
    
    match = re.search(time_pattern, message_text)
    if match:
        hours = int(match.group(1))
        minutes = int(match.group(2))
        seconds = int(match.group(3))
        
        # Переводим всё в минуты, округляя секунды до ближайшей минуты
        total_minutes = hours * 60 + minutes
        if seconds >= 30:
            total_minutes += 1
        
        # Проверка на разумные значения (не более 24 часов за раз)
        if 0 < total_minutes <= 24 * 60:
            return total_minutes
        else:
            return None
    
    # Альтернативный паттерн (просто пытаемся найти время в формате HH:MM:SS где-нибудь в сообщении)
    alt_pattern = r'(\d{2}):(\d{2}):(\d{2})'
    
    for match in re.finditer(alt_pattern, message_text):
        hours = int(match.group(1))
        minutes = int(match.group(2))
        seconds = int(match.group(3))
        
        # Допустимый диапазон для рабочего времени
        if 0 <= hours < 24 and 0 <= minutes < 60 and 0 <= seconds < 60:
            total_minutes = hours * 60 + minutes
            if seconds >= 30:
                total_minutes += 1
            
            # Проверка на разумные значения (не более 24 часов за раз)
            if 0 < total_minutes <= 24 * 60:
                return total_minutes
    
    return None


def generate_progress_bar(percent):
    """Генерация ASCII-прогресс бара"""
    # Используем символы прогресса
    full_char = '●'
    empty_char = '○'
    
    # Количество заполненных символов (из 10)
    filled = int(percent / 10)
    
    # Создаем бар
    bar = full_char * filled + empty_char * (10 - filled)
    
    return f"[{bar}] {percent}%"


def create_progress_chart(progress_data):
    """Создает круговую диаграмму прогресса и возвращает байтовый буфер с изображением"""
    earned = progress_data["earned"]
    goal = progress_data["goal"]
    percent = progress_data["percent"]
    
    # Создаем данные для диаграммы
    remaining = goal - earned if goal > earned else 0
    sizes = [earned, remaining]
    labels = [f'Заработано: {earned:.0f}₽', f'Осталось: {remaining:.0f}₽']
    colors = ['#4CAF50', '#ECEFF1']
    
    # Создаем диаграмму
    plt.figure(figsize=(10, 6))
    plt.clf()
    
    # Настраиваем шрифт для поддержки кириллицы
    plt.rcParams['font.family'] = 'DejaVu Sans'
    
    # Круговая диаграмма
    wedges, texts, autotexts = plt.pie(
        sizes, 
        labels=labels, 
        colors=colors, 
        autopct='%1.1f%%', 
        startangle=90,
        wedgeprops={'linewidth': 3, 'edgecolor': 'white'}
    )
    
    # Устанавливаем стиль текста
    for text in texts + autotexts:
        text.set_fontsize(12)
    
    # Добавляем заголовок с информацией о прогрессе
    plt.title(
        f"Прогресс: {percent}% от цели в {goal:.0f}₽\n"
        f"Осталось отработать: {progress_data['hours_left']:.1f} часов",
        fontsize=16, 
        pad=20
    )
    
    plt.axis('equal')  # Круглая форма
    
    # Сохраняем в буфер
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=100, bbox_inches='tight')
    buf.seek(0)
    
    return buf


def format_time(minutes):
    """Форматирование времени"""
    if minutes < 60:
        return f"{minutes}м"
    
    hours = minutes // 60
    mins = minutes % 60
    
    if mins == 0:
        return f"{hours}ч"
    
    return f"{hours}ч {mins}м"


def format_money(amount):
    """Форматирование денежной суммы"""
    return f"{amount:.0f}₽"


def format_progress_message(progress_data):
    """Форматирование сообщения о прогрессе"""
    goal = format_money(progress_data["goal"])
    earned = format_money(progress_data["earned"])
    percent = progress_data["percent"]
    hours_left = progress_data["hours_left"]
    
    progress_bar = generate_progress_bar(percent)
    
    message = (
        f"Цель: {goal} | Заработано: {earned}\n"
        f"{progress_bar}\n"
        f"Осталось: {hours_left:.1f} часов"
    )
    
    return message


def format_notification_message(progress_data):
    """Форматирование уведомления"""
    earned = format_money(progress_data["earned"])
    hours_left = progress_data["hours_left"]
    
    message = (
        f"📢\n"
        f"Заработано: {earned} | Осталось: {hours_left:.1f} ч"
    )
    
    return message


def format_time_record(record):
    """Форматирование записи времени для истории"""
    minutes = record["minutes"]
    earnings = format_money(record["earnings"])
    timestamp = datetime.strptime(record["timestamp"], "%Y-%m-%d %H:%M:%S")
    date_str = timestamp.strftime("%d.%m.%Y %H:%M")
    
    return f"{date_str} - {format_time(minutes)} ({earnings})" 