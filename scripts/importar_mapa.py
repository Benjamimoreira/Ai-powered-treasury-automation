"""Script de migração único: lê as linhas de um dia do Mapa de Pagamentos
e Recebimentos e importa-as para a tabela linhas_mapa. Só lê o .xlsx -
nunca escreve nele.

Uso:
    python scripts/importar_mapa.py <caminho_mapa.xlsx> <DD-MM-YYYY>
"""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.session import SessionLocal
from app.services.mapa_importer import importar_dia_do_mapa
from app.services.monitorizacao import monitorizar_execucao


def main():
    if len(sys.argv) != 3:
        print("Uso: python scripts/importar_mapa.py <caminho_mapa.xlsx> <DD-MM-YYYY>")
        sys.exit(1)

    caminho, data_str = sys.argv[1], sys.argv[2]
    dia = datetime.strptime(data_str, "%d-%m-%Y").date()

    db = SessionLocal()
    try:
        try:
            with monitorizar_execucao(db, "importar_mapa") as log:
                n_receb, n_pag = importar_dia_do_mapa(db, caminho, dia)
                db.commit()
                mensagem = f"Importadas {n_receb} linhas de recebimento e {n_pag} linhas de pagamento para o dia {data_str}."
                print(mensagem)
                log.append(mensagem)
        except KeyError as e:
            print(str(e))
            sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
