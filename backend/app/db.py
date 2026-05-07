import psycopg2
from psycopg2.extras import RealDictCursor

from app.config import Config


def get_db_connection():
    """
    Create and return a PostgreSQL connection.
    RealDictCursor makes query results easier to convert into JSON.
    """
    return psycopg2.connect(
        host=Config.DB_HOST,
        port=Config.DB_PORT,
        dbname=Config.DB_NAME,
        user=Config.DB_USER,
        password=Config.DB_PASSWORD,
        cursor_factory=RealDictCursor
    )