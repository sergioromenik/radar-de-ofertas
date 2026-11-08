// Função do Supabase usada pela página "Comparar" do Radar de Ofertas.
// Recebe um nome de produto (?q=) ou um link de loja (?url=), procura no Zoom (mesmo catálogo do Buscapé,
// que reúne Magalu, Americanas, Casas Bahia, Amazon e outras) e devolve as ofertas por loja e o histórico
// de 12 meses do produto mais parecido. Só lê dados públicos; não grava nada.
// Guarda cada busca por 10 minutos para não repetir consultas ao Zoom.

const UA = "Mozilla/5.0 (compatible; radar-de-ofertas/0.1; uso pessoal)";
const CACHE_MS = 10 * 60_000;
const cache = new Map<string, { quando: number; corpo: string }>();

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, OPTIONS",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
};

async function nextData(url: string): Promise<Record<string, any>> {
  const r = await fetch(url, { headers: { "User-Agent": UA, "Accept-Language": "pt-BR" } });
  if (!r.ok) throw new Error(`Zoom HTTP ${r.status}`);
  const m = (await r.text()).match(/<script id="__NEXT_DATA__"[^>]*>([\s\S]*?)<\/script>/);
  if (!m) throw new Error("Zoom mudou o formato da página");
  return JSON.parse(m[1])?.props?.initialReduxState ?? {};
}

// Transforma um link de loja numa busca: o nome do produto costuma estar no próprio endereço.
function consultaDoLink(link: string): string | null {
  let u: URL;
  try { u = new URL(link.trim()); } catch { return null; }
  const partes = u.pathname.split("/").filter(Boolean).map(decodeURIComponent);
  let slug = "";
  const dp = partes.findIndex((p) => p === "dp" || p === "gp");
  if (u.hostname.includes("amazon") && dp > 0) slug = partes[dp - 1];
  else if (u.hostname.includes("mercadolivre") || u.hostname.includes("mercadolibre")) {
    slug = (partes.find((p) => /^MLB-?\d+-/i.test(p)) ?? partes[0] ?? "").replace(/^MLB-?\d+-/i, "").replace(/-_JM.*$/i, "");
  } else slug = partes.reduce((a, p) => (p.split("-").length > a.split("-").length ? p : a), "");
  const q = slug.replace(/[-_+]+/g, " ").replace(/\s+/g, " ").trim();
  return q.split(" ").length >= 2 ? q.split(" ").slice(0, 9).join(" ") : null;
}

function resumoHistorico(dias: { date: string; price: number }[]) {
  const precos = dias.map((d) => d.price).filter((p) => p > 0);
  if (!precos.length) return null;
  const ord = [...precos].sort((a, b) => a - b);
  const minimo = ord[0];
  return {
    dias: precos.length, minimo, mediana: ord[Math.floor(ord.length / 2)], maximo: ord[ord.length - 1],
    dia_minimo: dias.find((d) => d.price === minimo)?.date ?? null,
    serie: dias.filter((_, i) => i % 6 === 0 || i === dias.length - 1).map((d) => [d.date, d.price]),
  };
}

async function historico12m(id: string) {
  const u = `https://api-v1.zoom.com.br/restql/run-query/sherlock/product_price_history/1?tenant=DEFAULT&product_id=${id}&period=months&amount=12`;
  const r = await fetch(u, { headers: { "User-Agent": UA } });
  if (!r.ok) return null;
  const j = await r.json();
  return resumoHistorico((j?.product_price_history?.result ?? []) as { date: string; price: number }[]);
}

// Ofertas de cada loja na página do produto.
async function ofertasDoProduto(caminho: string) {
  const s = await nextData("https://www.zoom.com.br" + caminho);
  const achadas: Record<string, any>[] = [];
  const visitar = (v: unknown, prof = 0) => {
    if (prof > 6 || !v || typeof v !== "object") return;
    if (Array.isArray(v)) {
      for (const x of v) {
        if (x && typeof x === "object" && "sellerName" in x && "price" in x) achadas.push(x as Record<string, any>);
        else visitar(x, prof + 1);
      }
      return;
    }
    for (const x of Object.values(v)) visitar(x, prof + 1);
  };
  visitar(s.offers);
  const vistas = new Set<string>();
  const ofertas = achadas
    // a página repete a mesma oferta em vários blocos (melhor oferta, lista, variações)
    .filter((o) => { const k = `${o.sellerName}|${o.price}`; return Number(o.price) > 0 && !vistas.has(k) && !!vistas.add(k); })
    .map((o) => ({
      loja: String(o.sellerName ?? "?"), preco: Number(o.price), total: Number(o.totalPrice) || null,
      marketplace: !!o.sellerIsMarketplace, nome: String(o.name ?? ""),
      url: o.id ? `https://www.zoom.com.br/lead?oid=${o.id}` : null,
    }))
    .sort((a, b) => a.preco - b.preco);
  return ofertas;
}

async function comparar(q: string) {
  const s = await nextData("https://www.zoom.com.br/search?q=" + encodeURIComponent(q));
  const hits = ((s.hits?.hits ?? []) as Record<string, any>[]).filter((h) => Number(h.price) > 0).slice(0, 16);
  const resultados = hits.map((h) => ({
    tipo: h.type === "product" ? "produto" : "anuncio", id: String(h.sourceId ?? ""), nome: String(h.name ?? ""),
    preco: Number(h.price), lojas: Number(h.storeCount) || 1,
    loja: h.merchantName ?? h.bestOffer?.merchantName ?? null, img: h.image ?? null,
    url: h.url ? "https://www.zoom.com.br" + h.url : null, caminho: h.type === "product" ? h.url : null,
  }));
  // detalha os 3 primeiros produtos do catálogo (os anúncios avulsos não têm histórico)
  const produtos = resultados.filter((r) => r.tipo === "produto").slice(0, 3);
  await Promise.all(produtos.map(async (p: Record<string, any>) => {
    const [of, hi] = await Promise.allSettled([ofertasDoProduto(p.caminho), historico12m(p.id)]);
    p.ofertas = of.status === "fulfilled" ? of.value : [];
    p.historico = hi.status === "fulfilled" ? hi.value : null;
  }));
  return { consulta: q, lido: new Date().toISOString(), resultados: resultados.map(({ caminho, ...r }) => r) };
}

Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: CORS });
  const p = new URL(req.url).searchParams;
  const link = p.get("url");
  const q = (link ? consultaDoLink(link) : p.get("q"))?.trim();
  const resp = (corpo: unknown, status = 200) =>
    new Response(typeof corpo === "string" ? corpo : JSON.stringify(corpo), {
      status, headers: { ...CORS, "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" },
    });
  if (!q || q.length < 3) {
    return resp({ erro: link ? "Não consegui tirar o nome do produto desse link. Cole o nome do produto." : "Digite o nome do produto." }, 400);
  }
  const chave = q.toLowerCase();
  const c = cache.get(chave);
  if (c && Date.now() - c.quando < CACHE_MS) return resp(c.corpo);
  try {
    const corpo = JSON.stringify(await comparar(q));
    cache.set(chave, { quando: Date.now(), corpo });
    if (cache.size > 300) cache.delete(cache.keys().next().value!);
    return resp(corpo);
  } catch (e) {
    return resp({ erro: `Falha ao consultar o Zoom: ${String((e as Error)?.message ?? e)}`, consulta: q }, 502);
  }
});
