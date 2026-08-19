"""Script de migração único: lê os saldos finais de cada extrato de uma
pasta (topo do ficheiro: "Saldo contabilístico"/"Saldo disponível") e
grava-os em saldos_diarios.

Uso:
    python scripts/importar_saldos.py <pasta> <DD-MM-YYYY>
"""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.session import SessionLocal
from app.services.monitorizacao import monitorizar_execucao
from app.services.saldos import registar_saldos_do_dia


def main():
    if len(sys.argv) != 3:
        print("Uso: python scripts/importar_saldos.py <pasta> <DD-MM-YYYY>")
        sys.exit(1)

    pasta, data_str = sys.argv[1], sys.argv[2]
    dia = datetime.strptime(data_str, "%d-%m-%Y").date()

    db = SessionLocal()
    try:
        with monitorizar_execucao(db, "importar_saldos") as log:
            total = registar_saldos_do_dia(db, dia, pasta)
            mensagem = f"Registadas {total} entidades com saldo para o dia {data_str}."
            print(mensagem)
            log.append(mensagem)
    finally:
        db.close()


if __name__ == "__main__":
    main()
