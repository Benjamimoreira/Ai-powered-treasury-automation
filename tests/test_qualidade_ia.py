"""Guardrail de números, avaliações online, fila "para rever", anotações e
promoção ao golden dataset do Assistente - sem Phoenix nem LLM reais."""
import json

from app.db.models import AnotacaoAssistente, AvaliacaoOnline, InteracaoAssistente
from app.evals import avaliacao_online, promover_golden
from app.services import guardrail_numeros


def test_guardrail_verifica_valores_com_a_precisao_escrita():
    resposta = ("O saldo total em 2026-10-05 é 233 203,52 €. A J. Pinto pagou cerca de 12 mil € em agosto "
                "de 2026, em 3 pagamentos. Previsão: 1,2 milhões. Inventado: 98 765,43 €.")
    dados = ['{"saldo_total": 233203.52, "pagamentos": [-6000.0, -6010.4], "total": -12010.4}',
             '{"previsto": 1180000}']

    resultado = guardrail_numeros.verificar(resposta, dados)

    # datas, o ano e o "3" não contam; 12 mil bate com 12 010,40 (±500); o sinal não conta
    assert resultado == {"verificados": 3, "nao_verificados": ["98 765,43 €"]}


def test_guardrail_sem_ferramentas_marca_todos_os_valores():
    resultado = guardrail_numeros.verificar("O saldo é 1 500,00 €.", [])
    assert resultado["nao_verificados"] == ["1 500,00 €"]


def test_guardrail_exige_precisao_ao_centimo():
    assert guardrail_numeros.verificar("Saldo: 1 250,10 €", ['{"saldo": 1250.0}'])["nao_verificados"] == ["1 250,10 €"]


def _span(trace, nome, kind, parent=None, entrada=None, saida=None):
    return {"name": nome, "span_kind": kind, "parent_id": parent, "start_time": "2026-10-06T10:00:00+00:00",
            "context": {"trace_id": trace, "span_id": nome},
            "attributes": {"input.value": json.dumps(entrada) if entrada else None,
                           "output.value": json.dumps(saida) if saida else None}}


SPANS = [
    _span("t1", "assistente", "CHAIN", entrada={"pergunta": "Saldo?"}, saida={"resposta": "10 €"}),
    _span("t1", "saldo_total_tool", "TOOL", parent="assistente", saida={"resultado": '{"total": 10}'}),
    _span("t2", "juiz_fundamentacao", "LLM"),  # um trace do próprio juiz: não é conversa
]


def test_agrupar_conversas_junta_pergunta_resposta_e_dados_por_trace():
    conversas = avaliacao_online.agrupar_conversas(SPANS)
    assert len(conversas) == 1
    assert conversas[0]["pergunta"] == "Saldo?"
    assert conversas[0]["resposta"] == "10 €"
    assert conversas[0]["dados"] == ['saldo_total_tool: {"total": 10}']


def test_avaliacao_online_anota_no_phoenix_e_grava_na_bd(db_session, monkeypatch):
    anotacoes = []
    monkeypatch.setattr(avaliacao_online.phoenix_cliente, "url_base", lambda: "http://phoenix")
    monkeypatch.setattr(avaliacao_online.phoenix_cliente, "listar_spans", lambda *a, **k: SPANS)
    monkeypatch.setattr(avaliacao_online.phoenix_cliente, "anotar_trace", lambda *a, **k: anotacoes.append((a, k)))
    monkeypatch.setattr(avaliacao_online, "julgar", lambda conversa, nome, modelo: (
        {"label": "alucinada", "score": 0.0, "explicacao": "x"} if nome == "fundamentacao"
        else {"label": "relevante", "score": 1.0, "explicacao": None}))

    resultado = avaliacao_online.avaliar(horas=1, modelo_juiz="juiz", db=db_session)

    assert resultado == {"conversas": 1, "medias": {"fundamentacao": 0.0, "relevancia": 1.0}}
    assert {a[0][1] for a in anotacoes} == {"fundamentacao", "relevancia"}
    assert db_session.query(AvaliacaoOnline).filter_by(trace_id="t1", avaliador="fundamentacao").one().score == 0.0


def test_fila_para_rever_e_anotacao(client, db_session):
    db_session.add_all([
        InteracaoAssistente(id=1, pergunta="a", resposta="r", status="ok", feedback=0, trace_id="t1"),
        InteracaoAssistente(id=2, pergunta="b", resposta="r", status="ok", numeros_nao_verificados=["5 €"]),
        InteracaoAssistente(id=3, pergunta="c", resposta="r", status="ok", feedback=1, trace_id="t3"),
        InteracaoAssistente(id=4, pergunta="d", resposta="r", status="ok", feedback=1),
    ])
    db_session.add(AvaliacaoOnline(trace_id="t3", avaliador="fundamentacao", label="alucinada", score=0.0))
    db_session.commit()

    fila = client.get("/monitorizacao/ia/para-rever").json()["interacoes"]
    assert sorted(i["id"] for i in fila) == [1, 2, 3]  # a 4 não tem nenhum motivo

    resposta = client.post("/monitorizacao/ia/interacoes/1/anotacao",
                           json={"label": "alucinada", "score": 0, "resposta_esperada": "O saldo é 10 €."})
    assert resposta.status_code == 200
    assert 1 not in [i["id"] for i in client.get("/monitorizacao/ia/para-rever").json()["interacoes"]]
    assert client.post("/monitorizacao/ia/interacoes/2/anotacao", json={"label": "ótima"}).status_code == 422


def test_promover_golden_pseudonimiza_e_marca_como_promovida(db_session, tmp_path, monkeypatch):
    db_session.add(InteracaoAssistente(id=1, pergunta="Quanto recebeu a empresa de TRF ANTONIO SILVA?",
                                       resposta="Nada.", status="ok", trace_id="t1",
                                       ferramentas_usadas=["movimentos_empresa_tool"]))
    db_session.add(AnotacaoAssistente(interacao_id=1, label="incorreta", resposta_esperada="Recebeu 250,00 €."))
    db_session.commit()
    enviados = []
    monkeypatch.setattr(promover_golden, "dados_do_trace",
                        lambda trace_id, criado_em: ['movimentos: [{"descricao": "TRF ANTONIO SILVA", "valor": 250.0}]'])
    monkeypatch.setattr(promover_golden.phoenix_cliente, "enviar_dataset", lambda *a, **k: enviados.append(k))
    caminho = tmp_path / "assistente.json"

    resultado = promover_golden.promover(db_session, str(caminho))

    assert resultado["promovidos"] == 1
    caso = json.loads(caminho.read_text(encoding="utf-8"))["casos"][0]
    assert "ANTONIO" not in caso["pergunta"] and "ANTONIO" not in caso["dados"][0]
    assert caso["resposta_esperada"] == "Recebeu 250,00 €."
    assert enviados and db_session.query(AnotacaoAssistente).one().promovida_em is not None
    assert promover_golden.promover(db_session, str(caminho))["promovidos"] == 0  # não repete


def test_calibracao_do_juiz_compara_com_anotacoes_e_feedback(client, db_session):
    # t1: humano má (anotação), juiz chumba  -> ambos má
    # t2: humano boa (👍),      juiz chumba  -> falso alarme
    # t3: humano má (👎),       juiz aprova  -> deixou passar
    # t4: humano boa (anotação "correta" prevalece sobre o 👎), juiz aprova -> ambos boa
    db_session.add_all([
        InteracaoAssistente(id=1, pergunta="a", status="ok", trace_id="t1"),
        InteracaoAssistente(id=2, pergunta="b", status="ok", trace_id="t2", feedback=1),
        InteracaoAssistente(id=3, pergunta="c", status="ok", trace_id="t3", feedback=0),
        InteracaoAssistente(id=4, pergunta="d", status="ok", trace_id="t4", feedback=0),
        AnotacaoAssistente(interacao_id=1, label="alucinada"),
        AnotacaoAssistente(interacao_id=4, label="correta"),
    ])
    for trace, score in [("t1", 0.0), ("t2", 0.0), ("t3", 1.0), ("t4", 1.0), ("sem_humano", 1.0)]:
        db_session.add(AvaliacaoOnline(trace_id=trace, avaliador="fundamentacao", score=score))
    db_session.commit()

    fund = client.get("/monitorizacao/ia/calibracao").json()["avaliadores"]["fundamentacao"]

    assert fund["pares"] == 4
    assert fund["matriz"] == {"ambos_boa": 1, "falso_alarme": 1, "deixou_passar": 1, "ambos_ma": 1}
    assert fund["concordancia"] == 0.5
    assert fund["kappa"] == 0.0  # metade certa é o que se teria por acaso
    assert fund["apanha_mas"] == 0.5
    assert fund["calibrado"] is False


def test_anotar_trace_novo_repete_enquanto_o_phoenix_nao_tem_o_trace(monkeypatch):
    from app.services import phoenix_cliente

    respostas = [404, 404, 200]
    pedidos = []

    class Resposta:
        def __init__(self, status):
            self.status_code = status

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError(self.status_code)

    def post(url, **kwargs):
        pedidos.append(url)
        return Resposta(respostas.pop(0))

    monkeypatch.setattr(phoenix_cliente.httpx, "post", post)
    monkeypatch.setattr(phoenix_cliente.time, "sleep", lambda s: None)

    assert phoenix_cliente._enviar_anotacao("http://phoenix", "t1", {"data": []}, tentativas=6) is True
    assert len(pedidos) == 3
