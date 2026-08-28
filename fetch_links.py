import urllib.request, re
url = 'http://www.igatemini.com/sat/manual3'
html = urllib.request.urlopen(url).read().decode('utf-8')
links = re.findall(r'href="([^"]+)"', html)
for l in links:
    if 'radio' in l.lower() or 'sat' in l.lower() or 'manual' in l.lower():
        print(l)
print('---all---')
seen = set()
for l in links:
    if l not in seen and (l.startswith('/') or l.startswith('http')):
        seen.add(l)
        print(l)
