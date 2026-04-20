# Publicação

## 1. Revisar o que vai para o repositório

O projeto já ignora:

- `__pycache__`
- `dashboard_state`
- `output/*_data.json`

Arquivos grandes que continuam no projeto:

- CSVs do snapshot em `output/`

## 2. Inicializar o Git local

Se ainda não estiver feito:

```bash
git init
git add .
git commit -m "Prepare public deploy"
```

## 3. Criar o repositório no GitHub

Depois de criar o repositório vazio no GitHub:

```bash
git remote add origin https://github.com/SEU-USUARIO/SEU-REPOSITORIO.git
git branch -M main
git push -u origin main
```

## 4. Publicar no Render

1. Acesse o painel do Render.
2. Crie um novo `Web Service`.
3. Conecte o repositório GitHub.
4. O Render detectará o `render.yaml`.
5. Faça o deploy.

## 5. Rodar com Docker

Build:

```bash
docker build -t alunos-consultoria-ranking .
```

Run:

```bash
docker run -p 8501:8501 alunos-consultoria-ranking
```

## Observações

- O deploy público não usa `*_data.json`.
- O dashboard depende dos CSVs em `output/`.
- Para a timeline ficar forte, o ideal é versionar novos snapshots ao longo do tempo.
