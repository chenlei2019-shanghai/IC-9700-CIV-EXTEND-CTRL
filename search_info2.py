import urllib.request, re, ssl
import urllib.parse
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

queries = [
    "wfview IC-9700 satellite doppler",
    "ICOM LAN protocol multiple clients CI-V",
    "iGateMini SAT remote operation audio",
]
for q in queries:
    try:
        print('===', q, '===')
        url = 'https://html.duckduckgo.com/html/?q=' + urllib.parse.quote(q)
        req = urllib.request.Request(url, headers={'User-Agent':'Mozilla/5.0'})
        html = urllib.request.urlopen(req, context=ctx, timeout=15).read().decode('utf-8', errors='ignore')
        # parse results
        for m in re.findall(r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html, re.DOTALL)[:5]:
            title = re.sub(r'<[^>]+>', '', m[1])
            link = m[0]
            print(title[:100], '|', link[:200])
    except Exception as e:
        print('ERROR:', e)
