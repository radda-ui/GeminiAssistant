import sqlite3
import json


class Database:
    def __init__(self, db_path):
        self.conn = sqlite3.connect(db_path)
        self.create_tables()

    def create_tables(self):
        cursor = self.conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER,
                role TEXT,
                content TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (conversation_id) REFERENCES conversations (id)
            )
        ''')
        self.conn.commit()

    def save_message(self, conversation_id, role, content):
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT INTO messages (conversation_id, role, content)
            VALUES (?, ?, ?)
        ''', (conversation_id, role, content))
        self.conn.commit()

    def get_conversation(self, conversation_id):
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT role, content FROM messages
            WHERE conversation_id = ?
            ORDER BY timestamp
        ''', (conversation_id,))
        return [{"role": row[0], "content": row[1]} for row in cursor.fetchall()]

    def get_all_conversations_meta(self):
        """Returns (conv_id, first_user_message, last_message) for each conversation."""
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT c.id, first_msg.content, last_msg.content
            FROM conversations c
            LEFT JOIN (
                SELECT conversation_id, content,
                       ROW_NUMBER() OVER(PARTITION BY conversation_id ORDER BY timestamp ASC) as rn
                FROM messages
                WHERE role = 'user'
            ) first_msg ON c.id = first_msg.conversation_id AND first_msg.rn = 1
            LEFT JOIN (
                SELECT conversation_id, content,
                       ROW_NUMBER() OVER(PARTITION BY conversation_id ORDER BY timestamp DESC) as rn
                FROM messages
            ) last_msg ON c.id = last_msg.conversation_id AND last_msg.rn = 1
            ORDER BY c.timestamp DESC
        ''')
        return cursor.fetchall()

    def delete_conversation(self, conversation_id):
        """Delete a conversation and all its messages."""
        cursor = self.conn.cursor()
        cursor.execute('DELETE FROM messages WHERE conversation_id = ?', (conversation_id,))
        cursor.execute('DELETE FROM conversations WHERE id = ?', (conversation_id,))
        self.conn.commit()

    def close(self):
        self.conn.close()