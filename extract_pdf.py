import fitz, os, sys
pdf = 'igatemini.com_sat_manual3_p=PDF.pdf'
if not os.path.exists(pdf):
    print('PDF not found:', pdf)
    sys.exit(1)
doc = fitz.open(pdf)
print(f'Pages: {len(doc)}')
text = ''
for i, page in enumerate(doc):
    t = page.get_text()
    text += f'--- Page {i+1} ---\n{t}\n'
with open('igatemini_text.txt', 'w', encoding='utf-8') as f:
    f.write(text)
print('Saved to igatemini_text.txt, length', len(text))
print(text[:4000])
