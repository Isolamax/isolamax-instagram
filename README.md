# Publicação do Instagram da Isolamax

Fila de posts **já aprovados pelo dono** e as mídias deles. Um GitHub Actions roda a cada
15 minutos e publica pela API oficial da Meta (app próprio IsolaMaxApp) o que chegou na hora.

- `fila.json`: os posts aprovados, com data e hora, legenda, mídias e status
  (`agendado`, `publicado`, `erro`, `atrasado`).
- `midia/`: as imagens (JPEG) e os vídeos, servidos pelo GitHub Pages para a Meta buscar.
- `publicar.py`: o publicador (`verificar`, `testar N` e a rodada normal).

Nenhum serviço de terceiro tem acesso ao Instagram. O token fica nos segredos do repositório.
