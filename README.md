# NIMAGE VIDEO STUDIO

Editor de vídeo desktop da Nimage para Windows. Você importa um vídeo, escolhe um padrão audiovisual, explica como quer a edição e recebe um MP4 editado pela IA.

## Status — versão 1.0.0

| Entrega | Conteúdo | Status |
|---|---|---|
| 1ª | App, interface, importação (botão e arrastar), miniaturas, prévia, padrões, biblioteca, projetos | ✅ |
| 2ª | Transcrição (faster-whisper), cenas (PySceneDetect), IA local (Ollama), plano de edição validado | ✅ |
| 3ª | FFmpeg: cortes, enquadramento no rosto, zoom nos cortes, vinhetas/cartões, transições, legendas no estilo do padrão, trilha que abaixa na fala, efeitos sonoros, exportação MP4 (Salvar como) | ✅ |
| 4ª | Ajustes em linguagem natural (com desfazer), Claude opcional | ✅ |

Testes automáticos: `python -m pytest -q` (46 testes, incluindo renderização real com o FFmpeg).
Os testes usam uma IA e uma transcrição **simuladas**. O primeiro teste com Ollama e Whisper reais acontece no computador da Nimage.

## Como funciona

1. **Nova edição**: importe os vídeos, escolha o padrão, o objetivo, a duração, o formato (livre), o tom e escreva as instruções.
2. **Analisar e planejar com IA**: extrai o áudio, transcreve com o tempo de cada palavra, identifica as cenas
   e pede à IA um plano. A IA escolhe trechos numerados (`V1-F012`); o programa valida tudo (ids, repetições,
   duração, destaques) e pede de novo se a resposta vier inválida.
3. **Edição e prévia**: confira o plano, toque a prévia dos trechos, reordene, remova e aprove.
4. **Gerar vídeo**: o FFmpeg monta o MP4 real. Os trechos ficam em cache, então um ajuste só refaz o que mudou.
5. **Ajustes**: escreva “Abaixe a música”, “Deixe o começo mais impactante”, “Retire o trecho T3”…
   A IA transforma o pedido em operações; o resto do plano é preservado e o vídeo é refeito. Há **Desfazer**.
6. **Exportar MP4 (Salvar como…)**: escolha formato, tamanho, qualidade e onde salvar.

## Preparar cada computador (uma vez)

1. Instalar o programa (instalador gerado pelo GitHub, aba **Actions**).
2. Instalar o **Ollama** (ollama.com) e deixá-lo aberto.
3. No programa, em **Configurações**:
   - **Baixar modelo recomendado**: `qwen2.5:14b` no PC de 12 GB, `qwen2.5:7b` no de 8 GB.
   - **Transcrição pela placa de vídeo → Ativar**: baixa ~1,3 GB de bibliotecas da NVIDIA, uma vez.
4. Na primeira análise, o modelo de transcrição (~3 GB) é baixado sozinho. Depois disso, tudo funciona sem internet.
5. Instalar as fontes dos padrões no Windows (Neue Plak, Source Serif 4…), para as legendas e os títulos usarem a fonte certa.

## Padrões

- Vêm **dentro do programa** (`nimage_studio/resources/padroes_embutidos.json`) e são ajustáveis na tela **Padrões**.
- Cada projeto guarda uma cópia do padrão usado: mudar um padrão não altera projetos antigos.
- Para levar ajustes a todos os computadores: **Padrões → Exportar todos para o programa…** → substitua o arquivo
  no GitHub → um novo instalador é gerado.
- A **Biblioteca** de cada padrão alimenta a edição: a primeira trilha disponível vira a música, a vinheta em vídeo
  (em Logos) vira abertura/encerramento, a imagem de logo vira o cartão final e os arquivos de efeitos sonoros
  (de preferência com “whoosh” no nome) entram nas transições.

## Claude (opcional)

Em **Configurações → IA usada**: escolha Claude, cole a chave de API (console.anthropic.com) e clique em **Testar Claude**.
Só a transcrição e os dados dos trechos são enviados; os vídeos nunca saem do computador. O uso é cobrado pela
Anthropic na conta da chave. O programa funciona totalmente sem o Claude.

## Desenvolvimento

```
pip install -r requirements.txt
python run.py                 # abre o programa
python run.py --autoteste     # confere bibliotecas e faz uma renderização mínima
python -m pytest -q           # testes
```

O FFmpeg precisa estar no PATH ou em `ffmpeg/bin` ao lado do programa (o instalador já inclui).
A cada envio para a branch `main`, o GitHub Actions roda os testes no Windows, gera o `.exe`, faz o autoteste
do executável e publica o instalador.

## Onde ficam os dados

`%LOCALAPPDATA%\NimageVideoStudio`: banco `nimage_studio.db`, análises, cache de trechos (apagado após 14 dias sem uso),
vídeos gerados (`renders`), modelos de transcrição, bibliotecas da NVIDIA e `erros.log`.
Os vídeos originais nunca são alterados.

## Limites conhecidos desta versão

- O enquadramento no rosto é fixo por trecho (não segue o rosto em movimento dentro do trecho).
- Não há motions animados (cards de interface, contadores etc.), apenas cartões de título, logo e chamada para ação.
- A IA só "vê" o vídeo pela fala e pelas mudanças de cena; trechos sem fala aparecem como "imagem sem fala".
- Vários vídeos são montados em sequência; não há sincronização multicâmera.
