import sqlite3
import os
import logging

# Настройка логирования
logger = logging.getLogger(__name__)


class Database:
    def __init__(self, db_name="time_tracker.db"):
        self.db_name = db_name
        self._init_db()

    def _init_db(self):
        """Инициализация базы данных и создание необходимых таблиц"""
        try:
            conn = sqlite3.connect(self.db_name)
            cursor = conn.cursor()
            
            # Создаем таблицу пользователей
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                rate REAL,
                goal REAL,
                earned REAL DEFAULT 0,
                notify_freq TEXT DEFAULT 'day'
            )
            ''')
            
            # Создаем таблицу для хранения записей времени
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS time_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                minutes INTEGER,
                earnings REAL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
            ''')
            
            conn.commit()
            logger.info("База данных инициализирована успешно")
        except Exception as e:
            logger.error(f"Ошибка при инициализации базы данных: {e}")
        finally:
            if conn:
                conn.close()

    def user_exists(self, user_id):
        """Проверка существования пользователя в базе данных"""
        conn = None
        try:
            conn = sqlite3.connect(self.db_name)
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,))
            result = cursor.fetchone() is not None
            logger.info(f"Проверка существования пользователя {user_id}: {result}")
            return result
        except Exception as e:
            logger.error(f"Ошибка при проверке пользователя {user_id}: {e}")
            return False
        finally:
            if conn:
                conn.close()

    def add_user(self, user_id, rate, goal, notify_freq='day'):
        """Добавление нового пользователя"""
        conn = None
        try:
            conn = sqlite3.connect(self.db_name)
            cursor = conn.cursor()
            
            # Проверяем, существует ли пользователь
            if self.user_exists(user_id):
                # Если существует, обновляем данные
                cursor.execute(
                    "UPDATE users SET rate = ?, goal = ?, notify_freq = ? WHERE user_id = ?",
                    (rate, goal, notify_freq, user_id)
                )
                logger.info(f"Пользователь {user_id} обновлен в базе данных")
            else:
                # Если не существует, добавляем
                cursor.execute(
                    "INSERT INTO users (user_id, rate, goal, notify_freq) VALUES (?, ?, ?, ?)",
                    (user_id, rate, goal, notify_freq)
                )
                logger.info(f"Пользователь {user_id} добавлен в базу данных")
            
            conn.commit()
        except Exception as e:
            logger.error(f"Ошибка при добавлении/обновлении пользователя {user_id}: {e}")
            if conn:
                conn.rollback()
            raise
        finally:
            if conn:
                conn.close()

    def update_rate(self, user_id, rate):
        """Обновление почасовой ставки пользователя"""
        conn = None
        try:
            conn = sqlite3.connect(self.db_name)
            cursor = conn.cursor()
            cursor.execute("UPDATE users SET rate = ? WHERE user_id = ?", (rate, user_id))
            conn.commit()
            logger.info(f"Ставка пользователя {user_id} обновлена: {rate}")
        except Exception as e:
            logger.error(f"Ошибка при обновлении ставки пользователя {user_id}: {e}")
            if conn:
                conn.rollback()
        finally:
            if conn:
                conn.close()

    def update_goal(self, user_id, goal):
        """Обновление цели пользователя"""
        conn = None
        try:
            conn = sqlite3.connect(self.db_name)
            cursor = conn.cursor()
            cursor.execute("UPDATE users SET goal = ? WHERE user_id = ?", (goal, user_id))
            conn.commit()
            logger.info(f"Цель пользователя {user_id} обновлена: {goal}")
            return True
        except Exception as e:
            logger.error(f"Ошибка при обновлении цели пользователя {user_id}: {e}")
            if conn:
                conn.rollback()
            return False
        finally:
            if conn:
                conn.close()

    def update_notify_freq(self, user_id, notify_freq):
        """Обновление частоты уведомлений"""
        conn = None
        try:
            conn = sqlite3.connect(self.db_name)
            cursor = conn.cursor()
            cursor.execute("UPDATE users SET notify_freq = ? WHERE user_id = ?", (notify_freq, user_id))
            conn.commit()
            logger.info(f"Частота уведомлений пользователя {user_id} обновлена: {notify_freq}")
            return True
        except Exception as e:
            logger.error(f"Ошибка при обновлении частоты уведомлений пользователя {user_id}: {e}")
            if conn:
                conn.rollback()
            return False
        finally:
            if conn:
                conn.close()

    def get_user_data(self, user_id):
        """Получение данных пользователя"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute("SELECT rate, goal, earned, notify_freq FROM users WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()
        conn.close()
        
        if result:
            return {
                "rate": result[0],
                "goal": result[1],
                "earned": result[2],
                "notify_freq": result[3]
            }
        return None

    def add_time_record(self, user_id, minutes):
        """Добавление записи о потраченном времени"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        # Получаем ставку пользователя
        cursor.execute("SELECT rate FROM users WHERE user_id = ?", (user_id,))
        rate = cursor.fetchone()[0]
        
        # Рассчитываем заработок
        earnings = (minutes / 60) * rate
        
        # Добавляем запись
        cursor.execute(
            "INSERT INTO time_records (user_id, minutes, earnings) VALUES (?, ?, ?)",
            (user_id, minutes, earnings)
        )
        
        # Обновляем общий заработок пользователя
        cursor.execute(
            "UPDATE users SET earned = earned + ? WHERE user_id = ?",
            (earnings, user_id)
        )
        
        conn.commit()
        conn.close()
        
        return earnings

    def get_time_history(self, user_id, limit=10):
        """Получение истории записей времени"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT minutes, earnings, timestamp FROM time_records WHERE user_id = ? ORDER BY timestamp DESC LIMIT ?",
            (user_id, limit)
        )
        records = cursor.fetchall()
        conn.close()
        
        return [{
            "minutes": record[0],
            "earnings": record[1],
            "timestamp": record[2]
        } for record in records]

    def get_progress(self, user_id):
        """Получение прогресса пользователя"""
        data = self.get_user_data(user_id)
        if not data:
            return None
            
        percent = min(100, int((data["earned"] / data["goal"]) * 100)) if data["goal"] > 0 else 0
        hours_left = max(0, (data["goal"] - data["earned"]) / data["rate"]) if data["rate"] > 0 else 0
        
        return {
            "goal": data["goal"],
            "earned": data["earned"],
            "percent": percent,
            "hours_left": hours_left
        }

    def get_total_hours(self, user_id):
        """Получение общего количества затраченных часов"""
        conn = None
        try:
            conn = sqlite3.connect(self.db_name)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT SUM(minutes) FROM time_records WHERE user_id = ?",
                (user_id,)
            )
            total_minutes = cursor.fetchone()[0] or 0
            total_hours = total_minutes / 60
            logger.info(f"Общее количество часов пользователя {user_id}: {total_hours}")
            return total_hours
        except Exception as e:
            logger.error(f"Ошибка при получении общего времени для пользователя {user_id}: {e}")
            return 0
        finally:
            if conn:
                conn.close()

    def reset_goal(self, user_id):
        """Сбрасывает текущий прогресс (earned) пользователя, но сохраняет историю"""
        conn = None
        try:
            conn = sqlite3.connect(self.db_name)
            cursor = conn.cursor()
            
            # Получаем текущие значения, чтобы их сохранить
            cursor.execute("SELECT rate, goal, earned FROM users WHERE user_id = ?", (user_id,))
            result = cursor.fetchone()
            
            if not result:
                logger.error(f"Пользователь {user_id} не найден при сбросе цели")
                return False
                
            rate, goal, earned = result
            
            # Сбрасываем только earned (заработано)
            cursor.execute("UPDATE users SET earned = 0 WHERE user_id = ?", (user_id,))
            
            # Добавляем запись о сбросе в журнал (можно расширить бд для этого)
            logger.info(f"Сброшен прогресс для пользователя {user_id}. Было заработано: {earned}")
            
            conn.commit()
            return True
            
        except Exception as e:
            logger.error(f"Ошибка при сбросе цели пользователя {user_id}: {e}")
            if conn:
                conn.rollback()
            return False
        finally:
            if conn:
                conn.close() 