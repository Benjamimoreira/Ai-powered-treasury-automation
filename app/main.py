from fastapi import FastAPI

from app.routers import ambiguos, reconciliacao, saldos, sync

app = FastAPI(title="API de Reconciliação de Tesouraria")

app.include_router(reconciliacao.router)
app.include_router(ambiguos.router)
app.include_router(saldos.router)
app.include_router(sync.router)


@app.get("/")
def raiz():
    return {"status": "ok"}
