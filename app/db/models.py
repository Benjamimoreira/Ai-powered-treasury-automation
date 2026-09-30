from datetime import datetime, timezone

from sqlalchemy import Column, Date, DateTime, Float, ForeignKey, Integer, JSON, String
from sqlalchemy.orm import relationship

from app.db.session import Base


def _utcnow_naive() -> datetime:
    """datetime.utcnow() está deprecated; isto devolve o mesmo valor (UTC
    "naive", sem tzinfo) para manter a convenção usada em todo o código
    (colunas DateTime sem timezone, comparações com .replace(tzinfo=None))."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class MovimentoBancario(Base):
    __tablename__ = "movimentos_bancarios"

    id = Column(Integer, primary_key=True)
    dia = Column(Date, nullable=False)
    empresa = Column(String, nullable=False)
    descricao = Column(String, nullable=False)
    valor = Column(Float, nullable=False)
    ficheiro_origem = Column(String, nullable=False)

    reconciliacoes = relationship("Reconciliacao", back_populates="movimento")


class LinhaMapa(Base):
    __tablename__ = "linhas_mapa"

    id = Column(Integer, primary_key=True)
    dia = Column(Date, nullable=False)
    tipo = Column(String, nullable=False)
    linha = Column(Integer, nullable=False)
    empresa = Column(String, nullable=False)
    previsto = Column(Float, nullable=True)
    pago = Column(Float, nullable=True)
    imputacao = Column(String, nullable=True)
    descricao = Column(String, nullable=True)

    reconciliacoes = relationship("Reconciliacao", back_populates="linha")


class Reconciliacao(Base):
    __tablename__ = "reconciliacoes"

    id = Column(Integer, primary_key=True)
    movimento_id = Column(Integer, ForeignKey("movimentos_bancarios.id"), nullable=False)
    linha_id = Column(Integer, ForeignKey("linhas_mapa.id"), nullable=True)
    tipo_match = Column(String, nullable=False)
    timestamp = Column(DateTime, default=_utcnow_naive, nullable=False)

    movimento = relationship("MovimentoBancario", back_populates="reconciliacoes")
    linha = relationship("LinhaMapa", back_populates="reconciliacoes")


class CasoAmbiguo(Base):
    __tablename__ = "casos_ambiguos"

    id = Column(Integer, primary_key=True)
    movimento_id = Column(Integer, ForeignKey("movimentos_bancarios.id"), nullable=False)
    dia = Column(Date, nullable=False)
    empresa = Column(String, nullable=False)
    valor = Column(Float, nullable=False)
    candidatos = Column(JSON, nullable=True)
    resolvido_por = Column(String, nullable=True)
    resolucao = Column(String, nullable=True)
    resolucao_sugerida = Column(String, nullable=True)
    justificacao_sugerida = Column(String, nullable=True)


class SaldoDiario(Base):
    __tablename__ = "saldos_diarios"

    id = Column(Integer, primary_key=True)
    dia = Column(Date, nullable=False)
    entidade = Column(String, nullable=False)
    saldo_contabilistico = Column(Float, nullable=True)
    saldo_disponivel = Column(Float, nullable=True)


class FaturaRecebida(Base):
    __tablename__ = "faturas_recebidas"

    id = Column(Integer, primary_key=True)
    outlook_id = Column(String, unique=True, nullable=False, index=True)
    dia = Column(Date, nullable=False, index=True)
    hora = Column(String, nullable=True)
    remetente = Column(String, nullable=True)
    assunto = Column(String, nullable=True)
    motivo = Column(String, nullable=True)
    empresa = Column(String, nullable=True)
    fornecedor = Column(String, nullable=True)
    nif_fornecedor = Column(String, nullable=True)
    n_anexos_pdf = Column(Integer, nullable=True)
    debito = Column(String, nullable=True)
    credito = Column(String, nullable=True)
    saldo = Column(String, nullable=True)
    valor_fatura = Column(String, nullable=True)
    pdf_relativo = Column(String, nullable=True)


class AuditoriaDia(Base):
    """Histórico persistente de auditorias já pedidas (uma linha por
    auditoria registada, não só a mais recente) - ao contrário de
    reconciliador.auditoria_dia (só leitura, recalcula na hora e não
    grava nada), isto fica consultável mais tarde mesmo depois de os
    dados em movimentos_bancarios/linhas_mapa terem mudado."""
    __tablename__ = "auditorias_dia"

    id = Column(Integer, primary_key=True)
    dia = Column(Date, nullable=False, index=True)
    timestamp = Column(DateTime, default=_utcnow_naive, nullable=False, index=True)
    sem_match_fwd = Column(Integer, nullable=False)
    sem_match_rev = Column(Integer, nullable=False)
    soma_extrato = Column(Float, nullable=False)
    soma_mapa = Column(Float, nullable=False)
    diferenca = Column(Float, nullable=False)
    movimentos_sem_match = Column(JSON, nullable=True)
    linhas_sem_match = Column(JSON, nullable=True)
    diferencas_por_empresa = Column(JSON, nullable=True)


class ExecucaoScript(Base):
    __tablename__ = "execucoes_scripts"

    id = Column(Integer, primary_key=True)
    script = Column(String, nullable=False, index=True)
    status = Column(String, nullable=False)
    erro = Column(String, nullable=True)
    log = Column(JSON, nullable=True)
    duracao_segundos = Column(Float, nullable=True)
    timestamp = Column(DateTime, default=_utcnow_naive, nullable=False, index=True)


class EventoScript(Base):
    """Um evento de log reportado em tempo real, a meio de uma corrida
    ainda a decorrer (ex.: um [ERRO] apanhado no dia 12 de 26 a processar)
    - ao contrário de ExecucaoScript, que só grava o resultado final de
    uma corrida já terminada. Pedido explícito (logging em tempo real,
    26/08/2026): dá visibilidade a um erro assim que acontece, em vez de
    só quando a corrida toda terminar, minutos depois."""
    __tablename__ = "eventos_scripts"

    id = Column(Integer, primary_key=True)
    script = Column(String, nullable=False, index=True)
    nivel = Column(String, nullable=False)
    mensagem = Column(String, nullable=False)
    timestamp = Column(DateTime, default=_utcnow_naive, nullable=False, index=True)
