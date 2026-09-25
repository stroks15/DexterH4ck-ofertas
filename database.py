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

        # Migración para bases creadas con versiones anteriores
        columnas = [
            row[1] for row in conn.execute("PRAGMA table_info(productos)").fetchall()
        ]

        if "clave" not in columnas:
            conn.execute("ALTER TABLE productos ADD COLUMN clave TEXT")

        if "precio" not in columnas:
            conn.execute("ALTER TABLE productos ADD COLUMN precio REAL")

        if "tienda" not in columnas:
            conn.execute("ALTER TABLE productos ADD COLUMN tienda TEXT")

        if "titulo" not in columnas:
            conn.execute("ALTER TABLE productos ADD COLUMN titulo TEXT")

        if "url" not in columnas:
            conn.execute("ALTER TABLE productos ADD COLUMN url TEXT")

        if "fecha" not in columnas:
            conn.execute("ALTER TABLE productos ADD COLUMN fecha TEXT")


def debe_alertar(clave, precio_actual):
    inicializar()

    with conectar() as conn:
        fila = conn.execute(
            "SELECT precio FROM productos WHERE clave=?",
            (clave,)
        ).fetchone()

        if not fila:
            return True

        return precio_actual < fila[0]


def guardar(clave, tienda, titulo, precio, url):
    inicializar()

    with conectar() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO productos
            (clave, tienda, titulo, precio, url, fecha)
            VALUES (?, ?, ?, ?, ?, ?)""",
            (clave, tienda, titulo, precio, url, datetime.now().isoformat())
        )
