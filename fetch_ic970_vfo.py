import urllib.request, re, ssl
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE
url = 'https://raw.githubusercontent.com/Hamlib/Hamlib/master/rigs/icom/ic7300.c'
try:
    req = urllib.request.Request(url, headers={'User-Agent':'Mozilla/5.0'})
    html = urllib.request.urlopen(req, context=ctx, timeout=30).read().decode('utf-8', errors='ignore')
    lines = html.split('\n')
    # Find ic9700_set_vfo function
    start = None
    for i, line in enumerate(lines):
        if 'int ic9700_set_vfo' in line:
            start = i
            break
    if start:
        print('=== ic9700_set_vfo ===')
        for i in range(start, min(start+120, len(lines))):
            print(f'{i+1}: {lines[i].rstrip()}')
except Exception as e:
    print('ERROR:', e)
