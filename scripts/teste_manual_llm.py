"""Script de teste manual (não faz parte da suite automática) para
confirmar que a integração real com a HuggingFace funciona: cria um caso
ambíguo de exemplo numa BD temporária em memória e pede uma sugestão real
ao LLM configurado no .env."""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import CasoAmbiguo, LinhaMapa, MovimentoBancario
from app.db.session import Base
from app.services.llm_resolver import sugerir_resolucao

engine = create_engine("sqlite:///:memory:")
Base.metadata.create_all(bind=engine)
db = sessionmaker(bind=engine)()

DIA = date(2026, 7, 21)

movimento = MovimentoBancario(
    dia=DIA, empresa="ANCORA APOGEU,LDA", descricao="TRANSF SEPA FATURA 1234", valor=-100.0,
    ficheiro_origem="teste.xlsx",
)
db.add(movimento)
db.flush()

linha_a = LinhaMapa(dia=DIA, tipo="pagamento", linha=5, empresa="Ancora Apogeu", previsto=-100.0, imputacao="Renda")
linha_b = LinhaMapa(dia=DIA, tipo="pagamento", linha=9, empresa="Ancora Apogeu", previsto=-100.0, imputacao="Fornecedor")
db.add(linha_a)
db.add(linha_b)
db.flush()

caso = CasoAmbiguo(
    movimento_id=movimento.id, dia=DIA, empresa="ANCORA APOGEU,LDA", valor=-100.0,
    candidatos=[linha_a.id, linha_b.id],
)
db.add(caso)

# Um caso parecido já resolvido no passado, para forçar a parte RAG real
# (embeddings) a ser exercitada e não só o LLM.
movimento_antigo = MovimentoBancario(
    dia=DIA, empresa="ANCORA APOGEU,LDA", descricao="TRANSF SEPA FATURA 1000", valor=-100.0,
    ficheiro_origem="teste.xlsx",
)
db.add(movimento_antigo)
db.flush()
caso_antigo = CasoAmbiguo(
    movimento_id=movimento_antigo.id, dia=DIA, empresa="ANCORA APOGEU,LDA", valor=-100.0,
    candidatos=[linha_a.id, linha_b.id],
    resolvido_por="benjamim", resolucao=f"linha_id={linha_a.id}",
)
db.add(caso_antigo)
db.commit()

print(f"Caso ambíguo criado (id={caso.id}). A pedir sugestão ao LLM...")
resultado = sugerir_resolucao(db, caso.id)
print("resolucao_sugerida:", resultado.resolucao_sugerida)
print("justificacao_sugerida:", resultado.justificacao_sugerida)
