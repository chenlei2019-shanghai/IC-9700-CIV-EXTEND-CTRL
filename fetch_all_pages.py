import urllib.request, re
url = 'http://www.igatemini.com/sat/manual3'
html = urllib.request.urlopen(url).read().decode('utf-8')
links = re.findall(r'\?a=1&p=([^"\']+)', html)
for l in sorted(set(links)):
    print(l)
