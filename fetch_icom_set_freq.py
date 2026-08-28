import urllib.request, re, ssl
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE
url = 'https://raw.githubusercontent.com/Hamlib/Hamlib/master/rigs/icom/icom.c'
try:
    req = urllib.request.Request(url, headers={'User-Agent':'Mozilla/5.0'})
    html = urllib.request.urlopen(req, context=ctx, timeout=30).read().decode('utf-8', errors='ignore')
    lines = html.split('\n')
    
    # Find icom_set_freq
    for keyword in ['icom_set_freq', 'icom_set_split_freq', 'icom_set_split_vfo']:
        start = None
        for i, line in enumerate(lines):
            if f'int {keyword}' in line and i > 100:
                start = i
                break
        if start:
            print(f'=== {keyword} at line {start+1} ===')
            for i in range(start, min(start+80, len(lines))):
                print(f'{i+1}: {lines[i].rstrip()[:300]}')
            print()
except Exception as e:
    print('ERROR:', e)
