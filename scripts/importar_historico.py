"""Importa todo o histórico de extratos (mensais + diários) da pasta do
OneDrive para a base de dados - ver onedrive_sync.importar_historico.

Uso: python scripts/importar_historico.py [aaaa-mm-dd]"""
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from app.db.session import SessionLocal
from app.services.onedrive_sync import importar_historico

if __name__ == "__main__":
    desde = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else None
    db = SessionLocal()
    try:
        r = importar_historico(db, desde)
    finally:
        db.close()
    for chave in ("dias_com_movimentos_novos", "dias_com_saldos_novos", "dias_com_mapa_novo"):
        r[chave] = len(r[chave])
    print(json.dumps(r, ensure_ascii=False, indent=2))
