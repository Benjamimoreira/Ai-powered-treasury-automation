"""Guardrail de números do Assistente: cada valor que a resposta afirma
tem de aparecer no que as ferramentas devolveram nessa pergunta.

É a verificação de alucinação mais útil numa app de tesouraria, e é
determinística: não precisa de outro LLM, corre em cada resposta e custa
milissegundos. Um número "não verificado" não é necessariamente errado
(o modelo pode ter somado dois valores), mas é um número que não veio dos
dados tal como foi escrito - a dashboard avisa quem lê.

A tolerância é a precisão com que o número foi escrito: "233 203,52 €"
tem de bater ao cêntimo, "233 mil €" a ±500 €, "1,2 milhões" a ±50 000 €.
O sinal não conta (os pagamentos vêm negativos nos dados e a resposta diz
"pagou 500 €"). Ficam de fora datas, anos e números pequenos sem € (dias,
contagens), que dariam falsos alarmes."""
import re
from typing import Iterable, List

# um número escrito: notação portuguesa (233 203,52), inglesa (233,203.52)
# ou de máquina (233203.52), com sufixo opcional. A ordem das alternativas
# importa: as formas com separador de milhares primeiro.
_NUMERO = re.compile(
    r"(?<![\w.,/-])("
    r"-?\d{1,3}(?:,\d{3})+\.\d+"                   # inglês com decimais: 233,203.52
    r"|-?\d{1,3}(?:[ \u00a0.]\d{3})+(?:,\d+)?"     # português: 233 203,52 / 233.203,52 / 233 203
    r"|-?\d{1,3}(?:,\d{3})+"                       # ambíguo: 233,203 (inglês) ou 233,203 (pt, 3 casas)
    r"|-?\d+(?:[.,]\d+)?"                          # simples: 12,50 / 12.50 / 1500
    r")"
    r"(\s*(?:mil\b|milh(?:ão|ões)\b|k\b|M\b))?"
    r"(\s*(?:€|EUR\b|euros?\b))?",
    re.IGNORECASE,
)
_DATA = re.compile(r"\b\d{1,4}[-/]\d{1,2}[-/]\d{1,4}\b")
_MULTIPLICADORES = {"mil": 1e3, "k": 1e3, "milhão": 1e6, "milhões": 1e6, "m": 1e6}
# números em JSON/texto das ferramentas: 233203.52, -1234.5, 1e3
_NUMERO_MAQUINA = re.compile(r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")


def _leituras(texto: str, sufixo: str) -> List[tuple]:
    """[(valor, tolerância)] - as leituras possíveis do número como foi
    escrito. Quase sempre uma; "233,203" tem duas (233 203 em inglês ou
    233,203 em português), e basta uma bater com os dados.
    '233 203,52' -> [(233203.52, 0.005)]; '1,2' + 'milhões' -> [(1.2e6, 5e4)]."""
    texto = texto.replace("\u00a0", " ")
    multiplicador = _MULTIPLICADORES.get(sufixo.strip().lower(), 1.0) if sufixo else 1.0

    def leitura(inteiro: str, decimais: str = ""):
        valor = float(f"{inteiro}.{decimais}" if decimais else inteiro)
        return valor * multiplicador, 0.5 * 10 ** -len(decimais) * multiplicador

    if re.fullmatch(r"-?\d{1,3}(?:,\d{3})+\.\d+", texto):  # inglês: vírgula = milhares, ponto = decimais
        inteiro, decimais = texto.replace(",", "").split(".")
        return [leitura(inteiro, decimais)]
    if re.fullmatch(r"-?\d{1,3}(?:,\d{3})+", texto):  # ambíguo
        inteiro, decimais = texto.split(",", 1)
        leituras = [leitura(texto.replace(",", ""))]
        if "," not in decimais:  # "1,234" também pode ser 1,234 em português
            leituras.append(leitura(inteiro, decimais))
        return leituras
    if "," in texto:  # português: milhares com espaço/ponto, decimais com vírgula
        inteiro, decimais = texto.replace(" ", "").replace(".", "").split(",")
        return [leitura(inteiro, decimais)]
    if re.fullmatch(r"-?\d{1,3}(?:[ .]\d{3})+", texto):  # 233 203 ou 233.203 (milhares)
        return [leitura(texto.replace(" ", "").replace(".", ""))]
    inteiro, _, decimais = texto.partition(".")
    return [leitura(inteiro, decimais)]


def numeros_da_resposta(resposta: str) -> List[tuple]:
    """[(texto como escrito, [(valor, tolerância), ...])] dos valores que a
    resposta afirma - só montantes: com €, com casas decimais, com
    mil/milhões, ou a partir de 1 000. Datas e anos ficam de fora."""
    sem_datas = _DATA.sub(" ", resposta)
    encontrados = []
    for m in _NUMERO.finditer(sem_datas):
        numero, sufixo, moeda = m.group(1), m.group(2) or "", m.group(3) or ""
        leituras = _leituras(numero, sufixo)
        tem_decimais = "," in numero or re.search(r"\.\d{1,2}$", numero) is not None
        e_montante = bool(moeda or sufixo or tem_decimais or max(abs(v) for v, _ in leituras) >= 1000)
        e_ano = not moeda and not sufixo and re.fullmatch(r"(19|20)\d{2}", numero) is not None
        if e_montante and not e_ano:
            encontrados.append((m.group(0).strip(), leituras))
    return encontrados


def numeros_das_ferramentas(resultados: Iterable[str]) -> List[float]:
    valores = []
    for texto in resultados:
        for m in _NUMERO_MAQUINA.finditer(str(texto or "")):
            try:
                valores.append(abs(float(m.group(0))))
            except ValueError:
                continue
    return valores


def verificar(resposta: str, resultados_ferramentas: Iterable[str]) -> dict:
    """{"verificados": n, "nao_verificados": ["126k€", ...]} - um número da
    resposta é verificado se alguma das suas leituras cai dentro da
    tolerância de algum valor das ferramentas (ignorando o sinal)."""
    dados = numeros_das_ferramentas(resultados_ferramentas)
    verificados, nao_verificados = 0, []
    for texto, leituras in numeros_da_resposta(resposta):
        if any(abs(abs(valor) - d) <= tolerancia + 1e-9 for valor, tolerancia in leituras for d in dados):
            verificados += 1
        elif texto not in nao_verificados:
            nao_verificados.append(texto)
    return {"verificados": verificados, "nao_verificados": nao_verificados}
