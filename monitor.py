"""Monitor de promoções reais.

Coleta preços em várias fontes, guarda o histórico num SQLite local e dá uma nota a cada oferta
comparando o preço de hoje com o próprio histórico (até 365 dias). Gera painel.html.

Uso:
    python monitor.py                 escolhe sozinho: completa na 1ª execução do dia, rápida nas outras
    python monitor.py rapida          só o que muda ao longo do dia (Promobit, ofertas do Zoom, KaBuM)
    python monitor.py completa        tudo: categorias do Zoom, Keepa grátis e a fila de vínculos com o Zoom
    python monitor.py zoom kabum      coleta só essas fontes e gera o painel
    python monitor.py painel          só regenera o painel com o que já está no banco

Arquivos: dados/precos.db (histórico), dados/execucoes.log, modelos/painel_modelo.html -> painel.html.

Fontes: promobit, zoom, kabum, keepa (Amazon; só com chave da API Keepa em config.json ou KEEPA_KEY).
Só usa páginas e APIs abertas, uma requisição por endereço, com intervalo entre elas.
Se uma fonte recusar, ela é pulada nesta execução.
"""
import html as htmlmod, json, os, re, sqlite3, statistics, sys, time, unicodedata, urllib.error, urllib.parse, urllib.request
from datetime import date, datetime, timedelta

D = os.path.dirname(os.path.abspath(__file__))
DADOS = os.path.join(D, 'dados')
os.makedirs(DADOS, exist_ok=True)
CFG = json.load(open(os.path.join(D, 'config.json'), encoding='utf-8'))
HOJE = date.today().isoformat()
AGORA = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
MODO = 'completa'  # trocado em main()
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) monitor-precos/0.1 (uso pessoal)'
_ultima_req = {}
_uso = {'req': 0, 'bytes': 0}
_log =open(os.path.join(DADOS, 'execucoes.log'), 'a', encoding='utf-8')


def modo(chave):
    """Valor da configuração do modo atual (rápida/completa)."""
    return CFG['modos'][MODO][chave]


def log(msg):
    linha = f'{datetime.now():%Y-%m-%d %H:%M:%S} {msg}'
    print(linha, flush=True)
    _log.write(linha + '\n')


def baixar(url, como_json=False):
    """GET simples com intervalo mínimo por host. Devolve None em qualquer falha."""
    host = urllib.parse.urlparse(url).netloc
    espera = CFG['intervalo_entre_requisicoes_s'] - (time.time() - _ultima_req.get(host, 0))
    if espera > 0:
        time.sleep(espera)
    _ultima_req[host] = time.time()
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept-Language': 'pt-BR,pt;q=0.9'})
    seguro = re.sub(r'key=[^&]+', 'key=***', url)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            bruto = r.read()
            _uso['req'] += 1
            _uso['bytes'] += len(bruto)
            texto = bruto.decode('utf-8', 'replace')
    except urllib.error.HTTPError as e:
        log(f'  HTTP {e.code} em {seguro}')
        return None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        log(f'  falhou {seguro}: {e}')
        return None
    if not como_json:
        return texto
    try:
        return json.loads(texto)
    except ValueError:
        log(f'  resposta não é JSON em {seguro}')
        return None


def next_data(html):
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html or '', re.S)
    return json.loads(m.group(1)) if m else {}


# ---------------------------------------------------------------- banco

def abrir_banco():
    db = sqlite3.connect(os.path.join(DADOS, 'precos.db'), timeout=30)
    db.executescript('''
    CREATE TABLE IF NOT EXISTS item(
        id TEXT PRIMARY KEY, fonte TEXT, loja TEXT, titulo TEXT, url TEXT, img TEXT, categoria TEXT,
        preco REAL, preco_de REAL, curtidas INTEGER, engajamento INTEGER, destaque INTEGER, cupom TEXT,
        publicado TEXT, ref_min REAL, ref_media REAL, ref_dias INTEGER, hist_buscado TEXT,
        primeiro_visto TEXT, visto_em TEXT);
    CREATE TABLE IF NOT EXISTS preco(id TEXT, dia TEXT, preco REAL, origem TEXT, PRIMARY KEY(id, dia));
    CREATE TABLE IF NOT EXISTS cupom(
        id TEXT PRIMARY KEY, loja TEXT, titulo TEXT, codigo TEXT, desconto TEXT, regras TEXT,
        url TEXT, publicado TEXT, visto_em TEXT);
    CREATE TABLE IF NOT EXISTS hist_zoom(zoom_id TEXT PRIMARY KEY, dia TEXT);
    CREATE TABLE IF NOT EXISTS vinculo(id TEXT PRIMARY KEY, zoom_id TEXT, titulo_zoom TEXT, semelhanca REAL, dia TEXT);
    CREATE TABLE IF NOT EXISTS execucao(inicio TEXT PRIMARY KEY, fim TEXT, modo TEXT, fontes TEXT);
    ''')
    existentes = {r[1] for r in db.execute('PRAGMA table_info(item)')}
    for col, tipo in (('ref_media90', 'REAL'), ('ref_max', 'REAL'), ('ref_dia_min', 'TEXT'), ('keepa_lido', 'TEXT'),
                      ('visto_ts', 'TEXT'), ('primeiro_ts', 'TEXT'), ('graus', 'INTEGER')):
        if col not in existentes:
            db.execute(f'ALTER TABLE item ADD COLUMN {col} {tipo}')
    return db


def salvar(db, itens):
    for it in itens:
        hist = it.pop('_hist', None)
        cols = list(it)
        db.execute(
            f"INSERT INTO item({','.join(cols)},primeiro_visto,visto_em,primeiro_ts,visto_ts) "
            f"VALUES({','.join('?' * len(cols))},?,?,?,?) "
            f"ON CONFLICT(id) DO UPDATE SET {','.join(f'{c}=excluded.{c}' for c in cols if c != 'id')},"
            f"visto_em=excluded.visto_em, visto_ts=excluded.visto_ts",
            [it[c] for c in cols] + [HOJE, HOJE, AGORA, AGORA])
        # menor preço visto no dia
        db.execute('INSERT INTO preco VALUES(?,?,?,?) ON CONFLICT(id,dia) DO UPDATE SET '
                   'preco=min(preco, excluded.preco), origem=excluded.origem',
                   (it['id'], HOJE, it['preco'], 'coleta'))
        for dia, p in hist or []:
            if dia < HOJE and p:
                db.execute('INSERT OR IGNORE INTO preco VALUES(?,?,?,?)', (it['id'], dia, p, it['fonte'].lower()))
    db.commit()


def salvar_cupons(db, cupons):
    for c in cupons:
        db.execute('INSERT INTO cupom VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET visto_em=excluded.visto_em',
                   (c['id'], c['loja'], c['titulo'], c['codigo'], c['desconto'], c['regras'], c['url'], c['publicado'], HOJE))
    db.commit()


# ---------------------------------------------------------------- fontes

def achatar(o):
    """Cupons às vezes vêm em lista, às vezes num dict de listas; devolve só os dicts de cupom."""
    if isinstance(o, dict):
        return [o] if 'couponId' in o else [x for v in o.values() for x in achatar(v)]
    if isinstance(o, list):
        return [x for v in o for x in achatar(v)]
    return []


def promobit(db, so_feed=False):
    """Ofertas aprovadas pela comunidade/equipe do Promobit em todas as lojas, mais os cupons de cada loja.
    so_feed=True lê só a 1ª página do feed (uma requisição pequena), para o modo ao vivo."""
    c = CFG['promobit']
    itens, cupons = {}, []

    def conv(o):
        # a API usa snake_case e a página usa camelCase; aceita os dois
        def g(k):
            return o.get(k, o.get(re.sub(r'_([a-z])', lambda m: m.group(1).upper(), k)))
        preco = g('offer_price')
        status = (g('offer_status_name') or '').upper()
        if not preco or preco <= 0.01 or g('offer_price_type') == 'STARTING_AT' or status in ('EXPIRED', 'FINISHED', 'INACTIVE'):
            return None  # vitrines "a partir de" e ofertas encerradas
        de = g('offer_old_price')
        foto = g('offer_photo') or ''
        cup = g('offer_coupon')
        if isinstance(cup, dict):
            cup = cup.get('code') or cup.get('couponCode') or cup.get('coupon_code')
        return dict(
            id=f"promobit:{g('offer_id')}", fonte='Promobit', loja=g('store_name') or '?', titulo=g('offer_title'),
            url=f"https://www.promobit.com.br/oferta/{g('offer_slug')}/",
            img=foto if foto.startswith('http') else (f'https://i.promobit.com.br/268{foto}' if foto else None),
            categoria=g('category_name'), preco=float(preco), preco_de=float(de) if de and de > preco else None,
            curtidas=g('offer_likes') or 0, engajamento=g('offer_engagement_score') or 0,
            destaque=int(status == 'TOP_OFFER' or bool(g('offer_is_highlight'))),
            cupom=cup if isinstance(cup, str) and cup else None, publicado=g('offer_published'))

    after = ''
    for _ in range(1 if so_feed else c['paginas_feed']):
        j = baixar('https://api.promobit.com.br/offers?sort=latest' + (f'&after={after}' if after else ''), como_json=True)
        if not j:
            break
        for o in j.get('offers', []):
            it = conv(o)
            if it:
                itens[it['id']] = it
        after = j.get('after')
        if not after:
            break
    if so_feed:
        return list(itens.values())

    for slug in c['lojas']:
        pp = next_data(baixar(f'https://www.promobit.com.br/promocoes/loja/{slug}/')).get('props', {}).get('pageProps', {})
        if not pp:
            log(f'  Promobit: loja "{slug}" não encontrada')
            continue
        so = pp.get('serverOffers') or {}
        for o in so.get('offers', []) if isinstance(so, dict) else so:
            it = conv(o)
            if it:
                itens[it['id']] = it
        for cp in achatar(pp.get('couponsByStore')) + achatar(pp.get('serverCoupons')):
            if not isinstance(cp, dict) or cp.get('couponStatusName') not in (None, 'APPROVED'):
                continue
            cupons.append(dict(
                id=f"promobit:{cp.get('couponId')}", loja=cp.get('storeName') or slug, titulo=cp.get('couponTitle'),
                codigo=cp.get('couponCode'), desconto=cp.get('couponDiscountShort') or cp.get('couponDiscount'),
                regras=cp.get('couponInstructions'), publicado=cp.get('couponPublished'),
                url=f"https://www.promobit.com.br/cupons/loja/{cp.get('storeSlug') or slug}/"))
    salvar_cupons(db, cupons)
    log(f'Promobit: {len(itens)} ofertas, {len(cupons)} cupons')
    return list(itens.values())


ZOOM_API = 'https://api-v1.zoom.com.br/restql/run-query/sherlock'


def _astro_props(html):
    """Propriedades dos componentes Astro de uma página. O Astro guarda cada valor como [tipo, valor]."""
    def un(v):
        if isinstance(v, list) and len(v) == 2 and isinstance(v[0], int):
            t, x = v
            return [un(i) for i in x] if t == 1 else ({k: un(y) for k, y in x.items()} if isinstance(x, dict) else x)
        return {k: un(y) for k, y in v.items()} if isinstance(v, dict) else v
    for m in re.finditer(r'<astro-island[^>]*\sprops="([^"]*)"', html or ''):
        try:
            yield {k: un(v) for k, v in json.loads(htmlmod.unescape(m.group(1))).items()}
        except ValueError:
            continue


def pelando(db, so_feed=False):
    """Ofertas do Pelando (a maior comunidade de promoções do Brasil): página de recentes e, fora do modo ao vivo,
    a de mais quentes. A temperatura (graus) mede os votos da comunidade."""
    itens = {}
    for caminho in ['/recentes'] if so_feed else ['/recentes', '/mais-quentes']:
        for p in _astro_props(baixar('https://www.pelando.com.br' + caminho)):
            for d in p.get('initialDeals') or []:
                if not isinstance(d, dict) or d.get('status') != 'active' or not d.get('price') or d.get('kind') != 'promotion':
                    continue
                loja = (d.get('store') or {}).get('name') or '?'
                itens[f"pelando:{d['id']}"] = dict(
                    id=f"pelando:{d['id']}", fonte='Pelando', loja=loja, titulo=d.get('title'),
                    url=f"https://www.pelando.com.br/d/{d.get('slug')}", img=d.get('imageUrl'), categoria=None,
                    preco=float(d['price']), preco_de=None, graus=int(d.get('temperature') or 0),
                    curtidas=int(d.get('temperature') or 0) // 10,  # escala parecida com as curtidas do Promobit
                    publicado=d.get('firstApprovedAt') or d.get('createdAt'))
    if not so_feed:
        log(f'Pelando: {len(itens)} ofertas')
    return list(itens.values())


def zoom_historico_365(db, sid):
    """Histórico diário de 12 meses do Zoom (menor preço entre lojas em cada dia), o mesmo que o gráfico
    "Histórico" do site mostra. Grava em preco sob o id zoom:<sid>. Pula se já foi buscado há pouco."""
    sid = str(sid)
    row = db.execute('SELECT dia FROM hist_zoom WHERE zoom_id=?', (sid,)).fetchone()
    corte = (date.today() - timedelta(days=CFG['zoom']['rebuscar_historico_apos_dias'])).isoformat()
    if row and row[0] >= corte:
        return True
    j = baixar(f'{ZOOM_API}/product_price_history/1?tenant=DEFAULT&product_id={sid}&period=months&amount=12', como_json=True)
    res = ((j or {}).get('product_price_history') or {}).get('result')
    if not isinstance(res, list):
        return False
    for d in res:
        if d.get('price') and d.get('date'):
            db.execute('INSERT INTO preco VALUES(?,?,?,?) ON CONFLICT(id,dia) DO NOTHING',
                       (f'zoom:{sid}', d['date'], float(d['price']), 'zoom'))
    db.execute('INSERT OR REPLACE INTO hist_zoom VALUES(?,?)', (sid, HOJE))
    db.commit()
    return True


def zoom(db):
    """Comparador Zoom (mesma base do Buscapé): menor preço entre lojas (Magalu, Mercado Livre, Amazon, KaBuM,
    Casas Bahia...) e 12 meses de histórico diário de cada produto."""
    c = CFG['zoom']
    urls = [f'https://www.zoom.com.br/ofertas' + (f'?page={p}' if p > 1 else '') for p in range(1, c['paginas_ofertas'] + 1)]
    if modo('zoom_categorias'):
        urls += [f'https://www.zoom.com.br/{cat}' for cat in c['categorias']]
    vistos = {}
    for u in urls:
        hits = ((next_data(baixar(u)).get('props', {}).get('initialReduxState') or {}).get('hits') or {}).get('hits') or []
        for h in hits:
            if h.get('type') == 'product' and h.get('price') and h.get('sourceId'):
                vistos[h['sourceId']] = h
    itens, com_hist = [], 0
    for sid, h in vistos.items():
        url = h['url'] if h['url'].startswith('http') else 'https://www.zoom.com.br' + h['url']
        itens.append(dict(id=f'zoom:{sid}', fonte='Zoom', loja=(h.get('bestOffer') or {}).get('merchantName') or '?',
                          titulo=h['name'], url=url, img=h.get('image'), categoria=h.get('categoryName'),
                          preco=float(h['price']), preco_de=None))
        com_hist += zoom_historico_365(db, sid)
    log(f'Zoom: {len(itens)} produtos, {com_hist} com histórico de 12 meses')
    return itens


# ---------------------------------------------------------------- vínculo com o Zoom (365 dias para as outras fontes)

_UNIDADE = re.compile(r'\d+(gb|tb|mb|g|w|l|hz|mhz|ghz|mah|v|mm|cm|m|kg|btus?|k|p|x|pol|ml|lts?|gen|th|nd|rd|st|un|pcs)|'
                      r'(g?ddr|usb|wifi|pcie|hdmi|bt|ip|ps|qi|lpddr)\d+')


def _tokens(s, juntar=True):
    s = re.sub(r'(?<=[a-z0-9])\+', ' plus', unicodedata.normalize('NFKD', (s or '').lower()))  # S26+ = S26 Plus
    seq = re.findall(r'[a-z0-9]+', ''.join(ch for ch in s if not unicodedata.combining(ch)))
    # "RX 7600" e "Club 130" também viram "rx7600" e "club130", como o Zoom costuma escrever
    juntos = [a + b for a, b in zip(seq, seq[1:]) if a.isalpha() and b.isdigit() and len(b) >= 2] if juntar else []
    return set(seq) | set(juntos)


def _modelos(toks):
    """Códigos que identificam o modelo: misturam letras e números (rx7600, 55c6k, s25, blf08bs).
    Capacidades e unidades (256gb, 5g, 180hz, ddr5) não contam, porque se repetem entre modelos diferentes."""
    return {t for t in toks if len(t) >= 3 and re.search(r'\d', t) and re.search(r'[a-z]', t) and not _UNIDADE.fullmatch(t)}


def _variante(so_item, so_zoom):
    """Códigos que começam com as mesmas letras mas têm números diferentes indicam outro modelo da mesma linha."""
    pref = lambda t: re.match(r'[a-z]*', t).group(0)
    return any(len(pref(a)) >= 1 and pref(a) == pref(b) for a in so_item for b in so_zoom)


def _capacidades(toks):
    return {t for t in toks if re.fullmatch(r'\d+(gb|tb)', t)}


_VARIANTES = {'ultra', 'pro', 'plus', 'max', 'lite', 'mini', 'fe', 'neo', 'edge', 'slim', 'kit', 'lente', 'camera', 'refil'}


def _conflito(titulo, titulo_zoom):
    """True quando os dois títulos claramente não são o mesmo produto: códigos curtos diferentes (i5 x i3),
    variante a mais de um lado (12S x 12S Ultra), outra capacidade ou outro tipo de item (película x lente da câmera)."""
    a, b = _tokens(titulo), _tokens(titulo_zoom)
    curtos = lambda s: {t for t in s if re.fullmatch(r'[a-z]\d{1,2}', t)}  # i3, i5, s9, a5...
    if curtos(a) and curtos(b) and not curtos(a) & curtos(b):
        return True
    if (_VARIANTES & a) ^ (_VARIANTES & b):
        return True
    if _variante(_modelos(a) - _modelos(b), _modelos(b) - _modelos(a)):
        return True
    # números de modelo diferentes dos dois lados (GTX 1650 x 1050Ti, i5-6400T x i5-6500)
    # geração do processador (i3 7ª x i3 4ª); se só um lado diz a geração, não dá para garantir que é o mesmo
    geracao = lambda s: set(re.findall(r'(\d{1,2})\s*a?\s*(?:ger|gen)', unicodedata.normalize('NFKD', (s or '').lower())))
    if geracao(titulo) != geracao(titulo_zoom):
        return True
    numeros = lambda s: {t for t in _tokens(s, juntar=False) if re.fullmatch(r'\d{3,5}[a-z]{0,2}', t) and not _UNIDADE.fullmatch(t)}
    na, nb = numeros(titulo), numeros(titulo_zoom)
    if na - nb and nb - na:
        return True
    ca, cb = _capacidades(a), _capacidades(b)
    return bool(ca - cb and cb - ca)


def _casar(titulo, preco, hits):
    """Escolhe o produto do Zoom que é o mesmo item: precisa dividir um código de modelo, ter boa sobreposição
    de palavras e preço na mesma faixa. Devolve (hit, semelhança) ou (None, 0)."""
    toks = {t for t in _tokens(titulo) if len(t) > 2}
    mods = _modelos(toks)
    # código "de verdade" escrito no título (não formado juntando palavra + número)
    tem_codigo = bool(_modelos({t for t in _tokens(titulo, juntar=False) if len(t) > 2}))
    melhor, nota = None, 0.0
    for h in hits:
        if h.get('type') != 'product' or not h.get('price') or not h.get('sourceId'):
            continue  # "offer" é anúncio avulso de loja, sem histórico no Zoom
        ht = {t for t in _tokens(h.get('name')) if len(t) > 2}
        hmods = _modelos(ht)
        if _conflito(titulo, h.get('name')):
            continue  # outra variante (PFR2250V x PFR2200P, Club 120 x 130, i5 x i3, 12S x 12S Ultra) ou capacidade
        if 'usado' in ht and 'usado' not in toks:
            continue
        sim = len(toks & ht) / len(ht | toks) if ht else 0
        sim = max(sim, len(toks & ht) / len(ht) * 0.8 if ht else 0)  # títulos de loja são longos; o do Zoom é curto
        razao = preco / float(h['price'])
        if tem_codigo:
            ok = bool(mods & hmods) and 0.4 <= razao <= 2.5 and sim >= 0.45
        else:  # sem código de modelo: exige nome quase igual e preço próximo
            ok = 0.6 <= razao <= 1.6 and sim >= 0.7
        if ok and sim > nota:
            melhor, nota = h, sim
    return melhor, nota


def vincular(db, ids=None, limite=None):
    """Para ofertas que não são do Zoom (KaBuM, Promobit, Keepa), acha o mesmo produto no Zoom e baixa o histórico
    de 12 meses dele. Assim dá para ver se o preço "promocional" é isca frente ao preço de mercado do último ano.
    Com ids (modo ao vivo), só procura esses itens e já baixa o histórico do que encontrar."""
    c = CFG['vincular']
    corte_sem = (date.today() - timedelta(days=c['refazer_sem_vinculo_apos_dias'])).isoformat()
    candidatos = db.execute(
        "SELECT i.id, i.titulo, i.preco, i.curtidas, i.preco_de FROM item i LEFT JOIN vinculo v ON v.id=i.id "
        "WHERE i.visto_em=? AND i.fonte!='Zoom' AND (v.id IS NULL OR (v.zoom_id IS NULL AND v.dia<?))", (HOJE, corte_sem)).fetchall()
    if ids is not None:
        candidatos = [r for r in candidatos if r[0] in set(ids)]
    # prioridade: mais curtidas no Promobit, depois maior desconto anunciado
    candidatos.sort(key=lambda r: (-(r[3] or 0), -((1 - r[2] / r[4]) if r[4] else 0)))
    feitos = achados = 0
    if limite is None:
        limite = int(os.environ.get('MONITOR_MAX_BUSCAS') or modo('max_buscas_vinculo'))
    for iid, titulo, preco, _, _ in candidatos[:limite]:
        # tira etiquetas da comunidade ("[PRIME]", "[Rec]", "[MOEDAS R$ 15]", "(Steam)") antes de buscar e comparar
        titulo = re.sub(r'\[[^\]]*\]|\([^)]*\)', ' ', titulo or '').strip() or titulo
        consulta = ' '.join(re.sub(r'[,|()\[\]/+-]', ' ', titulo or '').split()[:9])
        hits = ((next_data(baixar('https://www.zoom.com.br/search?q=' + urllib.parse.quote(consulta)))
                 .get('props', {}).get('initialReduxState') or {}).get('hits') or {}).get('hits') or []
        h, sim = _casar(titulo, preco, hits)
        feitos += 1
        if h:
            achados += 1
            db.execute('INSERT OR REPLACE INTO vinculo VALUES(?,?,?,?,?)', (iid, str(h['sourceId']), h['name'], round(sim, 2), HOJE))
            if ids is not None:
                zoom_historico_365(db, h['sourceId'])
        else:
            db.execute('INSERT OR REPLACE INTO vinculo VALUES(?,?,?,?,?)', (iid, None, None, 0, HOJE))
        db.commit()
    if ids is not None:
        return achados
    # mantém atualizado o histórico de todos os vínculos vistos hoje
    ligados = db.execute("SELECT DISTINCT v.zoom_id FROM vinculo v JOIN item i ON i.id=v.id WHERE i.visto_em=? AND v.zoom_id IS NOT NULL", (HOJE,)).fetchall()
    if MODO == 'completa':  # histórico dos vínculos: uma vez por dia basta (e só rebusca a cada 7 dias)
        for (zid,) in ligados:
            zoom_historico_365(db, zid)
    log(f'Vínculo Zoom: {feitos} buscas, {achados} produtos encontrados; {len(ligados)} vínculos com histórico ({len(candidatos) - feitos} na fila)')


def kabum(db):
    """API pública do catálogo KaBuM. Usa o preço no PIX (ou o da oferta relâmpago, se houver)."""
    c = CFG['kabum']
    itens = {}
    for cat in c['categorias']:
        for pg in range(1, modo('paginas_kabum') + 1):
            j = baixar('https://servicespub.prod.api.aws.grupokabum.com.br/catalog/v2/products-by-category/'
                       f'{cat}?page_number={pg}&page_size=100', como_json=True)
            if not j or not j.get('data'):
                break
            for d in j['data']:
                a = d.get('attributes') or {}
                off = a.get('offer') if isinstance(a.get('offer'), dict) else {}
                preco = off.get('price_with_discount') or a.get('price_with_discount') or a.get('price')
                if not a.get('available') or not preco:
                    continue
                de = a.get('old_price') or 0
                imgs = a.get('images') or []
                itens[f"kabum:{d['id']}"] = dict(
                    id=f"kabum:{d['id']}", fonte='KaBuM', loja='KaBuM', titulo=a.get('title'),
                    url=f"https://www.kabum.com.br/produto/{d['id']}", img=imgs[0] if imgs and isinstance(imgs[0], str) else None,
                    categoria=(a.get('menu') or '').split('/')[0] or cat, preco=float(preco),
                    preco_de=float(de) if de > preco else None, destaque=int(bool(off)))
    log(f'KaBuM: {len(itens)} produtos')
    return list(itens.values())


def keepa(db):
    """Amazon via API oficial do Keepa (paga). Usa as estatísticas de 365 dias de cada ASIN da lista."""
    k = CFG['keepa']
    chave = k.get('chave') or os.environ.get('KEEPA_KEY')
    if not chave:
        log('Keepa: sem chave da API, Amazon fica só via Zoom e Promobit')
        return []
    caminho = os.path.join(D, k['arquivo_asins'])
    asins = [l.strip() for l in open(caminho, encoding='utf-8') if re.fullmatch(r'[A-Z0-9]{10}', l.strip())] if os.path.exists(caminho) else []

    def centavos(arr, i):
        v = arr[i] if isinstance(arr, list) and len(arr) > i else None
        if isinstance(v, list):  # min/max vêm como [tempo, preço]
            v = v[1] if len(v) > 1 else None
        return v / 100 if isinstance(v, (int, float)) and v > 0 else None

    itens = []
    for i in range(0, len(asins), 100):
        j = baixar(f"https://api.keepa.com/product?key={chave}&domain=12&stats=365&history=0&asin={','.join(asins[i:i + 100])}", como_json=True)
        if not j or j.get('error'):
            log(f"Keepa: parou ({(j or {}).get('error')})")
            break
        for p in j.get('products') or []:
            st = p.get('stats') or {}
            atuais = [x for x in (centavos(st.get('current'), 0), centavos(st.get('current'), 1)) if x]
            if not atuais:
                continue
            img = (p.get('imagesCSV') or '').split(',')[0]
            itens.append(dict(
                id=f"amazon:{p['asin']}", fonte='Keepa', loja='Amazon', titulo=p.get('title') or p['asin'],
                url=f"https://www.amazon.com.br/dp/{p['asin']}", img=f'https://m.media-amazon.com/images/I/{img}' if img else None,
                categoria=None, preco=min(atuais), preco_de=None,
                ref_min=centavos(st.get('minInInterval'), 0) or centavos(st.get('minInInterval'), 1),
                ref_media=centavos(st.get('avg'), 0) or centavos(st.get('avg'), 1), ref_dias=365))
        if j.get('tokensLeft', 1) <= 0:
            time.sleep(min(j.get('refillIn', 60000) / 1000, 300))
    log(f'Keepa: {len(itens)} produtos Amazon')
    return itens


def keepa_gratis(db):
    """Estatísticas grátis do Keepa (tabela "Estatística" de keepa.com) já lidas e salvas em arquivo:
    asin|título|preço atual|menor preço da história|data do menor (aammdd)|média 90d|média 180d|máximo.
    Só importa o arquivo; não acessa o Keepa. Ignora arquivos com mais de N dias."""
    k = CFG['keepa_gratis']
    caminho = os.path.join(D, k['arquivo'])
    if not os.path.exists(caminho):
        log(f'Keepa grátis: arquivo {caminho} não existe')
        return []
    # data da leitura vem do config (na nuvem o arquivo é recriado a cada execução e a data do arquivo mentiria)
    lido = date.fromisoformat(k['lido_em']) if k.get('lido_em') else date.fromtimestamp(os.path.getmtime(caminho))
    if (date.today() - lido).days > k['validade_dias']:
        log(f'Keepa grátis: arquivo lido em {lido:%d/%m}, mais velho que {k["validade_dias"]} dias; pulando')
        return []
    titulos = {}
    extra = os.path.join(os.path.dirname(caminho), 'titulos.txt')
    if os.path.exists(extra):
        titulos = dict(l.rstrip('\n').split('|', 1) for l in open(extra, encoding='utf-8') if '|' in l)
    num = lambda s: float(s) if s else None
    itens = []
    for linha in open(caminho, encoding='utf-8'):
        f = linha.rstrip('\n').split('|')
        if len(f) < 8 or not f[2]:
            continue
        asin, tit, cur, mn, mnd, a90, a180, mx = f[:8]
        itens.append(dict(
            id=f'amazon:{asin}', fonte='Keepa grátis', loja='Amazon', titulo=tit.strip() or titulos.get(asin, asin),
            url=f'https://www.amazon.com.br/dp/{asin}', img=None, categoria=None, preco=float(cur), preco_de=None,
            ref_min=num(mn), ref_media=num(a180) or num(a90), ref_dias=180, ref_media90=num(a90), ref_max=num(mx),
            ref_dia_min=f'20{mnd[:2]}-{mnd[2:4]}-{mnd[4:6]}' if len(mnd) == 6 else None, keepa_lido=lido.isoformat()))
    log(f'Keepa grátis: {len(itens)} produtos Amazon (lidos em {lido:%d/%m})')
    return itens


FONTES = {'promobit': promobit, 'pelando': pelando, 'zoom': zoom, 'kabum': kabum, 'keepa': keepa, 'keepa_gratis': keepa_gratis}


# ---------------------------------------------------------------- nota

def _serie(db, iid, corte):
    return db.execute('SELECT dia, preco FROM preco WHERE id=? AND dia>=? AND dia<? ORDER BY dia', (iid, corte, HOJE)).fetchall()


def avaliar(db, it):
    """Nota de 0 a 100 contra os últimos 365 dias. Pesa a queda frente à mediana de 365 dias e o menor preço do ano;
    marca como isca o desconto anunciado sobre preço inflado ou o preço que subiu logo antes da "promoção"."""
    corte = (date.today() - timedelta(days=CFG['historico_dias'])).isoformat()
    p = it['preco']
    serie, base, comparado = _serie(db, it['id'], corte), None, None
    if not it['id'].startswith('zoom:'):
        v = db.execute('SELECT zoom_id, titulo_zoom FROM vinculo WHERE id=? AND zoom_id IS NOT NULL', (it['id'],)).fetchone()
        if v and _conflito(it['titulo'], v[1]):  # ligação feita por uma regra antiga e mais frouxa
            db.execute('UPDATE vinculo SET zoom_id=NULL, titulo_zoom=NULL WHERE id=?', (it['id'],))
            v = None
        mercado = _serie(db, f'zoom:{v[0]}', corte) if v else []
        if len(mercado) > len(serie):
            serie, comparado = mercado, v[1]
    hist = [x for _, x in serie]
    ref_min = ref_med = ref_max = med30 = None
    dia_min = None
    dias = 0
    if len(hist) >= CFG['min_dias_historico']:
        ref_min, ref_med, ref_max, dias = min(hist), statistics.median(hist), max(hist), len(hist)
        dia_min = serie[hist.index(ref_min)][0]
        recentes = [x for d, x in serie if d >= (date.today() - timedelta(days=30)).isoformat()]
        med30 = statistics.median(recentes) if len(recentes) >= 7 and dias >= 60 else None
        base = 'mercado 365d (Zoom)' if comparado else ('Zoom 365d' if it['id'].startswith('zoom:') else 'coleta própria')
    elif it.get('ref_media'):
        ref_min, ref_med, dias = it.get('ref_min'), it['ref_media'], it.get('ref_dias') or 0
        ref_max, dia_min = it.get('ref_max'), it.get('ref_dia_min')
        base = 'Keepa grátis (média 180d, mínimo de toda a história)' if it['fonte'] == 'Keepa grátis' else 'Keepa 365d'
    nota, sinais = 0.0, []
    vs_med = (p / ref_med - 1) * 100 if ref_med else None
    vs_min = (p / ref_min - 1) * 100 if ref_min else None
    if ref_med:
        nota += max(min(-vs_med, 40), -20) * 1.25
        periodo = 'da história (Keepa)' if base and base.startswith('Keepa grátis') else 'de 365 dias'
        if ref_min and p < ref_min * 0.995:
            nota += 20
            sinais.append(f'abaixo do menor preço {periodo}')
        elif ref_min and p <= ref_min * 1.03:
            nota += 12
            sinais.append(f'no menor preço {periodo}')
        elif vs_min is not None and vs_min >= 15:
            sinais.append(f'{vs_min:.0f}% acima do menor preço {periodo}')
    de = it.get('preco_de')
    anunciado = (1 - p / de) * 100 if de and de > p else 0
    motivos_isca = []
    if ref_med and anunciado >= 20 and vs_med > -5:
        motivos_isca.append(f'anuncia -{anunciado:.0f}%, mas está {vs_med:+.0f}% vs mediana do ano')
    if med30 and med30 > ref_med * 1.15 and vs_med > -3:
        motivos_isca.append(f'preço subiu {((med30 / ref_med) - 1) * 100:.0f}% no último mês e "caiu" de volta ao normal')
    if de and ref_max and de > ref_max * 1.1:
        aviso = f'preço "de" {brl(de)} acima de tudo que o mercado cobrou no ano (máx. {brl(ref_max)})'
        # só é isca se o preço "promocional" não estiver claramente abaixo do normal
        (motivos_isca if vs_med > -10 else sinais).append(aviso)
    a90 = it.get('ref_media90')
    if a90 and p > a90 * 1.30:
        motivos_isca.append(f'Keepa: preço {((p / a90) - 1) * 100:.0f}% acima da média de 90 dias ({brl(a90)})')
    if it.get('fonte') == 'Keepa grátis' and comparado and it.get('ref_min'):
        dm = it.get('ref_dia_min') or ''
        sinais.append(f"Keepa: mín. histórico {brl(it['ref_min'])}{' em ' + dm[8:10] + '/' + dm[5:7] + '/' + dm[2:4] if dm else ''}, "
                      f"média 90d {brl(a90) if a90 else '?'}")
    # possível bug: preço errado ou promoção boa demais para ser verdade. Só com prova no histórico
    # (abaixo do menor preço do período e muito abaixo do normal) ou quando a comunidade marca como bug.
    motivos_bug = []
    if ref_min and p <= ref_min * 0.5:
        motivos_bug.append(f'{(1 - p / ref_min) * 100:.0f}% abaixo do menor preço do período ({brl(ref_min)})')
    elif ref_min and ref_med and p <= ref_min and p <= ref_med * 0.4:
        motivos_bug.append(f'abaixo do menor preço do período e {-vs_med:.0f}% abaixo da mediana ({brl(ref_med)})')
    if re.search(r'\b(bug|bugou|bugad[oa]|erro de pre[cç]o|pre[cç]o errado)\b', it.get('titulo') or '', re.I):
        motivos_bug.append('marcado como bug/erro de preço no título')
    if not ref_med and anunciado >= 70:
        sinais.append(f'anuncia -{anunciado:.0f}% sem histórico para confirmar; o "de" costuma ser inflado')
    bug = bool(motivos_bug)
    fachada = bool(motivos_isca) and not bug
    if bug:
        sinais = motivos_bug + sinais
    elif fachada:
        nota -= 25
        sinais = motivos_isca + sinais
    if not ref_med:
        nota += min(anunciado, 50) * 0.3
    curt = it.get('curtidas') or 0
    nota += min(curt, 100) / 100 * 15 + (5 if it.get('destaque') else 0)
    if it.get('graus'):
        if it['graus'] >= 200:
            sinais.append(f"{it['graus']}° no Pelando")
    elif curt >= 20:
        sinais.append(f'{curt} curtidas')
    if it.get('cupom'):
        sinais.append(f"cupom {it['cupom']}")
    nota = round(max(0, min(100, nota)))
    if bug:
        v = 'bug'
    elif fachada:
        v = 'isca'
    elif ref_med is not None:
        v = 'real' if nota >= 45 else 'boa' if nota >= 25 else 'normal'
    elif curt >= 20 or it.get('destaque'):
        v = 'comunidade'
    else:
        v = 'sem_hist'
    passo = max(1, len(hist) // 60)  # até ~60 pontos no gráfico: painel leve
    return dict(nota=nota, veredito=v, base=base, dias=dias, comparado=comparado,
                ref_min=ref_min, dia_min=dia_min, ref_max=ref_max, ref_med=round(ref_med, 2) if ref_med else None,
                vs_med=round(vs_med, 1) if vs_med is not None else None, anunciado=round(anunciado),
                sinais=sinais, serie=hist[::passo] + [p],
                # data do 1º ponto e, para cada ponto, quantos dias depois dele (o histórico tem buracos)
                s0=serie[0][0] if serie else None,
                sx=[(date.fromisoformat(d) - date.fromisoformat(serie[0][0])).days for d, _ in serie[::passo]] if serie else None)


def brl(v):
    return f'R$ {v:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')


def normalizar_loja(nome):
    n = (nome or '?').strip()
    low = n.lower()
    for chave, bonito in (('magazine luiza', 'Magalu'), ('magalu', 'Magalu'), ('mercado livre', 'Mercado Livre'),
                          ('kabum', 'KaBuM'), ('amazon', 'Amazon'), ('aliexpress', 'AliExpress'), ('casas bahia', 'Casas Bahia'),
                          ('ifood', 'iFood'), ('shopee', 'Shopee'), ('americanas', 'Americanas'), ('carrefour', 'Carrefour')):
        if chave in low:
            return bonito
    return n


def gerar_painel(db):
    # só o que foi visto nas últimas N horas: oferta que sumiu das fontes sai do painel
    corte_ts = (datetime.now() - timedelta(hours=CFG['painel_horas'])).strftime('%Y-%m-%d %H:%M:%S')
    ultima = db.execute('SELECT inicio, modo FROM execucao ORDER BY inicio DESC LIMIT 1').fetchone() or (AGORA, MODO)
    db.row_factory = sqlite3.Row
    linhas = [dict(r) for r in db.execute('SELECT * FROM item WHERE visto_ts>=? OR (visto_ts IS NULL AND visto_em=?)', (corte_ts, HOJE))]
    db.row_factory = None
    itens = []
    for it in linhas:
        a = avaliar(db, it)
        itens.append(dict(id=it['id'], fonte=it['fonte'], loja=normalizar_loja(it['loja']), titulo=it['titulo'], url=it['url'],
                          img=it['img'], categoria=it['categoria'], preco=it['preco'], preco_de=it['preco_de'],
                          novo=bool(it.get('primeiro_ts') and it['primeiro_ts'] >= ultima[0]), **a))
    itens.sort(key=lambda x: -x['nota'])
    # painel leve: sem campos vazios e preços com 2 casas
    itens = [{k: (round(v, 2) if isinstance(v, float) else v) for k, v in i.items()
              if k in ('sinais', 'nota', 'anunciado', 'preco') or v not in (None, '', False, 0, [])} for i in itens]
    for i in itens:
        if len(i.get('serie', [])) < 3:
            i.pop('serie', None)
        else:
            i['serie'] = [round(x, 2) for x in i['serie']]
    corte7 = (date.today() - timedelta(days=7)).isoformat()
    cupons = [dict(zip(('loja', 'titulo', 'codigo', 'desconto', 'regras', 'url'), r)) for r in
              db.execute('SELECT loja,titulo,codigo,desconto,regras,url FROM cupom WHERE visto_em>=? ORDER BY loja', (corte7,))]
    for c in cupons:
        c['loja'] = normalizar_loja(c['loja'])
    dias_banco = db.execute('SELECT COUNT(DISTINCT dia) FROM preco WHERE origem="coleta"').fetchone()[0]
    hoje_execs = db.execute('SELECT COUNT(1) FROM execucao WHERE inicio>=? AND fim IS NOT NULL', (HOJE,)).fetchone()[0]
    dados = dict(gerado=datetime.now().strftime('%d/%m/%Y %H:%M'), dias_coleta=dias_banco, itens=itens, cupons=cupons,
                 ultima=datetime.strptime(ultima[0], '%Y-%m-%d %H:%M:%S').strftime('%H:%M'), modo=ultima[1],
                 execucoes_hoje=hoje_execs, horas=CFG['painel_horas'])
    html = open(os.path.join(D, 'modelos', 'painel_modelo.html'), encoding='utf-8').read()
    html = html.replace('__DADOS__', json.dumps(dados, ensure_ascii=False, separators=(',', ':')).replace('</', '<\\/'))
    saida = os.path.join(D, 'painel.html')
    # versão local com doctype (sem ele o navegador usa modo de compatibilidade); a de publicar fica em dados/
    open(saida, 'w', encoding='utf-8').write('<!doctype html>\n<html lang="pt-BR">\n' + html)
    open(os.path.join(DADOS, 'painel_publicar.html'), 'w', encoding='utf-8').write(html)
    from collections import Counter
    log(f'Painel: {len(itens)} ofertas {dict(Counter(i["veredito"] for i in itens))}, {len(cupons)} cupons -> {saida}')


def _relogio():
    """Atualiza data e hora usadas nos registros (o modo ao vivo roda por horas, atravessando dias)."""
    global HOJE, AGORA
    HOJE, AGORA = date.today().isoformat(), datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def gerar_ao_vivo(db, novos, intervalo=None):
    """Página leve com as ofertas que chegaram nas últimas horas, a mais nova primeiro. Recarrega sozinha.
    intervalo: segundos até a próxima leitura (na nuvem, o intervalo do agendamento)."""
    c = dict(CFG['ao_vivo'])
    if intervalo:
        c['intervalo_s'] = intervalo
    corte = (datetime.now() - timedelta(hours=c['horas_na_pagina'])).strftime('%Y-%m-%d %H:%M:%S')
    db.row_factory = sqlite3.Row
    linhas = [dict(r) for r in db.execute("SELECT * FROM item WHERE fonte IN ('Promobit','Pelando') AND primeiro_ts>=? "
                                          "ORDER BY primeiro_ts DESC, publicado DESC LIMIT ?", (corte, c['max_itens']))]
    db.row_factory = None
    itens = []
    for it in linhas:
        a = avaliar(db, it)
        itens.append(dict(id=it['id'], fonte=it['fonte'], loja=normalizar_loja(it['loja']), titulo=it['titulo'], url=it['url'], img=it['img'],
                          preco=it['preco'], preco_de=it['preco_de'], chegou=it['primeiro_ts'][11:16], novo=it['id'] in novos,
                          curtidas=it['curtidas'], nota=a['nota'], veredito=a['veredito'], sinais=a['sinais'],
                          vs_med=a['vs_med'], comparado=a['comparado']))
    dados = dict(atualizado=datetime.now().strftime('%H:%M:%S'), intervalo=c['intervalo_s'], itens=itens,
                 horas=c['horas_na_pagina'], curtidas_alerta=c['curtidas_alerta'])
    html = open(os.path.join(D, 'modelos', 'ao_vivo_modelo.html'), encoding='utf-8').read()
    html = html.replace('__INTERVALO__', str(min(c['intervalo_s'], 300)))  # recarrega no máximo a cada 5 min
    html = html.replace('__DADOS__', json.dumps(dados, ensure_ascii=False, separators=(',', ':')).replace('</', '<\\/'))
    tmp = os.path.join(D, 'ao_vivo.tmp')
    open(tmp, 'w', encoding='utf-8').write(html)
    os.replace(tmp, os.path.join(D, 'ao_vivo.html'))  # troca de uma vez: o navegador nunca lê página pela metade
    return itens


def ao_vivo(db):
    """A cada intervalo_s lê só a 1ª página do feed do Promobit (uma requisição pequena), avalia as ofertas novas
    e atualiza ao_vivo.html. A cada rapida_a_cada_min roda a coleta rápida e refaz o painel completo.
    Bipa quando chega bug, promoção real ou oferta muito curtida. Ctrl+C para parar."""
    global MODO
    c = CFG['ao_vivo']
    if c['intervalo_s'] < 30:
        c['intervalo_s'] = 30  # mais rápido que isso só aumenta a carga no site, sem ganho real
    log(f"== ao vivo: feed a cada {c['intervalo_s']} s, coleta rápida a cada {c['rapida_a_cada_min']} min (Ctrl+C para parar)")
    ultima_rapida, ultimo_pelando, primeira = 0.0, 0.0, True
    while True:
        _relogio()
        try:
            if time.time() - ultima_rapida >= c['rapida_a_cada_min'] * 60:
                coletar(db, [])
                ultima_rapida = time.time()
                _relogio()  # o que a coleta acabou de gravar não conta como "novo" no feed
            MODO = 'rapida'
            salvar(db, promobit(db, so_feed=True))
            if time.time() - ultimo_pelando >= c['pelando_a_cada_s']:
                salvar(db, pelando(db, so_feed=True))
                ultimo_pelando = time.time()
            novos = [r[0] for r in db.execute("SELECT id FROM item WHERE fonte IN ('Promobit','Pelando') AND primeiro_ts=?", (AGORA,))]
            if novos:
                vincular(db, ids=novos, limite=c['vinculos_por_ciclo'])
            itens = gerar_ao_vivo(db, set(novos))
            for i in itens:
                if not i['novo']:
                    continue
                alerta = i['veredito'] in ('bug', 'real') or (i['curtidas'] or 0) >= c['curtidas_alerta']
                log(f"  {'>>> ' if alerta else ''}novo [{i['veredito']}] {i['loja']} {brl(i['preco'])} {i['titulo'][:70]}")
                if alerta:
                    try:
                        import winsound
                        winsound.Beep(1200, 250)
                    except Exception:
                        print('\a', end='', flush=True)
            if primeira and not os.environ.get('MONITOR_SEM_NAVEGADOR'):
                import webbrowser
                webbrowser.open('file:///' + os.path.join(D, 'ao_vivo.html').replace('\\', '/'))
                primeira = False
        except Exception as e:  # um ciclo com erro não derruba o ao vivo
            log(f'ao vivo: erro {type(e).__name__}: {e}')
        time.sleep(c['intervalo_s'])


def coletar(db, args):
    """Coleta normal (rápida/completa ou fontes escolhidas) e regenera o painel."""
    global MODO
    _relogio()
    pedido = [a for a in args if a in CFG['modos']]
    fontes = [a for a in args if a in FONTES]
    if pedido:
        MODO = pedido[0]
    else:  # completa na primeira execução do dia; rápida nas outras
        feita = db.execute("SELECT 1 FROM execucao WHERE modo='completa' AND inicio>=? AND fim IS NOT NULL", (HOJE,)).fetchone()
        MODO = 'rapida' if feita and not fontes else 'completa'
    _uso.update(req=0, bytes=0)
    escolhidas = fontes or modo('fontes')
    t0 = time.time()
    log(f'== coleta {MODO} {AGORA}: {", ".join(escolhidas)}')
    db.execute('INSERT OR REPLACE INTO execucao VALUES(?,?,?,?)', (AGORA, None, MODO, ','.join(escolhidas)))
    db.commit()
    for nome in escolhidas:
        try:
            salvar(db, FONTES[nome](db))
        except Exception as e:  # uma fonte quebrada não derruba as outras
            log(f'{nome}: erro {type(e).__name__}: {e}')
    if CFG.get('vincular', {}).get('ativo', True):
        try:
            vincular(db)
        except Exception as e:
            log(f'vínculo: erro {type(e).__name__}: {e}')
    db.execute('UPDATE execucao SET fim=? WHERE inicio=?', (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), AGORA))
    db.commit()
    log(f"== fim da coleta {MODO} em {(time.time() - t0) / 60:.1f} min, {_uso['req']} requisições, "
        f"{_uso['bytes'] / 1e6:.0f} MB baixados")
    gerar_painel(db)


def main(args):
    db = abrir_banco()
    if args == ['painel']:
        gerar_painel(db)
    elif args == ['aovivo']:
        try:
            ao_vivo(db)
        except KeyboardInterrupt:
            log('== ao vivo parado')
    elif args == ['nuvem']:
        # uma execução agendada na nuvem: coleta (completa na 1ª do dia), refaz o painel e a página ao vivo
        coletar(db, [])
        novos = {r[0] for r in db.execute("SELECT id FROM item WHERE primeiro_ts>=? AND fonte IN ('Promobit','Pelando')", (AGORA,))}
        gerar_ao_vivo(db, novos, intervalo=CFG['nuvem']['intervalo_min'] * 60)
    else:
        coletar(db, args)


if __name__ == '__main__':
    main(sys.argv[1:])
