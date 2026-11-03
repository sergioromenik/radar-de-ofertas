# Radar de Ofertas

Coleta preços várias vezes por dia, compara com os últimos 365 dias de cada produto e gera `painel.html`
com uma nota para cada oferta e alertas de **possível bug**, **isca** e **promoção real**.
Só precisa do Python 3 (sem bibliotecas extras).

## Atalhos na área de trabalho

- **Radar de Ofertas**: atualiza (rápida, ou completa na 1ª vez do dia) e abre o painel.
- **Radar Ao Vivo**: abre `ao_vivo.html` e fica lendo ofertas novas a cada 30 s. Feche a janela preta para parar.

Os atalhos apontam para esta pasta; se ela mudar de lugar, recrie-os.

## Na nuvem (celular, com o PC desligado)

`.github/workflows/radar.yml` roda `python monitor.py nuvem` no GitHub Actions a cada 15 minutos
(completa na 1ª execução do dia, rápida nas outras) e publica `painel.html` e `ao_vivo.html` no GitHub Pages.
O histórico (`dados/precos.db`) fica no ramo `dados` do repositório, sobrescrito a cada execução.
Para rodar na hora: aba Actions do repositório → Radar de Ofertas → Run workflow.

## Modo ao vivo

A cada 30 s lê só a 1ª página do feed do Promobit (uma requisição de ~20 KB) e, a cada 60 s, a página de
recentes do Pelando (~430 KB). Avalia cada oferta nova
(bug, isca, real), procura até 3 delas no Zoom para comparar com 365 dias e atualiza `ao_vivo.html`,
que se recarrega sozinha. A cada 15 min roda a coleta rápida e refaz o painel completo.
Bipa quando chega bug, promoção real ou oferta com 30+ curtidas. Ajustes em `config.json` → `ao_vivo`
(o intervalo mínimo é 30 s).

## Pastas

    monitor.py            o programa
    config.json           ajustes (lojas, categorias, modos)
    abrir_painel.bat      atualiza e abre o painel (atalho "Radar de Ofertas")
    ao_vivo.bat           modo ao vivo (atalho "Radar Ao Vivo")
    rodar_monitor.bat     só coleta, sem abrir nada
    painel.html           o painel: abra no navegador
    ao_vivo.html          feed ao vivo (gerado pelo modo ao vivo)
    modelos/              modelo do painel
    dados/precos.db       histórico de preços (não apague)
    dados/execucoes.log   registro de cada coleta
    dados/*.txt           ASINs da Amazon e estatísticas grátis do Keepa

## Rodar

    rodar_monitor.bat            escolhe sozinho: completa na 1ª vez do dia, rápida nas outras
    rodar_monitor.bat rapida     força a rápida
    rodar_monitor.bat completa   força a completa
    rodar_monitor.bat painel     só refaz o painel

| Modo | O que faz | Tempo e dados (medidos) |
|---|---|---|
| rápida | Promobit, ofertas do Zoom, KaBuM (1 página por categoria) | ~30 s, ~27 requisições, ~11 MB |
| completa | tudo da rápida + categorias do Zoom, Keepa grátis, 250 buscas de vínculo e atualização dos históricos | ~10–15 min |

Pensado para rodar umas 5 vezes por dia: a primeira do dia é completa, as outras são rápidas.
O painel mostra o que foi visto nas últimas 24 horas e marca como "novo" o que apareceu desde a coleta anterior.

## Fontes

| Fonte | O que traz | Lojas cobertas |
|---|---|---|
| Zoom | menor preço entre lojas + 365 dias de histórico diário | Magalu, Mercado Livre, Amazon, KaBuM, Casas Bahia, Fast Shop e outras |
| Promobit | ofertas aprovadas pela comunidade e cupons | Amazon, Mercado Livre, Magalu, AliExpress, Shopee, Casas Bahia e outras |
| Pelando | recentes e mais quentes; temperatura (graus) = votos da comunidade, 10° valem 1 curtida na nota | todas, inclusive Steam, Nintendo e outras lojas digitais |
| KaBuM | catálogo pela API pública (preço no PIX) | KaBuM |
| Keepa grátis | mínimo da história, médias 90/180 dias (arquivo lido à mão, vale 7 dias) | Amazon |
| Keepa API | estatísticas de 365 dias (paga; coloque a chave em config.json) | Amazon |

Ofertas do KaBuM e do Promobit são comparadas com o mesmo produto no Zoom (preço de mercado de 365 dias).
Mercado Livre, Magalu, iFood e Shopee bloqueiam acesso direto sem login, por isso entram pelo Zoom e pelo Promobit.
O monitor não tenta contornar bloqueios: se uma fonte recusar, ela é pulada naquela coleta.

## Vereditos

- **bug**: na metade do menor preço do período, ou abaixo dele e 60%+ abaixo da mediana; ou marcado como bug no título.
- **real**: nota 45+ (bem abaixo da mediana de 365 dias ou no menor preço do ano).
- **boa**: nota 25+.
- **isca**: desconto anunciado sobre preço inflado, preço que subiu no último mês e só "voltou", ou "de" nunca praticado.
- **comunidade**: sem histórico, mas muito curtida no Promobit.
- **normal** / **sem histórico**: escondidos por padrão no painel; clique no resumo para ver.

Nota: cada 1% abaixo da mediana vale 1,25 ponto (até 50); menor preço do ano +20; curtidas até +15; isca −25.
