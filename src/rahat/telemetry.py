"""
Distributed tracing to Azure Monitor (Application Insights), the same backend
the Foundry portal's tracing view reads from.

Off unless APPLICATIONINSIGHTS_CONNECTION_STRING is set, and a no-op if the
optional package is missing, so local runs and CI never depend on Azure.
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger("rahat.telemetry")


def setup_telemetry(app) -> bool:
    conn = os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING")
    if not conn:
        return False
    try:
        from azure.monitor.opentelemetry import configure_azure_monitor
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    except ImportError:
        log.warning("telemetry requested but azure-monitor-opentelemetry is not installed")
        return False
    configure_azure_monitor(connection_string=conn, logger_name="rahat")
    FastAPIInstrumentor.instrument_app(app, excluded_urls="metrics,healthz,readyz,api/stream,static/.*")
    log.info("Azure Monitor tracing on")
    return True
