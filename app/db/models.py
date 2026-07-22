from datetime import datetime

from sqlalchemy import Column, Date, DateTime, Float, ForeignKey, Integer, JSON, String
from sqlalchemy.orm import relationship

from app.db.session import Base


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

    reconciliacoes = relationship("Reconciliacao", back_populates="linha")


class Reconciliacao(Base):
    __tablename__ = "reconciliacoes"

    id = Column(Integer, primary_key=True)
    movimento_id = Column(Integer, ForeignKey("movimentos_bancarios.id"), nullable=False)
    linha_id = Column(Integer, ForeignKey("linhas_mapa.id"), nullable=True)
    tipo_match = Column(String, nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False)

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
