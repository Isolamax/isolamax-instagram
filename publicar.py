#!/usr/bin/env python3
"""Publica no Instagram da Isolamax os posts aprovados da fila.json cuja hora chegou.

Roda no GitHub Actions a cada 15 minutos (.github/workflows/publicar.yml), só com a
API oficial da Meta e o app próprio IsolaMaxApp. Nenhum serviço de terceiro.

Regras:
- Só entra na fila o post que o dono aprovou ("ok post N"); só publica status "agendado".
- Publica quando a hora chegou e o atraso é de até 6 h; mais que isso, marca "atrasado"
  e espera o dono decidir (não solta post velho fora de hora).
- Antes de publicar, confere se a legenda já está nos posts recentes (evita duplicar
  se uma rodada anterior caiu depois de publicar e antes de gravar a fila).
- As mídias saem pelo GitHub Pages deste repositório (a Meta exige URL pública; imagem
  só em JPEG).

Segredos (GitHub Actions): IG_ACCESS_TOKEN e IG_USER_ID.

Uso local:
  python3 publicar.py verificar          confere a fila e os arquivos (sem rede)
  python3 publicar.py testar N           cria o contêiner do post N na Meta SEM publicar
  python3 publicar.py                    rodada normal (é o que o Actions executa)
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

API = "https://graph.instagram.com/v24.0"
AQUI = Path(__file__).resolve().parent
FILA = AQUI / "fila.json"
BASE_MIDIA = os.environ.get("MIDIA_BASE", "https://isolamax.github.io/isolamax-instagram/")
ATRASO_MAXIMO = timedelta(hours=6)


def carregar_env_local():
    """No Mac, lê o token de ../automacao/.env-meta (no Actions vem dos segredos)."""
    arq = AQUI.parent / "automacao" / ".env-meta"
    if os.environ.get("IG_ACCESS_TOKEN") or not arq.exists():
        return
    for linha in arq.read_text().splitlines():
        if "=" in linha and not linha.startswith("#"):
            k, v = linha.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def api(metodo, caminho, **params):
    params["access_token"] = os.environ["IG_ACCESS_TOKEN"]
    dados = urllib.parse.urlencode(params)
    if metodo == "GET":
        req = urllib.request.Request(f"{API}/{caminho}?{dados}")
    else:
        req = urllib.request.Request(f"{API}/{caminho}", data=dados.encode(), method=metodo)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        corpo = e.read().decode(errors="replace")
        try:
            msg = json.loads(corpo).get("error", {}).get("message", corpo)
        except ValueError:
            msg = corpo
        raise RuntimeError(f"Meta {e.code}: {msg[:300]}") from None


def url_midia(rel):
    return BASE_MIDIA + urllib.parse.quote(rel)


def url_no_ar(url):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=30) as r:
            return r.status == 200
    except Exception:
        return False


def esperar_pronto(container, limite_s=600):
    fim = time.time() + limite_s
    while time.time() < fim:
        st = api("GET", container, fields="status_code,status").get("status_code")
        if st == "FINISHED":
            return
        if st in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"contêiner {container} terminou em {st}")
        time.sleep(10)
    raise RuntimeError(f"contêiner {container} não ficou pronto em {limite_s} s")


def criar_container(item):
    uid = os.environ["IG_USER_ID"]
    tipo, legenda, midias = item["tipo"], item["legenda"], item["midias"]
    # Rótulo "Info de IA" da Meta: só nos posts marcados com "ia": true (ex.: avatar).
    ia = {"is_ai_generated": "true"} if item.get("ia") else {}
    if tipo == "imagem":
        c = api("POST", f"{uid}/media", image_url=url_midia(midias[0]), caption=legenda, **ia)["id"]
    elif tipo == "carrossel":
        filhos = []
        for m in midias:
            f = api("POST", f"{uid}/media", image_url=url_midia(m), is_carousel_item="true")["id"]
            esperar_pronto(f)
            filhos.append(f)
        c = api("POST", f"{uid}/media", media_type="CAROUSEL", children=",".join(filhos), caption=legenda, **ia)["id"]
    elif tipo == "reel":
        extra = {"cover_url": url_midia(item["capa"])} if item.get("capa") else {}
        c = api("POST", f"{uid}/media", media_type="REELS", video_url=url_midia(midias[0]),
                caption=legenda, share_to_feed="true", **extra, **ia)["id"]
    else:
        raise RuntimeError(f"tipo desconhecido: {tipo}")
    esperar_pronto(c)
    return c


def ja_publicado(legenda):
    inicio = " ".join(legenda.split())[:120]
    recentes = api("GET", "me/media", fields="caption,permalink,timestamp", limit=10).get("data", [])
    for m in recentes:
        if " ".join((m.get("caption") or "").split())[:120] == inicio:
            return m.get("permalink") or True
    return None


def verificar(fila):
    erros = []
    for item in fila:
        n = item.get("post")
        for campo in ("post", "quando", "tipo", "legenda", "midias", "status"):
            if campo not in item:
                erros.append(f"post {n}: falta {campo}")
        datetime.fromisoformat(item["quando"])  # precisa ter fuso, ex. -03:00
        if len(item["legenda"]) > 2200:
            erros.append(f"post {n}: legenda com mais de 2200 caracteres")
        if item["legenda"].count("#") > 30:
            erros.append(f"post {n}: mais de 30 hashtags")
        if item["tipo"] == "carrossel" and not 2 <= len(item["midias"]) <= 10:
            erros.append(f"post {n}: carrossel precisa de 2 a 10 imagens")
        for m in item["midias"] + ([item["capa"]] if item.get("capa") else []):
            p = AQUI / m
            if not p.exists():
                erros.append(f"post {n}: arquivo não existe: {m}")
            elif item["tipo"] != "reel" or m == item.get("capa"):
                if p.suffix.lower() not in (".jpg", ".jpeg"):
                    erros.append(f"post {n}: imagem precisa ser JPEG: {m}")
    return erros


def salvar(fila):
    FILA.write_text(json.dumps(fila, ensure_ascii=False, indent=2) + "\n")


def rodada():
    fila = json.loads(FILA.read_text())
    agora = datetime.now(timezone.utc)
    mudou = False
    problemas = []
    for item in fila:
        if item["status"] != "agendado":
            continue
        quando = datetime.fromisoformat(item["quando"])
        if quando > agora:
            continue
        if agora - quando > ATRASO_MAXIMO:
            item["status"] = "atrasado"
            item["obs"] = f"não publicado: passou mais de 6 h do horário ({agora:%d/%m %H:%M} UTC)"
            problemas.append(item["post"])
            mudou = True
            continue
        urls = [url_midia(m) for m in item["midias"]] + ([url_midia(item["capa"])] if item.get("capa") else [])
        if not all(url_no_ar(u) for u in urls):
            print(f"post {item['post']}: mídia ainda não está no ar no Pages; tento na próxima rodada")
            continue
        link = ja_publicado(item["legenda"])
        if link:
            item.update(status="publicado", link=link if isinstance(link, str) else None,
                        obs="já estava no Instagram (não publicado de novo)")
            mudou = True
            continue
        try:
            c = criar_container(item)
            media_id = api("POST", f"{os.environ['IG_USER_ID']}/media_publish", creation_id=c)["id"]
            link = api("GET", media_id, fields="permalink").get("permalink")
            item.update(status="publicado", media_id=media_id, link=link,
                        publicado_em=datetime.now(timezone.utc).isoformat(timespec="seconds"))
            print(f"post {item['post']}: publicado {link}")
        except Exception as e:
            item.update(status="erro", obs=str(e)[:400])
            problemas.append(item["post"])
            print(f"post {item['post']}: ERRO {e}")
        mudou = True
        salvar(fila)  # grava logo depois de cada post
    if mudou:
        salvar(fila)
    if problemas:
        print(f"posts com problema: {problemas}")
    return 1 if problemas else 0


def main():
    carregar_env_local()
    fila = json.loads(FILA.read_text())
    cmd = sys.argv[1] if len(sys.argv) > 1 else "rodada"
    if cmd == "verificar":
        erros = verificar(fila)
        print("\n".join(erros) if erros else f"fila ok ({len(fila)} posts)")
        return 1 if erros else 0
    if cmd == "testar":
        n = int(sys.argv[2])
        item = next(i for i in fila if i["post"] == n)
        c = criar_container(item)
        print(f"post {n}: contêiner {c} pronto (FINISHED). NÃO publicado; expira sozinho em 24 h.")
        return 0
    return rodada()


if __name__ == "__main__":
    sys.exit(main())
