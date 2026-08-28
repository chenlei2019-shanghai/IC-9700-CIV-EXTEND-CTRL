import urllib.request, re, ssl
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE
urls = [
    'https://wfview.org',
    'https://forum.wfview.org/search?q=satellite',
    'https://forum.wfview.org/search?q=passive',
    'https://forum.wfview.org/search?q=listen+only',
    'https://forum.wfview.org/t/ic-9700-sub-band-audio-over-network-connection/4640',
]
for url in urls:
    try:
        print('===', url, '===')
        req = urllib.request.Request(url, headers={'User-Agent':'Mozilla/5.0'})
        html = urllib.request.urlopen(req, context=ctx, timeout=15).read().decode('utf-8', errors='ignore')
        # extract text only
        text = re.sub(r'<[^>]+>', ' ', html)
        text = re.sub(r'\s+', ' ', text)
        # find satellite related snippets
        if 'satellite' in text.lower() or 'passive' in text.lower() or 'listen' in text.lower():
            idx = text.lower().find('satellite')
            if idx == -1:
                idx = text.lower().find('passive')
            if idx == -1:
                idx = text.lower().find('listen')
            print(text[max(0,idx-200):idx+800])
        else:
            print(text[:1500])
    except Exception as e:
        print('ERROR:', e)
