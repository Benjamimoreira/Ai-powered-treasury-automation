"""
Separa um PDF de várias páginas em vários PDFs de 1 página cada,
nomeando cada arquivo com o código "diario NN/NNNNN" encontrado
no lado direito da própria página.
"""

from __future__ import annotations

import os
import re
import sys

from pypdf import PdfReader, PdfWriter
import pdfplumber

# A interface gráfica só existe no Windows; no contentor (sem Tk) fica só o
# modo linha de comando: python pdf_depesas.py arquivo.pdf [pasta_destino]
try:
    import tkinter as tk
    from tkinter import filedialog, messagebox
    from tkinter import ttk
    from PIL import ImageTk
except ImportError:
    tk = None

# Padrão do código procurado: "diario" ou "diário", seguido de
# 2 dígitos, uma barra, e 5 ou 6 dígitos. Ex.: "diario 01/70023"
PADRAO_CODIGO = re.compile(
    r"di[aá]ri[oó]\s*(\d{2})\s*/\s*(\d{5,6})",
    re.IGNORECASE,
)


def extrair_texto_pagina(caminho_pdf: str, indice_pagina: int) -> str:
    """
    Extrai o texto de uma única página do PDF.

    Usa o pdfplumber (mais preciso para texto solto/rotacionado)
    e, se não encontrar nada, tenta o pypdf como alternativa.
    """
    with pdfplumber.open(caminho_pdf) as pdf:
        pagina = pdf.pages[indice_pagina]
        texto = pagina.extract_text() or ""

    if not texto.strip():
        leitor = PdfReader(caminho_pdf)
        texto = leitor.pages[indice_pagina].extract_text() or ""

    return texto


def renderizar_pagina_completa(caminho_pdf: str, indice_pagina: int, resolucao: int = 150):
    """
    Renderiza a página inteira do PDF em alta resolução como imagem PIL
    (sem redimensionar ainda). Serve de base tanto para a miniatura de
    contexto quanto para o recorte ampliado do código manuscrito.
    """
    with pdfplumber.open(caminho_pdf) as pdf:
        pagina = pdf.pages[indice_pagina]
        return pagina.to_image(resolution=resolucao).original


def redimensionar_para_exibicao(imagem, largura_max: int = 220) -> ImageTk.PhotoImage:
    """
    Redimensiona uma imagem PIL para caber na interface, mantendo a
    proporção original, e converte para o formato que o Tkinter aceita.
    """
    proporcao = largura_max / imagem.width
    novo_tamanho = (largura_max, max(1, int(imagem.height * proporcao)))
    imagem_redimensionada = imagem.resize(novo_tamanho)
    return ImageTk.PhotoImage(imagem_redimensionada)


def recortar_faixa_codigo(imagem, rotacao_graus: int = 90):
    """
    Recorta a faixa vertical esquerda da página, onde o código
    "diario NN/NNNNN" costuma ser escrito à mão, e gira o recorte
    para que o texto fique na horizontal e mais fácil de ler.

    A página está em formato paisagem (A4 deitado) e o código é
    escrito na vertical nessa faixa — por isso o recorte é estreito
    (25% da largura) e alto (toda a altura da página), e depois
    é girado (90° por padrão; use `rotacao_graus` para ajustar caso
    o sentido do giro fique invertido).
    """
    largura, altura = imagem.size
    caixa_recorte = (0, 0, int(largura * 0.25), altura)
    recorte = imagem.crop(caixa_recorte)

    if rotacao_graus:
        recorte = recorte.rotate(rotacao_graus, expand=True)

    return recorte


def encontrar_codigo_diario(texto: str) -> str | None:
    """
    Procura no texto da página o código no formato "diario NN/NNNNN"
    e devolve já formatado como nome de arquivo (ex.: "01_70023").

    A barra "/" é trocada por "_" porque não é permitida em nomes
    de arquivo no Windows.
    """
    resultado = PADRAO_CODIGO.search(texto)
    if not resultado:
        return None

    parte1, parte2 = resultado.groups()
    return f"{parte1}_{parte2}"


def gerar_nome_arquivo(texto_pagina: str, indice_pagina: int, usados: set) -> str:
    """
    Decide o nome do arquivo para a página atual.

    Se o código "diario NN/NNNNN" for encontrado, usa esse código.
    Caso contrário, usa um nome genérico "pagina_N" para não perder
    a página. Garante nomes únicos caso o mesmo código apareça
    mais de uma vez.
    """
    codigo = encontrar_codigo_diario(texto_pagina)
    nome_base = codigo if codigo else f"pagina_{indice_pagina + 1}"

    nome_final = nome_base
    contador = 2
    while nome_final in usados:
        nome_final = f"{nome_base}_{contador}"
        contador += 1

    usados.add(nome_final)
    return nome_final


def criar_pasta_saida(caminho_pdf_origem: str) -> str:
    """
    Cria (se não existir) a pasta onde os PDFs separados serão salvos.
    Usada quando o usuário não escolhe uma pasta destino: a pasta é
    criada ao lado do PDF original, com o sufixo "_separado".
    """
    pasta_base = os.path.dirname(os.path.abspath(caminho_pdf_origem))
    nome_pdf = os.path.splitext(os.path.basename(caminho_pdf_origem))[0]
    pasta_saida = os.path.join(pasta_base, f"{nome_pdf}_separado")

    os.makedirs(pasta_saida, exist_ok=True)
    return pasta_saida


def salvar_pagina_como_pdf(leitor: PdfReader, indice_pagina: int, caminho_destino: str) -> None:
    """
    Salva uma única página do PDF de origem como um novo arquivo PDF.
    """
    escritor = PdfWriter()
    escritor.add_page(leitor.pages[indice_pagina])

    with open(caminho_destino, "wb") as arquivo_saida:
        escritor.write(arquivo_saida)


def salvar_paginas_com_nomes(caminho_pdf: str, pasta_saida: str, nomes_paginas: list[str]) -> str:
    """
    Gera um PDF por página usando os nomes já confirmados pelo usuário
    na tela de revisão (necessário para os códigos manuscritos, que
    não podem ser lidos automaticamente do texto do PDF).
    """
    leitor = PdfReader(caminho_pdf)
    os.makedirs(pasta_saida, exist_ok=True)

    nomes_usados = set()
    for indice, nome_digitado in enumerate(nomes_paginas):
        nome_base = nome_digitado.strip() or f"pagina_{indice + 1}"

        nome_final = nome_base
        contador = 2
        while nome_final in nomes_usados:
            nome_final = f"{nome_base}_{contador}"
            contador += 1
        nomes_usados.add(nome_final)

        caminho_destino = os.path.join(pasta_saida, f"{nome_final}.pdf")
        salvar_pagina_como_pdf(leitor, indice, caminho_destino)

    return pasta_saida


def separar_pdf(caminho_pdf: str, pasta_saida: str | None = None) -> str:
    """
    Função principal: percorre todas as páginas do PDF de entrada,
    identifica o código "diario NN/NNNNN" de cada uma e gera um
    arquivo PDF individual por página, salvo na pasta de saída.

    Se `pasta_saida` não for informado, cria automaticamente uma
    pasta "<nome_pdf>_separado" ao lado do PDF original.
    Retorna o caminho da pasta onde os arquivos foram salvos.
    """
    leitor = PdfReader(caminho_pdf)
    total_paginas = len(leitor.pages)

    if not pasta_saida:
        pasta_saida = criar_pasta_saida(caminho_pdf)
    else:
        os.makedirs(pasta_saida, exist_ok=True)

    nomes_usados = set()

    for indice in range(total_paginas):
        texto_pagina = extrair_texto_pagina(caminho_pdf, indice)
        nome_arquivo = gerar_nome_arquivo(texto_pagina, indice, nomes_usados)

        caminho_destino = os.path.join(pasta_saida, f"{nome_arquivo}.pdf")
        salvar_pagina_como_pdf(leitor, indice, caminho_destino)

        print(f"Página {indice + 1}/{total_paginas} -> {caminho_destino}")

    print(f"\nConcluído. {total_paginas} arquivo(s) salvos em: {pasta_saida}")
    return pasta_saida


def selecionar_arquivo_pdf(campo_arquivo: tk.Entry) -> None:
    """
    Abre a janela do sistema para escolher o PDF de entrada e
    escreve o caminho escolhido no campo de texto da interface.
    """
    caminho = filedialog.askopenfilename(
        title="Selecionar arquivo PDF",
        filetypes=[("Arquivos PDF", "*.pdf")],
    )
    if caminho:
        campo_arquivo.delete(0, tk.END)
        campo_arquivo.insert(0, caminho)


def selecionar_pasta_destino(campo_pasta: tk.Entry) -> None:
    """
    Abre a janela do sistema para escolher a pasta destino e
    escreve o caminho escolhido no campo de texto da interface.
    """
    caminho = filedialog.askdirectory(title="Selecionar pasta destino")
    if caminho:
        campo_pasta.delete(0, tk.END)
        campo_pasta.insert(0, caminho)


def confirmar_e_salvar(caminho_pdf: str, pasta_saida: str, campos_nomes: list[tk.Entry], janela_revisao: tk.Toplevel) -> None:
    """
    Lê os códigos digitados pelo usuário na tela de revisão, gera os
    PDFs individuais com esses nomes e fecha a tela de revisão.
    """
    nomes = [campo.get() for campo in campos_nomes]

    try:
        pasta_final = salvar_paginas_com_nomes(caminho_pdf, pasta_saida, nomes)
    except Exception as erro:
        messagebox.showerror("Erro ao salvar PDFs", str(erro))
        return

    janela_revisao.destroy()
    messagebox.showinfo("Concluído", f"Arquivos salvos em:\n{pasta_final}")


def atualizar_recorte_exibido(imagem_pagina, rotulo_recorte: tk.Label, estado_rotacao: dict, imagens_referencia: list) -> None:
    """
    Recorta e gira novamente a faixa do código a partir da rotação
    atual guardada em `estado_rotacao`, e atualiza a imagem exibida
    no rótulo correspondente. Chamada toda vez que o botão "Girar"
    é pressionado, pois a imagem preparada pode ter vindo no sentido
    errado (o giro correto pode variar conforme o PDF de origem).
    """
    recorte = recortar_faixa_codigo(imagem_pagina, estado_rotacao["graus"])
    imagem_tk = redimensionar_para_exibicao(recorte, largura_max=260)

    imagens_referencia.append(imagem_tk)  # mantém referência viva (Tkinter não guarda sozinho)
    rotulo_recorte.configure(image=imagem_tk)


def girar_recorte(imagem_pagina, rotulo_recorte: tk.Label, estado_rotacao: dict, imagens_referencia: list) -> None:
    """
    Gira o recorte do código mais 90 graus e reexibe. Usado pelo
    botão "Girar", para o usuário ajustar o sentido de leitura quando
    o recorte não aparece na orientação correta de primeira.
    """
    estado_rotacao["graus"] = (estado_rotacao["graus"] + 90) % 360
    atualizar_recorte_exibido(imagem_pagina, rotulo_recorte, estado_rotacao, imagens_referencia)


def abrir_janela_revisao(caminho_pdf: str, pasta_saida: str) -> None:
    """
    Abre a tela de revisão: para cada página do PDF mostra (1) uma
    miniatura da página inteira, para contexto, e (2) um recorte
    ampliado e girado da faixa esquerda, onde o código
    "diario NN/NNNNN" costuma ser escrito à mão na vertical (a
    página em si fica em formato paisagem). Ao lado, um campo de
    texto para o usuário digitar/conferir o código antes de gerar
    os PDFs. Quando a extração automática de texto já encontra o
    código (caso não seja manuscrito), o campo vem pré-preenchido.
    """
    leitor = PdfReader(caminho_pdf)
    total_paginas = len(leitor.pages)

    janela = tk.Toplevel()
    janela.title("Revisar códigos das páginas")
    janela.geometry("620x650")

    quadro_rolavel = tk.Frame(janela)
    quadro_rolavel.pack(fill="both", expand=True)

    canvas = tk.Canvas(quadro_rolavel)
    barra_rolagem = ttk.Scrollbar(quadro_rolavel, orient="vertical", command=canvas.yview)
    quadro_interno = tk.Frame(canvas)

    quadro_interno.bind("<Configure>", lambda evento: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.create_window((0, 0), window=quadro_interno, anchor="nw")
    canvas.configure(yscrollcommand=barra_rolagem.set)

    canvas.pack(side="left", fill="both", expand=True)
    barra_rolagem.pack(side="right", fill="y")

    imagens_referencia = []  # evita que o Tkinter "esqueça" as imagens (garbage collector)
    campos_nomes = []

    for indice in range(total_paginas):
        imagem_pagina = renderizar_pagina_completa(caminho_pdf, indice)

        miniatura_contexto = redimensionar_para_exibicao(imagem_pagina, largura_max=140)
        imagens_referencia.append(miniatura_contexto)

        texto_pagina = extrair_texto_pagina(caminho_pdf, indice)
        codigo_detectado = encontrar_codigo_diario(texto_pagina) or ""

        linha = tk.Frame(quadro_interno, pady=8)
        linha.pack(fill="x", padx=10)

        tk.Label(linha, image=miniatura_contexto).pack(side="left")

        quadro_recorte = tk.Frame(linha)
        quadro_recorte.pack(side="left", padx=10)

        rotulo_recorte = tk.Label(quadro_recorte)
        rotulo_recorte.pack()

        estado_rotacao = {"graus": 90}
        atualizar_recorte_exibido(imagem_pagina, rotulo_recorte, estado_rotacao, imagens_referencia)

        tk.Button(
            quadro_recorte, text="Girar ↻",
            command=lambda img=imagem_pagina, rot=rotulo_recorte, est=estado_rotacao: girar_recorte(
                img, rot, est, imagens_referencia
            ),
        ).pack(pady=4)

        campo_nome = tk.Entry(linha, width=16)
        campo_nome.insert(0, codigo_detectado)
        campo_nome.pack(side="left", padx=10)
        campos_nomes.append(campo_nome)

    tk.Button(
        janela, text="Confirmar e gerar PDFs",
        command=lambda: confirmar_e_salvar(caminho_pdf, pasta_saida, campos_nomes, janela),
    ).pack(pady=10)

    janela.grab_set()


def executar_separacao(campo_arquivo: tk.Entry, campo_pasta: tk.Entry) -> None:
    """
    Lê os valores preenchidos na interface principal e abre a tela de
    revisão, onde o usuário confere/digita o código de cada página
    antes de os PDFs serem efetivamente gerados.
    """
    caminho_pdf = campo_arquivo.get().strip()
    pasta_saida = campo_pasta.get().strip()

    if not caminho_pdf:
        messagebox.showerror("Erro", "Selecione o arquivo PDF de origem.")
        return

    if not pasta_saida:
        pasta_saida = criar_pasta_saida(caminho_pdf)

    try:
        abrir_janela_revisao(caminho_pdf, pasta_saida)
    except Exception as erro:
        messagebox.showerror("Erro ao abrir revisão", str(erro))


def iniciar_interface() -> None:
    """
    Monta e exibe a janela principal do programa, com os campos e
    botões para escolher o PDF de origem e a pasta destino.
    """
    janela = tk.Tk()
    janela.title("Separar PDF por página")
    janela.geometry("520x160")
    janela.resizable(False, False)

    tk.Label(janela, text="Arquivo PDF:").grid(row=0, column=0, padx=10, pady=10, sticky="w")
    campo_arquivo = tk.Entry(janela, width=50)
    campo_arquivo.grid(row=0, column=1, padx=5, pady=10)
    tk.Button(
        janela, text="Selecionar...",
        command=lambda: selecionar_arquivo_pdf(campo_arquivo),
    ).grid(row=0, column=2, padx=10, pady=10)

    tk.Label(janela, text="Pasta destino:").grid(row=1, column=0, padx=10, pady=10, sticky="w")
    campo_pasta = tk.Entry(janela, width=50)
    campo_pasta.grid(row=1, column=1, padx=5, pady=10)
    tk.Button(
        janela, text="Selecionar...",
        command=lambda: selecionar_pasta_destino(campo_pasta),
    ).grid(row=1, column=2, padx=10, pady=10)

    tk.Button(
        janela, text="Revisar e separar PDF", width=20,
        command=lambda: executar_separacao(campo_arquivo, campo_pasta),
    ).grid(row=2, column=0, columnspan=3, pady=20)

    janela.mainloop()


if __name__ == "__main__":
    if len(sys.argv) >= 2:
        # Uso via linha de comando: python pdf_depesas.py arquivo.pdf [pasta_destino]
        pasta_destino_cli = sys.argv[2] if len(sys.argv) >= 3 else None
        separar_pdf(sys.argv[1], pasta_destino_cli)
    elif tk is None:
        sys.exit("Uso: python pdf_depesas.py arquivo.pdf [pasta_destino]")
    else:
        # Sem argumentos: abre a interface gráfica
        iniciar_interface()
