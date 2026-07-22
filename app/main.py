from fastapi import FastAPI

app = FastAPI()


@app.get("/")
def raiz():
    return {"status": "ok"}
