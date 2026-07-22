import os
import shutil
import tempfile
import time

import openpyxl
from sqlalchemy.orm import Session

from app.db.models import MovimentoBancario


def abrir_workbook_com_retry(caminho, tentativas=5, espera=1.0):
    """Abre um .xlsx com openpyxl, tentando novamente em caso de
    PermissionError transitório (ficheiros sincronizados no OneDrive,
    antivírus a indexar, ficheiro aberto no Excel). Lê a partir de uma
    cópia temporária para não depender de o ficheiro original ficar
    livre a meio da tentativa."""
    ultimo_erro = None
    for tentativa in range(tentativas):
        try:
            with tempfile.TemporaryDirectory() as tmp:
                destino = os.path.join(tmp, os.path.basename(caminho))
                shutil.copyfile(caminho, destino)
                return openpyxl.load_workbook(destino, data_only=True)
        except PermissionError as e:
            ultimo_erro = e
            if tentativa < tentativas - 1:
                time.sleep(espera)
    raise PermissionError(
        f"Não consegui ler '{caminho}' depois de {tentativas} tentativas "
        f"(ficheiro continua bloqueado)."
    ) from ultimo_erro


def ler_movimentos_do_extrato(caminho):
    wb = abrir_workbook_com_retry(caminho)
    ws = wb.active

    linha_cabecalho = None
    for row in ws.iter_rows(min_row=1, max_row=20):
        if row[0].value and str(row[0].value).strip().startswith("Data mov"):
            linha_cabecalho = row[0].row
            break
    if linha_cabecalho is None:
        return []

    movimentos = []
    for row in ws.iter_rows(min_row=linha_cabecalho + 1):
        data_mov, _data_valor, descricao, montante = row[0].value, row[1].value, row[2].value, row[3].value
        if data_mov is None and descricao is None:
            break
        if montante is None:
            continue
        valor = float(str(montante).strip().replace(".", "").replace(",", "."))
        movimentos.append({
            "descricao": str(descricao).strip() if descricao else "",
            "valor": valor,
        })
    return movimentos


def importar_extrato_para_bd(db: Session, caminho: str, dia, empresa: str) -> int:
    """Lê um ficheiro de extrato (.xlsx) e grava cada movimento como uma
    linha em movimentos_bancarios. Devolve o número de movimentos
    inseridos. Não faz deteção de duplicados (isso é Fase 2 -
    reconciliação); esta função só migra os dados brutos do Excel para
    a base de dados."""
    movimentos = ler_movimentos_do_extrato(caminho)
    for mov in movimentos:
        db.add(MovimentoBancario(
            dia=dia,
            empresa=empresa,
            descricao=mov["descricao"],
            valor=mov["valor"],
            ficheiro_origem=os.path.basename(caminho),
        ))
    db.commit()
    return len(movimentos)
