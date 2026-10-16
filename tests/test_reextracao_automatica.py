from datetime import date, datetime

from app.db.models import DeployScripts, EventoScript, LinhaMapa, MovimentoBancario
from app.services import reextracao_automatica as ra

# quinta-feira 15/10/2026, 10:00 em Lisboa (09:00 UTC)
AGORA = datetime(2026, 10, 15, 9, 0)


def _aviso(db, dia: int, empresa: str, valor: float = 500.0, visto=datetime(2026, 10, 15, 8, 0)) -> None:
    db.add(EventoScript(script="preencher_mapa", nivel="aviso", timestamp=visto, mensagem=(
        f"[AVISO] Dia {dia:02d} {empresa}: extrato bancário (1.00 EUR) difere de Mapa de Saldos "
        f"(2.00 EUR) em {valor:.2f} EUR (> 100 EUR) - dados desincronizados.")))
    db.commit()


def test_horario(db_session):
    assert ra.dentro_do_horario(AGORA)
    assert not ra.dentro_do_horario(datetime(2026, 10, 15, 7, 10))   # 08:10 - tarefas da CGD
    assert not ra.dentro_do_horario(datetime(2026, 10, 15, 14, 0))   # 15:00 - tarefas da CGD
    assert not ra.dentro_do_horario(datetime(2026, 10, 17, 9, 0))    # sábado
    assert not ra.dentro_do_horario(datetime(2026, 10, 15, 21, 0))   # noite: ninguém responde ao SMS


def test_pede_a_maior_uma_de_cada_vez_e_so_uma_vez(db_session):
    _aviso(db_session, 13, "Vidor SGPS", 500)
    _aviso(db_session, 14, "HCC", 9000)
    _aviso(db_session, 15, "Hoje Lda", 9999)          # hoje: fica para as tarefas da tarde
    _aviso(db_session, 1, "Antiga Lda", 9999)         # mais de 7 dias
    db_session.add(EventoScript(script="preencher_mapa", nivel="aviso", timestamp=datetime(2026, 10, 15, 8, 0),
                                mensagem="[AVISO] 14-10: extrato de 'X,LDA' sem entidade correspondente conhecida"))
    db_session.commit()

    pedido = ra.pedir_proxima(db_session, AGORA)
    assert pedido["extracao"] == {"modo": "periodo", "inicio": "2026-10-08", "fim": "2026-10-14", "dia": "2026-10-14",
                                  "empresa": "HCC", "tipo": "Extrato/Mapa ≠ Mapa de Saldos", "origem": "automatica"}
    # ainda pendente: não pede outra
    assert ra.pedir_proxima(db_session, AGORA) is None

    db_session.get(DeployScripts, pedido["id"]).estado = "ok"
    db_session.commit()
    assert ra.pedir_proxima(db_session, AGORA)["extracao"]["empresa"] == "Vidor SGPS"

    db_session.query(DeployScripts).update({"estado": "ok"})
    db_session.commit()
    _aviso(db_session, 14, "HCC", 9000, visto=datetime(2026, 10, 15, 8, 50))  # continua a ser reportada
    assert ra.pedir_proxima(db_session, AGORA) is None  # já tentadas as duas


def test_respeita_o_botao_e_o_horario(db_session):
    _aviso(db_session, 14, "HCC")
    assert ra.pedir_proxima(db_session, datetime(2026, 10, 15, 21, 0)) is None
    # atualizada à mão há pouco: não repete
    db_session.add(DeployScripts(alvo=ra.ALVO, estado="ok", pedido_em=datetime(2026, 10, 15, 7, 0), log=[], extracao={
        "modo": "periodo", "inicio": "2026-10-08", "fim": "2026-10-14", "dia": "2026-10-14", "empresa": "HCC",
        "tipo": "Extrato/Mapa ≠ Mapa de Saldos"}))
    db_session.commit()
    assert ra.pedir_proxima(db_session, AGORA) is None


def test_nao_reextrai_o_que_so_esta_noutro_dia_no_mapa(db_session):
    _aviso(db_session, 14, "HCC")
    db_session.add(MovimentoBancario(empresa="HCC", dia=date(2026, 10, 14), descricao="Trf Mbway", valor=-374.5,
                                     ficheiro_origem="hcc.xlsx"))
    db_session.add(LinhaMapa(empresa="HCC", dia=date(2026, 10, 12), tipo="pagamento", linha=10, descricao="Mbway", pago=-374.5))
    db_session.commit()
    assert ra.pedir_proxima(db_session, AGORA) is None
