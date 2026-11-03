// Função do Supabase usada pela página "ao vivo" do Radar de Ofertas.
// Devolve as ofertas mais recentes do Promobit (e do Pelando, quando o site aceitar o acesso) em JSON,
// no mesmo formato de id do monitor.py. Só lê dados públicos; não grava nada.
// Guarda a resposta por 20 s: com várias páginas abertas, as fontes recebem no máximo uma consulta a cada 20 s.

const UA = "Mozilla/5.0 (compatible; radar-de-ofertas/0.1; uso pessoal)";
const CACHE_MS = 20_000;
let cache: { quando: number; corpo: string } | null = null;

type Oferta = {
  id: string; fonte: string; loja: string; titulo: string; url: string; img: string | null;
  preco: number; preco_de: number | null; curtidas: number; publicado: string | null; cupom: string | null;
};

async function promobit(): Promise<Oferta[]> {
  const r = await fetch("https://api.promobit.com.br/offers?sort=latest", { headers: { "User-Agent": UA } });
  if (!r.ok) throw new Error(`Promobit HTTP ${r.status}`);
  const j = await r.json();
  const out: Oferta[] = [];
  for (const o of j.offers ?? []) {
    const status = String(o.offer_status_name ?? "").toUpperCase();
    const preco = Number(o.offer_price);
    // vitrines "a partir de" e ofertas encerradas ficam de fora, como no monitor.py
    if (!preco || preco <= 0.01 || o.offer_price_type === "STARTING_AT" || ["EXPIRED", "FINISHED", "INACTIVE"].includes(status)) continue;
    const de = Number(o.offer_old_price) || null;
    const foto = String(o.offer_photo ?? "");
    let cupom = o.offer_coupon;
    if (cupom && typeof cupom === "object") cupom = cupom.code ?? cupom.couponCode ?? null;
    out.push({
      id: `promobit:${o.offer_id}`, fonte: "Promobit", loja: o.store_name ?? "?", titulo: o.offer_title ?? "",
      url: `https://www.promobit.com.br/oferta/${o.offer_slug}/`,
      img: foto.startsWith("http") ? foto : foto ? `https://i.promobit.com.br/268${foto}` : null,
      preco, preco_de: de && de > preco ? de : null, curtidas: Number(o.offer_likes) || 0,
      publicado: o.offer_published ?? null, cupom: typeof cupom === "string" && cupom ? cupom : null,
    });
  }
  return out;
}

async function pelando(): Promise<Oferta[]> {
  const r = await fetch("https://www.pelando.com.br/recentes", { headers: { "User-Agent": UA } });
  if (!r.ok) throw new Error(`Pelando HTTP ${r.status}`);
  const html = await r.text();
  const out: Oferta[] = [];
  // o Astro guarda as propriedades de cada componente como [tipo, valor]
  const un = (v: unknown): unknown => {
    if (Array.isArray(v) && v.length === 2 && typeof v[0] === "number") {
      const [t, x] = v as [number, unknown];
      if (t === 1 && Array.isArray(x)) return x.map(un);
      if (x && typeof x === "object") return Object.fromEntries(Object.entries(x).map(([k, y]) => [k, un(y)]));
      return x;
    }
    if (v && typeof v === "object") return Object.fromEntries(Object.entries(v).map(([k, y]) => [k, un(y)]));
    return v;
  };
  const des = (s: string) => s.replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&");
  for (const m of html.matchAll(/<astro-island[^>]*\sprops="([^"]*)"/g)) {
    let p: Record<string, unknown>;
    try { p = un(JSON.parse(des(m[1]))) as Record<string, unknown>; } catch { continue; }
    for (const d of (p.initialDeals as Record<string, any>[] | undefined) ?? []) {
      if (d?.status !== "active" || !d.price || d.kind !== "promotion") continue;
      const graus = Number(d.temperature) || 0;
      out.push({
        id: `pelando:${d.id}`, fonte: "Pelando", loja: d.store?.name ?? "?", titulo: d.title ?? "",
        url: `https://www.pelando.com.br/d/${d.slug}`, img: d.imageUrl ?? null, preco: Number(d.price), preco_de: null,
        curtidas: Math.floor(graus / 10), publicado: d.firstApprovedAt ?? d.createdAt ?? null, cupom: null,
      });
    }
  }
  return out;
}

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, OPTIONS",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
};

Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: CORS });
  if (!cache || Date.now() - cache.quando > CACHE_MS) {
    const [pb, pl] = await Promise.allSettled([promobit(), pelando()]);
    const ofertas = [...(pb.status === "fulfilled" ? pb.value : []), ...(pl.status === "fulfilled" ? pl.value : [])];
    const falhas = [pb, pl].flatMap((r, i) => r.status === "rejected" ? [`${["Promobit", "Pelando"][i]}: ${String(r.reason?.message ?? r.reason)}`] : []);
    cache = { quando: Date.now(), corpo: JSON.stringify({ lido: new Date().toISOString(), ofertas, falhas }) };
  }
  return new Response(cache.corpo, { headers: { ...CORS, "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" } });
});
