import sqlite3
from typing import List, Optional, Dict
from datetime import datetime

DATABASE_NAME = 'database.db'

def connect_db():
    conn = sqlite3.connect(DATABASE_NAME)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def initialize_database():
    conn = connect_db()
    cursor = conn.cursor()

    if cursor.execute('SELECT name FROM sqlite_master WHERE type="table" AND name="users"').fetchone() is None:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                username TEXT DEFAULT NULL,
                stars REAL DEFAULT 0.0,
                referral_id INTEGER DEFAULT NULL,
                withdrawn REAL DEFAULT 0.0,
                registration_time REAL DEFAULT (strftime('%s','now')),
                banned INTEGER DEFAULT 0,
                count_photo_selling INTEGER DEFAULT 0
            )
        ''')
        print('Таблица "users" создана')
    else:
        print('Выполнено подключение к таблице "users".')

    if cursor.execute('SELECT name FROM sqlite_master WHERE type="table" AND name="channels_op"').fetchone() is None:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS channels_op (
                id INTEGER PRIMARY KEY,
                id_channel TEXT NOT NULL,
                link_invite TEXT DEFAULT NULL
            )
        ''')
        print('Таблица "channels_op" создана')
    else:
        print('Выполнено подключение к таблице "channels_op".')
    
    if cursor.execute('SELECT name FROM sqlite_master WHERE type="table" AND name="promocodes"').fetchone() is None:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS promocodes (
                id INTEGER PRIMARY KEY,
                code TEXT NOT NULL UNIQUE,
                stars REAL NOT NULL,
                max_uses INTEGER NOT NULL,
                current_uses INTEGER DEFAULT 0,
                is_active BOOLEAN DEFAULT TRUE
            )
        ''')
        print('Таблица "promocodes" создана')
    else:
        print('Выполнено подключение к таблице "promocodes".')

    if cursor.execute('SELECT name FROM sqlite_master WHERE type="table" AND name="promocode_uses"').fetchone() is None:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS promocode_uses (
                id INTEGER PRIMARY KEY,
                promocode_id INTEGER,
                user_id INTEGER,
                used_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (promocode_id) REFERENCES promocodes(id),
                FOREIGN KEY (user_id) REFERENCES users(id),
                UNIQUE(promocode_id, user_id)
            )
        ''')
        print('Таблица "promocode_uses" создана')
    else:
        print('Выполнено подключение к таблице "promocode_uses".')

    if cursor.execute('SELECT name FROM sqlite_master WHERE type="table" AND name="withdrawales"').fetchone() is None:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS withdrawales (
                id INTEGER PRIMARY KEY,
                username TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                stars REAL NOT NULL,
                status TEXT NOT NULL
            )
        """)
        print('Таблица "withdrawales" создана')
    else:
        print('Выполнено подключение к таблице "withdrawales".')
    
    if cursor.execute('SELECT name FROM sqlite_master WHERE type="table" AND name="photos"').fetchone() is None:
        cursor.execute("""
            CREATE TABLE photos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                price REAL NOT NULL,
                path_to_photo TEXT NOT NULL,
                purchased INTEGER NOT NULL DEFAULT 0,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)
        print('Таблица "photos" создана')
    else:
        print('Выполнено подключение к таблице "photos".')
    
    conn.commit()
    conn.close()
    print('База данных успешно инициализирована.')

initialize_database()

def add_channel(id_channel: str, link_invite: str = None):
    with sqlite3.connect(DATABASE_NAME) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO channels_op (id_channel, link_invite) VALUES (?, ?)",
            (id_channel, link_invite)
        )
        conn.commit()


def get_all_channels():
    with sqlite3.connect(DATABASE_NAME) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM channels_op")
        return [dict(row) for row in cursor.fetchall()]


def get_channels_ids():
    with sqlite3.connect(DATABASE_NAME) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT id_channel FROM channels_op")
        rows = cursor.fetchall()
        return [row["id_channel"] for row in rows]


def get_channel(id_channel: str):
    with sqlite3.connect(DATABASE_NAME) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM channels_op WHERE id_channel = ?", (id_channel,))
        row = cursor.fetchone()
        return dict(row) if row else None


def update_invite_link(id_channel: str, new_link: str):
    with sqlite3.connect(DATABASE_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE channels_op SET link_invite = ? WHERE id_channel = ?",
            (new_link, id_channel)
        )
        conn.commit()


def delete_channel(id_channel: str):
    with sqlite3.connect(DATABASE_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM channels_op WHERE id_channel = ?", (id_channel,))
        conn.commit()



def get_user_log_html(user_id: int) -> str:
    with sqlite3.connect(DATABASE_NAME) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        cur.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        user = cur.fetchone()
        if not user:
            return f"<b>Пользователь с id={user_id} не найден.</b>"

        reg_time = datetime.fromtimestamp(user["registration_time"])\
                       .strftime("%Y-%m-%d %H:%M:%S")
        stars      = user["stars"]
        withdrawn  = user["withdrawn"]
        banned     = user["banned"]
        username   = user["username"] or "—"
        ref_id     = user["referral_id"] or "—"
        sell_cnt   = user["count_photo_selling"]

        cur.execute("SELECT COUNT(*) FROM users WHERE referral_id = ?", (user_id,))
        count_ref = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM promocode_uses WHERE user_id = ?", (user_id,))
        promo_count = cur.fetchone()[0]

        cur.execute("""
            SELECT 
                COUNT(*) AS total_photos,
                SUM(CASE WHEN purchased = 1 THEN 1 ELSE 0 END) AS sold_photos
            FROM photos
            WHERE user_id = ?
        """, (user_id,))
        photos = cur.fetchone()
        total_photos = photos["total_photos"] or 0
        sold_photos  = photos["sold_photos"]  or 0

    # Собираем HTML
    return (
        f"🧾<b>Информация о пользователе:</b>\n\n"
        f"👤 <b>ID пользователя:</b> <code>{user_id}</code>\n"
        f"📛 <b>Имя пользователя:</b> {username}\n"
        f"⭐️ <b>Звёзды:</b> {stars:.2f}\n"
        f"<b>────────────────────────────────────────</b>\n"
        f"👥 <b>Рефералов:</b> {count_ref}\n"
        f"🔗 <b>ID реферера:</b> {ref_id}\n"
        f"<b>────────────────────────────────────────</b>\n"
        f"🎟️ <b>Промокодов использовано:</b> {promo_count}\n"
        f"💰 <b>Выведено:</b> {withdrawn:.2f}\n"
        f"<b>────────────────────────────────────────</b>\n"
        f"📷 <b>Фото (всего/продано):</b> {total_photos}/{sold_photos}\n"
        f"<b>────────────────────────────────────────</b>\n"
        f"⏰ <b>Дата регистрации:</b> {reg_time}\n"
        f"🚦 <b>Статус:</b> {'🟩 Не заблокирован' if banned == 0 else '❌ Заблокирован'}"
    )
    
def get_banned_user(user_id):
    with sqlite3.connect(DATABASE_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT banned FROM users WHERE id = ?", (user_id,))
        result = cursor.fetchone()
        if result:
            return result[0]
        else:
            return 0
        
def set_banned_user(user_id, banned):
    with sqlite3.connect(DATABASE_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET banned = ? WHERE id = ?", (banned, user_id))
        conn.commit()
        return True

def add_photo(user_id: int, price: float, path_to_photo: str) -> int:
    with sqlite3.connect(DATABASE_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO photos (user_id, price, path_to_photo) VALUES (?, ?, ?)",
            (user_id, price, path_to_photo)
        )
        return cursor.lastrowid

def get_photo(photo_id: int) -> Optional[Dict]:
    with sqlite3.connect(DATABASE_NAME) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM photos WHERE id = ?", (photo_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

def list_photos(only_unsold: bool = True) -> List[Dict]:
    with sqlite3.connect(DATABASE_NAME) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        if only_unsold:
            cursor.execute("SELECT * FROM photos WHERE purchased = 0 ORDER BY created_at DESC")
        else:
            cursor.execute("SELECT * FROM photos ORDER BY created_at DESC")
        return [dict(r) for r in cursor.fetchall()]

def delete_photo(photo_id: int) -> bool:
    with sqlite3.connect(DATABASE_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM photos WHERE id = ?", (photo_id,))
        return cursor.rowcount > 0

def mark_photo_purchased(photo_id: int) -> bool:
    with sqlite3.connect(DATABASE_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE photos SET purchased = 1 WHERE id = ? AND purchased = 0",
            (photo_id,)
        )
        return cursor.rowcount > 0
    
def get_user_photos(user_id: int, only_unsold: bool = False) -> List[Dict]:
    with sqlite3.connect(DATABASE_NAME) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        if only_unsold:
            cursor.execute(
                "SELECT * FROM photos WHERE user_id = ? AND purchased = 0 ORDER BY created_at DESC",
                (user_id,)
            )
        else:
            cursor.execute(
                "SELECT * FROM photos WHERE user_id = ? ORDER BY created_at DESC",
                (user_id,)
            )
        return [dict(r) for r in cursor.fetchall()]

def add_withdrawale(username, user_id, stars, status='Ожидает обработки ⚙️'):
    with sqlite3.connect(DATABASE_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute('INSERT INTO withdrawales (username, user_id, stars, status) VALUES (?, ?, ?, ?)', (username, user_id, stars, status))
        conn.commit()
        return True, cursor.lastrowid

def update_status_withdrawal(withdrawal_id, status):
    with sqlite3.connect(DATABASE_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute('UPDATE withdrawales SET status = ? WHERE id = ?', (status, withdrawal_id))
        conn.commit()
        return True

def get_status_withdrawal(user_id):
    with sqlite3.connect(DATABASE_NAME) as conn:
        cursor = conn.cursor()
        return cursor.execute('SELECT status FROM withdrawales WHERE user_id = ?', (user_id,)).fetchone()[0]

def get_withdrawals(user_id):
    with sqlite3.connect(DATABASE_NAME) as conn:
        cursor = conn.cursor()
        return cursor.execute('SELECT * FROM withdrawales WHERE user_id = ?', (user_id,)).fetchall()

def add_promocode(code, stars, max_uses):
    with sqlite3.connect(DATABASE_NAME) as conn:
        cursor = conn.cursor()
        try:
            cursor.execute('INSERT INTO promocodes (code, stars, max_uses) VALUES (?, ?, ?)',
                          (code, stars, max_uses))
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False
        
def get_all_promocodes():
    try:
        with sqlite3.connect(DATABASE_NAME) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM promocodes")
            rows = cursor.fetchall()
            promocodes = [dict(row) for row in rows]
            return promocodes
    except sqlite3.Error as e:
        print(f"Ошибка доступа к базе данных: {e}")
        return []

def use_promocode(code, user_id):
    with sqlite3.connect(DATABASE_NAME) as conn:
        cursor = conn.cursor()
        try:
            promo = cursor.execute('''
                SELECT * FROM promocodes
                WHERE code = ? AND is_active = TRUE
                AND current_uses < max_uses
            ''', (code,)).fetchone()

            if not promo:
                return False, "Промокод недействителен или закончились использования"

            used = cursor.execute('''
                SELECT 1 FROM promocode_uses
                WHERE promocode_id = ? AND user_id = ?
            ''', (promo[0], user_id)).fetchone()

            if used:
                return False, "Вы уже использовали этот промокод"

            cursor.execute('''
                UPDATE promocodes
                SET current_uses = current_uses + 1
                WHERE code = ?
            ''', (code,))

            cursor.execute('''
                INSERT INTO promocode_uses (promocode_id, user_id)
                VALUES (?, ?)
            ''', (promo[0], user_id))

            cursor.execute('''
                UPDATE users
                SET stars = stars + ?
                WHERE id = ?
            ''', (promo[2], user_id))

            conn.commit()
            return True, promo[2]
        except Exception as e:
            conn.rollback()
            return False, f"❌ {str(e)}"

def delete_promocode(code):
    with sqlite3.connect(DATABASE_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM promocodes WHERE code = ?', (code,))
        conn.commit()

def deactivate_promocode(code):
    with sqlite3.connect(DATABASE_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute('UPDATE promocodes SET is_active = FALSE WHERE code = ?', (code,))
        conn.commit()

def add_user(user_id, username, referral_id):
    with sqlite3.connect(DATABASE_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute('INSERT INTO users (id, username, referral_id) VALUES (?, ?, ?)', (user_id, username, referral_id))
        conn.commit()

def get_total_withdrawn():
    with sqlite3.connect(DATABASE_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT SUM(withdrawn) FROM users')
        result = cursor.fetchone()[0]
        return result or 0.0

def get_withdrawed(user_id):
    with sqlite3.connect(DATABASE_NAME) as conn:
        cursor = conn.cursor()
        result = cursor.execute('SELECT withdrawn FROM users WHERE id = ?', (user_id,)).fetchone()
        return result[0]

def get_total_photo_selling_count():
    with sqlite3.connect(DATABASE_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT SUM(count_photo_selling) FROM users')
        result = cursor.fetchone()[0]
        return result or 0

def increment_count_photo_selling(user_id):
    with sqlite3.connect(DATABASE_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute('UPDATE users SET count_photo_selling = count_photo_selling + 1 WHERE id = ?', (user_id,))
        conn.commit()

def user_exists(user_id):
    with sqlite3.connect(DATABASE_NAME) as conn:
        cursor = conn.cursor()
        result = cursor.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
        return bool(result)
    
def get_balance_user(user_id):
    with sqlite3.connect(DATABASE_NAME) as conn:
        cursor = conn.cursor()
        result = cursor.execute('SELECT stars FROM users WHERE id = ?', (user_id,)).fetchone()
        return result[0]

def get_photo_sell_count(user_id):
    with sqlite3.connect(DATABASE_NAME) as conn:
        cursor = conn.cursor()
        result = cursor.execute('SELECT count_photo_selling FROM users WHERE id = ?', (user_id,)).fetchone()
        return result[0]

def add_withdrawal(user_id, amount):
    with sqlite3.connect(DATABASE_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute('UPDATE users SET withdrawn = withdrawn + ? WHERE id = ?', (amount, user_id))
        conn.commit()

def get_count_users():
    with sqlite3.connect(DATABASE_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM users')
        return cursor.fetchone()[0]

def get_users_ids():
    try:
        with sqlite3.connect(DATABASE_NAME) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT id FROM users')
            return [str(row[0]) for row in cursor.fetchall()]
            
    except sqlite3.Error as e:
        print(f"Database error: {e}")
        return []

def get_banned_user(user_id):
    with sqlite3.connect(DATABASE_NAME) as conn:
        cursor = conn.cursor()
        row = cursor.execute(
            'SELECT banned FROM users WHERE id = ?', 
            (user_id,)
        ).fetchone()
        return row[0] if row is not None else 0

def add_stars(user_id, amount):
    with sqlite3.connect(DATABASE_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute('UPDATE users SET stars = stars + ? WHERE id = ?', (amount, user_id))
        conn.commit()

def remove_stars(user_id, amount):
    with sqlite3.connect(DATABASE_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute('UPDATE users SET stars = stars - ? WHERE id = ?', (amount, user_id))
        conn.commit()





