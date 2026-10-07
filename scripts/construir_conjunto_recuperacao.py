"""Constrói o conjunto de avaliação da recuperação (evals/recuperacao.json)
a partir dos pares reais "movimento bancário <-> linha do Mapa que o pagou"
(llm_resolver.pares_movimento_linha).

Porquê um conjunto à parte do evals/ambiguos.json: esse guarda só os 25
movimentos mais recentes de cada caso e tem um caso por rubrica, por isso
em 30 dos 48 casos com resposta não há nenhum movimento relevante no
histórico - sobram 18, poucos para distinguir métodos ou modelos. Aqui
cada par real é uma consulta, contra todo o histórico anterior da empresa:
centenas de consultas, sem etiquetagem manual (a relevância é "foi
imputado à mesma rubrica" - ver app/evals/avaliar_recuperacao.py).

Guarda-se um histórico por empresa, cronológico; a consulta i usa como
conjunto de pesquisa os itens com dia anterior ao seu (sem espreitar o
futuro). Tudo passa pelo Pseudonimizador - um por empresa, para o mesmo
arrendatário ficar com o mesmo pseudónimo em todo o histórico dela (é
precisamente o sinal que a recuperação tem de apanhar).

    python scripts/construir_conjunto_recuperacao.py
"""
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.session import SessionLocal  # noqa: E402
from app.evals.pseudonimizar import Pseudonimizador  # noqa: E402
from app.services.llm_resolver import pares_movimento_linha  # noqa: E402
from app.services.reconciliador import chave_empresa  # noqa: E402

SAIDA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "evals", "recuperacao.json")


def construir() -> list:
    db = SessionLocal()
    try:
        pares = pares_movimento_linha(db)
    finally:
        db.close()
    por_empresa = defaultdict(list)
    for linha, mov in pares:
        por_empresa[chave_empresa(mov.empresa)].append((linha, mov))

    empresas = []
    for i, (_, lista) in enumerate(sorted(por_empresa.items()), start=1):
        lista.sort(key=lambda p: (p[1].dia, p[1].id))
        p = Pseudonimizador()
        empresas.append({
            # o nome da empresa não interessa à recuperação - fica um código
            "empresa": f"EMPRESA_{i:02d}",
            "historico": [
                {"dia": m.dia.isoformat(), "descricao": p.texto(m.descricao), "valor": m.valor,
                 "imputacao_no_mapa": p.texto(l.imputacao or l.descricao)}
                for l, m in lista
            ],
        })
    return empresas


def main():
    empresas = construir()
    with open(SAIDA, "w", encoding="utf-8") as f:
        json.dump({"versao": 1, "empresas": empresas}, f, ensure_ascii=False, indent=1)
    print(f"{len(empresas)} empresas, {sum(len(e['historico']) for e in empresas)} movimentos -> {SAIDA}")
    # revisão antes de ir para o repositório, como no conjunto dos ambíguos
    import re
    textos = {h[c] for e in empresas for h in e["historico"] for c in ("descricao", "imputacao_no_mapa")}
    rever = sorted(
        t for t in textos
        if t and re.match(r"(?i)(TRF|TFI|TRANSF|REEMBOLSO|ORDENADO)\s", t) and "PESSOA_" not in t
    )
    print(f"para rever ({len(rever)}):", rever)


if __name__ == "__main__":
    main()
