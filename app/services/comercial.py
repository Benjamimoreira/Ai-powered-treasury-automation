"""Índice do Departamento Comercial (CPCVs/Escrituras) - lido do ficheiro
do site SharePoint DEPTCOMERCIAL ("Índice do Departamento Comercial") para
a tabela "CPCVs / Escrituras do período" da aba Análise de Extratos.

Duas formas de apontar para lá (ver _caminho_indice()):
- COMERCIAL_INDICE_PATH a um .xlsx numa pasta SINCRONIZADA do SharePoint
  (site DEPTCOMERCIAL adicionado como atalho/sincronizado no OneDrive
  desta máquina, mesmo mecanismo já usado para o Mapa/extratos em
  ONEDRIVE_RAIZ) - fica sempre atualizado sozinho, sem passo manual
  nenhum; lido diretamente com pandas.read_excel.
- COMERCIAL_INDICE_CSV (nome antigo, mantido por compatibilidade) a um
  .csv exportado manualmente do SharePoint - só usado quando
  COMERCIAL_INDICE_PATH não está definido; requer descarregar/exportar
  de novo sempre que se quiserem dados atualizados.
Nunca inventa dados: sem ficheiro ou sem linhas no período, a tabela fica
simplesmente vazia."""
import os
from datetime import datetime
from typing import Optional

import pandas as pd

# Colunas por posição (0-indexed), não por nome - o ficheiro tem
# cabeçalho em duas linhas (grupo + campo, ex. "Assinatura CPCV" / "Data")
# e muitas colunas vazias à direita (exportação direta de um Excel com
# formatação larga), por isso ler por nome de coluna não é fiável.
COL_REF = 5
COL_ESPACO_FISICO = 3
COL_FRACAO = 4
COL_NOME_CLIENTE = 2
COL_EMPRESA = 7
COL_VALOR_TABELA = 10
COL_VALOR_PROPOSTO = 11
COL_DATA_CPCV = 12
COL_VALOR_RECEBIDO = 13
# Calendário de pagamentos do comprador depois do CPCV (grupos "Reforço
# Sinal" x2 e "Escritura" do cabeçalho): pares (coluna da data, coluna do
# valor, tipo) - usados pela previsão de saldo (ver
# listar_recebimentos_previstos).
AGENDA_RECEBIMENTOS = (
    (14, 15, "Reforço de sinal"),
    (16, 17, "Reforço de sinal"),
    (18, 19, "Escritura"),
)
PRIMEIRA_LINHA_DE_DADOS = 4  # 0-indexed: linhas 0-3 são título/cabeçalhos

CAMINHO_INDICE_OMISSAO = (
    r"C:\Users\Benjamim\Downloads\Índice do Departamento Comercial 15_05(Mapa CPCV 2026).csv"
)


def _caminho_indice() -> str:
    """COMERCIAL_INDICE_PATH (um .xlsx numa pasta sincronizada do
    SharePoint - ver docstring do módulo) tem prioridade sobre
    COMERCIAL_INDICE_CSV (nome antigo, .csv exportado à mão) - só cai no
    caminho de omissão (Downloads) quando nenhuma das duas está definida."""
    return (
        os.environ.get("COMERCIAL_INDICE_PATH")
        or os.environ.get("COMERCIAL_INDICE_CSV")
        or CAMINHO_INDICE_OMISSAO
    )


def _parse_valor_misto(valor) -> Optional[float]:
    """Os valores monetários deste ficheiro vêm em dois formatos
    diferentes, linha a linha (exportação manual do Excel, sem
    formatação consistente): "390,000.00 €" (americano, vírgula=milhar)
    e "45.000,00" (europeu, ponto=milhar) - deteta qual é pela posição
    do último separador, em vez de assumir sempre o mesmo formato."""
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return None
    if isinstance(valor, (int, float)):
        return float(valor)
    texto = str(valor).replace("€", "").replace("\xa0", " ").strip()
    if not texto:
        return None
    if "," in texto and "." in texto:
        if texto.rfind(",") > texto.rfind("."):
            texto = texto.replace(".", "").replace(",", ".")
        else:
            texto = texto.replace(",", "")
    elif "," in texto:
        parte_decimal = texto.split(",")[-1]
        texto = texto.replace(",", ".") if len(parte_decimal) == 2 else texto.replace(",", "")
    try:
        return float(texto)
    except ValueError:
        return None


def _parse_data_cpcv(valor):
    """Datas no .csv exportado à mão vêm como texto em formato americano
    (M/D/AAAA, ex. "7/31/2026") - confirmado por valores como dia 31 num
    mês que não pode ser o primeiro campo se fosse dia-primeiro. Lido
    diretamente do .xlsx (pasta sincronizada - ver COMERCIAL_INDICE_PATH),
    o pandas já devolve a data como Timestamp/datetime, não texto - trata-
    -se esse caso primeiro, sem tentar fazer parse de texto nenhum."""
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return None
    if isinstance(valor, datetime):
        return valor.date()
    if hasattr(valor, "year") and hasattr(valor, "month") and hasattr(valor, "day"):
        return valor  # já é um datetime.date (ou compatível)
    try:
        return datetime.strptime(str(valor).strip(), "%m/%d/%Y").date()
    except ValueError:
        return None


def _parse_data_agenda(valor, dia_do_cpcv: Optional[int] = None):
    """Datas de reforço/escritura: no .csv aparecem nos dois formatos na
    mesma coluna ("1/31/2027" americano, "16/10/2026" europeu). Quando só
    uma leitura é válida, é essa; quando as duas são (ex. "4/9/2026"),
    fica a que mantém o dia do mês do CPCV - os reforços são marcados "a
    X meses do CPCV" (CPCV a 3/4 -> reforço a 4/9, 4 de setembro). Sem
    desempate possível, formato americano (o do resto do ficheiro)."""
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return None
    if isinstance(valor, datetime):
        return valor.date()
    if hasattr(valor, "year") and hasattr(valor, "month") and hasattr(valor, "day"):
        return valor
    texto = str(valor).strip()
    leituras = []
    for formato in ("%m/%d/%Y", "%d/%m/%Y"):
        try:
            leituras.append(datetime.strptime(texto, formato).date())
        except ValueError:
            pass
    if not leituras:
        return None
    if dia_do_cpcv is not None:
        mesmo_dia = [d for d in leituras if d.day == dia_do_cpcv]
        if mesmo_dia:
            return mesmo_dia[0]
    return leituras[0]


def _texto_ou_none(valor) -> Optional[str]:
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return None
    texto = str(valor).strip()
    return texto or None


EXTENSOES_EXCEL = (".xlsx", ".xlsm", ".xls")


def _ler_linhas_indice():
    """Lê todas as linhas de dados do índice comercial (ver
    _caminho_indice()), sem filtro nenhum - base comum de
    listar_cpcv_escrituras() (filtra por data de assinatura) e
    mapa_espaco_fracao_por_ref() (procura estática por código REF,
    independente de quando o CPCV foi assinado). Devolve um DataFrame
    vazio (não um erro) se o ficheiro não existir.

    Lê .xlsx/.xlsm/.xls diretamente (caso normal quando o site
    DEPTCOMERCIAL está sincronizado - ver COMERCIAL_INDICE_PATH) ou .csv
    (exportação manual antiga, COMERCIAL_INDICE_CSV) - mesmas posições de
    coluna nos dois formatos, o ficheiro é o mesmo, só muda como chega
    aqui."""
    caminho = _caminho_indice()
    if not os.path.isfile(caminho):
        return pd.DataFrame()
    if caminho.lower().endswith(EXTENSOES_EXCEL):
        bruto = pd.read_excel(caminho, header=None, sheet_name=0)
    else:
        bruto = pd.read_csv(caminho, encoding="cp1252", header=None, skip_blank_lines=False)
    dados = bruto.iloc[PRIMEIRA_LINHA_DE_DADOS:]
    return dados[dados[0].notna()]


def listar_cpcv_escrituras(dia_inicio=None, dia_fim=None) -> list:
    """Lê o índice comercial (ver _caminho_indice()) e devolve as linhas
    cuja data de assinatura do CPCV caia em [dia_inicio, dia_fim].
    Devolve lista vazia (não um erro) se o ficheiro não existir - a
    tabela do dashboard já trata isso como "sem dados", não como falha
    da API."""
    dados = _ler_linhas_indice()

    resultado = []
    for _, linha in dados.iterrows():
        data_cpcv = _parse_data_cpcv(linha[COL_DATA_CPCV])
        if data_cpcv is None:
            continue
        if dia_inicio and data_cpcv < dia_inicio:
            continue
        if dia_fim and data_cpcv > dia_fim:
            continue
        resultado.append({
            "ref": _texto_ou_none(linha[COL_REF]),
            "empreendimento": _texto_ou_none(linha[COL_ESPACO_FISICO]),
            "fracao": _texto_ou_none(linha[COL_FRACAO]),
            "cliente": _texto_ou_none(linha[COL_NOME_CLIENTE]),
            "empresa": _texto_ou_none(linha[COL_EMPRESA]),
            "valor_tabela": _parse_valor_misto(linha[COL_VALOR_TABELA]),
            "valor_proposto": _parse_valor_misto(linha[COL_VALOR_PROPOSTO]),
            "valor_recebido": _parse_valor_misto(linha[COL_VALOR_RECEBIDO]),
            "data_cpcv": data_cpcv.isoformat(),
        })

    resultado.sort(key=lambda r: r["data_cpcv"], reverse=True)
    return resultado


def listar_recebimentos_previstos() -> list:
    """Recebimentos com data marcada no índice comercial: o sinal no CPCV
    (data + valor recebido), os reforços de sinal e o valor a receber na
    escritura (na data limite). Uma entrada por pagamento, com data e
    valor - são as entradas grandes e pontuais que o histórico não deixa
    adivinhar, por isso a previsão de saldo usa-as tal como estão marcadas
    (ver previsao_ancorada.py). Pagamentos sem data ou sem valor ficam de
    fora; lista vazia se o ficheiro não existir."""
    dados = _ler_linhas_indice()
    resultado = []
    for _, linha in dados.iterrows():
        data_cpcv = _parse_data_cpcv(linha.get(COL_DATA_CPCV))
        agenda = [(data_cpcv, _parse_valor_misto(linha.get(COL_VALOR_RECEBIDO)), "CPCV")]
        for col_data, col_valor, tipo in AGENDA_RECEBIMENTOS:
            dia = _parse_data_agenda(linha.get(col_data), data_cpcv.day if data_cpcv else None)
            agenda.append((dia, _parse_valor_misto(linha.get(col_valor)), tipo))
        for dia, valor, tipo in agenda:
            if dia is None or not valor or valor <= 0:
                continue
            resultado.append({
                "dia": dia,
                "valor": valor,
                "tipo": tipo,
                "empresa": _texto_ou_none(linha.get(COL_EMPRESA)),
                "ref": _texto_ou_none(linha.get(COL_REF)),
                "empreendimento": _texto_ou_none(linha.get(COL_ESPACO_FISICO)),
                "fracao": _texto_ou_none(linha.get(COL_FRACAO)),
                "cliente": _texto_ou_none(linha.get(COL_NOME_CLIENTE)),
            })
    resultado.sort(key=lambda r: r["dia"])
    return resultado


def mapa_espaco_fracao_por_ref() -> dict:
    """Dicionário {REF: {"espaco_fisico", "fracao"}} de TODO o índice
    comercial, sem filtro de data - para ligar uma linha do Mapa de
    Pagamentos e Recebimentos (que só tem o código REF, ex. "00.PO.23.035",
    escrito na Descrição - ver reconciliador._refs_fracao_de_linha) ao
    espaço físico/fração a que corresponde, independentemente de quando o
    CPCV foi assinado (uma linha do Mapa pode referir-se a um CPCV
    assinado há meses)."""
    dados = _ler_linhas_indice()
    resultado = {}
    for _, linha in dados.iterrows():
        ref = _texto_ou_none(linha[COL_REF])
        if not ref:
            continue
        resultado[ref.strip().upper()] = {
            "espaco_fisico": _texto_ou_none(linha[COL_ESPACO_FISICO]),
            "fracao": _texto_ou_none(linha[COL_FRACAO]),
        }
    return resultado


def listar_negocios() -> list:
    """Um negócio por linha do índice comercial (com REF), com a agenda de
    pagamentos do comprador: o sinal no CPCV (data do CPCV + valor
    recebido), os reforços de sinal e a escritura - cada um com data e
    valor quando marcados. Base da vista detalhada de Análise de Extratos
    (ver vendas.detalhe_negocios); lista vazia se o ficheiro não existir."""
    dados = _ler_linhas_indice()
    resultado = []
    for _, linha in dados.iterrows():
        ref = _texto_ou_none(linha.get(COL_REF))
        if not ref:
            continue
        data_cpcv = _parse_data_cpcv(linha.get(COL_DATA_CPCV))
        pagamentos = [{"tipo": "Sinal (CPCV)", "dia": data_cpcv, "valor": _parse_valor_misto(linha.get(COL_VALOR_RECEBIDO))}]
        for col_data, col_valor, tipo in AGENDA_RECEBIMENTOS:
            pagamentos.append({
                "tipo": tipo,
                "dia": _parse_data_agenda(linha.get(col_data), data_cpcv.day if data_cpcv else None),
                "valor": _parse_valor_misto(linha.get(col_valor)),
            })
        resultado.append({
            "ref": ref.strip().upper(),
            "empreendimento": _texto_ou_none(linha.get(COL_ESPACO_FISICO)),
            "fracao": _texto_ou_none(linha.get(COL_FRACAO)),
            "cliente": _texto_ou_none(linha.get(COL_NOME_CLIENTE)),
            "empresa": _texto_ou_none(linha.get(COL_EMPRESA)),
            "valor_tabela": _parse_valor_misto(linha.get(COL_VALOR_TABELA)),
            "valor_proposto": _parse_valor_misto(linha.get(COL_VALOR_PROPOSTO)),
            "data_cpcv": data_cpcv,
            "pagamentos": [p for p in pagamentos if p["dia"] is not None and p["valor"] and p["valor"] > 0],
        })
    return resultado
