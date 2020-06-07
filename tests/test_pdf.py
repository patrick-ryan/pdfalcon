import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/../')


from pdfalcon.pdf import PdfFile


def test_build_pdf_file():
    pdf = PdfFile.build()
    print(pdf.format())
    # page = pdf.add_page()
    # content = page.add_text("basic test")

    # assert pdf.to_pretty_dict() == {'body': {'document_catalog': {'pages': [{'page': {'text': 'basic test'}}]}}}

