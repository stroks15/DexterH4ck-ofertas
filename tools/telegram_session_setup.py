"""Genera TELEGRAM_SESSION_STRING para leer el grupo privado con Telethon.

EJECUTAR SOLO EN TU EQUIPO (pide teléfono, código y 2FA por consola); nunca en GitHub Actions.

    export TELEGRAM_API_ID=...        # https://my.telegram.org -> API development tools
    export TELEGRAM_API_HASH=...
    export TELEGRAM_WALMART_INVITE="https://t.me/+XXXXXXXXXXXX"   # opcional, para unir la cuenta al grupo
    python tools/telegram_session_setup.py --unirse

Copia la cadena impresa a GitHub -> Settings -> Secrets -> TELEGRAM_SESSION_STRING.
La cadena equivale a una sesión iniciada: trátala como una contraseña, no la subas al repositorio y,
si se filtra, ciérrala en Telegram -> Ajustes -> Dispositivos. Usa una cuenta secundaria dedicada.
"""
from __future__ import annotations

import argparse
import os
import re
import sys


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--unirse", action="store_true", help="unir la cuenta al grupo con TELEGRAM_WALMART_INVITE")
    args = ap.parse_args()
    try:
        api_id, api_hash = int(os.environ["TELEGRAM_API_ID"]), os.environ["TELEGRAM_API_HASH"]
    except (KeyError, ValueError):
        print("Define TELEGRAM_API_ID (numérico) y TELEGRAM_API_HASH.", file=sys.stderr)
        return 1

    from telethon import TelegramClient, errors
    from telethon.sessions import StringSession
    from telethon.tl.functions.messages import ImportChatInviteRequest

    with TelegramClient(StringSession(), api_id, api_hash) as client:
        if args.unirse:
            m = re.search(r"([A-Za-z0-9_-]{12,})$", (os.environ.get("TELEGRAM_WALMART_INVITE") or "").strip().rstrip("/"))
            if not m:
                print("Falta TELEGRAM_WALMART_INVITE para --unirse.", file=sys.stderr)
            else:
                try:
                    client(ImportChatInviteRequest(m.group(1)))
                    print("Cuenta unida al grupo.")
                except errors.UserAlreadyParticipantError:
                    print("La cuenta ya es miembro del grupo.")
                except Exception as exc:
                    print(f"No se pudo unir al grupo: {type(exc).__name__}", file=sys.stderr)
        print("\nTELEGRAM_SESSION_STRING (guárdalo como secret, no lo compartas):\n")
        print(client.session.save())
    return 0


if __name__ == "__main__":
    sys.exit(main())
