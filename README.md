# PDFalcon

A pythonic PDF management library (and falcons are cool)


## Examples

Writing a new pdf:
```python
from pdfalcon.pdf import PdfFile
pdf = PdfFile()
page = pdf.add_page()
quote = '"Just as sight recognizes darkness by the experience of not seeing, '
    'so imagination recognizes the infinite by not understanding it" - Proclus'
text = page.add_text(quote, size=40, line_size=42, translate_x=150)

with open('/tmp/test.pdf', 'wb') as f:
    pdf.write(f)
```

Parsing a pdf:
```python
from pdfalcon.pdf import PdfFile
with open('/tmp/test.pdf', 'rb') as f:
    pdf = PdfFile(setup=False).read(f)
```


## Tips

To test/view in linux:
```
qpdfview /tmp/test.pdf
```


## Planned

* support special stream whitespace
* add date parsing
* add image management capabilities
  * add image to page
* finish adding graphics operations
* support more stream filters
* add more text formatting options
  * line wrapping
  * more fonts
* add methods to extract text/images from pdf + pages
* expand unit testing
* validate parser behavior with example PDFs
* add object stream support
* add annotations support
* add outline support
* add comments support
* add remaining page layout options
* documentation
* icon (multidirectional F with falcon outline)
* support security
* support encryption


## Expansion

* define common API spec for file formats, used for conversion support
* define common storage format for file components which can be the basis of a file generation tool


## Futurism

* ability to define new file formats quickly such that they are immediately accepted by standards-conforming tooling
