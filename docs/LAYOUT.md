# Layout do dashboard

Este documento junta tudo o que define o aspeto do dashboard: o tema, as cores, o CSS, o formato dos números e a forma como o separador **Visão Geral** está montado. Serve de referência para pôr outro separador, ou outra app, com o mesmo aspeto.

O fundo da página é **cinza-rato** (`#c8cacd`). As caixas e os cartões dos KPIs são brancos e destacam-se do fundo.

## 1. Tema base: [.streamlit/config.toml](../.streamlit/config.toml)

Define o fundo da página, o fundo branco das caixas e o vermelho dos botões e da aba ativa.

```toml
[theme]
base = "light"
primaryColor = "#c8102e"
backgroundColor = "#c8cacd"
secondaryBackgroundColor = "#ffffff"
textColor = "#1b2433"
font = "sans serif"

[client]
toolbarMode = "minimal"
showSidebarNavigation = false
```

## 2. Grelhas, eixos e letra dos gráficos: [app.py:20-53](../dashboard/app.py#L20-L53)

```python
# Números e datas dos gráficos em português (170 249 em vez de 170,249;
# meses em português) - os eixos usavam o formato inglês do Vega, e as
# tabelas e os KPIs o português, na mesma página.
LOCALE_PT = {
    "number": {"decimal": ",", "thousands": " ", "grouping": [3], "currency": ["", " €"]},
    "time": {
        "dateTime": "%A, %e de %B de %Y, %X", "date": "%d/%m/%Y", "time": "%H:%M:%S",
        "periods": ["AM", "PM"],
        "days": ["domingo", "segunda", "terça", "quarta", "quinta", "sexta", "sábado"],
        "shortDays": ["dom", "seg", "ter", "qua", "qui", "sex", "sáb"],
        "months": ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro",
                   "outubro", "novembro", "dezembro"],
        "shortMonths": ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"],
    },
}


@alt.theme.register("tesouraria_pt", enable=True)
def _tema_pt():
    # locale português + aspeto de relatório financeiro: mesma tipografia do
    # dashboard, grelha discreta, sem moldura à volta do gráfico
    letra, tinta, cinza, linha = "IBM Plex Sans", "#1b2433", "#5b6577", "#e6e9ef"
    return alt.theme.ThemeConfig({"config": {
        "locale": LOCALE_PT,
        "font": letra,
        "background": "#ffffff",
        "view": {"stroke": "transparent"},
        "axis": {"labelColor": cinza, "titleColor": tinta, "labelFont": letra, "titleFont": letra,
                 "labelFontSize": 11, "titleFontSize": 11, "titleFontWeight": 600,
                 "gridColor": linha, "domainColor": "#cfd5de", "tickColor": "#cfd5de"},
        "axisX": {"grid": False},
        "legend": {"labelColor": tinta, "titleColor": cinza, "labelFont": letra, "titleFont": letra,
                   "titleFontWeight": 600, "labelFontSize": 11},
        "title": {"color": "#0b1f3a", "font": letra, "fontSize": 14, "fontWeight": 600, "anchor": "start"},
    }})
```

## 3. Cores dos dados da Visão Geral: [app.py:95-108](../dashboard/app.py#L95-L108)

O vermelho VIDÓR é só para a moldura (caixas, aba ativa, botões). Os dados usam esta paleta:

```python
COR_SALDO_CONTABILISTICO = "#2a78d6"
COR_SALDO_DISPONIVEL = "#1baf7a"

COR_FLUXO_POSITIVO = "#2a78d6"
COR_FLUXO_NEGATIVO = "#e34948"

COR_RANKING_SALDO = "#2a78d6"

COR_RECEBIMENTOS_MES = "#1baf7a"
COR_PAGAMENTOS_MES = "#e34948"
```

e na [linha 168](../dashboard/app.py#L168):

```python
COLUNA_VALOR_EUR = {"valor": st.column_config.NumberColumn("Valor", format="euro")}
```

## 4. Cabeçalho, fundo, caixas, KPIs, separadores e botões: [app.py:366-511](../dashboard/app.py#L366-L511)

```python
st.set_page_config(page_title="Predição Financeira & Tesouraria", page_icon="💶", layout="wide")

# Cabeçalho de relatório financeiro: faixa azul-marinho, título, data e a
# hora da última atualização dos dados (GET /sync/estado), com o traço
# vermelho VIDÓR por baixo.
def _hora_ultima_atualizacao() -> str:
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        marca = api.estado_sincronizacao().get("atualizado_em")
        return datetime.fromisoformat(marca.replace("Z", "+00:00")).astimezone(ZoneInfo("Europe/Lisbon")).strftime("%H:%M")
    except Exception:
        return "—"


st.markdown(
    f"""
    <div class="cabecalho-vidor">
      <div class="cabecalho-linha">
        <div class="cabecalho-marca">VIDÓR · TESOURARIA</div>
        <div class="cabecalho-meta">{date.today().strftime('%d/%m/%Y')} &nbsp;·&nbsp; Dados atualizados às {_hora_ultima_atualizacao()}</div>
      </div>
      <div class="cabecalho-linha" style="margin-top:6px;">
        <div class="cabecalho-titulo">Plataforma de Análise de Tesouraria</div>
        <div class="cabecalho-sub">Análise financeira consolidada · liquidez, fluxos e previsão</div>
      </div>
    </div>
    """,
    unsafe_allow_html=True,
)

# Caixas (st.container(border=True)) com a classe "st-key-caixa-N", para o
# CSS abaixo as conseguir pintar: o Streamlit 1.65 não marca no HTML quais
# os contentores com borda. O contador volta a 0 em cada execução da página,
# por isso as chaves são estáveis entre reruns.
_container_original = getattr(st, "_container_original", st.container)
st._container_original = _container_original
_contador_caixas = [0]


def _container_com_classe(*args, border=None, key=None, **kwargs):
    if border and key is None:
        _contador_caixas[0] += 1
        key = f"caixa-{_contador_caixas[0]}"
    return _container_original(*args, border=border, key=key, **kwargs)


st.container = _container_com_classe
```

O CSS:

```css
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&display=swap');
:root {
    --azul: #0b1f3a; --azul-2: #16325c; --tinta: #1b2433; --cinza: #454d5c;
    --linha: #dfe3ea; --fundo-suave: #f7f8fa;
    --vermelho: #c8102e; --vermelho-escuro: #8e0b20; --vermelho-linha: rgba(200, 16, 46, .45);
    --fundo: #c8cacd;
}
/* fundo cinza-rato: as caixas e os cartões brancos destacam-se da página */
.stApp, [data-testid="stAppViewContainer"], [data-testid="stHeader"] { background: var(--fundo) !important; }
html, body, .stMarkdown, button, input, textarea, select, [data-testid="stWidgetLabel"] {
    font-family: 'IBM Plex Sans', -apple-system, 'Segoe UI', sans-serif;
}
div[data-testid="stMetricValue"], [data-testid="stDataFrame"], .stMarkdown td, .stMarkdown code {
    font-variant-numeric: tabular-nums;
}

/* cabeçalho */
.cabecalho-vidor {
    background: linear-gradient(100deg, var(--azul) 0%, var(--azul-2) 100%);
    margin: -1rem -1rem 1.6rem -1rem; padding: 18px 32px 16px 32px;
    border-bottom: 4px solid var(--vermelho);
}
.cabecalho-linha { display: flex; align-items: baseline; justify-content: space-between; gap: 16px; flex-wrap: wrap; }
.cabecalho-marca { color: #ffffff; font-size: .78rem; font-weight: 700; letter-spacing: .18em; }
.cabecalho-marca::before { content: ""; display: inline-block; width: 10px; height: 10px; background: var(--vermelho);
                           margin-right: 8px; vertical-align: -1px; }
.cabecalho-meta, .cabecalho-sub { color: #aeb9cc; font-size: .8rem; font-weight: 500; letter-spacing: .03em; }
.cabecalho-titulo { color: #ffffff; font-size: 1.5rem; font-weight: 600; letter-spacing: .005em; }

/* caixas */
[class*="st-key-caixa-"] {
    background: #ffffff; border: 1px solid var(--vermelho-linha) !important; border-top: 3px solid var(--vermelho) !important;
    border-radius: 6px !important; box-shadow: 0 1px 2px rgba(11, 31, 58, .06), 0 4px 14px rgba(11, 31, 58, .04);
}
/* títulos de caixa: parágrafo só com um **negrito** (convenção do dashboard) */
[class*="st-key-caixa-"] [data-testid="stMarkdownContainer"] > p:only-child > strong:only-child {
    color: var(--azul); font-size: 1.02rem; font-weight: 700; letter-spacing: .07em; text-transform: uppercase;
    display: block; padding-bottom: 8px; margin-bottom: 4px; border-bottom: 1px solid var(--linha);
}
[data-testid="stCaptionContainer"] { color: var(--cinza); }
[data-testid="stExpander"] details { border-color: var(--linha) !important; border-radius: 6px; }
[data-testid="stExpander"] summary { background: var(--fundo-suave); border-radius: 6px; }
[data-testid="stExpander"] summary p { font-weight: 600; color: var(--tinta); }
[data-testid="stExpander"] summary:hover p { color: var(--vermelho-escuro); }

/* separadores - Streamlit 1.65 (react-aria): role=tablist/tab/tabpanel */
.stTabs [role="tablist"] { flex-wrap: wrap; overflow: visible; gap: 6px; border-bottom: 1px solid var(--linha); }
.stTabs [role="tab"] { padding: 12px 14px 10px 14px; height: auto; }
.stTabs [role="tab"] p {
    font-size: .95rem; font-weight: 700; color: var(--cinza); letter-spacing: .07em; text-transform: uppercase;
}
.stTabs [role="tab"]:hover p { color: var(--azul); }
.stTabs [role="tab"][aria-selected="true"] p { color: var(--azul) !important; }
.stTabs > div > [role="tabpanel"], .stTabs [role="tabpanel"] { padding-top: 22px; }
.stTabs .stTabs [role="tab"] p { font-size: .85rem; }

/* títulos de secção: azul-marinho com traço vermelho curto */
h3 {
    color: var(--azul); font-weight: 700; letter-spacing: .005em; font-size: 1.45rem !important;
    margin: 1.8rem 0 1rem 0 !important; padding-bottom: 10px;
    border-bottom: 1px solid var(--linha);
    background: linear-gradient(var(--vermelho), var(--vermelho)) left bottom / 56px 3px no-repeat;
}

/* KPIs em cartão: fechados com borda à volta toda */
div[data-testid="stMetric"] {
    background: #ffffff; border: 1px solid #c3cad5; border-top: 3px solid var(--azul);
    border-radius: 6px; padding: 12px 16px 10px 16px; box-shadow: 0 1px 2px rgba(11, 31, 58, .05);
}
[data-testid="stMetricLabel"] p {
    color: var(--cinza); font-size: .74rem; font-weight: 600; letter-spacing: .08em; text-transform: uppercase;
}
div[data-testid="stMetricValue"] { color: var(--azul); font-weight: 700; font-size: 1.7rem; }
div[data-testid="stDataFrame"] { border: 1px solid var(--linha); border-radius: 6px; }

button[kind="primary"] { background: var(--vermelho); border-color: var(--vermelho); border-radius: 4px; font-weight: 600; }
button[kind="primary"]:hover { background: var(--vermelho-escuro); border-color: var(--vermelho-escuro); }
button[kind="secondary"] { border-radius: 4px; font-weight: 600; border-color: var(--linha); color: var(--tinta); }
button[kind="secondary"]:hover { border-color: var(--azul); color: var(--azul); }
```

## 5. Formato dos números: [app.py:540-545](../dashboard/app.py#L540-L545)

```python
def _n(valor, casas: int = 0, sinal: bool = False) -> str:
    """Número no formato português: 170 249 / 1 234,56 / +7 885 (espaço
    inquebrável nos milhares, vírgula decimal) - as tabelas já mostravam
    assim e os KPIs/legendas em inglês (170,249), na mesma página."""
    texto = format(valor, f"{'+' if sinal else ''},.{casas}f")
    return texto.replace(",", " ").replace(".", ",")
```

## 6. Separadores: [app.py:603-612](../dashboard/app.py#L603-L612)

```python
(
    aba_visao_geral, aba_saldos, aba_analise_extratos, aba_faturas,
    aba_forecast, aba_monitorizacao,
) = st.tabs(
    ["Visão Geral", "Saldos", "Análise de Extratos", "Faturas", "Predição", "Monitorização"],
    key="aba_principal", on_change="rerun",
)
```

## 7. Grelha da Visão Geral

O separador está em [app.py:1673-1868](../dashboard/app.py#L1673-L1868). O Assistente de IA fica no topo ([app.py:1453-1549](../dashboard/app.py#L1453-L1549), função `_painel_assistente`).

- **Ordem**: primeiro a barra do filtro, depois a linha de KPIs e depois `st.columns([3, 2])`, com a caixa grande à esquerda e a pequena à direita. Por baixo vem a caixa da liquidez do mês e, a seguir, as tabelas de recebimentos e pagamentos do dia em `st.columns(2)`.
- **Filtro**: numa caixa com `st.container(border=True, horizontal=True, vertical_alignment="bottom")`. O `date_input` tem `width=220` e o `st.caption` fica ao lado.
- **KPIs**: `st.container(horizontal=True)` com os `st.metric(..., border=True)` lado a lado. Alguns usam `chart_data=`, `chart_type=` e `delta_arrow=`, que só existem num Streamlit recente (1.65).
- **Título de caixa**: `st.markdown("**Título**")` sozinho na primeira linha da caixa. O CSS só apanha este padrão e põe-no em maiúsculas azuis, com uma linha por baixo.
- **Gráficos de linhas**: eixo X `%d/%m` inclinado a -45°, eixo Y com título `"EUR"` e formato `,.0f`, legenda em cima sem título, `strokeWidth=2` e `width="stretch"`.
- **Tabelas**: `hide_index=True`, `width="stretch"` e os valores em euros com `COLUNA_VALOR_EUR` (`format="euro"`).

O gráfico das contas com mais saldo usa o `rotulo_conta` ([app.py:199-205](../dashboard/app.py#L199-L205)) para os nomes das contas.

## De onde vem cada cor e cada linha

- **Fundo da página** (`#c8cacd`, cinza-rato): `backgroundColor` no `config.toml` e a regra `.stApp` no CSS. É preciso a regra do CSS porque, sem `!important`, o fundo do tema do Streamlit prevalece.
- **Fundo branco das caixas, borda vermelha e topo vermelho de 3px**: o bloco `/* caixas */` do CSS. Só funciona com o patch `_container_com_classe`, que dá a classe `st-key-caixa-N` a cada `st.container(border=True)`.
- **Cartões dos KPIs**: fundo branco, borda `#c3cad5` à volta toda e traço azul de 3px em cima. Com a borda `#dfe3ea` (a das outras linhas) não se via no fundo cinza e os cartões pareciam ter só o traço de cima.
- **Grelhas dos gráficos**: vêm do tema Altair. As linhas horizontais são `#e6e9ef`, não há grelha vertical (`axisX grid False`), os eixos e os traços são `#cfd5de` e o fundo é branco sem moldura (`view stroke transparent`).
