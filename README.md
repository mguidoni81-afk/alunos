# Alunos Consultoria Ranking

Base inicial para transformar paginas do Ranking dos Concursos em dados estruturados.

## Objetivo desta fase

Primeiro vamos extrair o maximo de dados possivel do site.
Depois, com os dados em maos, a gente decide como trabalhar com ranking, prioridade comercial e algoritmos.

## Parser local

Existe um parser em [`ranking_parser.py`](C:\Users\guido\Downloads\Alunos-Consultoria-Ranking\ranking_parser.py) para ler uma pagina salva em `.txt` ou `.html`.

Ele ja consegue:

- extrair o catalogo de concursos do seletor
- extrair a tabela do concurso selecionado
- identificar marcacoes de `nomeado` e `dentro das vagas`
- extrair os outros concursos feitos pelo candidato

## Tampermonkey

O userscript principal esta em [`tampermonkey/ranking-dos-concursos.user.js`](C:\Users\guido\Downloads\Alunos-Consultoria-Ranking\tampermonkey\ranking-dos-concursos.user.js).

### Como instalar

1. Instale a extensao Tampermonkey no navegador.
2. Crie um novo script.
3. Cole o conteudo de [`tampermonkey/ranking-dos-concursos.user.js`](C:\Users\guido\Downloads\Alunos-Consultoria-Ranking\tampermonkey\ranking-dos-concursos.user.js).
4. Abra [rankingdosconcursos.com.br](https://www.rankingdosconcursos.com.br/).
5. Use o painel `Alunos Consultoria Scraper` no canto superior direito.

### O que o userscript exporta

- `selector_contests.csv`: lista de concursos encontrados no seletor
- `contest_pages.csv`: resumo de cada pagina coletada
- `candidates.csv`: tabela principal de candidatos
- `other_results.csv`: tabela achatada com os resultados cruzados de "fez tb"
- `data.json`: dump completo com paginas, candidatos, resultados cruzados e HTML bruto

### Campos brutos coletados

- metadados da pagina
- URL da coleta
- concurso selecionado
- nome do candidato
- notas
- colocacao
- flags de nomeado e dentro das vagas
- outros concursos feitos pelo candidato
- link e parametros de `nomeacao_email.php` quando existirem
- HTML bruto da pagina no JSON

### Modos

- `Exportar concurso atual`: coleta so a lista aberta
- `Varrer todos do filtro atual`: percorre todos os concursos visiveis no seletor daquele filtro

### Observacoes praticas

- O script respeita o filtro atual da pagina. Se estiver em `Fiscais`, ele vai percorrer esse conjunto.
- Existe um campo de `delay` entre requisicoes para evitar bater no site de forma agressiva.
- O `data.json` pode ficar grande, principalmente na varredura completa, porque inclui HTML bruto.

## Como rodar o parser local

```bash
python ranking_parser.py "SEFAZ PR.txt"
```

## Dashboard

O dashboard exploratorio esta em [`app.py`](C:\Users\guido\Downloads\Alunos-Consultoria-Ranking\app.py).

### O que ele faz

- carrega snapshots da pasta `output`
- consolida candidatos por aluno
- permite ajustar pesos de score
- mostra ranking de alunos, concursos, score lab e qualidade dos dados

### Como rodar

```bash
python -m streamlit run app.py
```

### Dependencias

```bash
python -m pip install -r requirements.txt
```

## Deploy público

O projeto já está preparado para deploy público com:

- [`Dockerfile`](C:\Users\guido\Downloads\Alunos-Consultoria-Ranking\Dockerfile)
- [`render.yaml`](C:\Users\guido\Downloads\Alunos-Consultoria-Ranking\render.yaml)
- [`\.streamlit/config.toml`](C:\Users\guido\Downloads\Alunos-Consultoria-Ranking\.streamlit\config.toml)
- [`\.dockerignore`](C:\Users\guido\Downloads\Alunos-Consultoria-Ranking\.dockerignore)

### O que foi ajustado para produção

- o app não depende mais do `data.json` para funcionar no deploy
- arquivos gigantes desnecessários foram excluídos do build via `dockerignore`
- o Streamlit já sobe em modo headless e usa a porta da plataforma

### Deploy no Render

1. Suba o projeto para um repositório GitHub.
2. No Render, crie um novo `Web Service`.
3. Conecte o repositório.
4. O Render detectará o [`render.yaml`](C:\Users\guido\Downloads\Alunos-Consultoria-Ranking\render.yaml).
5. Faça o deploy.

### Deploy com Docker em qualquer plataforma

Build local:

```bash
docker build -t alunos-consultoria-ranking .
```

Rodar localmente:

```bash
docker run -p 8501:8501 alunos-consultoria-ranking
```

### Observação importante sobre dados

O deploy público vai levar os CSVs da pasta `output`, mas não leva os arquivos `*_data.json`, que são pesados e não são necessários para o app.

Se vocês quiserem reduzir ainda mais o deploy:

- mantenham no repositório apenas o snapshot mais recente
- removam coletas auxiliares e arquivos de teste

### Recomendação de hospedagem

- Para subir rápido: Render
- Para ter mais controle: VPS com Docker
- Para protótipo interno: Railway também funciona bem com o Dockerfile atual

## Teste rapido

```bash
python -m unittest discover -s tests
```
