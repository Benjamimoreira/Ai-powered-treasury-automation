"""Faturas recebidas em faturas@vidor.pt, reportadas pelo
recolher_faturas_recebidas.py (pasta "Fornecedores", fora do Docker) via
POST /faturas/recebidas - guarda na BD o mesmo que já vai para o Excel
mensal, para a dashboard mostrar sem precisar de abrir o ficheiro."""
from datetime import date
from typing import List, Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.db.models import FaturaRecebida
from app.models import FaturaRecebidaIn


def registar_faturas(db: Session, linhas: List[FaturaRecebidaIn]) -> dict:
    novas = duplicadas = 0
    vistos_neste_pedido = set()
    for linha in linhas:
        # duas verificações: já na BD (pedidos anteriores) e já visto neste
        # mesmo pedido (o mesmo email pode aparecer 2x no lote, ex. num
        # backfill a partir do Excel - sem isto, a 2ª linha só falhava na
        # BD com "unique constraint" em vez de ser tratada como duplicada)
        if linha.outlook_id in vistos_neste_pedido:
            duplicadas += 1
            continue
        existe = db.query(FaturaRecebida).filter(FaturaRecebida.outlook_id == linha.outlook_id).first()
        if existe:
            duplicadas += 1
            continue
        vistos_neste_pedido.add(linha.outlook_id)
        db.add(FaturaRecebida(
            outlook_id=linha.outlook_id,
            dia=linha.data_recebido.date(),
            hora=linha.data_recebido.strftime("%H:%M"),
            remetente=linha.remetente,
            assunto=linha.assunto,
            motivo=linha.motivo,
            empresa=linha.empresa,
            fornecedor=linha.fornecedor,
            nif_fornecedor=linha.nif_fornecedor,
            n_anexos_pdf=linha.n_anexos_pdf,
            debito=linha.debito,
            credito=linha.credito,
            saldo=linha.saldo,
            valor_fatura=linha.valor_fatura,
            pdf_relativo=linha.pdf_relativo,
        ))
        novas += 1
    db.commit()
    return {"novas": novas, "duplicadas": duplicadas}


def obter_fatura(db: Session, fatura_id: int) -> Optional[FaturaRecebida]:
    return db.query(FaturaRecebida).filter(FaturaRecebida.id == fatura_id).first()


def listar_faturas(
    db: Session,
    dia: Optional[date] = None,
    desde: Optional[date] = None,
    ate: Optional[date] = None,
    pesquisa: Optional[str] = None,
    limit: int = 200,
) -> list:
    query = db.query(FaturaRecebida)
    if dia:
        query = query.filter(FaturaRecebida.dia == dia)
    if desde:
        query = query.filter(FaturaRecebida.dia >= desde)
    if ate:
        query = query.filter(FaturaRecebida.dia <= ate)
    if pesquisa:
        # pesquisa em qualquer dia (não só na janela recente do "limit") -
        # por isso é um filtro à parte, não um contains() sobre o que já
        # foi carregado no browser
        termo = f"%{pesquisa}%"
        query = query.filter(
            or_(
                FaturaRecebida.empresa.ilike(termo),
                FaturaRecebida.fornecedor.ilike(termo),
                FaturaRecebida.nif_fornecedor.ilike(termo),
                FaturaRecebida.assunto.ilike(termo),
                FaturaRecebida.remetente.ilike(termo),
                FaturaRecebida.valor_fatura.ilike(termo),
            )
        )
    return (
        query.order_by(FaturaRecebida.dia.desc(), FaturaRecebida.hora.desc())
        .limit(limit)
        .all()
    )


# ---------------------------------------------------------------------------
# Fornecedor normalizado
# ---------------------------------------------------------------------------
# O fornecedor vem do recolher_faturas_recebidas.py (texto extraído do PDF/
# email) e em set/2026 vinha vazio em ~40% das faturas e com lixo noutras:
# "do titular IBAN", "Payment method Paid", moradas, "Referente a Factura nº
# ... | PCI ...", ou o próprio CLIENTE (uma empresa do grupo - ex. a morada
# da Palavradicional nas faturas da Miio). Aqui escolhe-se o melhor nome por
# esta ordem, sem tocar no valor original:
#   1. texto extraído, limpo, se não for lixo, morada nem empresa do grupo;
#   2. o nome mais comum para o mesmo NIF entre as faturas com texto válido;
#   3. o domínio do remetente (faturaedp@edp.pt -> "EDP");
#   4. emails internos (@vidor.pt) sem mais nada -> "(encaminhado internamente)".
import re
import unicodedata
from collections import Counter

ENCAMINHADO_INTERNAMENTE = "(encaminhado internamente)"
DOMINIOS_INTERNOS = {"vidor.pt"}
DOMINIOS_GENERICOS = {"gmail.com", "hotmail.com", "outlook.com", "outlook.pt", "sapo.pt", "live.com", "yahoo.com", "icloud.com"}
# nomes de marca com grafia própria (o resto fica com a 1.ª letra maiúscula)
NOMES_DOMINIO = {"edp": "EDP", "prio": "PRIO Energy", "galp": "Galp", "miio": "Miio", "meo": "MEO", "nos": "NOS",
                 "aquamatrix": "Águas (Aquamatrix)", "claranet": "Claranet", "ageas": "Ageas", "via-verde": "Via Verde",
                 "facebookmail": "Facebook"}
_LIXO = re.compile(
    r"^(do titular|payment method|documento|consultar|pro ?forma|referente|fatura|factura|nota de|recibo|"
    r"rua |r\. |av\.|avenida|praça|praca|largo|travessa|estrada|nif|iban|total|data|morada)",
    re.IGNORECASE,
)
_CODIGO_POSTAL = re.compile(r"\b\d{4}-\d{3}\b")
_PREFIXOS = re.compile(r"^(referente a fa[c]?tura n[ºo°]?\s*\S+\s*\|\s*|da entidade:\s*)", re.IGNORECASE)
# palavras genéricas que não identificam uma empresa do grupo
_GENERICAS = {"LDA", "SA", "UNIPESSOAL", "SOCIEDADE", "INVESTIMENTOS", "IMOBILIARIOS", "IMOBILIARIA", "CONSTRUCOES",
              "CONSTRUCAO", "GESTORA", "PARTICIPACOES", "SOCIAIS", "CIVIL", "INDUSTRIA", "NORTE", "CENTRO", "SUL",
              "ADMINISTRACAO", "CONDOMINIOS", "TURISTICOS", "SERVICOS", "EMPRESA", "CAPITAL", "RISCO", "PREDIO",
              "TRABALHO", "TEMPORARIO", "FORMACAO", "PROFISSIONAL"}


def _maiusculas(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", texto or "") if not unicodedata.combining(c)).upper()


def palavras_do_grupo(empresas_grupo: list) -> set:
    """A 1.ª palavra distintiva de cada empresa do grupo (HABISERVE,
    PALAVRADICIONAL, VIDOR, CERRO...) - para não confundir o cliente com o
    fornecedor. Só a primeira: as seguintes são muitas vezes apelidos
    comuns ("CORREIA" de Felizardo Correia) que apanhariam fornecedores."""
    palavras = set()
    for nome in empresas_grupo:
        distintivas = [p for p in re.findall(r"[A-Z]{5,}", _maiusculas(nome)) if p not in _GENERICAS]
        if distintivas:
            palavras.add(distintivas[0])
    return palavras


def limpar_fornecedor(texto: Optional[str], palavras_grupo: set) -> Optional[str]:
    """O texto extraído, limpo - ou None se for lixo, morada ou o cliente."""
    if not texto:
        return None
    t = _PREFIXOS.sub("", texto.strip())
    t = t.split(" | ")[0].strip(" ,;-|")
    if len(t) < 3 or _LIXO.match(t) or _CODIGO_POSTAL.search(t) or not re.search(r"[A-Za-zÀ-ÿ]{3,}", t):
        return None
    if set(re.findall(r"[A-Z]{5,}", _maiusculas(t))) & palavras_grupo:
        return None  # é uma empresa do grupo: o cliente, não o fornecedor
    return t


def fornecedor_do_remetente(remetente: Optional[str]) -> Optional[str]:
    if not remetente or "@" not in remetente:
        return None
    dominio = remetente.rsplit("@", 1)[1].strip().lower().rstrip(">")
    if dominio in DOMINIOS_INTERNOS:
        return ENCAMINHADO_INTERNAMENTE
    if dominio in DOMINIOS_GENERICOS:
        return None
    partes = dominio.split(".")
    # o nome da organização é o penúltimo bloco (hello.galp.com -> galp)
    rotulo = partes[-2] if len(partes) >= 2 else partes[0]
    return NOMES_DOMINIO.get(rotulo, rotulo.replace("-", " ").title())


def _chaves_nif(nif: Optional[str]) -> list:
    return re.findall(r"\d{9}", nif or "")


def normalizar_fornecedores(faturas: list, todas: list, empresas_grupo: list) -> list:
    """[(nome, fonte)] para cada fatura de `faturas`, com fonte "extraido" |
    "nif" | "remetente" | None. `todas` (todas as faturas da BD) serve para
    aprender o nome de cada NIF."""
    palavras_grupo = palavras_do_grupo(empresas_grupo)
    nomes_por_nif = {}
    for f in todas:
        nome = limpar_fornecedor(f.fornecedor, palavras_grupo)
        if nome:
            for nif in _chaves_nif(f.nif_fornecedor):
                nomes_por_nif.setdefault(nif, Counter())[nome] += 1

    resultado = []
    for f in faturas:
        nome = limpar_fornecedor(f.fornecedor, palavras_grupo)
        if nome:
            resultado.append((nome, "extraido"))
            continue
        do_nif = next((nomes_por_nif[n].most_common(1)[0][0] for n in _chaves_nif(f.nif_fornecedor) if n in nomes_por_nif), None)
        if do_nif:
            resultado.append((do_nif, "nif"))
            continue
        do_remetente = fornecedor_do_remetente(f.remetente)
        resultado.append((do_remetente, "remetente" if do_remetente else None))
    return resultado
