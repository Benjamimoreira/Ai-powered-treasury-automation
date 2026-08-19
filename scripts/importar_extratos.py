"""Script de migração único: lê todos os extratos .xlsx de uma pasta e
importa os movimentos para a base de dados (tabela movimentos_bancarios).

Uso:
    python scripts/importar_extratos.py <pasta> <DD-MM-YYYY>

Os ficheiros devem seguir o padrão "DD-MM-AAAA_NomeEmpresa.xlsx" (mesmo
formato usado pelo preencher_mapa.py). Este script não faz reconciliação
nem deteção de duplicados - só migra os dados brutos do Excel para a BD;
correr mais que uma vez para o mesmo dia duplica os movimentos.
"""
import glob
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.session import SessionLocal
from app.services.monitorizacao import monitorizar_execucao
from app.services.reconciliador import importar_extrato_para_bd, nome_empresa_do_ficheiro


def main():
    if len(sys.argv) != 3:
        print("Uso: python scripts/importar_extratos.py <pasta> <DD-MM-YYYY>")
        sys.exit(1)

    pasta, data_str = sys.argv[1], sys.argv[2]
    dia = datetime.strptime(data_str, "%d-%m-%Y").date()

    ficheiros = sorted(glob.glob(os.path.join(pasta, "*.xlsx")))
    if not ficheiros:
        print(f"Nenhum ficheiro .xlsx encontrado em {pasta}")
        sys.exit(1)

    db = SessionLocal()
    try:
        with monitorizar_execucao(db, "importar_extratos") as log:
            total = 0
            for caminho in ficheiros:
                empresa = nome_empresa_do_ficheiro(caminho)
                n = importar_extrato_para_bd(db, caminho, dia, empresa)
                total += n
                mensagem = f"  {os.path.basename(caminho)} -> {n} movimentos ({empresa})"
                print(mensagem)
                log.append(mensagem)

            print(f"Total importado: {total} movimentos para o dia {data_str}.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
