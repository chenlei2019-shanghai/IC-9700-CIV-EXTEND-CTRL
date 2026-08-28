import urllib.request, re, ssl
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE
url = 'https://raw.githubusercontent.com/Hamlib/Hamlib/master/rigs/icom/ic7300.c'
try:
    req = urllib.request.Request(url, headers={'User-Agent':'Mozilla/5.0'})
    html = urllib.request.urlopen(req, context=ctx, timeout=30).read().decode('utf-8', errors='ignore')
    lines = html.split('\n')
    start = None
    for i, line in enumerate(lines):
        if 'int ic9700_set_vfo(RIG *rig, vfo_t vfo)' in line and '{' not in line:
            # find the actual definition, skip the forward declaration
            pass
        if 'int ic9700_set_vfo(RIG *rig, vfo_t vfo)' in line and i > 100:
            start = i
            break
    if start:
        print(f'=== ic9700_set_vfo at line {start+1} ===')
        for i in range(start, min(start+120, len(lines))):
            print(f'{i+1}: {lines[i].rstrip()}')
    else:
        print('Function definition not found')
except Exception as e:
    print('ERROR:', e)
