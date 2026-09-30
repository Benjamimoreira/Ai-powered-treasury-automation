"""Pseudonimização dos textos bancários/do Mapa antes de entrarem no
conjunto de avaliação (RGPD, minimização - ver docs/GOVERNANCA_IA.md).

Troca nomes de pessoas por pseudónimos estáveis ("PESSOA_07") e mascara
números de telefone, IBAN e NIF. Os nomes das empresas do grupo e de
fornecedores (pessoas coletivas) ficam: não são dados pessoais e são o
sinal de que o modelo precisa para decidir.

Duas regras:
- depois de "TRF"/"TFI"/"Trf"/"Reembolso"/"Ordenado", o resto do texto é
  uma pessoa, a não ser que seja claramente uma entidade (tem LDA/SA/EMP,
  uma empresa do grupo ou um intermediário conhecido - ENTIDADES abaixo).
  Uma lista de nomes próprios nunca é completa ("TRF FULGENCIO MISSUA"
  passava), por isso aqui o que não é reconhecido como entidade sai;
- noutro sítio do texto, um nome próprio da lista (e até 3 apelidos a
  seguir - o banco corta o descritivo a ~17 caracteres).
O conjunto gerado é revisto antes de ir para o repositório (ver
scripts/construir_conjunto_avaliacao.py, que lista o que ficou depois
destes prefixos)."""
import re
import unicodedata

NOMES_PROPRIOS = {
    "ADRIANA", "AFONSO", "AIXING", "ALBERTO", "ALEXANDRA", "ALEXANDRE", "ALICE", "ALVARO", "AMELIA", "AMERICO",
    "ANA", "ANDRE", "ANDREIA", "ANGELA", "ANTONIO", "ARMANDO", "ARTUR", "AUGUSTO", "BARBARA", "BEATRIZ",
    "BENJAMIM", "BERNARDO", "BRUNA", "BRUNO", "CAMILA", "CARLA", "CARLOS", "CAROLINA", "CATARINA", "CELIA",
    "CESAR", "CLARA", "CLAUDIA", "CRISTIANO", "CRISTINA", "DANIEL", "DANIELA", "DANIELLE", "DAVID", "DIANA",
    "DIOGO", "DOMINGOS", "DUARTE", "EDUARDO", "ELAINE", "ELISABETE", "EMANUEL", "EMILIA", "ERICA", "FABIO",
    "FATIMA", "FELIPE", "FERNANDA", "FERNANDO", "FILIPA", "FILIPE", "FLAVIO", "FRANCISCA", "FRANCISCO", "GABRIEL",
    "GABRIELA", "GONCALO", "GUILHERME", "GUSTAVO", "HELDER", "HELENA", "HENRIQUE", "HUGO", "IGOR", "ILLIA",
    "INES", "IRENE", "ISABEL", "IVO", "JAIME", "JOANA", "JOAO", "JOAQUIM", "JORGE", "JOSE", "JOSEFA", "JULIA",
    "JULIO", "LARA", "LAURA", "LEONOR", "LIDIA", "LILIANA", "LUCAS", "LUCIA", "LUIS", "LUISA", "MANUEL",
    "MANUELA", "MARCIA", "MARCIO", "MARCO", "MARCOS", "MARGARIDA", "MARIA", "MARIANA", "MARINA", "MARIO",
    "MARTA", "MATEUS", "MIGUEL", "MONICA", "NATALIA", "NELSON", "NUNO", "OLGA", "OLIVIA", "PATRICIA", "PAULA",
    "PAULO", "PEDRO", "RAFAEL", "RAFAELA", "RAQUEL", "RENATA", "RICARDO", "RITA", "ROBERTO", "RODRIGO", "ROGERIO",
    "ROSA", "RUI", "SAMUEL", "SANDRA", "SARA", "SERGIO", "SILVIA", "SIMAO", "SOFIA", "SONIA", "SUSANA", "TANIA",
    "TATIANA", "TERESA", "TIAGO", "TOMAS", "VANESSA", "VASCO", "VERA", "VICTOR", "VITOR", "VITORIA", "XAVIER",
}
# palavras que nunca fazem parte de um nome (evita "ANA PAGAMENTO ...")
_NAO_NOME = {
    "LDA", "SA", "S", "A", "EMP", "PAGAMENTO", "RENDA", "SINAL", "CPCV", "TRF", "TFI", "MBWAY", "DE",
    "JANEIRO", "FEVEREIRO", "MARCO", "ABRIL", "MAIO", "JUNHO", "JULHO", "AGOSTO", "SETEMBRO", "OUTUBRO",
    "NOVEMBRO", "DEZEMBRO", "FRACAO", "LOTE", "ESCRITURA", "REFORCO",
}

# o que vem depois destes prefixos é, por omissão, uma pessoa
_PREFIXOS_PESSOA = ("TRF ", "TFI ", "TRANSF ", "REEMBOLSO ", "ORDENADO ", "SALARIO ", "ADIANTAMENTO ")
# ...exceto se contiver uma destas palavras (entidades, não pessoas)
ENTIDADES = {
    "LDA", "SA", "S A", "EMP", "UNIPESSOAL", "SGPS", "SOCIEDADE", "CAIXADIRECTA", "IFTHENPAY", "IRN", "IMEDIATA",
    "DEPOSITO", "AIRBNB", "PAYMENTS", "BOOKING", "VIDOR", "HABISERVE", "PALAVRA", "PALAVRADICI", "PALAVRADICIONAL",
    "CONSTRUCOES", "VIDUARTE", "ANCORA", "VISLUMBRE", "CONVITEPROEZA", "FALESIAS", "DINASTIA", "SUBLIME", "RUBRICA",
    "MANEIRA", "SADIMOVEL", "SODEFIG", "CERRO", "FELIZARDO", "MBWAY", "RESTITUICOE", "RESTITUICOES", "SEPA",
    "CREDITO", "CONDOMINIO", "CONDOMINIOS", "MUTUO", "IG", "BANCO", "CGD", "SEGURANCA", "SOCIAL", "AT",
    "AUTORIDADE", "TRIBUTARIA", "FINANCAS", "EDP", "MEO", "NOS", "VODAFONE", "GALP", "IBERDROLA", "EPAL",
}

_TELEFONE = re.compile(r"\b9\d{2}X{2,3}\d{3}\b|\b9\d{8}\b", re.IGNORECASE)
_IBAN = re.compile(r"\bPT50\s?(?:\d{4}\s?){5}\d\b", re.IGNORECASE)
_NIF = re.compile(r"(?<!\d)[1235689]\d{8}(?!\d)")


def _sem_acentos(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c))


class Pseudonimizador:
    """Mantém o mapa nome -> pseudónimo, para a mesma pessoa ficar com o
    mesmo pseudónimo em todos os textos de um caso (o movimento, as linhas
    do Mapa e o histórico têm de continuar a "bater" entre si)."""

    def __init__(self):
        self._mapa = {}

    def _pseudonimo(self, nome: str) -> str:
        # chave pelos 2 primeiros nomes: "BRUNO GOMES MONIZ" e "Bruno Gomes"
        # são a mesma pessoa (o banco corta o descritivo)
        chave = " ".join(_sem_acentos(nome).upper().split()[:2])
        if chave not in self._mapa:
            self._mapa[chave] = f"PESSOA_{len(self._mapa) + 1:02d}"
        return self._mapa[chave]

    def texto(self, texto):
        if not texto:
            return texto
        texto = _IBAN.sub("PT50 [IBAN]", texto)
        texto = _TELEFONE.sub("9XXXXXXXX", texto)
        texto = _NIF.sub("[NIF]", texto)
        maiusculo = _sem_acentos(texto).upper()
        for prefixo in _PREFIXOS_PESSOA:
            if maiusculo.startswith(prefixo):
                resto = texto[len(prefixo):].strip()
                palavras_resto = set(re.findall(r"[A-Z0-9]+", _sem_acentos(resto).upper()))
                letras = re.sub(r"[^A-Za-z]", "", resto)
                if letras and not (palavras_resto & ENTIDADES) and not re.fullmatch(r"[\dX ]+", resto):
                    return f"{texto[:len(prefixo)]}{self._pseudonimo(resto)}"
                break
        palavras = texto.split(" ")
        saida, i = [], 0
        while i < len(palavras):
            normal = _sem_acentos(palavras[i]).upper().strip(".,;:")
            if normal in NOMES_PROPRIOS:
                j = i + 1
                while (
                    j < len(palavras) and j - i <= 3 and palavras[j]
                    and palavras[j][0].isupper() and _sem_acentos(palavras[j]).upper().strip(".,") not in _NAO_NOME
                    and not any(ch.isdigit() for ch in palavras[j])
                ):
                    j += 1
                saida.append(self._pseudonimo(" ".join(palavras[i:j])))
                i = j
            else:
                saida.append(palavras[i])
                i += 1
        return " ".join(saida)
