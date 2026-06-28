import sqlite3

def init_db():
    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()
    # Таблица пользователей
    cursor.execute('''CREATE TABLE IF NOT EXISTS users 
                      (user_id INTEGER PRIMARY KEY, balance INTEGER DEFAULT 10000000)''')
    # Таблица рефералов (кто кого пригласил)
    cursor.execute('''CREATE TABLE IF NOT EXISTS referrals 
                      (referrer_id INTEGER, invited_id INTEGER)''')
    conn.commit()
    conn.close()

def add_referral(referrer_id, invited_id):
    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()
    # Проверяем, не был ли этот пользователь уже приглашен кем-то
    cursor.execute("SELECT * FROM referrals WHERE invited_id = ?", (invited_id,))
    if cursor.fetchone() is None:
        cursor.execute("INSERT INTO referrals VALUES (?, ?)", (referrer_id, invited_id))
        cursor.execute("UPDATE users SET balance = balance + 50000 WHERE user_id = ?", (referrer_id,))
        conn.commit()
        conn.close()
        return True
    conn.close()
    return False
