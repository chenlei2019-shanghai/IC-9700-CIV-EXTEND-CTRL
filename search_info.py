import urllib.request, re, ssl, json
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

queries = [
    "wfview IC-9700 multiple clients simultaneous control",
    "ICOM IC-9700 LAN multiple CI-V connections same time",
    "wfview satellite operation doppler tracking",
]
for q in queries:
    try:
        print('===', q, '===')
        url = 'https://www.google.com/search?q=' + urllib.parse.quote(q)
        req = urllib.request.Request(url, headers={'User-Agent':'Mozilla/5.0'})
        html = urllib.request.urlopen(req, context=ctx, timeout=15).read().decode('utf-8', errors='ignore')
        # find result titles and snippets
        for m in re.findall(r'<h3[^>]*>(.*?)</h3>.*?<div[^>]*>(.*?)</div>', html, re.DOTALL)[:5]:
            title = re.sub(r'<[^>]+>', '', m[0])
            snippet = re.sub(r'<[^>]+>', '', m[1])
            print(title[:100], '|', snippet[:300])
    except Exception as e:
        print('ERROR:', e)
