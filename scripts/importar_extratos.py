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
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.session import SessionLocal
from app.services.reconciliador import importar_extrato_para_bd


def nome_empresa_do_ficheiro(caminho):
    nome = os.path.splitext(os.path.basename(caminho))[0]
    nome = re.sub(r"^\d{2}-\d{2}-\d{4}_", "", nome)
    return nome.strip()


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
    total = 0
    try:
        for caminho in ficheiros:
            empresa = nome_empresa_do_ficheiro(caminho)
            n = importar_extrato_para_bd(db, caminho, dia, empresa)
            total += n
            print(f"  {os.path.basename(caminho)} -> {n} movimentos ({empresa})")
    finally:
        db.close()

    print(f"Total importado: {total} movimentos para o dia {data_str}.")


if __name__ == "__main__":
    main()
