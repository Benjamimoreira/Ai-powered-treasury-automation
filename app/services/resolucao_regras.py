"""Decisão sem LLM para casos ambíguos (decisão e recolha condicionais).

O LLM só deve ser chamado quando é mesmo preciso. Antes dele há dois
passos baratos, e cada um só corre se o anterior não decidir:

1. triagem (sem recolha nenhuma): o descritivo do banco partilha palavras
   com o texto de exatamente UMA linha candidata (ex. "EDP COMERCIAL" e a
   linha "EDP") -> decide;
2. histórico (recolha condicional - só consulta a base de dados aqui):
   movimentos anteriores desta empresa com descritivo parecido foram
   imputados a um texto que bate com exatamente UMA linha -> decide.

Não decide - e passa ao LLM - quando há empate (vários candidatos com
sinal), quando os dois passos apontam para linhas diferentes, ou quando
não há sinal nenhum (pode ser "nenhuma serve", e isso é um juízo que as
regras não fazem). As regras nunca devolvem "nenhuma serve".

Medido em docs/AVALIACAO_LLM.md (estratégia "hibrida")."""
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Callable, Optional

_PALAVRAS_VAZIAS = {"TRF", "TFI", "DE", "DA", "DO", "LDA", "SA", "S", "A", "EMP", "COMPRA", "PAGAMENTO"}


def palavras_significativas(texto: str) -> set:
    """Palavras com 3+ caracteres, em maiúsculas e sem acentos ("Águas" e
    "AGUAS" são a mesma), sem as que não distinguem nada ("TRF", "LDA")."""
    sem_acentos = "".join(c for c in unicodedata.normalize("NFKD", texto or "") if not unicodedata.combining(c))
    return {p for p in re.findall(r"[A-Z0-9_]{3,}", sem_acentos.upper()) if p not in _PALAVRAS_VAZIAS}


_palavras = palavras_significativas


@dataclass
class DecisaoRegras:
    linha_id: Optional[int]
    fonte: Optional[str]            # "triagem_texto" | "historico" | None (passa ao LLM)
    motivo: str
    consultou_historico: bool = False
    sinais: dict = field(default_factory=dict)

    @property
    def decidiu(self) -> bool:
        return self.fonte is not None


def _texto_candidato(c: dict) -> str:
    return f"{c.get('imputacao') or ''} {c.get('descricao') or ''}"


def _com_sinal(candidatos: list, palavras: set) -> list:
    return [c["id"] for c in candidatos if palavras and _palavras(_texto_candidato(c)) & palavras]


def decidir_sem_llm(movimento: dict, candidatos: list, historico: Callable[[], list]) -> DecisaoRegras:
    """`historico` é chamado só se a triagem não decidir (recolha
    condicional) e devolve [{"descricao", "imputacao_no_mapa", ...}]."""
    palavras_mov = _palavras(movimento.get("descricao"))

    # 1. triagem: texto do movimento vs texto das linhas
    por_texto = _com_sinal(candidatos, palavras_mov)
    if len(por_texto) == 1:
        return DecisaoRegras(por_texto[0], "triagem_texto",
                             "O descritivo do banco partilha palavras só com esta linha.",
                             sinais={"texto": por_texto})

    # 2. histórico: como é que este descritivo foi imputado antes
    anteriores = [h for h in historico() if palavras_mov & _palavras(h.get("descricao"))]
    palavras_hist = set().union(*(_palavras(h.get("imputacao_no_mapa")) for h in anteriores)) if anteriores else set()
    por_historico = _com_sinal(candidatos, palavras_hist)
    sinais = {"texto": por_texto, "historico": por_historico, "movimentos_anteriores": len(anteriores)}

    if len(por_historico) == 1 and (not por_texto or por_texto == por_historico):
        return DecisaoRegras(por_historico[0], "historico",
                             f"{len(anteriores)} movimento(s) anterior(es) com este descritivo foram imputados como esta linha.",
                             consultou_historico=True, sinais=sinais)

    if len(por_texto) > 1 or len(por_historico) > 1:
        motivo = "Empate: várias linhas com sinal - decide o LLM."
    elif por_texto and por_historico and por_texto != por_historico:
        motivo = "Texto e histórico apontam para linhas diferentes - decide o LLM."
    else:
        motivo = "Sem sinal em nenhuma linha (pode ser 'nenhuma serve') - decide o LLM."
    return DecisaoRegras(None, None, motivo, consultou_historico=True, sinais=sinais)
