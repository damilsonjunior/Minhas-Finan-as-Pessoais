"""Atualiza os índices (CDI, IPCA, Ibovespa e Bitcoin) embutidos no index.html da Minhas Finanças Pessoais.

Roda todo dia no GitHub Actions (.github/workflows/indices.yml). Só usa a biblioteca padrão do Python.
- CDI: Banco Central, série SGS 12 (% ao dia). A data de corte ("ate") passa a ser o último dia com CDI publicado.
- IPCA: Banco Central, série SGS 433 (% ao mês). Meses ainda não divulgados usam uma projeção: a média
  mensal (geométrica) dos últimos 12 meses divulgados. Esses meses ficam listados em "ipca_proj".
- Ibovespa: fechamentos diários do Yahoo Finanças (^BVSP); guarda a variação de cada mês e a data do último
  fechamento ("ibov_ate"). Meses já fechados que existem no arquivo não são trocados.
- Bitcoin: preço diário em reais (Yahoo BTC-BRL; reserva: CoinGecko), guardado em "mkt". A tela aplica a variação
  sobre a última cotação que a pessoa registrou; se as duas fontes falharem, mantém o preço anterior.
Nenhum dado pessoal entra ou sai: o script só mexe nos índices de mercado.

uso: python3 scripts/atualizar_indices.py [index.html] [--fixtures pasta]   (fixtures: testes sem internet)
"""
import json, re, sys, time, math, datetime as dt, urllib.request, os

ARQ = next((a for a in sys.argv[1:] if not a.startswith('--') and a.endswith('.html')), 'index.html')
FIX = sys.argv[sys.argv.index('--fixtures') + 1] if '--fixtures' in sys.argv else None
HOJE = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - dt.timedelta(hours=3)          # horário de Brasília
UA = {'User-Agent': 'Mozilla/5.0 (atualizador de indices; github actions)'}


def baixar(url, nome):
    if FIX:
        with open(os.path.join(FIX, nome)) as f:
            return json.load(f)
    for tent in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
                return json.loads(r.read().decode('utf-8'))
        except Exception as e:
            print('falhou', url, e); time.sleep(5 * (tent + 1))
    raise RuntimeError('sem resposta: ' + url)


def sgs(serie, ini, fim, nome):
    """Série do SGS em janelas de até 9 anos (a API limita séries diárias a 10 anos por consulta)."""
    out, a = [], ini
    while a <= fim:
        b = min(fim, a.replace(year=a.year + 9) - dt.timedelta(days=1))
        url = (f'https://api.bcb.gov.br/dados/serie/bcdata.sgs.{serie}/dados?formato=json'
               f'&dataInicial={a:%d/%m/%Y}&dataFinal={b:%d/%m/%Y}')
        out += baixar(url, f'{nome}_{a:%Y}.json' if FIX else nome)
        a = b + dt.timedelta(days=1)
        if FIX: break
    return [(dt.datetime.strptime(x['data'], '%d/%m/%Y').date(), float(x['valor'])) for x in out]


html = open(ARQ, encoding='utf-8').read()
m = re.search(r'(<script id="iv-dados" type="application/json">)(.*?)(</script>)', html, re.S)
if not m:
    sys.exit('não achei os dados dos investimentos (#iv-dados) no ' + ARQ)
B = json.loads(m.group(2).replace('<\\/', '</'))
antes = json.dumps({k: B.get(k) for k in ('cdi', 'ipca', 'ibov', 'ate', 'ibov_ate', 'mkt')}, sort_keys=True)

# ---------- CDI ----------
d0 = dt.date.fromisoformat(B['cdi']['d0'])
cdi = sorted(set(sgs(12, d0, HOJE.date(), 'cdi')))
if len(cdi) < 100 or cdi[0][0] > d0 + dt.timedelta(days=10):
    sys.exit('CDI incompleto; nada foi alterado')
B['cdi'] = {'d0': d0.isoformat(), 'off': [(d - d0).days for d, _ in cdi], 'tx': [v for _, v in cdi]}
ate = cdi[-1][0]
B['ate'] = ate.isoformat()
B['data_base'] = HOJE.date().isoformat()

# ---------- IPCA ----------
ipca = sgs(433, dt.date(d0.year, d0.month, 1), HOJE.date(), 'ipca')
IP = {f'{d:%Y-%m}': v for d, v in ipca}
ult = sorted(IP)[-12:]
proj = round((pow(math.prod(1 + IP[k] / 100 for k in ult), 1 / 12) - 1) * 100, 2) if len(ult) == 12 else 0.4
B['ipca_proj'] = []
y, mth = map(int, max(IP).split('-'))
while True:
    mth += 1
    if mth > 12: y, mth = y + 1, 1
    k = f'{y:04d}-{mth:02d}'
    if k > ate.strftime('%Y-%m'): break
    IP[k] = proj; B['ipca_proj'].append(k)
B['ipca'] = dict(sorted(IP.items()))
B['ipca_proj_valor'] = proj

# ---------- Ibovespa ----------
try:
    j = baixar('https://query1.finance.yahoo.com/v8/finance/chart/%5EBVSP?interval=1d&range=2y', 'ibov.json')
    r = j['chart']['result'][0]
    pts = [(dt.datetime.fromtimestamp(t, dt.timezone.utc).replace(tzinfo=None) - dt.timedelta(hours=3), c) for t, c in zip(r['timestamp'], r['indicators']['quote'][0]['close']) if c]
    fim_mes = {}
    for d, c in pts:
        fim_mes[d.strftime('%Y-%m')] = (d.date(), c)
    meses = sorted(fim_mes)
    IB = dict(B.get('ibov') or {})
    for a, b in zip(meses, meses[1:]):
        if b not in IB or b >= max(IB):                    # só meses novos e o mês corrente
            IB[b] = round((fim_mes[b][1] / fim_mes[a][1] - 1) * 100, 2)
    B['ibov'] = dict(sorted(IB.items()))
    B['ibov_ate'] = fim_mes[meses[-1]][0].isoformat()
except Exception as e:
    print('Ibovespa não atualizado (mantido o anterior):', e)

# ---------- Bitcoin (BTC em reais, um preço por dia corrido; dias sem preço repetem o anterior) ----------
def serie_diaria(pares):
    pares = sorted(pares)
    d0, fim, out, i, ult = pares[0][0], pares[-1][0], [], 0, None
    d = d0
    while d <= fim:
        while i < len(pares) and pares[i][0] <= d:
            ult = pares[i][1]; i += 1
        out.append(round(ult, 2)); d += dt.timedelta(days=1)
    return {'d0': d0.isoformat(), 'px': out, 'ate': fim.isoformat()}

def btc_yahoo():
    j = baixar('https://query1.finance.yahoo.com/v8/finance/chart/BTC-BRL?interval=1d&range=2y', 'btc.json')
    r = j['chart']['result'][0]
    return [(dt.datetime.fromtimestamp(t, dt.timezone.utc).date(), c) for t, c in zip(r['timestamp'], r['indicators']['quote'][0]['close']) if c]

def btc_coingecko():
    j = baixar('https://api.coingecko.com/api/v3/coins/bitcoin/market_chart?vs_currency=brl&days=365&interval=daily', 'btc_cg.json')
    return [(dt.datetime.fromtimestamp(t / 1000, dt.timezone.utc).date(), c) for t, c in j['prices'] if c]

MKT = dict(B.get('mkt') or {})
for fonte in (btc_yahoo, btc_coingecko):
    try:
        pares = [(d, c) for d, c in fonte() if d <= HOJE.date()]
        if len(pares) < 30: raise RuntimeError('poucos pontos')
        MKT['BTC-BRL'] = serie_diaria(pares); break
    except Exception as e:
        print('Bitcoin: fonte', fonte.__name__, 'falhou:', e)
else:
    print('Bitcoin não atualizado (mantido o anterior)')
if MKT: B['mkt'] = MKT

depois = json.dumps({k: B.get(k) for k in ('cdi', 'ipca', 'ibov', 'ate', 'ibov_ate', 'mkt')}, sort_keys=True)
novo = json.dumps(B, ensure_ascii=False, separators=(',', ':')).replace('</', '<\\/')
html = html[:m.start(2)] + novo + html[m.end(2):]
open(ARQ, 'w', encoding='utf-8').write(html)
print('mudou' if antes != depois else 'sem mudança', '| CDI até', B['ate'], '| IPCA projetado:', B['ipca_proj'] or '-', '| Ibovespa até', B.get('ibov_ate'), '| Bitcoin até', (B.get('mkt') or {}).get('BTC-BRL', {}).get('ate'))
