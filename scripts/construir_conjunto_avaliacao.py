"""Constrói o conjunto de avaliação dos casos ambíguos (evals/ambiguos.json)
a partir de dados reais, com a resposta certa conhecida.

Porquê construído e não recolhido: casos ambíguos reais resolvidos por um
humano são raríssimos (0 na base de dados em set/2026). Mas há ~1800 pares
reais "movimento bancário <-> linha do Mapa que o pagou" - a resposta certa
é conhecida. Cada caso junta a esse par 2 linhas "isco" reais da mesma
empresa (outros pagamentos/recebimentos dela, com outra descrição) com o
mesmo valor, o que cria exatamente a ambiguidade que o reconciliador
encontra: mesma empresa, mesmo valor, várias linhas. Em ~20% dos casos a
linha certa é retirada e a resposta esperada é "nenhuma serve" (null).

Cada caso leva também o histórico da entidade (últimos pares reais dessa
empresa antes do dia - "este descritivo costuma ser imputado a X"), que é
o contexto de que a sugestão e o agente precisam.

Tudo passa pelo Pseudonimizador (nomes de pessoas, telefones, IBAN, NIF)
antes de ser escrito. Correr:
    python scripts/construir_conjunto_avaliacao.py [--n 60] [--semente 7]
"""
import argparse
import json
import os
import random
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.session import SessionLocal  # noqa: E402
from app.evals.pseudonimizar import Pseudonimizador  # noqa: E402
from app.services.llm_resolver import pares_movimento_linha  # noqa: E402
from app.services.reconciliador import chave_empresa, normalizar  # noqa: E402

SAIDA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "evals", "ambiguos.json")
N_ISCOS = 2
FRACAO_NENHUMA = 0.2
# guardado longo (25) para o agente poder pedir mais; o prompt usa os últimos 6
N_HISTORICO = 25
N_PARECIDOS = 5
# imputações que não dizem nada ("não sei") - não servem de resposta certa
IMPUTACOES_VAZIAS = {"", "DESCONHECO", "DESCONHECIDO", "OUTROS", "NONE"}


def _palavras(texto) -> set:
    from app.services.agente_ambiguos import _palavras as palavras_agente
    return palavras_agente(texto)


def _texto_linha(linha) -> str:
    return normalizar(f"{linha.imputacao or ''} {linha.descricao or ''}")


def _pares_reais(db) -> list:
    """(linha, movimento) com a resposta certa conhecida (ver
    llm_resolver.pares_movimento_linha), sem as linhas cuja imputação não
    diz nada ("Desconheço") - não servem de resposta certa."""
    return [
        (linha, mov) for linha, mov in pares_movimento_linha(db)
        if normalizar(linha.imputacao or linha.descricao or "") not in IMPUTACOES_VAZIAS
    ]


def construir(n: int, semente: int) -> list:
    db = SessionLocal()
    try:
        pares = _pares_reais(db)
    finally:
        db.close()
    aleatorio = random.Random(semente)

    por_empresa = defaultdict(list)
    for linha, mov in pares:
        por_empresa[chave_empresa(mov.empresa)].append((linha, mov))
    for lista in por_empresa.values():
        lista.sort(key=lambda p: p[1].dia)

    # um caso por (empresa, imputação), para não ficar só com rendas/IRN
    vistos, elegiveis = set(), []
    aleatorio.shuffle(pares)
    for linha, mov in pares:
        chave = (chave_empresa(mov.empresa), _texto_linha(linha))
        iscos = {_texto_linha(l) for l, _ in por_empresa[chave[0]]} - {chave[1]}
        if chave in vistos or len(iscos) < N_ISCOS + 1:
            continue
        vistos.add(chave)
        elegiveis.append((linha, mov))

    casos = []
    for i, (linha, mov) in enumerate(elegiveis[:n]):
        p = Pseudonimizador()
        nenhuma = i < round(n * FRACAO_NENHUMA)
        empresa = chave_empresa(mov.empresa)
        textos_usados = {_texto_linha(linha)}
        # Um isco não pode ser uma resposta igualmente certa: sem palavras
        # em comum com o descritivo do movimento nem com a linha certa, e
        # sem uma imputação que o histórico já tenha dado a este descritivo
        # (ex. "INSTITUTO REGISTOS NO" vs isco "IRN" - o modelo acertava e
        # contava como erro nos casos "nenhuma serve").
        palavras_certas = _palavras(mov.descricao) | _palavras(f"{linha.imputacao or ''} {linha.descricao or ''}")
        imputacoes_do_descritivo = {
            _texto_linha(l) for l, m in por_empresa[empresa] if _palavras(m.descricao) & _palavras(mov.descricao)
        }
        iscos = []
        for l, _ in aleatorio.sample(por_empresa[empresa], len(por_empresa[empresa])):
            texto = _texto_linha(l)
            if (
                texto not in textos_usados and l.tipo == linha.tipo
                and not (_palavras(f"{l.imputacao or ''} {l.descricao or ''}") & palavras_certas)
                and texto not in imputacoes_do_descritivo
            ):
                textos_usados.add(texto)
                iscos.append(l)
            if len(iscos) == N_ISCOS + (1 if nenhuma else 0):
                break
        if len(iscos) < N_ISCOS:
            continue

        linhas_caso = iscos + ([] if nenhuma else [linha])
        aleatorio.shuffle(linhas_caso)
        ids = aleatorio.sample(range(100, 999), len(linhas_caso))
        candidatos = [
            {
                "id": id_, "linha": aleatorio.randint(5, 60), "tipo": l.tipo, "empresa": linha.empresa,
                # mesmo valor em todas - é isso que torna o caso ambíguo
                "previsto": linha.pago, "imputacao": p.texto(l.imputacao), "descricao": p.texto(l.descricao),
            }
            for id_, l in zip(ids, linhas_caso)
        ]
        esperado = None if nenhuma else ids[linhas_caso.index(linha)]

        historico = [
            {
                "dia": m.dia.isoformat(), "descricao": p.texto(m.descricao), "valor": m.valor,
                "imputacao_no_mapa": p.texto(l.imputacao or l.descricao),
            }
            for l, m in por_empresa[empresa] if m.dia < mov.dia and m.id != mov.id
        ][-N_HISTORICO:]

        palavras_mov = _palavras(mov.descricao)
        parecidos = [
            {
                "dia": m.dia.isoformat(), "descricao": p.texto(m.descricao), "valor": m.valor,
                "imputacao_no_mapa": p.texto(l.imputacao or l.descricao),
            }
            for l, m in por_empresa[empresa]
            if m.dia < mov.dia and m.id != mov.id and palavras_mov & _palavras(m.descricao)
        ][-N_PARECIDOS:]

        casos.append({
            "id": f"caso_{len(casos) + 1:03d}",
            "categoria": "nenhuma_serve" if nenhuma else "linha_correta",
            "movimento": {
                "dia": mov.dia.isoformat(), "empresa": mov.empresa, "descricao": p.texto(mov.descricao), "valor": mov.valor,
            },
            "candidatos": candidatos,
            "esperado": esperado,
            "historico_entidade": historico,
            "movimentos_parecidos": parecidos,
        })
    # ordem baralhada: um --max-casos pequeno tem de ter das duas categorias
    aleatorio.shuffle(casos)
    for i, caso in enumerate(casos, start=1):
        caso["id"] = f"caso_{i:03d}"
    return casos


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=60)
    parser.add_argument("--semente", type=int, default=7)
    args = parser.parse_args()
    casos = construir(args.n, args.semente)
    os.makedirs(os.path.dirname(SAIDA), exist_ok=True)
    with open(SAIDA, "w", encoding="utf-8") as f:
        json.dump({"versao": 2, "semente": args.semente, "casos": casos}, f, ensure_ascii=False, indent=1)
    categorias = defaultdict(int)
    for c in casos:
        categorias[c["categoria"]] += 1
    print(f"{len(casos)} casos -> {SAIDA} ({dict(categorias)})")
    # revisão: o que ficou depois dos prefixos de pessoa sem ser pseudónimo
    # tem de ser uma entidade - se aparecer aqui um nome, acrescentar a regra
    import re
    textos = set()
    for c in casos:
        textos.add(c["movimento"]["descricao"])
        textos.update(x.get("imputacao") for x in c["candidatos"])
        textos.update(x.get("descricao") for x in c["candidatos"])
        textos.update(h["descricao"] for h in c["historico_entidade"])
        textos.update(h["imputacao_no_mapa"] for h in c["historico_entidade"])
    rever = sorted(
        t for t in textos
        if t and re.match(r"(?i)(TRF|TFI|TRANSF|REEMBOLSO|ORDENADO)\s", t) and "PESSOA_" not in t
    )
    print(f"para rever ({len(rever)}):", rever)


if __name__ == "__main__":
    main()
