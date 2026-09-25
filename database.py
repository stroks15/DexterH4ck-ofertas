import sqlite3
from datetime import datetime

DB = "historial.db"


def conectar():
    return sqlite3.connect(DB)


def inicializar():
    with conectar() as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS productos (
            clave TEXT PRIMARY KEY,
            tienda TEXT,
            titulo TEXT,
            precio REAL,
            url TEXT,
            fecha TEXT
        )
        """)


def debe_alertar(clave, precio_actual):
    with conectar() as conn:
        fila = conn.execute(
            "SELECT precio FROM productos WHERE clave=?",
            (clave,)
        ).fetchone()

        if not fila:
            return True

        return precio_actual < fila[0]


def guardar(clave, tienda, titulo, precio, url):
    with conectar() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO productos
            (clave, tienda, titulo, precio, url, fecha)
            VALUES (?, ?, ?, ?, ?, ?)""",
            (clave, tienda, titulo, precio, url, datetime.now().isoformat())
        )
