import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/../')


from pdfalcon.pdf import PdfFile


# use `qpdfview <file>` to open pdf and view logs


def test_build_pdf_file():
    pdf = PdfFile()
    page = pdf.add_page()
    content_stream = page.add_text("basic test")
    print()
    print(pdf.format())
    print()

    # assert pdf.to_pretty_dict() == {'body': {'document_catalog': {'pages': [{'page': {'text': 'basic test'}}]}}}

