import urllib.request, re, ssl
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE
url = 'https://raw.githubusercontent.com/Hamlib/Hamlib/master/rigs/icom/ic9700.c'
try:
    req = urllib.request.Request(url, headers={'User-Agent':'Mozilla/5.0'})
    html = urllib.request.urlopen(req, context=ctx, timeout=30).read().decode('utf-8', errors='ignore')
    lines = html.split('\n')
    print('Total lines:', len(lines))
    for i, line in enumerate(lines):
        if any(x in line.lower() for x in ['vfo','main','sub','split','satellite','vfo_ops']):
            if len(line.strip()) > 0:
                print(f'{i+1}: {line.strip()[:250]}')
except Exception as e:
    print('ERROR:', e)
