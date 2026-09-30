"""Envio de alertas por email quando a auditoria diária (reconciliador.
registar_auditoria_dia) encontra discrepâncias entre os extratos bancários
e o Mapa de Pagamentos e Recebimentos - avisa que algo pode ter falhado no
preencher_mapa.py (script externo) antes que alguém repare manualmente.

Configuração via variáveis de ambiente (ver .env.example, secção SMTP_*).
Sem SMTP_HOST definido, enviar_alerta_auditoria() não faz nada (ambiente de
desenvolvimento/testes) - nunca falha a auditoria por causa do email."""
import logging
import os
import smtplib
from email.mime.text import MIMEText
from typing import Any, Dict

logger = logging.getLogger("api_tesouraria.alertas")

DESTINATARIO_PADRAO = "benjamim.moreira@vidor.pt"


def _config_smtp():
    host = os.environ.get("SMTP_HOST")
    if not host:
        return None
    return {
        "host": host,
        "port": int(os.environ.get("SMTP_PORT", "587")),
        "utilizador": os.environ.get("SMTP_USER"),
        "password": os.environ.get("SMTP_PASSWORD"),
        "remetente": os.environ.get("SMTP_FROM") or os.environ.get("SMTP_USER"),
        "destinatario": os.environ.get("SMTP_ALERTA_PARA", DESTINATARIO_PADRAO),
    }


def _corpo_alerta(dia, resultado: Dict[str, Any]) -> str:
    linhas = [
        f"Auditoria do dia {dia} encontrou discrepâncias entre os extratos e o Mapa.",
        "",
        f"Movimentos do extrato sem linha correspondente no Mapa: {resultado['sem_match_fwd']}",
        f"Linhas do Mapa por confirmar sem movimento correspondente: {resultado['sem_match_rev']}",
        f"Diferença total (extrato - mapa): {resultado['diferenca_extrato_mapa']:.2f}",
        "",
    ]
    diferencas = resultado.get("diferencas_por_empresa") or []
    if diferencas:
        linhas.append("Diferença por empresa:")
        for d in diferencas:
            linhas.append(f"  {d['empresa']}: {d['diferenca']:.2f}")
    return "\n".join(linhas)


def alerta_necessario(resultado: Dict[str, Any]) -> bool:
    return resultado["sem_match_fwd"] > 0 or resultado["sem_match_rev"] > 0


def enviar_alerta_auditoria(dia, resultado: Dict[str, Any]) -> bool:
    """Envia um email de alerta se a auditoria tiver alguma discrepância
    (ver alerta_necessario). Devolve True se o email foi enviado, False
    caso contrário (sem discrepância, ou SMTP não configurado). Nunca
    propaga exceções - um alerta falhado não pode impedir a auditoria de
    ficar registada."""
    if not alerta_necessario(resultado):
        return False

    config = _config_smtp()
    if config is None:
        logger.warning("SMTP_HOST não configurado - alerta de auditoria de %s não foi enviado.", dia)
        return False

    mensagem = MIMEText(_corpo_alerta(dia, resultado), "plain", "utf-8")
    mensagem["Subject"] = f"[api-tesouraria] Discrepância na auditoria do dia {dia}"
    mensagem["From"] = config["remetente"]
    mensagem["To"] = config["destinatario"]

    try:
        with smtplib.SMTP(config["host"], config["port"], timeout=15) as servidor:
            servidor.starttls()
            if config["utilizador"]:
                servidor.login(config["utilizador"], config["password"])
            servidor.sendmail(config["remetente"], [config["destinatario"]], mensagem.as_string())
        return True
    except Exception:
        logger.exception("Falha ao enviar alerta de auditoria de %s.", dia)
        return False
