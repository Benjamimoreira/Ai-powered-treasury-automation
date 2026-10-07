# Postgres com a extensão pgvector (embeddings do histórico - ver
# app/services/indice_vetorial.py).
#
# Mesma base de antes (postgres:16-alpine) em vez da imagem pgvector/pgvector
# (Debian): o volume postgres_data foi criado com a musl do Alpine, e passar
# para a glibc muda a ordenação do texto por baixo dos índices existentes
# (ficam inconsistentes sem REINDEX). Aqui só se acrescenta a extensão.
FROM postgres:16-alpine

ARG PGVECTOR_VERSION=0.8.1

# with_llvm=no: sem os ficheiros de JIT (precisariam do clang/llvm da
# versão exata com que o Postgres foi compilado); OPTFLAGS vazio: sem
# -march=native, para a imagem correr em qualquer CPU x86-64.
RUN apk add --no-cache --virtual .build-deps git build-base \
    && git clone --branch v${PGVECTOR_VERSION} --depth 1 https://github.com/pgvector/pgvector.git /tmp/pgvector \
    && cd /tmp/pgvector \
    && make with_llvm=no OPTFLAGS="" \
    && make with_llvm=no install \
    && cd / && rm -rf /tmp/pgvector \
    && apk del .build-deps
